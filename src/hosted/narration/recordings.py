"""Private derivative publication, outside canonical generations and History.

Every object attempt has a durable intent BEFORE I/O. Failed attempts retain
their capacity charge until generation-fenced GC proves absence. No deletion
or background work is performed here; composition remains explicitly disabled.
"""
from __future__ import annotations

import hashlib
import gzip
import io
import json
import math
import secrets
import time

from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.narration.metadata import MAX_AUDIO, MAX_METADATA, MAX_METADATA_CHARACTERS, normalize, validate_audio
from hosted.narration.sealed_parts import PART_BYTES
from hosted.narration.source import NarrationSource
from hosted.narration.authority import require_authority

ROOT = 'narration_projects_v1'
USAGE = 'narration_storage_usage_v1'
CAPACITY = 5 * 1024**3
HEADROOM = 64 * 1024**2
MAX_RECORDS = 20_000
UNDO_SECONDS = 86400
UPLOAD_SECONDS = 3600
ATTEMPT_SECONDS = 120
FENCE_BYTES = 2048  # retained zero-byte GCS fence + small physical-attempt index
ATTEMPTS = 'narration_object_attempts_v1'
QUEUE = 'narration_publication_queue_v1'


def _queue(pid, publication, owner, eligible):
    return dict(v=1, project_id=pid, publication=publication, owner_user_id=owner, eligible_at=eligible)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()


def _intent(pid, publication):
    return (ROOT, require_project_id(pid), 'intents', require_opaque_id(publication))


def _head(pid, rid):
    return (ROOT, require_project_id(pid), 'records', require_opaque_id(rid))


def _required(row):
    if row is None:
        raise DocumentNotFound('NARRATION_NOT_FOUND')
    data = dict(row.data)
    if type(data.get('v')) is not int or data['v'] != 1:
        raise HostedValidationError('NARRATION_STATE_INVALID')
    return data


def _ledger(row):
    data = {'v': 1, 'bytes': 0, 'records': 0, 'uploads': {}} if row is None else _required(row)
    if (set(data) != {'v', 'bytes', 'records', 'uploads'}
            or any(type(data[key]) is not int or data[key] < 0 for key in ('bytes', 'records'))
            or not isinstance(data['uploads'], dict) or len(data['uploads']) > MAX_RECORDS + 2
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in data['uploads'].values())):
        raise HostedValidationError('NARRATION_CAPACITY_STATE_INVALID')
    return {**data, 'uploads': dict(data['uploads'])}


