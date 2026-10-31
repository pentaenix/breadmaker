"""Bounded, generation-fenced derivative collection. NOT an HTTP route.

Plain deletion is insufficient: a paused create-only upload could commit AFTER
deletion. An atomic, exact-generation replacement by a permanent zero-byte
object at the SAME random attempt name closes that cross-provider race. There
is no delete/recreate gap. Install/read back every fence BEFORE quota settlement.
All writers MUST remain create-only. Fences are never expired/deleted by GC.
Each costs a conservative 2 KiB retained capacity charge; no private payload,
keys, text, voice, user identity or source hashes are retained in these objects.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re
import secrets
import time

from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.narration.maintenance import CONTROL, RECEIPTS, NarrationMaintenanceStore
from hosted.narration.recordings import ROOT, USAGE, ATTEMPTS, QUEUE, FENCE_BYTES, _head, _intent, _ledger, _required
from hosted.storage.contracts import ObjectAlreadyExists

GRACE = 172_800
FENCE_METADATA = {'breadmaker-narration-fence': 'v1'}
EMPTY_SHA = hashlib.sha256(b'').hexdigest()
LEASE = (CONTROL, 'daily')
_OBJECT = re.compile(r'projects/([0-9a-f]{32})/narration/([0-9a-f]{32})/([0-9]{3})\.bin')


def is_fence(metadata):
    return (metadata is not None and metadata.size == 0 and metadata.sha256 == EMPTY_SHA
            and metadata.metadata.get('breadmaker-narration-fence') == 'v1')


@dataclass(frozen=True)
class GCLease:
    holder: str


class NarrationGC:
    def __init__(self, documents, objects, *, retention_allows, now=time.time):
        if not isinstance(documents, NarrationMaintenanceStore) or not callable(retention_allows):
            raise ValueError('NARRATION_GC_MAINTENANCE_SCOPE_REQUIRED')
        self.documents, self.objects = documents, objects
        # Callback is READ ONLY: verify owner and Trash/account holds in SAME tx.
        # Literal True only; missing/uncertain authority must not permit erasure.
        self.retention_allows, self.now = retention_allows, now

    def _time(self):
        value = self.now()
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value < 253402214400:
            raise ValueError('NARRATION_GC_CLOCK_INVALID')
        return value

    def acquire(self):
        lease, stamp = GCLease(secrets.token_hex(16)), self._time()
        def commit(tx):
            row = tx.get(LEASE)
            if row is not None:
                self._lease_row(row)
                if row.data['until'] > stamp:
                    raise DocumentConflict('NARRATION_GC_LEASE_BUSY')
            data = dict(row.data) if row else dict(v=1, intent_after='', object_after='')
            data.update(holder=lease.holder, until=stamp + 120)
            tx.set(LEASE, data)
            return lease
        return self.documents.run_transaction(commit)

    @staticmethod
    def _lease_row(row):
        data = row.data
        if (set(data) != {'v', 'holder', 'until', 'intent_after', 'object_after'}
                or type(data['v']) is not int or data['v'] != 1
                or not isinstance(data['holder'], str)
                or type(data['until']) not in (int, float) or not math.isfinite(data['until'])
                or any(not isinstance(data[key], str) or len(data[key]) > 1024 for key in ('intent_after', 'object_after'))):
            raise HostedValidationError('NARRATION_GC_LEASE_INVALID')
        return dict(data)

    def _check(self, tx, lease):
        row = tx.get(LEASE)
        if not isinstance(lease, GCLease) or row is None:
            raise DocumentConflict('NARRATION_GC_LEASE_LOST')
        data = self._lease_row(row)
        if data['holder'] != lease.holder or data['until'] <= self._time():
            raise DocumentConflict('NARRATION_GC_LEASE_LOST')
        return data

    def checkpoint(self, lease, **cursor):
        if set(cursor) - {'intent_after', 'object_after'}:
            raise ValueError('NARRATION_GC_CURSOR_INVALID')
        def commit(tx):
            data = self._check(tx, lease)
            if any(not isinstance(value, str) or len(value) > 1024 for value in cursor.values()):
                raise ValueError('NARRATION_GC_CURSOR_INVALID')
            data.update(cursor, until=self._time() + 120)
            tx.set(LEASE, data)
            return data
        return self.documents.run_transaction(commit)

    def release(self, lease):
        def commit(tx):
            data = self._check(tx, lease)
            data.update(holder='', until=self._time())
            tx.set(LEASE, data)
        self.documents.run_transaction(commit)

    def _manifest(self, pid, publication, data):
        if (data.get('project_id') != pid or data.get('publication') != publication
                or data.get('state') not in {'pending', 'published'}
                or not isinstance(data.get('attempts'), list) or len(data['attempts']) > 32
                or not isinstance(data.get('parts'), dict) or len(data['parts']) > 16
                or type(data.get('reserved_bytes')) is not int or data['reserved_bytes'] < 1
                or any(type(data.get(key)) not in (int, float) or not math.isfinite(data[key]) or data[key] <= 0
                       for key in ('created_at', 'expires_at'))
                or data['expires_at'] < data['created_at']):
            raise HostedValidationError('NARRATION_GC_INTENT_INVALID')
        require_user_id(data.get('owner_user_id'))
        objects, seen = [], set()
        for attempt in data['attempts']:
            aid, ordinal = attempt.get('id'), attempt.get('ordinal')
            if (not isinstance(aid, str) or not re.fullmatch('[0-9a-f]{32}', aid) or aid in seen
                    or type(ordinal) is not int or not 0 <= ordinal < 16
                    or type(attempt.get('bytes')) is not int or not 17 <= attempt['bytes'] <= 2 * 1024**2 + 16
                    or not isinstance(attempt.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', attempt['sha256'])
                    or type(attempt.get('until')) not in (int, float) or not math.isfinite(attempt['until'])):
                raise HostedValidationError('NARRATION_GC_ATTEMPT_INVALID')
            if attempt['until'] + GRACE > self._time():
                return None
            seen.add(aid)
            name = f'projects/{pid}/narration/{aid}/{ordinal:03d}.bin'
            ref = data['parts'].get(str(ordinal))
            generation = None
            if ref and ref.get('attempt') == aid:
                if (ref.get('name') != name or ref.get('size') != attempt['bytes']
                        or ref.get('sha256') != attempt['sha256']
                        or type(ref.get('generation')) is not int or ref['generation'] < 1):
                    raise HostedValidationError('NARRATION_GC_REFERENCE_INVALID')
                generation = ref['generation']
            objects.append(dict(name=name, size=attempt['bytes'], sha256=attempt['sha256'], generation=generation))
        if any(ref.get('attempt') not in seen for ref in data['parts'].values()):
            raise HostedValidationError('NARRATION_GC_REFERENCE_INVALID')
        if data['reserved_bytes'] < len(objects) * FENCE_BYTES:
            raise HostedValidationError('NARRATION_GC_RESERVATION_INVALID')
        return objects

    def claim(self, pid, publication, lease):
        pid, path = require_project_id(pid), _intent(pid, publication)
        def commit(tx):
            self._check(tx, lease)
            row = tx.get(path)
            if row is None:
                return None
            data = _required(row)
            owner = require_user_id(data.get('owner_user_id'))
            if self.retention_allows(tx, pid, owner) is not True:
                return None
            if data.get('state') == 'collecting':
                self._validate_collection(data, pid, publication)
                return data
            objects = self._manifest(pid, publication, data)
            if objects is None:
                return None
            refs = tx.query_collection((ROOT, pid, 'records'), field='publication', value=publication)
            head = refs[0] if len(refs) == 1 else None
            if len(refs) > 1 or head and head.path != _head(pid, data['record_id']):
                raise DocumentConflict('NARRATION_GC_REFERENCE_CHANGED')
            stamp = self._time()
            if data['state'] == 'published':
                if head is None:
                    raise DocumentConflict('NARRATION_GC_HEAD_MISSING')
                state = head.data.get('state')
                if state == 'active':
                    return None  # NEVER age-expire an active recording
                cutoff = head.data.get('undo_until') if state == 'deleted' else head.data.get('retired_at', stamp) + GRACE if state == 'retired' else None
                if type(cutoff) not in (int, float) or not math.isfinite(cutoff):
                    raise HostedValidationError('NARRATION_GC_HEAD_INVALID')
                if cutoff > stamp:
                    return None
            elif head is not None or data['expires_at'] + GRACE > stamp:
                return None
            usage = _ledger(tx.get((USAGE, owner)))
            for item in objects:
                aid = _OBJECT.fullmatch(item['name']).group(2)
                index = tx.get((ATTEMPTS, aid))
                expected = dict(v=1, project_id=pid, publication=publication, owner_user_id=owner,
                    ordinal=int(_OBJECT.fullmatch(item['name']).group(3)), size=item['size'],
                    sha256=item['sha256'], fenced=False, generation=None)
                if index is None or dict(index.data) != expected:
                    raise HostedValidationError('NARRATION_GC_ATTEMPT_INDEX_INVALID')
            if usage['bytes'] < data['reserved_bytes'] or usage['records'] < 1:
                raise HostedValidationError('NARRATION_GC_RESERVATION_INVALID')
            claimed = dict(v=1, state='collecting', project_id=pid, publication=publication,
                record_id=data['record_id'], owner_user_id=owner, epoch=data['epoch'],
                reserved_bytes=data['reserved_bytes'], objects=objects, cursor=0, claimed_at=stamp)
            # Fencing publication + Undo precedes EVERY object mutation. No prose,
            # encrypted metadata or keys are needed by the collector afterward.
            tx.set(path, claimed)
            if head:
                tx.set(head.path, dict(v=1, state='collecting', publication=publication,
                    epoch=data['epoch'], generation=head.data['generation'] + 1))
            return claimed
        return self.documents.run_transaction(commit)

    @staticmethod
    def _validate_collection(data, pid, publication):
        if (set(data) != {'v', 'state', 'project_id', 'publication', 'record_id', 'owner_user_id', 'epoch',
                         'reserved_bytes', 'objects', 'cursor', 'claimed_at'}
                or data['state'] != 'collecting' or data['project_id'] != pid or data['publication'] != publication
                or not isinstance(data['objects'], list) or len(data['objects']) > 32
                or type(data['cursor']) is not int or not 0 <= data['cursor'] <= len(data['objects'])
                or type(data['reserved_bytes']) is not int or data['reserved_bytes'] < len(data['objects']) * FENCE_BYTES):
            raise HostedValidationError('NARRATION_GC_CLAIM_INVALID')
        for item in data['objects']:
            if (not isinstance(item, dict) or set(item) != {'name', 'size', 'sha256', 'generation'}
                    or not isinstance(item['name'], str) or not _OBJECT.fullmatch(item['name'])
                    or _OBJECT.fullmatch(item['name']).group(1) != pid
                    or type(item['size']) is not int or not 17 <= item['size'] <= 2 * 1024**2 + 16
                    or not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
                    or item['generation'] is not None and (type(item['generation']) is not int or item['generation'] < 1)):
                raise HostedValidationError('NARRATION_GC_CLAIM_INVALID')

    def _current(self, tx, pid, publication, lease, expected):
        self._check(tx, lease)
        data = _required(tx.get(_intent(pid, publication)))
        self._validate_collection(data, pid, publication)
        if data != expected:
            raise DocumentConflict('NARRATION_GC_CLAIM_CHANGED')
        if self.retention_allows(tx, pid, data['owner_user_id']) is not True:
            raise DocumentConflict('NARRATION_GC_RETENTION_HELD')
        return data

    def collect(self, pid, publication, lease, *, max_objects=32, deadline=None):
        if type(max_objects) is not int or not 1 <= max_objects <= 32:
            raise ValueError('NARRATION_GC_BATCH_INVALID')
        data = self.claim(pid, publication, lease)
        if data is None:
            return False
        stop = min(len(data['objects']), data['cursor'] + max_objects)
        while data['cursor'] < stop:
            if deadline is not None and time.monotonic() >= deadline - 45:
                return False
            self.checkpoint(lease)  # renew/check BEFORE provider I/O
            item = data['objects'][data['cursor']]
            self.documents.run_transaction(lambda tx: self._current(tx, pid, publication, lease, data))
            actual = self.objects.stat(item['name'])
            if not is_fence(actual):
                if actual is not None:
                    if ((actual.size, actual.sha256) != (item['size'], item['sha256'])
                            or item['generation'] is not None and item['generation'] != actual.generation):
                        raise DocumentConflict('NARRATION_GC_OBJECT_CHANGED')
                    # Persist an uncertain attempt's observed generation before
                    # conditional replacement. A lost response resumes it.
                    if item['generation'] is None:
                        updated = {**item, 'generation': actual.generation}
                        def bind(tx):
                            current = self._current(tx, pid, publication, lease, data)
                            current['objects'][current['cursor']] = updated
                            tx.set(_intent(pid, publication), current)
                            return current
                        data = self.documents.run_transaction(bind)
                        item = data['objects'][data['cursor']]
                try:
                    self.checkpoint(lease)
                    # Existing audio is replaced ATOMICALLY, never deleted then
                    # recreated. Create-only late writers cannot commit either
                    # before or after the zero-byte generation replaces it.
                    self.objects.write_chunks(item['name'], [b''], max_bytes=0,
                        content_type='application/octet-stream',
                        if_generation_match=actual.generation if actual else 0, metadata=FENCE_METADATA)
                except ObjectAlreadyExists:
                    pass  # a late writer may win; never settle unless OUR fence exists
                actual = self.objects.stat(item['name'])
                if not is_fence(actual):
                    raise DocumentConflict('NARRATION_GC_FENCE_NOT_INSTALLED')
            def advance(tx):
                current = self._current(tx, pid, publication, lease, data)
                aid = _OBJECT.fullmatch(item['name']).group(2)
                index = _required(tx.get((ATTEMPTS, aid)))
                if index.get('project_id') != pid or index.get('publication') != publication:
                    raise DocumentConflict('NARRATION_GC_ATTEMPT_INDEX_INVALID')
                index.update(fenced=True, generation=actual.generation)
                current['cursor'] += 1
                tx.set((ATTEMPTS, aid), index)
                tx.set(_intent(pid, publication), current)
                return current
            data = self.documents.run_transaction(advance)
        if data['cursor'] != len(data['objects']):
            return False
        # Final independent readback. A fence must not be removed by another
        # cleanup path, bucket lifecycle, or physical-erasure inventory sweeper.
        for item in data['objects']:
            if deadline is not None and time.monotonic() >= deadline - 30:
                return False
            self.checkpoint(lease)
            if not is_fence(self.objects.stat(item['name'])):
                raise DocumentConflict('NARRATION_GC_FENCE_NOT_INSTALLED')
        receipt = (RECEIPTS, pid + '_' + publication)
        def settle(tx):
            current = self._current(tx, pid, publication, lease, data)
            usage = _ledger(tx.get((USAGE, current['owner_user_id'])))
            head_path = _head(pid, current['record_id'])
            head = tx.get(head_path)
            previous = tx.get(receipt)
            queue = tx.get((QUEUE, publication))
            if queue is None or queue.data.get('project_id') != pid or queue.data.get('owner_user_id') != current['owner_user_id']:
                raise DocumentConflict('NARRATION_GC_QUEUE_CHANGED')
            if previous is not None or head and (head.data.get('state') != 'collecting' or head.data.get('publication') != publication):
                raise DocumentConflict('NARRATION_GC_SETTLEMENT_CHANGED')
            refund = current['reserved_bytes'] - len(current['objects']) * FENCE_BYTES
            if usage['bytes'] < current['reserved_bytes'] or usage['records'] < 1:
                raise HostedValidationError('NARRATION_GC_RESERVATION_INVALID')
            usage.update(bytes=usage['bytes'] - refund, records=usage['records'] - 1)
            usage['uploads'].pop(publication, None)
            stamp = datetime.fromtimestamp(self._time(), timezone.utc)
            tx.set((USAGE, current['owner_user_id']), usage)
            tx.create(receipt, dict(v=1, project_id=pid, publication=publication,
                owner_user_id=current['owner_user_id'], fenced_objects=len(current['objects']),
                retained_capacity_bytes=len(current['objects']) * FENCE_BYTES,
                completed_at=stamp, expires_at=stamp + timedelta(days=30)))
            tx.delete(_intent(pid, publication))
            tx.delete((QUEUE, publication))
            if head:
                tx.delete(head_path)
        self.documents.run_transaction(settle)
        return True
