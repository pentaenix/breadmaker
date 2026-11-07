"""Inactive, resumable manuscript revision publication rehearsal.

No HTTP route or runtime constructs this service. It requires the revision-aware
reader and a bound structural action for publication. Adoption and image phases
are durable and bounded. Runtime/operator rollout and retention gates remain
separate work; this module itself enables no production capability.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import gzip
import hashlib
import io
import json
import time

from hosted.action_journal import ActionDocumentStore, _ACTION, _path as action_path
from hosted.account_lifecycle import AccountLifecycleDocumentStore, AccountLifecycleObjectStore
from hosted.document_io import manuscript_is_default_blank, parse_document_bytes
from hosted.domain_store import _ID_FIELDS
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.firestore.repositories import HostedProjectRepository, _canonical_payload, _fingerprint, render_model_path
from hosted.idempotency import stable_entity_id
from hosted.storage.keys import require_hybrid_entity_object_key
from hosted.manuscript_visibility import (HEAD_FIELD, REVISION_FIELD,
    DELETED_FIELD, ManuscriptDocumentStore)
from hosted.manuscript_adoption import LegacyManuscriptAdoption
from hosted.revision_images import RevisionImageStager, prepare_revision_images
from security.image_security import HOSTED_DEFAULT_IMAGE_BYTES, inspect_hosted_image_bytes

BATCH_SIZE = 64
MAX_RECORDS = 10_000
MAX_PLAN_BYTES = 32 * 1024 * 1024
MAX_BATCH_BYTES = 4 * 1024 * 1024
ADOPTION_PAGES_PER_STEP = 4
MAX_PUBLICATION_ROWS = 95
MAX_PUBLICATION_BYTES = 300_000


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def _view(documents):
    value = documents.store if isinstance(documents, ActionDocumentStore) else documents
    if not isinstance(value, ManuscriptDocumentStore) or value.include_retained:
        raise HostedValidationError('HOSTED_MANUSCRIPT_REVISION_READER_REQUIRED')
    return value


def _require_actor(view, uid):
    # This is a request-only service, never an unauthenticated background worker.
    # Threaded uploads copy this verified request context into each bounded task.
    fence = view.store
    if not isinstance(fence, AccountLifecycleDocumentStore) or fence.actor() != uid:
        raise HostedValidationError('HOSTED_IMPORT_AUTHENTICATED_ACTOR_REQUIRED')


def _require_live_accounts(tx, pid, uid):
    owners = tx.query_collection(('projects', pid, 'members'), field='role', value='owner')
    if not owners or any(row.path[-1] != row.data.get('user_id') for row in owners):
        raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
    users = {uid, *(require_user_id(row.path[-1]) for row in owners)}
    paths = [(family, user) for user in sorted(users) for family in ('users', 'account_erasure_locks')]
    rows = dict(zip(paths, tx.get_many(paths), strict=True))
    for user in users:
        profile = rows[('users', user)]
        if (profile is None or profile.data.get('status', 'active') != 'active'
                or profile.data.get('_bm_erasure_operation') or rows[('account_erasure_locks', user)] is not None):
            raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')


def _require_action(pid, uid):
    action = _ACTION.get()
    if action is None or action[1:] != (pid, uid):
        raise HostedValidationError('HOSTED_MANUSCRIPT_UNDO_ACTION_REQUIRED')


class ManuscriptRevisionImport:
    def __init__(self, domain):
        if domain.encrypted_fields is not None or domain.encrypted_objects is not None:
            raise HostedValidationError('HOSTED_PRIVATE_BATCH_NOT_READY')
        self.domain = domain
        self.view = _view(domain.documents)
        if (not isinstance(domain.objects, AccountLifecycleObjectStore)
                or domain.objects.documents is not self.view.store):
            raise HostedValidationError('HOSTED_IMPORT_OBJECT_ERASURE_FENCE_REQUIRED')
        self.retained = self.view.retained_view()
        self.publication = ActionDocumentStore(self.retained)
        self.adoption = LegacyManuscriptAdoption(self.view)
        self.images = RevisionImageStager(domain, self._load)

    @staticmethod
    def _path(pid, job):
        return ('projects', require_project_id(pid), 'manuscript_import_jobs',
            require_opaque_id(job, label='import_job'))

    def _root(self, tx, pid, uid, generation=None):
        _require_actor(self.view, uid)
        root, member = HostedProjectRepository(None)._require_member(tx, pid, uid)
        self.domain.projects._open_project(pid, root)
        if member.get('role') not in {'owner', 'editor'}:
            raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        _require_live_accounts(tx, pid, uid)
        if generation is not None and int(root['generation']) != generation:
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        return root

    def _load(self, tx, pid, uid, job):
        root = self._root(tx, pid, uid)
        row = tx.get(self._path(pid, job))
        if row is None or row.data.get('actor') != uid or row.data.get('actor_user_id') != uid:
            raise DocumentNotFound('HOSTED_IMPORT_NOT_FOUND')
        data = dict(row.data)
        if data.get('state') == 'cancelled':
            raise DocumentConflict('HOSTED_IMPORT_CANCELLED')
        if time.time() >= data['expires_at']:
            raise DocumentConflict('HOSTED_IMPORT_EXPIRED')
        if int(root['generation']) != data['generation']:
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        return data

    @staticmethod
    def _public(job, data):
        phase = ('adopting' if not data['adoption_ready'] else
            'images' if data['image_cursor'] < len(data['asset_manifest']) else 'content')
        remaining = data['total_batches'] - data['next_batch']
        remaining += (len(data['asset_manifest']) - data['image_cursor'] + 7) // 8
        if not data['adoption_ready']:
            remaining += 1  # Estimate only; legacy page count is deliberately not scanned.
        return {'job_id': job, 'next_batch': data['step_cursor'],
            'total_batches': data['step_cursor'] + remaining, 'phase': phase,
            'content_batch': data['next_batch'], 'content_batches': data['total_batches'],
            'adoption': data['adoption'], 'image_next': data['image_cursor'],
            'image_total': len(data['asset_manifest']), 'chapters': data['result']['chapters'],
            'images': data['result']['images'], 'warnings': data['result'].get('warnings', []),
            'ready': data['state'] == 'staging' and data['adoption_ready']
                and data['image_cursor'] == len(data['asset_manifest'])
                and data['next_batch'] == data['total_batches']}

    def _adopted(self, pid, uid, generation):
        def check(tx):
            self._root(tx, pid, uid, generation)
            return tx._enabled(pid)
        return self.retained.run_transaction(check)

    def begin_document(self, pid, uid, raw, filename, heading, *, operation,
            replace_main=False, after_document_id='', confirmed=False):
        # Authorize before parsing or retaining document/image bytes.
        self.retained.run_transaction(lambda tx: self._root(tx, pid, uid))
        assets = {}
        def collect_image(blob, extension):
            info = inspect_hosted_image_bytes(blob, extension, byte_limit=HOSTED_DEFAULT_IMAGE_BYTES)
            digest = hashlib.sha256(blob).hexdigest()
            path = '/api/assets/img_' + digest[:24] + info.extension
            assets[path] = (blob, info.extension)
            if len(assets) > 64 or sum(len(value[0]) for value in assets.values()) > MAX_PLAN_BYTES:
                raise HostedValidationError('HOSTED_IMPORT_IMAGE_BUDGET_EXCEEDED')
            return path
        tabs, warnings = parse_document_bytes(raw, filename, heading, image_store=collect_image)
        return self.begin(pid, uid, tabs, operation=operation, replace_main=replace_main,
            after_document_id=after_document_id, confirmed=confirmed, assets=assets, warnings=warnings)

    @staticmethod
    def _completed_result(tx, pid, uid, root, receipt):
        result = dict(receipt['result'])
        token = result.get('undo_token')
        action = tx.get(action_path(uid, token)) if isinstance(token, str) and token else None
        data = action.data if action is not None else {}
        available = (data.get('project_id') == pid and data.get('state') in {'open', 'finished'}
            and data.get('generation') == root['generation']
            and type(data.get('expires_at')) in {int, float} and data['expires_at'] > time.time())
        if available and data['state'] == 'finished':
            available = (root.get('_bm_structural_head'), root.get('_bm_structural_actor')) == (token, uid)
        return {**result, 'undo_available': bool(available)}

    def status(self, pid, uid, job):
        def status(tx):
            root = self._root(tx, pid, uid)
            row = tx.get(self._path(pid, job))
            receipt = tx.get(('projects', pid, 'mutation_receipts', job))
            if row is None or row.data.get('actor') != uid or row.data.get('actor_user_id') != uid:
                raise DocumentNotFound('HOSTED_IMPORT_NOT_FOUND')
            if receipt is not None:
                if receipt.data.get('actor') != uid or receipt.data.get('generation') != root['generation']:
                    raise DocumentConflict('HOSTED_REPLAY_SUPERSEDED')
                return {**self._public(job, row.data), 'ready': True, 'completed': True,
                    'result': self._completed_result(tx, pid, uid, root, receipt.data)}
            return self._public(job, self._load(tx, pid, uid, job))
        return self.retained.run_transaction(status)

    def begin(self, pid, uid, tabs, *, operation, replace_main=False, after_document_id='', confirmed=False,
            assets=None, warnings=None):
        pid, uid = require_project_id(pid), require_user_id(uid)
        _require_actor(self.view, uid)
        operation = require_opaque_id(operation, label='operation')
        job = stable_entity_id('revision_', f'{pid}_{uid}_{operation}')
        assets = assets or {}
        digest = _fingerprint({'tabs': tabs, 'replace': replace_main, 'after': after_document_id, 'confirmed': confirmed,
            'assets': {key: [hashlib.sha256(value[0]).hexdigest(), value[1]] for key, value in assets.items()},
            'warnings': warnings or []})
        generation = self.retained.run_transaction(lambda tx: int(self._root(tx, pid, uid)['generation']))
        if replace_main and not confirmed and not manuscript_is_default_blank(self.domain, pid, uid):
            raise DocumentConflict('Confirm replacement of the existing manuscript before importing.')
        def inspect(tx):
            self._root(tx, pid, uid, generation)
            rows = sorted(tx.query_collection(('projects', pid, 'tabs')),
                key=lambda row: (float(row.data.get('position') or 0), row.path[-1]))
            prior = tx.get(self._path(pid, job))
            if prior is not None and (prior.data.get('digest') != digest or prior.data.get('actor') != uid):
                raise DocumentConflict('HOSTED_IMPORT_REQUEST_CHANGED')
            if prior is not None:
                self._load(tx, pid, uid, job)  # Reject cancelled/expired/stale retries before upload.
            return rows, prior
        existing, prior = self.retained.run_transaction(inspect)
        if prior is not None and prior.data.get('source_ready'):
            return self.status(pid, uid, job)
        main = next((row for row in existing if row.data.get('is_manuscript') or row.data.get('kind') == 'manuscript'), None)
        if main is None:
            raise DocumentNotFound('HOSTED_MAIN_MANUSCRIPT_NOT_FOUND')
        positions = {row.path[-1]: index for index, row in enumerate(existing)}
        if not replace_main and after_document_id and after_document_id not in positions:
            raise DocumentNotFound('HOSTED_IMPORT_DESTINATION_NOT_FOUND')
        insert_at = positions.get(after_document_id, len(existing)-1) + 1
        stamp = prior.data['created_at'] if prior is not None else self.domain.clock.now_iso()
        entries, new_tabs, document_ids, chapters = [], [], [], 0
        image_destinations, image_count = {}, 0
        def prepare(entity, eid, public):
            ids = self.domain._ids_with_entity(entity, {'project_id': pid}, eid)
            public = _canonical_payload(self.domain._payload_with_identity(entity, public, eid))
            data = {**public, 'generation': 1, 'created_at': stamp, 'updated_at': stamp,
                '_bm_create_operation': operation, '_bm_create_fingerprint': _fingerprint(public)}
            return {'entity': entity, 'ids': ids, 'path': list(render_model_path(self.domain._model(entity), **ids)), 'data': data}
        def add(entity, public):
            eid = stable_entity_id(_ID_FIELDS[entity][1], f'{job}_{len(entries)}')
            entry = prepare(entity, eid, public)
            if entity == 'chapter':
                entry['data'][REVISION_FIELD] = job
            entries.append(entry)
            if len(entries) > MAX_RECORDS:
                raise HostedValidationError('HOSTED_IMPORT_DOCUMENT_TOO_LARGE')
            return eid
        for tpos, tab in enumerate(tabs):
            if replace_main:
                tid = main.path[-1]
            else:
                tid = stable_entity_id('tab_', f'{job}_tab_{tpos}')
                entry = prepare('document_tab', tid, {'title': str(tab.get('title') or 'Imported document')[:240],
                    'kind': 'document', 'is_manuscript': False, 'position': insert_at+tpos})
                entry['data'][HEAD_FIELD] = job
                new_tabs.append(entry)
            if tid not in document_ids:
                document_ids.append(tid)
            for cpos, chapter in enumerate(tab.get('chapters') or []):
                cid = add('chapter', {'tab_id': tid, 'title': str(chapter.get('title') or 'Chapter')[:240],
                    'position': chapters if replace_main else cpos})
                chapters += 1
                for bpos, block in enumerate(chapter.get('blocks') or [{'text': '', 'html': ''}]):
                    is_image = block.get('kind') == 'image' or bool(block.get('image_path'))
                    extra = {}
                    if is_image:
                        image_path = block.get('image_path')
                        if image_path not in assets:
                            raise HostedValidationError('HOSTED_REVISION_IMAGE_STAGING_SOURCE_REQUIRED')
                        image_destinations.setdefault(image_path, set()).add(tid)
                        image_count += 1
                        extra = {'image_path': image_path, 'image_width': 100, 'image_footer': '',
                            'image_description': '', 'image_row_id': ''}
                    add('manuscript_block', {'tab_id': tid, 'chapter_id': cid, 'position': bpos,
                        'block_type': 'image' if is_image else 'paragraph', 'semantic_style': block.get('style') or 'body',
                        'scene_id': None, 'text_content': block.get('text') or '', 'content_html': block.get('html') or '', **extra})
        if not entries:
            raise HostedValidationError('HOSTED_IMPORT_DOCUMENT_EMPTY')
        shifted = [] if replace_main else [
            {'path': list(row.path), 'before': dict(row.data), 'position': index + (len(new_tabs) if index >= insert_at else 0)}
            for index, row in enumerate(existing)
            if float(row.data.get('position') or 0) != index + (len(new_tabs) if index >= insert_at else 0)]
        # Bound only final publication/journal rows, not the novel's paragraph count.
        asset_manifest, image_payloads = prepare_revision_images(pid, [
            {'data': assets[path][0], 'extension': assets[path][1], 'tab_ids': sorted(destinations)}
            for path, destinations in image_destinations.items()])
        if {entry['api_path'] for entry in asset_manifest} != set(image_destinations):
            raise HostedValidationError('HOSTED_REVISION_IMAGE_STAGING_SOURCE_REQUIRED')
        if len(new_tabs) + len(shifted) + int(replace_main) + len(asset_manifest) > MAX_PUBLICATION_ROWS:
            raise HostedValidationError('HOSTED_IMPORT_TAB_PUBLICATION_LIMIT')
        plans = [_json(entries[index:index+BATCH_SIZE]) for index in range(0, len(entries), BATCH_SIZE)]
        if sum(map(len, plans)) > MAX_PLAN_BYTES or any(len(part) > MAX_BATCH_BYTES for part in plans):
            raise HostedValidationError('HOSTED_IMPORT_DOCUMENT_TOO_LARGE')
        data = {'actor': uid, 'actor_user_id': uid, 'generation': generation, 'digest': digest, 'operation': operation,
            'expires_at': time.time()+86400, 'created_at': stamp, 'state': 'preparing',
            'next_batch': 0, 'step_cursor': 0, 'total_batches': len(plans),
            'source_ready': False, 'adoption_ready': False, 'adoption': None, 'image_cursor': 0,
            'asset_manifest': asset_manifest,
            'plans': [], 'records': len(entries),
            'chapter_tabs': {entry['ids']['chapter_id']: entry['data']['tab_id'] for entry in entries if entry['entity'] == 'chapter'},
            'new_tabs': new_tabs, 'shifted': shifted,
            'main': {'path': list(main.path), 'before': dict(main.data)} if replace_main else None,
            'result': {'ok': True, 'document_ids': document_ids, 'first_document_id': document_ids[0],
                'tabs': len(document_ids), 'chapters': chapters, 'images': image_count, 'warnings': warnings or []}}
        if len(_json(data)) > 300_000:
            raise HostedValidationError('HOSTED_IMPORT_TAB_PUBLICATION_LIMIT')
        data['adoption_ready'] = self._adopted(pid, uid, generation)
        if not data['adoption_ready']:
            data['adoption'] = self.adoption.begin(pid, uid, generation=generation, operation=operation)
        def reserve(tx):
            self._root(tx, pid, uid, generation)
            current = tx.get(self._path(pid, job))
            if current is None:
                tx.create(self._path(pid, job), data)
            elif current.data.get('digest') != digest or current.data.get('actor') != uid or current.data.get('generation') != generation:
                raise DocumentConflict('HOSTED_IMPORT_REQUEST_CHANGED')
        self.retained.run_transaction(reserve)
        self.images.publish_sources(pid, uid, job, image_payloads)
        def publish(item):
            index, part = item
            return self.domain._publish_hybrid('manuscript_block', {'project_id': pid, 'block_id': f'b_{job}_plan_{index}'},
                gzip.compress(part, mtime=0), user_id=uid, operation=operation)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(copy_context().run, publish, item) for item in enumerate(plans)]
            pointers = [future.result() for future in futures]
        def ready(tx):
            current = self._load(tx, pid, uid, job)
            if current['state'] == 'preparing':
                tx.set(self._path(pid, job), {**current, 'plans': pointers, 'state': 'staging', 'source_ready': True})
        self.retained.run_transaction(ready)
        return self.status(pid, uid, job)

    def _validate_plan(self, entries, pid, job, index, data):
        """Hash integrity alone never grants arbitrary retained-store writes."""
        expected_count = min(BATCH_SIZE, data['records']-index*BATCH_SIZE)
        if not isinstance(entries, list) or len(entries) != expected_count or not 1 <= expected_count <= BATCH_SIZE:
            raise HostedValidationError('HOSTED_IMPORT_PLAN_SCHEMA_INVALID')
        common = {'id', 'generation', 'created_at', 'updated_at', '_bm_create_operation', '_bm_create_fingerprint'}
        fields = {
            'chapter': common | {'chapter_id', 'tab_id', 'title', 'position', REVISION_FIELD},
            'manuscript_block': common | {'block_id', 'tab_id', 'chapter_id', 'position', 'block_type',
                'semantic_style', 'scene_id', 'text_content', 'content_html'},
        }
        for offset, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {'entity', 'ids', 'path', 'data'}:
                raise HostedValidationError('HOSTED_IMPORT_PLAN_SCHEMA_INVALID')
            entity = entry['entity']
            if not isinstance(entity, str) or entity not in fields or not isinstance(entry['data'], dict):
                raise HostedValidationError('HOSTED_IMPORT_PLAN_SCHEMA_INVALID')
            expected_chapter = stable_entity_id('chapter_', f'{job}_{index*BATCH_SIZE+offset}')
            expected_entity = 'chapter' if expected_chapter in data['chapter_tabs'] else 'manuscript_block'
            if entity != expected_entity:
                raise HostedValidationError('HOSTED_IMPORT_PLAN_SCHEMA_INVALID')
            id_field, prefix = _ID_FIELDS[entity]
            eid = stable_entity_id(prefix, f'{job}_{index*BATCH_SIZE+offset}')
            ids = {'project_id': pid, id_field: eid}
            stored = entry['data']
            if (entry['ids'] != ids or entry['path'] != list(render_model_path(self.domain._model(entity), **ids))
                    or set(stored) != (fields[entity] | ({'image_path', 'image_width', 'image_footer',
                        'image_description', 'image_row_id'} if entity == 'manuscript_block' and stored.get('block_type') == 'image' else set()))
                    or stored['id'] != eid or stored[id_field] != eid
                    or type(stored['generation']) is not int or stored['generation'] != 1
                    or stored['_bm_create_operation'] != data['operation']
                    or not isinstance(stored['created_at'], str) or len(stored['created_at']) > 64
                    or stored['created_at'] != stored['updated_at']
                    or type(stored['position']) is not int or stored['position'] < 0
                    or not isinstance(stored['tab_id'], str) or not stored['tab_id']
                    or stored['tab_id'] not in data['result']['document_ids']
                    or not isinstance(stored['chapter_id'], str)
                    or stored['chapter_id'] not in data['chapter_tabs']):
                raise HostedValidationError('HOSTED_IMPORT_PLAN_SCHEMA_INVALID')
            if entity == 'chapter':
                if (stored[REVISION_FIELD] != job or data['chapter_tabs'].get(eid) != stored['tab_id']
                        or not isinstance(stored['title'], str) or len(stored['title']) > 240):
                    raise HostedValidationError('HOSTED_IMPORT_PLAN_SCHEMA_INVALID')
            elif (data['chapter_tabs'].get(stored['chapter_id']) != stored['tab_id']
                    or stored['block_type'] not in {'paragraph', 'image'} or stored['scene_id'] is not None
                    or any(not isinstance(stored[field], str) for field in ('semantic_style', 'text_content', 'content_html'))):
                raise HostedValidationError('HOSTED_IMPORT_PLAN_SCHEMA_INVALID')
            if entity == 'manuscript_block' and stored['block_type'] == 'image':
                paths = {entry['api_path'] for entry in data['asset_manifest'] if stored['tab_id'] in entry['tab_ids']}
                if (stored['image_path'] not in paths or type(stored['image_width']) is not int or stored['image_width'] != 100
                        or any(stored[key] != '' for key in ('image_footer', 'image_description', 'image_row_id'))):
                    raise HostedValidationError('HOSTED_IMPORT_PLAN_SCHEMA_INVALID')
            public = {key: value for key, value in stored.items()
                if key not in {'generation', 'created_at', 'updated_at'} and not key.startswith('_bm_')}
            if stored['_bm_create_fingerprint'] != _fingerprint(public):
                raise HostedValidationError('HOSTED_IMPORT_PLAN_SCHEMA_INVALID')

    def _phase_commit(self, pid, uid, job, before, updates):
        def commit(tx):
            current = self._load(tx, pid, uid, job)
            if current['step_cursor'] > before['step_cursor']:
                return self._public(job, current)
            mutable = {'asset_next_index', 'asset_receipts'}
            if ({key: value for key, value in current.items() if key not in mutable}
                    != {key: value for key, value in before.items() if key not in mutable}):
                raise DocumentConflict('HOSTED_IMPORT_PLAN_CHANGED')
            current.update(updates, step_cursor=current['step_cursor'] + 1)
            tx.set(self._path(pid, job), current)
            return self._public(job, current)
        return self.retained.run_transaction(commit)

    def step(self, pid, uid, job, index):
        data = self.retained.run_transaction(lambda tx: self._load(tx, pid, uid, job))
        if data['state'] != 'staging' or not data['source_ready'] or type(index) is not int or index < 0:
            raise HostedValidationError('HOSTED_IMPORT_BATCH_INVALID')
        if index < data['step_cursor']:
            return self._public(job, data)
        if index != data['step_cursor']:
            raise DocumentConflict('HOSTED_IMPORT_BATCH_OUT_OF_ORDER')
        if not data['adoption_ready']:
            progress = data['adoption']
            if self._adopted(pid, uid, data['generation']):
                # Another authorized proof may have activated this exact generation.
                progress = {**progress, 'ready': True, 'activated': True}
            else:
                for expected in range(progress['next_step'], progress['next_step'] + ADOPTION_PAGES_PER_STEP):
                    progress = self.adoption.step(pid, uid, progress['job_id'], expected)
                    if progress['ready']:
                        progress = self.adoption.finish(pid, uid, progress['job_id'])
                        break
            return self._phase_commit(pid, uid, job, data,
                {'adoption': progress, 'adoption_ready': progress['activated']})
        if data['image_cursor'] < len(data['asset_manifest']):
            progress = self.images.stage_sources(pid, uid, job, data['image_cursor'])
            return self._phase_commit(pid, uid, job, data, {'image_cursor': progress['asset_next_index']})
        content_index = data['next_batch']
        if content_index == data['total_batches']:
            return self._public(job, data)
        index = content_index
        pointer = data['plans'][index]
        try:
            if (not isinstance(pointer, dict) or set(pointer) != {'object_name', 'object_generation', 'object_sha256', 'object_size'}
                    or type(pointer['object_generation']) is not int or pointer['object_generation'] <= 0
                    or type(pointer['object_size']) is not int or not 0 < pointer['object_size'] <= MAX_BATCH_BYTES
                    or require_hybrid_entity_object_key('manuscript_block',
                        {'project_id': pid, 'block_id': f'b_{job}_plan_{index}'}, pointer['object_name']) != pointer['object_sha256']):
                raise ValueError('invalid pointer')
        except Exception:
            raise HostedValidationError('HOSTED_IMPORT_PLAN_SCOPE_INVALID') from None
        encoded = b''.join(self.domain.objects.read_chunks(pointer['object_name'], max_bytes=MAX_BATCH_BYTES,
            if_generation_match=int(pointer['object_generation'])))
        if len(encoded) != pointer['object_size'] or hashlib.sha256(encoded).hexdigest() != pointer['object_sha256']:
            raise HostedValidationError('HOSTED_IMPORT_PLAN_INTEGRITY_FAILED')
        with gzip.GzipFile(fileobj=io.BytesIO(encoded)) as source:
            body = source.read(MAX_BATCH_BYTES+1)
        if len(body) > MAX_BATCH_BYTES:
            raise HostedValidationError('HOSTED_IMPORT_PLAN_INTEGRITY_FAILED')
        entries = json.loads(body)
        self._validate_plan(entries, pid, job, index, data)
        def publish(entry):
            entity, ids, stored = entry['entity'], entry['ids'], entry['data']
            hybrid = self.domain._hybrid_payload(entity, stored)
            if hybrid is not None:
                metadata, content = hybrid
                pointer = self.domain._publish_hybrid(entity, ids, content, user_id=uid, operation=data['operation'])
                stored = {**metadata, 'payload_object': pointer}
            return tuple(entry['path']), stored
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(copy_context().run, publish, entry) for entry in entries]
            staged = [future.result() for future in futures]
        def commit(tx):
            current = self._load(tx, pid, uid, job)
            immutable = lambda value: {key: item for key, item in value.items() if key not in {'next_batch', 'step_cursor'}}
            if immutable(current) != immutable(data):
                raise DocumentConflict('HOSTED_IMPORT_PLAN_CHANGED')
            if current['next_batch'] > index:
                return self._public(job, current)
            if current['next_batch'] != index:
                raise DocumentConflict('HOSTED_IMPORT_BATCH_OUT_OF_ORDER')
            old = tx.get_many([path for path, _ in staged])
            if any(row is not None for row in old):
                raise DocumentConflict('HOSTED_IMPORT_ENTITY_EXISTS')
            planned = dict(staged)
            block_rows = [stored for path, stored in staged if path[2] == 'blocks']
            parent_paths = list(dict.fromkeys(('projects', pid, 'chapters', stored['chapter_id']) for stored in block_rows))
            physical_parents = dict(zip(parent_paths, tx.get_many(parent_paths), strict=True))
            for stored in block_rows:
                parent_path = ('projects', pid, 'chapters', stored['chapter_id'])
                parent_row = physical_parents[parent_path]
                parent = planned.get(parent_path) or (dict(parent_row.data) if parent_row is not None else None)
                if (parent is None or parent.get(REVISION_FIELD) != job or parent.get('tab_id') != stored['tab_id']
                        or parent.get('_bm_create_operation') != data['operation'] or parent.get('generation') != 1):
                    raise DocumentConflict('HOSTED_IMPORT_PARENT_CHANGED')
            for path, stored in staged:
                tx.create(path, stored)
            current['next_batch'] += 1
            current['step_cursor'] += 1
            tx.set(self._path(pid, job), current)
            return self._public(job, current)
        return self.retained.run_transaction(commit)

    def cancel(self, pid, uid, job):
        """Cancellation wins or loses atomically against final publication."""
        def cancel(tx):
            self._root(tx, pid, uid)
            path = self._path(pid, job)
            row = tx.get(path)
            receipt = tx.get(('projects', pid, 'mutation_receipts', job))
            if row is None or row.data.get('actor') != uid:
                raise DocumentNotFound('HOSTED_IMPORT_NOT_FOUND')
            if receipt is not None:
                raise DocumentConflict('HOSTED_IMPORT_ALREADY_PUBLISHED_USE_UNDO')
            if row.data.get('state') != 'cancelled':
                tx.set(path, {**row.data, 'state': 'cancelled'})
            return {'ok': True, 'cancelled': True}
        return self.retained.run_transaction(cancel)

    def finish(self, pid, uid, job):
        pid, uid = require_project_id(pid), require_user_id(uid)
        _require_action(pid, uid)
        receipt_path = ('projects', pid, 'mutation_receipts', job)
        status = self.status(pid, uid, job)
        if status.get('completed'):
            return status['result']
        if not status['ready']:
            raise DocumentConflict('HOSTED_IMPORT_NOT_FINISHED')
        verified_images = self.images.verify_for_publication(pid, uid, job)
        def commit(tx):
            root = self._root(tx, pid, uid)
            receipt = tx.get(receipt_path)
            if receipt is not None:
                if receipt.data.get('actor') != uid or receipt.data.get('generation') != root['generation']:
                    raise DocumentConflict('HOSTED_REPLAY_SUPERSEDED')
                return self._completed_result(tx, pid, uid, root, receipt.data)
            data = self._load(tx, pid, uid, job)
            if not self._public(job, data)['ready']:
                raise DocumentConflict('HOSTED_IMPORT_NOT_FINISHED')
            changes = []
            for entry in data['new_tabs']:
                path = tuple(entry['path'])
                if tx.get(path) is not None:
                    raise DocumentConflict('HOSTED_IMPORT_ENTITY_EXISTS')
                changes.append((path, entry['data'], True))
            for entry in data['shifted'] + ([data['main']] if data['main'] else []):
                path = tuple(entry['path'])
                before = tx.get(path)
                if before is None or dict(before.data) != entry['before']:
                    raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
                after = {**entry['before'], 'generation': int(entry['before']['generation'])+1,
                    'updated_at': self.domain.clock.now_iso()}
                if 'position' in entry:
                    after['position'] = entry['position']
                else:
                    after[HEAD_FIELD] = job
                changes.append((path, after, False))
            changes.extend(self.images.publication_changes(tx, pid, uid, job, verified_images))
            before_images = tx.get_many([path for path, _, _ in changes])
            journal_bytes = len(_json([{'path': path, 'before': dict(before.data) if before else None, 'after': after}
                for (path, after, _), before in zip(changes, before_images, strict=True)]))
            if len(changes) > MAX_PUBLICATION_ROWS or journal_bytes > MAX_PUBLICATION_BYTES:
                raise HostedValidationError('HOSTED_IMPORT_TAB_PUBLICATION_LIMIT')
            self.domain._advance_project(tx, ('projects', pid), root, expected_generation=data['generation'], stamp=self.domain.clock.now_iso())
            for path, value, create in changes:
                (tx.create if create else tx.set)(path, value)
            result = {**data['result'], 'undo_token': _ACTION.get()[0]}
            tx.create(receipt_path, {'actor': uid, 'generation': data['generation']+1, 'digest': data['digest'], 'result': result})
            return {**result, 'undo_available': True}
        return self.publication.run_transaction(commit)


def delete_chapter_revision(domain, pid, cid, uid):
    pid, uid = require_project_id(pid), require_user_id(uid)
    cid = require_opaque_id(cid, label='chapter_id')
    _require_action(pid, uid)
    service = ManuscriptRevisionImport(domain)
    view = service.view
    _require_actor(view, uid)
    generation = service.retained.run_transaction(lambda tx: int(service._root(tx, pid, uid)['generation']))
    chapter = view.get(('projects', pid, 'chapters', cid))
    if chapter is None:
        raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
    if not service._adopted(pid, uid, generation):
        raise HostedValidationError('HOSTED_MANUSCRIPT_ADOPTION_REQUIRED')
    def remove(tx):
        root = service._root(tx, pid, uid, generation)
        current = tx.get(chapter.path)
        if current is None or dict(current.data) != dict(chapter.data):
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        stamp = domain.clock.now_iso()
        domain._advance_project(tx, ('projects', pid), root, expected_generation=generation, stamp=stamp)
        tx.set(chapter.path, {**chapter.data, DELETED_FIELD: True,
            'generation': int(chapter.data['generation'])+1, 'updated_at': stamp})
        return {'ok': True}
    return service.publication.run_transaction(remove)
