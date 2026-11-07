"""Durable, actor-bound native package imports with bounded request steps.

Only the final transaction grants visibility. Plans are immutable and hash-bound
outside the small job document; metadata and the cumulative receipt commit
atomically. There is no process-local job, lease, or background publication.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import copy
import gzip
import hashlib
import io
import json
import re
import time

from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_user_id
from hosted.firestore.model_registry import MODELS
from hosted.firestore.repositories import _fingerprint, render_model_path
from hosted.idempotency import stable_entity_id, stable_project_id
from hosted.import_staging import package_import_staging
from hosted.packages import HostedProjectPackageService, _fields, _restore_safe, _SKIP
from hosted.storage.keys import project_asset_key
from hosted.storage.publication import ImmutableObjectPublisher
from security.image_security import HOSTED_DEFAULT_IMAGE_BYTES, inspect_hosted_image_bytes

BATCH_SIZE = 64
UPLOAD_WORKERS = 8
LIFETIME = 86400
MAX_SOURCE_BYTES = 64 * 1024**2
MAX_PLAN_BYTES = 256 * 1024**2  # the existing expanded archive boundary, not a row cap
TARGET_PLAN_BYTES = 4 * 1024**2
_EMPTY_CHAIN = '0' * 64


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _parallel(fn, items):
    # Never create an unbounded queue of futures, or share an entered Context.
    result = []
    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        for start in range(0, len(items), UPLOAD_WORKERS):
            pending = [pool.submit(copy_context().run, fn, item) for item in items[start:start + UPLOAD_WORKERS]]
            result.extend(future.result() for future in pending)
    return result


class _GuardedObjects:
    def __init__(self, objects, check):
        self.objects, self.check = objects, check

    def write_chunks(self, name, chunks, **kwargs):
        self.check()
        result = self.objects.write_chunks(name, chunks, **kwargs)
        self.check()
        return result

    def stat(self, name):
        self.check()
        result = self.objects.stat(name)
        self.check()
        return result

    def read_chunks(self, name, **kwargs):
        self.check()
        for chunk in self.objects.read_chunks(name, **kwargs):
            yield chunk
        self.check()


class PackageImport:
    def __init__(self, projects, *, clock=time.time):
        self.projects = projects
        self.documents = projects.documents
        self.clock = clock

    @staticmethod
    def _path(uid, job):
        # Explicitly classified by package_import_ownership/account deletion census.
        return ('users', require_user_id(uid), 'package_imports', require_opaque_id(job, label='import_job'))

    @classmethod
    def _plan_path(cls, uid, job, index):
        return (*cls._path(uid, job), 'plans', str(index))

    @classmethod
    def _receipt_path(cls, uid, job):
        return (*cls._path(uid, job), 'receipts', 'head')

    @staticmethod
    def _account(tx, uid, rows=None):
        user = rows[('users', uid)] if rows is not None else tx.get(('users', uid))
        lock = rows[('account_erasure_locks', uid)] if rows is not None else tx.get(('account_erasure_locks', uid))
        if (user is None or user.data.get('status', 'active') != 'active'
                or user.data.get('_bm_erasure_operation') or lock is not None):
            raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
        return dict(user.data)

    def _load_tx(self, tx, uid, job, *, active=False, expected=None):
        paths = [self._path(uid, job), ('users', uid), ('account_erasure_locks', uid)]
        if expected is not None:
            paths += [('hosted_import_operations', expected['operation']), ('projects', expected['project_id']),
                      ('projects', expected['project_id'], 'members', uid)]
        rows = dict(zip(paths, tx.get_many(paths)))
        row = rows[self._path(uid, job)]
        if row is None or row.data.get('actor') != uid:
            raise DocumentNotFound('HOSTED_IMPORT_NOT_FOUND')
        data = dict(row.data)
        self._account(tx, uid, rows)
        if expected is not None and any(data.get(key) != value for key, value in self._binding(expected).items()):
            raise DocumentConflict('HOSTED_IMPORT_BINDING_INVALID')
        if (data.get('v') != 1 or data.get('job_id') != job
                or stable_project_id(data['operation']) != data.get('project_id')
                or stable_entity_id('package_', data['operation']) != job):
            raise DocumentConflict('HOSTED_IMPORT_BINDING_INVALID')
        if expected is None:
            paths = [('hosted_import_operations', data['operation']), ('projects', data['project_id']),
                     ('projects', data['project_id'], 'members', uid)]
            rows.update(zip(paths, tx.get_many(paths)))
        reservation = rows[('hosted_import_operations', data['operation'])]
        if (reservation is None or reservation.data.get('user_id') != uid
                or reservation.data.get('sha256') != data['source_sha256']
                or reservation.data.get('name_override') != data['name_override']):
            raise DocumentConflict('HOSTED_IMPORT_BINDING_INVALID')
        root = rows[('projects', data['project_id'])]
        if data['phase'] == 'completed':
            member = rows[('projects', data['project_id'], 'members', uid)]
            if (root is None or member is None or member.data.get('role') != 'owner'
                    or root.data.get('status', 'active') != 'active'
                    or root.data.get('_bm_erasure_operation')):
                raise DocumentNotFound('HOSTED_IMPORT_PROJECT_REMOVED')
        elif root is not None:
            raise DocumentConflict('HOSTED_IMPORT_PROJECT_ALREADY_PUBLISHED')
        if active and data['phase'] in {'completed', 'cancelled'}:
            raise DocumentConflict('HOSTED_IMPORT_ALREADY_PUBLISHED' if data['phase'] == 'completed' else 'HOSTED_IMPORT_CANCELLED')
        if data['phase'] not in {'completed', 'cancelled'} and self.clock() > data['expires_at']:
            raise DocumentConflict('HOSTED_IMPORT_EXPIRED')
        return data

    def _load(self, uid, job, *, active=False):
        return self.documents.run_transaction(lambda tx: self._load_tx(tx, uid, job, active=active))

    def _objects(self, uid, job, data):
        # Expected identity only selects fresh BatchGet paths; it is not cached
        # authority. The job, reservation, account, lock and root are reread.
        check = lambda: self.documents.run_transaction(
            lambda tx: self._load_tx(tx, uid, job, active=True, expected=data))
        return _GuardedObjects(self.projects.objects, check)

    def _packages(self, uid, job, data):
        # Isolate request-scoped guards. Never mutate shared runtime services.
        projects = copy(self.projects)
        projects.domain = copy(self.projects.domain)
        projects.objects = self._objects(uid, job, data)
        projects.domain.objects = projects.objects
        projects.domain.immutable = ImmutableObjectPublisher(projects.objects)
        return HostedProjectPackageService(projects)

    @staticmethod
    def _public(data):
        fields = ('job_id', 'phase', 'next_batch', 'total_batches', 'completed_records',
                  'total_records', 'completed_images', 'total_images', 'source_sha256')
        result = {key: data[key] for key in fields}
        result['ready'] = data['phase'] == 'ready'
        if data['phase'] == 'completed':
            result.update(project=data['result'], reload=True, ok=True)
        return result

    def status(self, uid, job):
        return self._public(self._load(require_user_id(uid), job))

    @staticmethod
    def _annotation_items(plan, pid, stamp):
        """Rebuild packed ranges using the validated source, not provider scans.

        One pack is one bounded immutable canonical record (existing pack limits
        apply). Never split one head across steps or rewrite a partial head.
        """
        from api.annotations.editing_lexicon import PASSES
        from hosted.editing_packs import ITEM, MAX_PACKS, MAX_ROWS, MAX_PACK_ROWS, MAX_DECODED, MAX_TOTAL_DECODED
        blocks = {r['id']: r['data'] for r in plan['records'] if r['entity'] == 'manuscript_block'}
        chapters = {r['id']: r['data'] for r in plan['records'] if r['entity'] == 'chapter'}
        labels = {r['id'] for r in plan['records'] if r['entity'] == 'markup_label'}
        result, groups, seen = [], {}, set()
        for item in plan['annotations']:
            iid = require_opaque_id(item['ids']['annotation_id'], label='annotation_id')
            if iid in seen:
                raise HostedValidationError('HOSTED_PACKAGE_ENTITY_COLLISION')
            seen.add(iid)
            data = {k: _restore_safe(v) for k, v in item['data'].items() if k != 'project_id'}
            match = ITEM.fullmatch(iid)
            kind = str(data.get('body') or '').removeprefix('editing-search/v1:')
            if match is None:
                result.append({'kind': 'record', 'entity': 'annotation', 'id': iid,
                               'parents': {'project_id': pid}, 'data': data})
                continue
            if kind not in PASSES or data.get('annotation_type') != 'highlight':
                raise HostedValidationError('HOSTED_PACKAGE_ANNOTATION_CONTEXT_INVALID')
            block = blocks.get(data.get('block_id'))
            chapter = chapters.get(block.get('chapter_id')) if block else None
            if (chapter is None or data.get('label_id') not in labels
                    or block.get('tab_id') not in (None, chapter['tab_id'])):
                raise HostedValidationError('HOSTED_PACKAGE_ANNOTATION_CONTEXT_INVALID')
            hid = 'epack_' + match[1]
            key = (chapter['tab_id'], data['label_id'], kind)
            head, rows = groups.setdefault(hid, ({'id': hid, 'annotation_id': hid,
                'project_id': pid, 'tab_id': key[0], 'label_id': key[1], 'scan': key[2],
                'annotation_type': 'editing-pack/v1', 'generation': 1, 'created_at': stamp, 'updated_at': stamp}, []))
            if key != (head['tab_id'], head['label_id'], head['scan']):
                raise HostedValidationError('HOSTED_PACKAGE_ANNOTATION_CONTEXT_INVALID')
            text = block.get('text_content') or ''
            start, end = data.get('start_offset'), data.get('end_offset')
            if not data.get('stale') and not (type(start) is int and type(end) is int
                    and 0 <= start <= end <= len(text) and text[start:end] == data.get('selected_text')):
                data['stale'] = True
            rows.append({**data, 'id': iid, 'annotation_id': iid, 'project_id': pid,
                         'chapter_id': None, 'source_generation': 1, 'generation': 1,
                         'created_at': stamp, 'updated_at': stamp})
        if len(groups) > MAX_PACKS or sum(len(rows) for _, rows in groups.values()) > MAX_ROWS:
            raise HostedValidationError('HOSTED_PACKAGE_ANNOTATION_LIMIT')
        decoded = 0
        for head, rows in groups.values():
            size = len(_json({'v': 1, 'head': head['id'], 'rows': rows}))
            decoded += size
            if len(rows) > MAX_PACK_ROWS or size > MAX_DECODED or decoded > MAX_TOTAL_DECODED:
                raise HostedValidationError('HOSTED_PACKAGE_ANNOTATION_LIMIT')
            result.append({'kind': 'pack', 'head': head, 'rows': rows})
        return result

    @staticmethod
    def _binding(data):
        return {key: data[key] for key in ('job_id', 'actor', 'project_id', 'source_sha256', 'operation')}

    def _batches(self, plan, data):
        items = [{'kind': 'asset', 'path': item['path'], 'extension': item['extension'], 'sha256': item['sha256'],
                  'data': base64.b64encode(item['data']).decode('ascii')} for item in plan['assets']]
        items.extend({'kind': 'record', **item} for item in plan['records'])
        items.extend(self._annotation_items(plan, data['project_id'], data['stamp']))
        batches, current, size = [], [], 0
        for item in items:
            encoded_size = len(_json(item))
            if encoded_size > MAX_PLAN_BYTES:
                raise HostedValidationError('HOSTED_IMPORT_PLAN_TOO_LARGE')
            if current and (len(current) >= BATCH_SIZE or size + encoded_size > TARGET_PLAN_BYTES):
                batches.append(current)
                current, size = [], 0
            current.append(item)
            size += encoded_size
        if current:
            batches.append(current)
        return batches

    def begin(self, raw, uid, *, name_override=None, operation):
        uid = require_user_id(uid)
        op = require_opaque_id(operation, label='operation')
        if self.projects.domain.encrypted_objects is not None or self.projects.domain.encrypted_fields is not None:
            raise HostedValidationError('HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY')
        if not raw or len(raw) > MAX_SOURCE_BYTES:
            raise HostedValidationError('HOSTED_PACKAGE_TOO_LARGE')
        source_hash = hashlib.sha256(raw).hexdigest()
        job, pid = stable_entity_id('package_', op), stable_project_id(op)
        name = str(name_override or '')
        path = self._path(uid, job)
        def previous(tx):
            self._account(tx, uid)
            old = tx.get(path)
            if old is None:
                return None
            data = self._load_tx(tx, uid, job)
            if data['source_sha256'] != source_hash or data['name_override'] != name:
                raise DocumentConflict('HOSTED_IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PACKAGE')
            return data
        old = self.documents.run_transaction(previous)
        if old is not None and old['phase'] != 'preparing':
            return self._public(old)
        from hosted.native_package import prepare_project_package
        plan = prepare_project_package(raw, pid, name_override=name_override)
        stamp = self.projects.domain.clock.now_iso()
        data = {'v': 1, 'actor': uid, 'job_id': job, 'project_id': pid, 'operation': op,
                'name_override': name, 'title': plan['title'], 'source_sha256': source_hash,
                'phase': 'preparing', 'next_batch': 0, 'total_batches': 0,
                'completed_records': 0, 'total_records': 0, 'completed_images': 0, 'total_images': 0,
                'stamp': stamp, 'expires_at': self.clock() + LIFETIME, 'expected_chain': _EMPTY_CHAIN,
                'prepared_batches': 0, 'preparation_chain': _EMPTY_CHAIN}
        def reserve(tx):
            self._account(tx, uid)
            existing = tx.get(path)
            reservation_path = ('hosted_import_operations', op)
            reservation = tx.get(reservation_path)
            if existing is not None:
                return previous(tx)
            if tx.get(('projects', pid)) is not None:
                raise DocumentConflict('HOSTED_IMPORT_PROJECT_ALREADY_PUBLISHED')
            if reservation is not None and (reservation.data.get('user_id') != uid
                    or reservation.data.get('sha256') != source_hash or reservation.data.get('name_override') != name):
                raise DocumentConflict('HOSTED_IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PACKAGE')
            if reservation is None:
                tx.create(reservation_path, {'user_id': uid, 'sha256': source_hash, 'name_override': name, 'created_at': stamp})
            tx.create(path, data)
            return data
        data = self.documents.run_transaction(reserve)
        if data['phase'] != 'preparing':
            return self._public(data)
        batches = self._batches(plan, data)
        # Pin the complete pure plan before the first source-plan publication.
        # On a replica/version change the same archive cannot quietly become a
        # different prepared suffix after an already committed prefix.
        plan_digest = hashlib.sha256()
        for entries in batches:
            plan_digest.update(bytes.fromhex(_hash(entries)))
        fingerprint = plan_digest.hexdigest()
        def pin(tx):
            current = self._load_tx(tx, uid, job, active=True)
            if current.get('plan_sha256') not in (None, fingerprint):
                raise DocumentConflict('HOSTED_IMPORT_PLAN_CHANGED')
            if current['phase'] != 'preparing':
                return current
            current.update(plan_sha256=fingerprint, total_batches=len(batches),
                           total_records=sum(item['kind'] != 'asset' for group in batches for item in group),
                           total_images=sum(item['kind'] == 'asset' for group in batches for item in group))
            tx.set(path, current)
            return current
        data = self.documents.run_transaction(pin)
        if data['phase'] != 'preparing':
            return self._public(data)
        objects = self._objects(uid, job, data)
        def publish(item):
            index, entries = item
            source = _json({'v': 1, **self._binding(data), 'index': index, 'items': entries})
            if len(source) > MAX_PLAN_BYTES:
                raise HostedValidationError('HOSTED_IMPORT_PLAN_TOO_LARGE')
            encoded = gzip.compress(source, mtime=0)
            digest = hashlib.sha256(encoded).hexdigest()
            key = f'projects/{pid}/package-imports/{job}/plans/{index}/{digest}.json.gz'
            obj = ImmutableObjectPublisher(objects).publish_bytes(key, encoded, max_bytes=MAX_PLAN_BYTES,
                                                                  content_type='application/gzip').object
            return {'v': 1, **self._binding(data), 'index': index, 'object_name': obj.name,
                    'object_generation': obj.generation, 'object_sha256': obj.sha256, 'object_size': obj.size,
                    'decoded_bytes': len(source), 'records': sum(item['kind'] != 'asset' for item in entries),
                    'images': sum(item['kind'] == 'asset' for item in entries)}
        with package_import_staging(op, uid):
            # Persist progress after <=8 plan uploads. An HTTP timeout can lose
            # this small group only; same-key replay skips the committed prefix.
            for start in range(data['prepared_batches'], len(batches), UPLOAD_WORKERS):
                if start < data['prepared_batches']:
                    continue
                group = _parallel(publish, list(enumerate(batches[start:start + UPLOAD_WORKERS], start)))
                chain = data['preparation_chain']
                for descriptor in group:
                    descriptor['previous_chain'] = chain
                    chain = _hash(descriptor)
                    descriptor['chain'] = chain
                def store(tx):
                    current = self._load_tx(tx, uid, job, active=True)
                    if current['phase'] != 'preparing':
                        return current
                    paths = [self._plan_path(uid, job, part['index']) for part in group]
                    existing = tx.get_many(paths)
                    for row, part in zip(existing, group):
                        if row is not None and dict(row.data) != part:
                            raise DocumentConflict('HOSTED_IMPORT_PLAN_CHANGED')
                    if current['prepared_batches'] > start:
                        if any(row is None for row in existing):
                            raise DocumentConflict('HOSTED_IMPORT_PLAN_MISSING')
                        return current
                    if (current['prepared_batches'] != start
                            or current['preparation_chain'] != group[0]['previous_chain']):
                        raise DocumentConflict('HOSTED_IMPORT_PLAN_CHANGED')
                    for row, target, part in zip(existing, paths, group):
                        if row is None:
                            tx.create(target, part)
                    current.update(prepared_batches=start + len(group), preparation_chain=chain)
                    tx.set(path, current)
                    return current
                data = self.documents.run_transaction(store)
            def ready(tx):
                current = self._load_tx(tx, uid, job, active=True)
                if current['phase'] != 'preparing':
                    return current
                receipt = tx.get(self._receipt_path(uid, job))
                if receipt is not None or current['prepared_batches'] != current['total_batches']:
                    raise DocumentConflict('HOSTED_IMPORT_RECEIPT_INVALID')
                current.update(expected_chain=current['preparation_chain'],
                               phase='staging' if current['total_batches'] else 'ready')
                tx.create(self._receipt_path(uid, job), {'next_batch': 0, 'chain': _EMPTY_CHAIN,
                                                       'records': 0, 'images': 0})
                tx.set(path, current)
                return current
            return self._public(self.documents.run_transaction(ready))

    def _read_plan(self, uid, job, data, index):
        def descriptor(tx):
            self._load_tx(tx, uid, job, active=True)
            row = tx.get(self._plan_path(uid, job, index))
            if row is None:
                raise HostedValidationError('HOSTED_IMPORT_PLAN_MISSING')
            return dict(row.data)
        part = self.documents.run_transaction(descriptor)
        expected_fields = {'v', *self._binding(data), 'index', 'object_name', 'object_generation',
                           'object_sha256', 'object_size', 'decoded_bytes', 'records', 'images', 'previous_chain', 'chain'}
        expected_name = f"projects/{data['project_id']}/package-imports/{job}/plans/{index}/{part.get('object_sha256')}.json.gz"
        if (set(part) != expected_fields or type(part['v']) is not int or part['v'] != 1
                or type(part['index']) is not int or part['index'] != index
                or any(not isinstance(part[key], str) or re.fullmatch('[0-9a-f]{64}', part[key]) is None
                       for key in ('object_sha256', 'previous_chain', 'chain'))
                or any(type(part[key]) is not int or not 0 <= part[key] <= BATCH_SIZE
                       for key in ('records', 'images'))
                or any(part.get(key) != value for key, value in self._binding(data).items())
                or part['object_name'] != expected_name or type(part['object_generation']) is not int
                or part['object_generation'] < 1 or type(part['object_size']) is not int
                or not 0 < part['object_size'] <= MAX_PLAN_BYTES or type(part['decoded_bytes']) is not int
                or not 0 < part['decoded_bytes'] <= MAX_PLAN_BYTES
                or part['chain'] != _hash({k: v for k, v in part.items() if k != 'chain'})):
            raise HostedValidationError('HOSTED_IMPORT_PLAN_BINDING_INVALID')
        raw = b''.join(self._objects(uid, job, data).read_chunks(part['object_name'], max_bytes=part['object_size'],
                                              if_generation_match=part['object_generation']))
        if len(raw) != part['object_size'] or hashlib.sha256(raw).hexdigest() != part['object_sha256']:
            raise HostedValidationError('HOSTED_IMPORT_PLAN_INTEGRITY_FAILED')
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                decoded = stream.read(part['decoded_bytes'] + 1)
            body = json.loads(decoded)
            if (len(decoded) != part['decoded_bytes'] or set(body) != {'v', *self._binding(data), 'index', 'items'}
                    or type(body['v']) is not int or body['v'] != 1
                    or type(body['index']) is not int or body['index'] != index
                    or any(body.get(key) != value for key, value in self._binding(data).items())
                    or not isinstance(body['items'], list) or not 1 <= len(body['items']) <= BATCH_SIZE):
                raise ValueError()
        except (ValueError, TypeError, KeyError, OSError):
            raise HostedValidationError('HOSTED_IMPORT_PLAN_INVALID') from None
        return part, body['items']

    def _prepare(self, packages, data, item, bundles=None):
        pid = data['project_id']
        kind = item.get('kind')
        if kind == 'record':
            if set(item) != {'kind', 'entity', 'parents', 'id', 'data'}:
                raise HostedValidationError('HOSTED_IMPORT_PLAN_INVALID')
            model = MODELS.get(item['entity'])
            if model is None or item['entity'] in _SKIP:
                raise HostedValidationError('HOSTED_IMPORT_PLAN_INVALID')
            fields = _fields(model.path_template)
            if (not isinstance(item['parents'], dict) or set(item['parents']) != set(fields[:-1])
                    or item['parents'].get('project_id') != pid or not isinstance(item['data'], dict)):
                raise HostedValidationError('HOSTED_IMPORT_PLAN_BINDING_INVALID')
            render_model_path(model, **item['parents'], **{fields[-1]: item['id']})
            return packages._prepare_import_entity(item['entity'], item['parents'], item['data'], entity_id=item['id'],
                                                   read_bundle=(bundles or {}).get(item['id']))
        if kind == 'asset':
            if set(item) != {'kind', 'path', 'extension', 'sha256', 'data'}:
                raise HostedValidationError('HOSTED_IMPORT_PLAN_INVALID')
            blob = base64.b64decode(item['data'], validate=True)
            digest = hashlib.sha256(blob).hexdigest()
            if digest != item['sha256']:
                raise HostedValidationError('HOSTED_PACKAGE_ASSET_HASH_MISMATCH')
            info = inspect_hosted_image_bytes(blob, item['extension'], byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
            obj = ImmutableObjectPublisher(packages.objects).publish_bytes(project_asset_key(pid, digest, info.extension),
                    blob, max_bytes=HOSTED_DEFAULT_IMAGE_BYTES, content_type=info.media_type).object
            canonical_name = 'img_' + digest[:24] + info.extension
            name = str(item['path']).removeprefix('/api/assets/')
            if (item['path'] != '/api/assets/' + name or not name or name in {'.', '..'}
                    or '/' in name or '\\' in name or len(name) > 240):
                raise HostedValidationError('HOSTED_PACKAGE_ASSET_PATH_INVALID')
            aid = 'asset_' + (digest if name == canonical_name else hashlib.sha256(item['path'].encode()).hexdigest())
            return packages._prepare_import_entity('project_asset', {'project_id': pid},
                {'api_path': '/api/assets/' + name, 'name': name, 'sha256': digest, 'object_name': obj.name,
                 'object_generation': obj.generation, 'object_sha256': obj.sha256, 'object_size': obj.size},
                entity_id=aid)
        if kind == 'pack':
            from hosted.editing_packs import PackedAnnotations
            if set(item) != {'kind', 'head', 'rows'} or item['head'].get('project_id') != pid:
                raise HostedValidationError('HOSTED_IMPORT_PLAN_INVALID')
            packs = PackedAnnotations(packages.domain)
            head = packs.stage(pid, item['head'], item['rows'])
            packs.rows(pid, head)
            head['_bm_create_operation'] = data['operation']
            head['_bm_create_fingerprint'] = _hash(item)
            return ('projects', pid, 'annotations', head['id']), head
        raise HostedValidationError('HOSTED_IMPORT_PLAN_INVALID')

    def step(self, uid, job, index):
        uid = require_user_id(uid)
        data = self._load(uid, job)
        if data['phase'] == 'completed':
            return self._public(data)
        if data['phase'] == 'cancelled':
            raise DocumentConflict('HOSTED_IMPORT_CANCELLED')
        if type(index) is not int or not 0 <= index < data['total_batches']:
            raise HostedValidationError('HOSTED_IMPORT_BATCH_INVALID')
        if index < data['next_batch']:
            return self._public(data)
        if index != data['next_batch'] or data['phase'] != 'staging':
            raise DocumentConflict('HOSTED_IMPORT_BATCH_OUT_OF_ORDER')
        part, items = self._read_plan(uid, job, data, index)
        if part['records'] != sum(i.get('kind') != 'asset' for i in items) or part['images'] != sum(i.get('kind') == 'asset' for i in items):
            raise HostedValidationError('HOSTED_IMPORT_PLAN_BINDING_INVALID')
        packages = self._packages(uid, job, data)
        with package_import_staging(data['operation'], uid):
            # Reuse the existing optional read acceleration. Every paragraph
            # still has its canonical per-block pointer for 10.5.95 rollback.
            bodies = {item['id']: {key: item['data'].get(key, '') for key in ('content_html', 'text_content')}
                      for item in items if item.get('kind') == 'record' and item.get('entity') == 'manuscript_block'}
            bundles = {}
            if bodies:
                encoded = gzip.compress(_json({'entries': bodies}), mtime=0)
                bid = 'b_bundle_' + hashlib.sha256('\0'.join(sorted(bodies)).encode()).hexdigest()[:24]
                pointer = packages.domain._publish_hybrid('manuscript_block',
                    {'project_id': data['project_id'], 'block_id': bid}, encoded)
                bundles = {eid: {**pointer, 'bundle_entry': eid} for eid in bodies}
            prepared = _parallel(lambda item: self._prepare(packages, data, item, bundles), items)
            if len({path for path, _ in prepared}) != len(prepared):
                raise HostedValidationError('HOSTED_IMPORT_DUPLICATE_ENTITY')
            def commit(tx):
                current = self._load_tx(tx, uid, job, active=True)
                receipt = tx.get(self._receipt_path(uid, job))
                descriptor = tx.get(self._plan_path(uid, job, index))
                prior = tx.get_many([path for path, _ in prepared])
                if index < current['next_batch']:
                    return current
                if index != current['next_batch'] or current['phase'] != 'staging':
                    raise DocumentConflict('HOSTED_IMPORT_BATCH_OUT_OF_ORDER')
                if (receipt is None or descriptor is None or dict(descriptor.data) != part
                        or receipt.data != {'next_batch': index, 'chain': part['previous_chain'],
                            'records': current['completed_records'], 'images': current['completed_images']}):
                    raise DocumentConflict('HOSTED_IMPORT_RECEIPT_INVALID')
                for row, (_, stored) in zip(prior, prepared):
                    if row is not None and any(row.data.get(key) != stored[key] for key in ('_bm_create_operation', '_bm_create_fingerprint')):
                        raise DocumentConflict('HOSTED_PACKAGE_ENTITY_COLLISION')
                for row, (path, stored) in zip(prior, prepared):
                    if row is None:
                        tx.create(path, stored)
                current.update(next_batch=index + 1, completed_records=current['completed_records'] + part['records'],
                               completed_images=current['completed_images'] + part['images'])
                if current['next_batch'] == current['total_batches']:
                    current['phase'] = 'ready'
                tx.set(self._receipt_path(uid, job), {'next_batch': index + 1, 'chain': part['chain'],
                        'records': current['completed_records'], 'images': current['completed_images']})
                tx.set(self._path(uid, job), current)
                return current
            return self._public(self.documents.run_transaction(commit))

    def finish(self, uid, job):
        uid = require_user_id(uid)
        # No objects or slow work inside this visibility transaction.
        def publish(tx):
            data = self._load_tx(tx, uid, job)
            if data['phase'] == 'completed':
                return data
            if data['phase'] == 'cancelled':
                raise DocumentConflict('HOSTED_IMPORT_CANCELLED')
            receipt = tx.get(self._receipt_path(uid, job))
            user = self._account(tx, uid)
            pid = data['project_id']
            member_path = ('projects', pid, 'members', uid)
            member = tx.get(member_path)
            if (data['phase'] != 'ready' or data['next_batch'] != data['total_batches']
                    or data['completed_records'] != data['total_records']
                    or data['completed_images'] != data['total_images']):
                raise DocumentConflict('HOSTED_IMPORT_NOT_FINISHED')
            if receipt is None or receipt.data != {'next_batch': data['total_batches'], 'chain': data['expected_chain'],
                                                  'records': data['total_records'], 'images': data['total_images']}:
                raise DocumentConflict('HOSTED_IMPORT_RECEIPT_INVALID')
            if member is not None:
                raise DocumentConflict('HOSTED_IMPORT_PROJECT_ALREADY_PUBLISHED')
            stamp = data['stamp']
            op = 'package_import_' + pid[:24]
            project = {'project_id': pid, 'title': data['title'], 'status': 'active', 'generation': 1,
                       'created_at': stamp, 'updated_at': stamp, '_bm_create_operation': op,
                       '_bm_create_fingerprint': _fingerprint({'project_id': pid, 'owner_user_id': uid, 'title': data['title']})}
            owner = {'user_id': uid, 'member_id': stable_entity_id('member_', data['operation']), 'role': 'owner',
                     'generation': 1, 'created_at': stamp, 'updated_at': stamp, 'last_opened_at': stamp,
                     '_bm_create_operation': op}
            from hosted.runtime_services import _project_public
            data.update(phase='completed', result=_project_public(project, owner_user_id=uid))
            tx.create(('projects', pid), project)
            tx.create(member_path, owner)
            tx.set(('users', uid), {**user, 'last_project_id': pid, 'generation': int(user.get('generation') or 0) + 1,
                                   'updated_at': stamp})
            tx.set(self._path(uid, job), data)
            return data
        # The ordinary account lifecycle fence independently rechecks reservation.
        data = self._load(uid, job)
        with package_import_staging(data['operation'], uid):
            return self._public(self.documents.run_transaction(publish))

    def cancel(self, uid, job):
        uid = require_user_id(uid)
        def cancel(tx):
            data = self._load_tx(tx, uid, job)
            if data['phase'] == 'completed':
                raise DocumentConflict('HOSTED_IMPORT_ALREADY_PUBLISHED')
            if data['phase'] != 'cancelled':
                data['phase'] = 'cancelled'
                tx.set(self._path(uid, job), data)
            return data
        return self._public(self.documents.run_transaction(cancel))