class HostedNarrationRecordings:
    def __init__(self, domain, parts, *, authority, catalog, epoch, now=time.time):
        self.domain, self.documents, self.parts = domain, domain.documents, parts
        self.authority, self.catalog, self.now = authority, catalog, now
        self.source = NarrationSource(domain, authority)
        if not isinstance(epoch, str) or not epoch:
            raise ValueError('NARRATION_AUTHORITY_EPOCH_REQUIRED')
        self.epoch = require_opaque_id(epoch)
        from hosted.narration.index import PrivateNarrationIndex
        self.index = PrivateNarrationIndex(self.documents, parts.envelope, authority=authority, epoch=self.epoch)

    def _context(self, pid, publication):
        return {'project_id': pid, 'entity': 'narration_metadata', 'record_id': publication}

    def _open(self, pid, publication, sealed):
        encoded = self.parts.envelope.open(sealed, context=self._context(pid, publication))
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(encoded)) as stream:
                payload = stream.read(MAX_METADATA + 1)
            if len(payload) > MAX_METADATA:
                raise ValueError()
            return json.loads(payload)
        except (OSError, EOFError, UnicodeError, ValueError):
            raise HostedValidationError('NARRATION_METADATA_INVALID') from None

    def _load(self, tx, uid, pid, publication, kind='read'):
        require_authority(self.authority, tx, uid, pid, kind)
        data = _required(tx.get(_intent(pid, publication)))
        if data.get('state') == 'collecting':
            raise DocumentConflict('NARRATION_UPLOAD_UNAVAILABLE')
        if data.get('epoch') != self.epoch or data.get('project_id') != pid:
            raise DocumentConflict('NARRATION_AUTHORITY_EPOCH_CHANGED')
        fields = {'v', 'project_id', 'publication', 'record_id', 'actor', 'owner_user_id', 'epoch',
                  'state', 'created_at', 'expires_at', 'source', 'sealed_metadata', 'audio_bytes',
                  'reserved_bytes', 'metadata_reserved', 'part_count', 'parts', 'attempts', 'replaces', 'previous_head'}
        if (set(data) != fields | ({'duration', 'published_at'} if data.get('state') == 'published' else set())
                or data.get('state') not in {'pending', 'published'} or data.get('publication') != publication
                or not isinstance(data.get('parts'), dict) or len(data['parts']) > 16
                or not isinstance(data.get('attempts'), list) or len(data['attempts']) > 32):
            raise HostedValidationError('NARRATION_STATE_INVALID')
        if data['owner_user_id'] != self.authority.owner(tx, pid):
            raise DocumentConflict('NARRATION_OWNERSHIP_CHANGED')
        return data

    @staticmethod
    def _put_intent(tx, path, data, *, create=False):
        # Include actual cipher/key headers, not an optimistic plaintext bound.
        # Leave space for Firestore's encoding and provider metadata below 1 MiB.
        if len(_json(data)) > 750_000:
            raise HostedValidationError('NARRATION_METADATA_TOO_LARGE')
        (tx.create if create else tx.set)(path, data)

    @staticmethod
    def _reserve_growth(data, usage):
        # Count encrypted Firestore metadata too: tiny audio with large timing
        # arrays must not create an unaccounted multi-GiB metadata loophole.
        # Four KiB also reserves the head, encrypted thin index and operation
        # controls. Per-attempt fences have their separate retained charge.
        while True:
            required = ((len(_json(data)) + 1023) // 1024) * 1024 + 4096
            extra = max(0, required - data['metadata_reserved'])
            if not extra:
                break
            data['metadata_reserved'] += extra
            data['reserved_bytes'] += extra
            usage['bytes'] += extra
        if (len(_json(data)) > 750_000
                or usage['bytes'] > CAPACITY + (HEADROOM if data['replaces'] else 0)):
            raise DocumentConflict('NARRATION_STORAGE_CAPACITY')

    def begin(self, uid, pid, metadata, *, audio_bytes, operation):
        uid, pid = require_user_id(uid), require_project_id(pid)
        operation = require_opaque_id(operation)
        if type(audio_bytes) is not int or not 44 < audio_bytes <= MAX_AUDIO:
            raise HostedValidationError('NARRATION_AUDIO_SIZE_INVALID')
        record, kind, eid, replaces = normalize(metadata, self.catalog)
        encoded = _json(record)
        if len(encoded) > MAX_METADATA or len(encoded.decode('utf-8')) > MAX_METADATA_CHARACTERS:
            raise HostedValidationError('NARRATION_METADATA_TOO_LARGE')
        fence, text = self.source.capture(uid, pid, kind=kind, entity_id=eid)
        if record['source_text'] != text:
            raise DocumentConflict('NARRATION_TEXT_CHANGED')
        # Hash ONLY operation identity, never private source/audio. Publication
        # and record locators are random; no private-content hash in object names.
        op_path = (ROOT, pid, 'operations', hashlib.sha256((uid + '\0' + operation).encode()).hexdigest())
        def previous(tx):
            require_authority(self.authority, tx, uid, pid, 'write')
            op = tx.get(op_path)
            return None if op is None else self._load(tx, uid, pid, op.data['publication'], 'write')
        prior = self.documents.run_transaction(previous)
        if prior is not None:
            if (prior.get('actor') != uid or prior.get('audio_bytes') != audio_bytes
                    or prior.get('replaces') != replaces or prior.get('source') != fence
                    or self._open(pid, prior['publication'], prior['sealed_metadata']) != record):
                raise DocumentConflict('NARRATION_IDEMPOTENCY_CONFLICT')
            if prior.get('state') not in {'pending', 'published'}:
                raise DocumentConflict('NARRATION_UPLOAD_UNAVAILABLE')
            return {'publication': prior['publication'], 'id': prior['record_id'], 'part_bytes': PART_BYTES}
        publication, rid, stamp = secrets.token_hex(16), secrets.token_hex(32), self.now()
        count = (audio_bytes + PART_BYTES - 1) // PART_BYTES
        # Lossless compression avoids making CJK/emoji metadata a Firestore-size
        # exception. The encrypted envelope and strict decoded bound stay intact.
        sealed = self.parts.envelope.seal(gzip.compress(encoded, mtime=0), context=self._context(pid, publication))
        reserved = audio_bytes + count * (16 + FENCE_BYTES)
        def commit(tx):
            self.source.check(tx, uid, pid, fence)
            owner = self.authority.owner(tx, pid)
            op = tx.get(op_path)
            if op is not None:
                raise DocumentConflict('NARRATION_OPERATION_RACING_RETRY')
            previous_head = _required(tx.get(_head(pid, replaces))) if replaces else None
            if previous_head and previous_head.get('state') != 'active':
                raise DocumentConflict('NARRATION_REPLACEMENT_INVALID')
            previous_intent = self._load(tx, uid, pid, previous_head['publication']) if previous_head else None
            if previous_intent and previous_intent.get('source', {}).get('id') != eid:
                raise DocumentConflict('NARRATION_REPLACEMENT_INVALID')
            usage = _ledger(tx.get((USAGE, owner)))
            if usage['bytes'] + reserved > CAPACITY + (HEADROOM if replaces else 0):
                raise DocumentConflict('NARRATION_STORAGE_CAPACITY')
            if usage['records'] >= MAX_RECORDS + (2 if replaces else 0):
                raise DocumentConflict('NARRATION_RECORD_CAPACITY')
            active = {key: value for key, value in usage['uploads'].items() if value > stamp}
            if len(active) >= 2:
                raise DocumentConflict('NARRATION_UPLOAD_BUSY')
            active[publication] = stamp + UPLOAD_SECONDS
            usage.update(bytes=usage['bytes'] + reserved, records=usage['records'] + 1, uploads=active)
            data = dict(v=1, project_id=pid, publication=publication, record_id=rid, actor=uid,
                        owner_user_id=owner, epoch=self.epoch, state='pending', created_at=stamp,
                        expires_at=stamp + UPLOAD_SECONDS, source=fence, sealed_metadata=sealed,
                        audio_bytes=audio_bytes, reserved_bytes=reserved, metadata_reserved=0, part_count=count,
                        parts={}, attempts=[], replaces=replaces, previous_head=previous_head)
            self._reserve_growth(data, usage)
            self._put_intent(tx, _intent(pid, publication), data, create=True)
            tx.create(op_path, {'v': 1, 'publication': publication, 'actor': uid})
            tx.create((QUEUE, publication), _queue(pid, publication, owner, stamp + UPLOAD_SECONDS + 172800))
            tx.set((USAGE, owner), usage)
        self.documents.run_transaction(commit)
        return {'publication': publication, 'id': rid, 'part_bytes': PART_BYTES}

    def _pending(self, tx, uid, pid, publication):
        data = self._load(tx, uid, pid, publication, 'write')
        if data.get('actor') != uid or data.get('state') != 'pending' or data['expires_at'] <= self.now():
            raise DocumentConflict('NARRATION_UPLOAD_UNAVAILABLE')
        self.source.check(tx, uid, pid, data['source'])
        return data

    def upload_part(self, uid, pid, publication, ordinal, payload):
        data = self.documents.run_transaction(lambda tx: self._pending(tx, uid, pid, publication))
        if type(ordinal) is not int or not 0 <= ordinal < data['part_count']:
            raise HostedValidationError('NARRATION_PART_INVALID')
        expected = min(PART_BYTES, data['audio_bytes'] - ordinal * PART_BYTES)
        if not isinstance(payload, bytes) or len(payload) != expected:
            raise HostedValidationError('NARRATION_PART_SIZE_INVALID')
        key = str(ordinal)
        if key in data['parts']:
            reference = data['parts'][key]
            actual = self.parts.read(reference, pid=pid, publication=reference['attempt'], ordinal=ordinal)
            if actual != payload:
                raise DocumentConflict('NARRATION_PART_REPLAY_CHANGED')
            # Reauthorize after external object/KMS I/O, including exact reference.
            def replay(tx):
                current = self._pending(tx, uid, pid, publication)
                if current['parts'].get(key) != reference:
                    raise DocumentConflict('NARRATION_PART_CHANGED')
            self.documents.run_transaction(replay)
            return {'ok': True}
        # A retry gets a NEW attempt context/object, never nonce reuse. Bind the
        # ciphertext to its attempt context rather than the outer publication.
        attempt = secrets.token_hex(16)
        ciphertext, header = self.parts.seal(payload, pid=pid, publication=attempt, ordinal=ordinal)
        stamp = self.now()
        def prepare(tx):
            current = self._pending(tx, uid, pid, publication)
            if key in current['parts']:
                raise DocumentConflict('NARRATION_PART_RACING_RETRY')
            old = [item for item in current['attempts'] if item['ordinal'] == ordinal]
            if any(item['until'] > stamp for item in old):
                raise DocumentConflict('NARRATION_PART_BUSY')
            if len(current['attempts']) >= 32:
                raise DocumentConflict('NARRATION_UPLOAD_RETRY_LIMIT')
            owner = current['owner_user_id']
            usage = _ledger(tx.get((USAGE, owner)))
            extra = expected + 16 + FENCE_BYTES if old else 0
            if usage['bytes'] + extra > CAPACITY + (HEADROOM if current['replaces'] else 0):
                raise DocumentConflict('NARRATION_STORAGE_CAPACITY')
            current['reserved_bytes'] += extra
            usage['bytes'] += extra
            current['attempts'].append(dict(id=attempt, ordinal=ordinal, until=stamp + ATTEMPT_SECONDS,
                bytes=expected + 16, header=header, sha256=hashlib.sha256(ciphertext).hexdigest()))
            self._reserve_growth(current, usage)
            tx.create((ATTEMPTS, attempt), dict(v=1, project_id=pid, publication=publication,
                owner_user_id=owner, ordinal=ordinal, size=expected + 16,
                sha256=hashlib.sha256(ciphertext).hexdigest(), fenced=False, generation=None))
            self._put_intent(tx, _intent(pid, publication), current)
            tx.set((USAGE, owner), usage)
        self.documents.run_transaction(prepare)
        reference = self.parts.write(ciphertext, header, pid=pid, publication=attempt, ordinal=ordinal)
        reference['attempt'] = attempt
        def attach(tx):
            current = self._pending(tx, uid, pid, publication)
            attempts = [item for item in current['attempts'] if item['ordinal'] == ordinal]
            if (key in current['parts'] or not attempts or attempts[-1]['id'] != attempt
                    or attempts[-1]['until'] <= self.now()):
                raise DocumentConflict('NARRATION_PART_SUPERSEDED')
            usage = _ledger(tx.get((USAGE, current['owner_user_id'])))
            current['parts'][key] = reference
            self._reserve_growth(current, usage)
            self._put_intent(tx, _intent(pid, publication), current)
            tx.set((USAGE, current['owner_user_id']), usage)
        self.documents.run_transaction(attach)
        return {'ok': True}

    def finalize(self, uid, pid, publication):
        def read(tx):
            data = self._load(tx, uid, pid, publication, 'write')
            if data['state'] == 'published':
                head = _required(tx.get(_head(pid, data['record_id'])))
                if head.get('state') != 'active' or head.get('publication') != publication:
                    raise DocumentConflict('NARRATION_REPLAY_SUPERSEDED')
                return data
            pending = self._pending(tx, uid, pid, publication)
            if pending['replaces'] and _required(tx.get(_head(pid, pending['replaces']))) != pending['previous_head']:
                raise DocumentConflict('NARRATION_REPLACEMENT_CHANGED')
            return pending
        before = self.documents.run_transaction(read)
        record = self._open(pid, publication, before['sealed_metadata'])
        if before['state'] == 'published':
            head, current = self.documents.run_transaction(lambda tx: self.record(tx, uid, pid, before['record_id']))
            if current != before:
                raise DocumentConflict('NARRATION_REPLAY_SUPERSEDED')
            return {**record, 'id': before['record_id'], 'duration': before['duration'], 'generation': head['generation']}
        if set(before['parts']) != {str(i) for i in range(before['part_count'])}:
            raise DocumentConflict('NARRATION_UPLOAD_INCOMPLETE')
        payload = b''.join(self.parts.read(before['parts'][str(i)], pid=pid,
            publication=before['parts'][str(i)]['attempt'], ordinal=i) for i in range(before['part_count']))
        if len(payload) != before['audio_bytes']:
            raise HostedValidationError('NARRATION_AUDIO_SIZE_INVALID')
        duration = validate_audio(payload, record)
        del payload
        stamp = self.now()
        from hosted.narration.index import summary
        previous = before['previous_head']
        removals = [(previous['index_page'], before['replaces'], previous['publication'], previous['generation'])] if previous else []
        indexed = self.index.prepare(uid, pid, add=[summary(record, before['source'],
            record_id=before['record_id'], publication=publication, generation=1, duration=duration,
            audio_bytes=before['audio_bytes'])], remove=removals)
        def commit(tx):
            current = self._pending(tx, uid, pid, publication)
            if current != before:
                raise DocumentConflict('NARRATION_UPLOAD_CHANGED')
            old_path = _head(pid, before['replaces']) if before['replaces'] else None
            old = _required(tx.get(old_path)) if old_path else None
            if old != before['previous_head']:
                raise DocumentConflict('NARRATION_REPLACEMENT_CHANGED')
            head_path = _head(pid, before['record_id'])
            if tx.get(head_path) is not None:
                raise DocumentConflict('NARRATION_RECORD_EXISTS')
            owner = current['owner_user_id']
            usage = _ledger(tx.get((USAGE, owner)))
            usage['uploads'].pop(publication, None)
            current.update(state='published', duration=duration, published_at=stamp)
            self._reserve_growth(current, usage)
            # Validate the serialized intent BEFORE any write (also for fakes).
            if len(_json(current)) > 750_000:
                raise HostedValidationError('NARRATION_METADATA_TOO_LARGE')
            indexed.commit(tx)  # remaining operations are WRITES ONLY
            if old:
                tx.set(old_path, {**old, 'state': 'retired', 'retired_at': stamp, 'generation': old['generation'] + 1})
                tx.set((QUEUE, old['publication']), _queue(pid, old['publication'], owner, stamp + 172800))
            self._put_intent(tx, _intent(pid, publication), current)
            tx.create(head_path, dict(v=1, state='active', publication=publication, epoch=self.epoch,
                                     generation=1, index_page=indexed.locations[before['record_id']]))
            tx.delete((QUEUE, publication))
            tx.set((USAGE, owner), usage)
        self.documents.run_transaction(commit)
        return {**record, 'id': before['record_id'], 'duration': duration, 'generation': 1}

    def record(self, tx, uid, pid, rid):
        require_authority(self.authority, tx, uid, pid, 'read')
        head = _required(tx.get(_head(pid, rid)))
        if head.get('state') != 'active' or head.get('epoch') != self.epoch:
            raise DocumentNotFound('NARRATION_NOT_FOUND')
        if type(head.get('generation')) is not int or head['generation'] < 1:
            raise HostedValidationError('NARRATION_STATE_INVALID')
        data = self._load(tx, uid, pid, head['publication'])
        if data.get('record_id') != rid or data.get('state') != 'published':
            raise HostedValidationError('NARRATION_STATE_INVALID')
        return head, data

    def delete(self, uid, pid, rid, *, expected_generation=None, operation=None):
        operation = require_opaque_id(operation or secrets.token_hex(16))
        if expected_generation is not None and (type(expected_generation) is not int or expected_generation < 1):
            raise HostedValidationError('NARRATION_GENERATION_INVALID')
        def read(tx):
            require_authority(self.authority, tx, uid, pid, 'write')
            head = _required(tx.get(_head(pid, rid)))
            if head.get('epoch') != self.epoch:
                raise DocumentNotFound('NARRATION_NOT_FOUND')
            if head.get('delete_operation') == operation:
                if head.get('state') != 'deleted' or head.get('undo_until', 0) <= self.now():
                    raise DocumentConflict('NARRATION_REPLAY_SUPERSEDED')
                return head
            if expected_generation is not None and head.get('generation') != expected_generation:
                raise DocumentConflict('NARRATION_GENERATION_CONFLICT')
            return self.record(tx, uid, pid, rid)[0]
        before = self.documents.run_transaction(read)
        context = {'project_id': pid, 'entity': 'narration_delete', 'record_id': rid}
        if before.get('delete_operation') == operation:
            token = self.parts.envelope.open(before['sealed_undo_token'], context=context).decode('ascii')
            if self.documents.run_transaction(read) != before:
                raise DocumentConflict('NARRATION_REPLAY_SUPERSEDED')
            return {'undo_token': token}
        token, stamp = secrets.token_hex(32), self.now()
        sealed = self.parts.envelope.seal(token.encode('ascii'), context=context)
        indexed = self.index.prepare(uid, pid, remove=[(before['index_page'], rid, before['publication'], before['generation'])])
        def commit(tx):
            require_authority(self.authority, tx, uid, pid, 'write')
            head, data = self.record(tx, uid, pid, rid)
            if head != before:
                raise DocumentConflict('NARRATION_RECORD_CHANGED')
            indexed.commit(tx)  # remaining operations are WRITES ONLY
            tx.set(_head(pid, rid), {**head, 'state': 'deleted', 'deleted_at': stamp,
                'generation': head['generation'] + 1, 'delete_operation': operation, 'sealed_undo_token': sealed,
                'undo_until': stamp + UNDO_SECONDS, 'undo_digest': hashlib.sha256(token.encode()).hexdigest()})
            eligible = max(stamp + UNDO_SECONDS, *(item['until'] + 172800 for item in data['attempts']))
            tx.set((QUEUE, head['publication']), _queue(pid, head['publication'], data['owner_user_id'], eligible))
        self.documents.run_transaction(commit)
        return {'undo_token': token}

    def restore(self, uid, pid, rid, token):
        require_opaque_id(token)
        stamp = self.now()
        def read(tx):
            require_authority(self.authority, tx, uid, pid, 'write')
            head = _required(tx.get(_head(pid, rid)))
            if (head.get('state') not in {'deleted', 'active'} or head.get('epoch') != self.epoch
                    or head.get('undo_until', 0) <= stamp
                    or not secrets.compare_digest(head.get('undo_digest', ''), hashlib.sha256(token.encode()).hexdigest())):
                raise DocumentNotFound('NARRATION_UNDO_NOT_FOUND')
            data = self._load(tx, uid, pid, head['publication'])
            if data.get('state') != 'published' or data.get('record_id') != rid:
                raise DocumentConflict('NARRATION_UNDO_UNAVAILABLE')
            return data, head
        before, previous = self.documents.run_transaction(read)
        record = self._open(pid, before['publication'], before['sealed_metadata'])
        if previous['state'] == 'deleted':
            from hosted.narration.index import summary
            indexed = self.index.prepare(uid, pid, add=[summary(record, before['source'], record_id=rid,
                publication=before['publication'], generation=previous['generation'] + 1,
                duration=before['duration'], audio_bytes=before['audio_bytes'])])
            def commit(tx):
                data, head = read(tx)
                if data != before or head != previous:
                    raise DocumentConflict('NARRATION_RECORD_CHANGED')
                indexed.commit(tx)  # remaining operations are WRITES ONLY
                head.update(state='active', generation=head['generation'] + 1, index_page=indexed.locations[rid])
                tx.set(_head(pid, rid), head)
                tx.delete((QUEUE, head['publication']))
                return data, head
            data, head = self.documents.run_transaction(commit)
        else:
            data, head = before, previous
        current_head, current_data = self.documents.run_transaction(lambda tx: self.record(tx, uid, pid, rid))
        if current_head != head or current_data != data:
            raise DocumentConflict('NARRATION_RECORD_CHANGED')
        return {**record, 'id': rid, 'duration': data['duration'], 'generation': head['generation']}
