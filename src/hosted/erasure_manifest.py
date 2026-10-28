"""Immutable, encrypted preparation records; NOT a deletion executor/permit.

Uses the existing protected audit prefix and envelope codec. No SDK, HTTP route,
job, key, IAM grant, identity reset or retention change is installed here.
Manifests bind exact provider versions but still require live fences, ownership,
backup verification and retention rechecks before any future destructive batch.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import re

from hosted.erasure_census import CensusDocument, SnapshotCensus, exact_timestamp
from hosted.firestore.contracts import DocumentConflict, require_document_path
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.storage.contracts import ObjectAlreadyExists, ObjectGenerationConflict
from hosted.storage.keys import require_object_name, require_sha256

MAX_ENTRIES = 25_000
SHARD_BYTES = 128 * 1024
MAX_SHARDS = 256
MAX_SEALED_BYTES = 192 * 1024
FORMAT = 'breadmaker-staged-erasure-preparation-v1'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _utc(stamp):
    canonical = exact_timestamp(stamp)
    # Date arithmetic below preserves safety by rounding deadlines UP, never
    # making a nanosecond provider retention deadline eligible early.
    value = datetime.fromisoformat(canonical.replace('Z', '+00:00'))
    if canonical[-4:-1] != '000':
        value += timedelta(microseconds=1)
    return value


def _positive(value):
    if (not isinstance(value, str) or not value.isascii() or not value.isdecimal()
            or len(value) > 20 or int(value) < 1):
        raise ValueError('ERASURE_OBJECT_VERSION_INVALID')
    return str(int(value))


def object_record(bucket: str, metadata: dict, *, project: str, project_id: str) -> dict:
    """Normalize only explicitly scoped provider metadata, including old copies."""
    pid = require_project_id(project_id)
    if not isinstance(metadata, dict):
        raise ValueError('ERASURE_OBJECT_METADATA_INVALID')
    if bucket not in {f'{project}-breadmaker-{kind}' for kind in ('content', 'assets', 'recovery')}:
        raise ValueError('ERASURE_BUCKET_SCOPE_INVALID')
    name = require_object_name(metadata.get('name'))
    prefixes = (f'projects/{pid}/', f'recovery/v1/projects/{pid}/')
    if not name.startswith(prefixes):
        raise ValueError('ERASURE_OBJECT_SCOPE_INVALID')
    size = metadata.get('size')
    if (not isinstance(size, str) or not size.isascii() or not size.isdecimal() or len(size) > 20):
        raise ValueError('ERASURE_OBJECT_SIZE_INVALID')
    for key in ('temporaryHold', 'eventBasedHold'):
        if key in metadata and type(metadata[key]) is not bool:
            raise ValueError('ERASURE_OBJECT_HOLD_INVALID')
    retention = metadata.get('retention', {})
    if not isinstance(retention, dict):
        raise ValueError('ERASURE_OBJECT_RETENTION_INVALID')
    if retention and retention.get('mode') not in {'Locked', 'Unlocked'}:
        raise ValueError('ERASURE_OBJECT_RETENTION_INVALID')
    deadlines = [exact_timestamp(metadata[key]) for key in ('retentionExpirationTime',) if key in metadata]
    if retention:
        deadlines.append(exact_timestamp(retention.get('retainUntilTime')))
    record = {'kind': 'object', 'bucket': bucket, 'name': name,
              'generation': _positive(metadata.get('generation')),
              'metageneration': _positive(metadata.get('metageneration')),
              'size': str(int(size)), 'created_at': exact_timestamp(metadata.get('timeCreated')),
              'hold': metadata.get('temporaryHold', False) or metadata.get('eventBasedHold', False),
              'retention_until': max(deadlines) if deadlines else None,
              'soft_deleted_at': None, 'hard_delete_at': None}
    soft, hard = metadata.get('softDeleteTime'), metadata.get('hardDeleteTime')
    if bool(soft) != bool(hard):
        raise ValueError('ERASURE_OBJECT_SOFT_DELETE_INVALID')
    if soft:
        record['soft_deleted_at'], record['hard_delete_at'] = exact_timestamp(soft), exact_timestamp(hard)
        if not record['created_at'] <= record['soft_deleted_at'] <= record['hard_delete_at']:
            raise ValueError('ERASURE_OBJECT_SOFT_DELETE_INVALID')
    return record


def retention_status(record: dict, *, requested_at: str, now: datetime) -> dict:
    """Diagnostic scheduling only; eligible != deleted or authorized.

    Existing 30-day project Trash/recovery floors stay in force. A soft-deleted
    copy is never proposed for restore/redelete; actual provider absence must
    later be verified even after its advertised hardDeleteTime has elapsed.
    """
    if now.tzinfo is None:
        raise ValueError('ERASURE_CLOCK_INVALID')
    floor = _utc(requested_at) + timedelta(days=30)
    if record['bucket'].endswith('-breadmaker-recovery'):
        floor = max(floor, _utc(record['created_at']) + timedelta(days=30))
    if record['retention_until']:
        floor = max(floor, _utc(record['retention_until']))
    if record['hold']:
        return {'state': 'held', 'next_check_at': None, 'complete': False}
    if record['soft_deleted_at']:
        floor = max(floor, _utc(record['hard_delete_at']))
        state = 'awaiting_provider_expiry' if now < floor else 'provider_absence_check_required'
    else:
        state = 'retained' if now < floor else 'eligible_for_revalidation'
    return {'state': state, 'next_check_at': floor.isoformat(), 'complete': False}


def project_entries(census: SnapshotCensus, *, project_id: str, objects=()) -> tuple[dict, ...]:
    """Only a single exact project subtree; never a whole-database delete list."""
    pid = require_project_id(project_id)
    root = ('projects', pid)
    if census.roots != (root,):
        raise ValueError('ERASURE_MANIFEST_CENSUS_SCOPE_INVALID')
    if not re.fullmatch(r'projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/databases/\(default\)', census.database):
        raise ValueError('ERASURE_MANIFEST_DATABASE_INVALID')
    if len(census.documents) + len(census.missing_parents) > MAX_ENTRIES:
        raise ValueError('ERASURE_MANIFEST_ENTRY_LIMIT')
    entries, seen = [], set()
    for document in census.documents:
        path = require_document_path(document.path)
        if path[:2] != root:
            raise ValueError('ERASURE_MANIFEST_DOCUMENT_SCOPE_INVALID')
        timestamp = exact_timestamp(document.update_time)
        if timestamp > exact_timestamp(census.read_time):
            raise ValueError('ERASURE_MANIFEST_FUTURE_DOCUMENT')
        entries.append({'kind': 'document', 'path': list(path), 'update_time': timestamp})
        if path in seen:
            raise ValueError('ERASURE_MANIFEST_DUPLICATE_ENTRY')
        seen.add(path)
    if root not in seen:
        raise ValueError('ERASURE_MANIFEST_ROOT_REQUIRED')
    for path in census.missing_parents:
        path = require_document_path(path)
        if path[:2] != root or path in seen:
            raise ValueError('ERASURE_MANIFEST_MISSING_PARENT_INVALID')
        seen.add(path)
        entries.append({'kind': 'missing_parent', 'path': list(path)})
    project = census.database.split('/')[1]
    seen_objects = set()
    for bucket, metadata in objects:
        record = object_record(bucket, metadata, project=project, project_id=pid)
        identity = (record['bucket'], record['name'], record['generation'])
        if identity in seen_objects:
            raise ValueError('ERASURE_MANIFEST_DUPLICATE_ENTRY')
        seen_objects.add(identity)
        entries.append(record)
        if len(entries) > MAX_ENTRIES:
            raise ValueError('ERASURE_MANIFEST_ENTRY_LIMIT')
    if len(entries) > MAX_ENTRIES:
        raise ValueError('ERASURE_MANIFEST_ENTRY_LIMIT')
    return tuple(sorted(entries, key=_json))


@dataclass(frozen=True)
class PreparedManifest:
    name: str
    generation: int
    entry_count: int
    shard_count: int
    state: str = 'prepared_not_authorized'


class SealedErasureManifestStore:
    """Create-only encrypted shards; the encrypted commit object is written LAST.

    Retry decrypts and compares existing records, never overwrites them. A crash
    can leave uncommitted shards, never a valid partial preparation. The caller
    must supply an independent positive operator authorization on EVERY call.
    This storage capability is not account-deletion or recovery authority.
    """
    def __init__(self, objects, codec, *, authorize):
        if not callable(authorize):
            raise ValueError('ERASURE_MANIFEST_OPERATOR_AUTH_REQUIRED')
        self.objects, self.codec, self.authorize = objects, codec, authorize

    @staticmethod
    def _context(pid, operation, index):
        return {'project_id': pid, 'entity': 'erasure_preparation',
                'record_id': operation + '_' + str(index)}

    def _read(self, name, context, *, generation=None):
        meta = self.objects.stat(name)
        if meta is None or (generation is not None and meta.generation != generation):
            raise DocumentConflict('ERASURE_MANIFEST_MISSING_OR_CHANGED')
        if not 0 < meta.size <= MAX_SEALED_BYTES:
            raise ValueError('ERASURE_MANIFEST_OBJECT_LIMIT')
        raw = bytearray()
        for chunk in self.objects.read_chunks(name, max_bytes=MAX_SEALED_BYTES,
                                               if_generation_match=meta.generation):
            raw.extend(chunk)
            if len(raw) > MAX_SEALED_BYTES:
                raise ValueError('ERASURE_MANIFEST_OBJECT_LIMIT')
        if len(raw) != meta.size or hashlib.sha256(raw).hexdigest() != meta.sha256:
            raise DocumentConflict('ERASURE_MANIFEST_CIPHERTEXT_CHANGED')
        try:
            plain = self.codec.open(json.loads(raw), context=context)
            if len(plain) > SHARD_BYTES:
                raise ValueError()
            value = json.loads(plain)
            if _json(value) != plain:
                raise ValueError()
        except Exception:
            raise DocumentConflict('ERASURE_MANIFEST_AUTHENTICATION_FAILED') from None
        return value, meta

    def _put(self, name, value, context):
        plain = _json(value)
        if len(plain) > SHARD_BYTES:
            raise ValueError('ERASURE_MANIFEST_SHARD_LIMIT')
        if self.objects.stat(name) is None:
            raw = _json(self.codec.seal(plain, context=context))
            if len(raw) > MAX_SEALED_BYTES:
                raise ValueError('ERASURE_MANIFEST_OBJECT_LIMIT')
            digest = hashlib.sha256(raw).hexdigest()
            try:
                self.objects.write_chunks(name, [raw], max_bytes=MAX_SEALED_BYTES,
                    content_type='application/json', if_generation_match=0,
                    metadata={'breadmaker-sha256': digest})
            except (ObjectAlreadyExists, ObjectGenerationConflict):
                pass  # Concurrent retry must independently authenticate below.
        previous, meta = self._read(name, context)
        if previous != value:
            raise DocumentConflict('ERASURE_MANIFEST_REPLAY_CONFLICT')
        return meta

    def prepare(self, census: SnapshotCensus, *, project_id: str, owner_user_id: str,
                operation: str, requested_at: str, objects=()) -> PreparedManifest:
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        op = require_sha256(operation)
        if self.authorize(pid, uid, op) is not True:
            raise DocumentConflict('ERASURE_MANIFEST_OPERATOR_DENIED')
        entries = project_entries(census, project_id=pid, objects=objects)
        requested = exact_timestamp(requested_at)
        if requested > exact_timestamp(census.read_time):
            raise ValueError('ERASURE_MANIFEST_REQUEST_AFTER_CENSUS')
        base = f'erasure-audit/v1/{pid}/{op}/'
        header = {'format': FORMAT, 'project_id': pid, 'owner_user_id': uid,
                  'operation': op, 'requested_at': requested,
                  'database': census.database, 'read_time': census.read_time,
                  'state': 'prepared_not_authorized', 'cross_provider_frozen': False}
        parts, pending, pending_bytes = [], [], 2

        def flush():
            nonlocal pending, pending_bytes
            if len(parts) >= MAX_SHARDS:
                raise ValueError('ERASURE_MANIFEST_SHARD_COUNT_LIMIT')
            index = len(parts)
            name = base + f'shard-{index:06d}.json'
            value = {'index': index, 'entries': pending}
            meta = self._put(name, value, self._context(pid, op, index))
            parts.append({'name': name, 'generation': meta.generation,
                          'sha256': meta.sha256, 'entry_count': len(pending)})
            pending, pending_bytes = [], 2

        for entry in entries:
            size = len(_json(entry)) + 1
            if size + 64 > SHARD_BYTES:
                raise ValueError('ERASURE_MANIFEST_ENTRY_BYTES_LIMIT')
            if pending and pending_bytes + size + 64 > SHARD_BYTES:
                flush()
            pending.append(entry)
            pending_bytes += size
        if pending:
            flush()
        commit = {**header, 'parts': parts, 'entry_count': len(entries)}
        name = base + 'commit.json'
        if self.authorize(pid, uid, op) is not True:
            raise DocumentConflict('ERASURE_MANIFEST_OPERATOR_DENIED')
        meta = self._put(name, commit, self._context(pid, op, 'commit'))
        return PreparedManifest(name, meta.generation, len(entries), len(parts))

    def read(self, *, project_id: str, owner_user_id: str, operation: str,
             generation: int) -> tuple[dict, tuple[dict, ...]]:
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        op = require_sha256(operation)
        if self.authorize(pid, uid, op) is not True:
            raise DocumentConflict('ERASURE_MANIFEST_OPERATOR_DENIED')
        if type(generation) is not int or generation < 1:
            raise ValueError('ERASURE_MANIFEST_GENERATION_INVALID')
        base = f'erasure-audit/v1/{pid}/{op}/'
        commit, _ = self._read(base+'commit.json', self._context(pid, op, 'commit'), generation=generation)
        fields = {'format', 'project_id', 'owner_user_id', 'operation', 'requested_at', 'database',
                  'read_time', 'state', 'cross_provider_frozen', 'parts', 'entry_count'}
        if (not isinstance(commit, dict) or set(commit) != fields or commit['format'] != FORMAT
                or commit['project_id'] != pid or commit['owner_user_id'] != uid
                or commit['operation'] != op or commit['state'] != 'prepared_not_authorized'
                or commit['cross_provider_frozen'] is not False
                or type(commit['entry_count']) is not int or not 1 <= commit['entry_count'] <= MAX_ENTRIES
                or not isinstance(commit['parts'], list) or not 1 <= len(commit['parts']) <= MAX_SHARDS):
            raise DocumentConflict('ERASURE_MANIFEST_COMMIT_INVALID')
        entries = []
        for index, part in enumerate(commit['parts']):
            if (not isinstance(part, dict) or set(part) != {'name', 'generation', 'sha256', 'entry_count'}
                    or part['name'] != base+f'shard-{index:06d}.json'
                    or type(part['generation']) is not int or part['generation'] < 1
                    or type(part['entry_count']) is not int or not 1 <= part['entry_count'] <= MAX_ENTRIES):
                raise DocumentConflict('ERASURE_MANIFEST_PART_INVALID')
            value, meta = self._read(part['name'], self._context(pid, op, index), generation=part['generation'])
            if (meta.sha256 != part['sha256'] or not isinstance(value, dict)
                    or set(value) != {'index', 'entries'} or value['index'] != index
                    or not isinstance(value['entries'], list) or len(value['entries']) != part['entry_count']):
                raise DocumentConflict('ERASURE_MANIFEST_PART_INVALID')
            entries.extend(value['entries'])
            if len(entries) > MAX_ENTRIES:
                raise ValueError('ERASURE_MANIFEST_ENTRY_LIMIT')
        if len(entries) != commit['entry_count']:
            raise DocumentConflict('ERASURE_MANIFEST_INCOMPLETE')
        self._validate_entries(commit, entries)
        if self.authorize(pid, uid, op) is not True:
            raise DocumentConflict('ERASURE_MANIFEST_OPERATOR_DENIED')
        return commit, tuple(entries)

    @staticmethod
    def _validate_entries(commit, entries):
        """Authenticated ciphertext is not a substitute for a scoped schema."""
        try:
            if exact_timestamp(commit['requested_at']) > exact_timestamp(commit['read_time']):
                raise ValueError()
            docs, missing, objects = [], [], []
            object_fields = {'kind', 'bucket', 'name', 'generation', 'metageneration', 'size',
                'created_at', 'hold', 'retention_until', 'soft_deleted_at', 'hard_delete_at'}
            for entry in entries:
                if entry.get('kind') == 'document' and set(entry) == {'kind', 'path', 'update_time'}:
                    docs.append(CensusDocument(tuple(entry['path']), entry['update_time']))
                elif entry.get('kind') == 'missing_parent' and set(entry) == {'kind', 'path'}:
                    missing.append(tuple(entry['path']))
                elif entry.get('kind') == 'object' and set(entry) == object_fields:
                    raw = {'name': entry['name'], 'generation': entry['generation'],
                           'metageneration': entry['metageneration'], 'size': entry['size'],
                           'timeCreated': entry['created_at'], 'temporaryHold': entry['hold']}
                    for source, target in (('retention_until', 'retentionExpirationTime'),
                                           ('soft_deleted_at', 'softDeleteTime'),
                                           ('hard_delete_at', 'hardDeleteTime')):
                        if entry[source] is not None:
                            raw[target] = entry[source]
                    objects.append((entry['bucket'], raw))
                else:
                    raise ValueError()
            pid = commit['project_id']
            census = SnapshotCensus(commit['database'], commit['read_time'], (('projects', pid),),
                                    tuple(docs), tuple(missing), 0)
            if tuple(entries) != project_entries(census, project_id=pid, objects=objects):
                raise ValueError()
        except (AttributeError, KeyError, TypeError, ValueError):
            raise DocumentConflict('ERASURE_MANIFEST_ENTRY_SCHEMA_INVALID') from None
