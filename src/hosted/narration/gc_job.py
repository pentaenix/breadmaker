"""Daily bounded Narration maintenance. Explicit CLI only; never a web route."""
from datetime import datetime, timezone
import json
import math
import time

from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.narration.gc import NarrationGC, _OBJECT, is_fence
from hosted.narration.maintenance import NarrationMaintenanceStore
from hosted.narration.recordings import ATTEMPTS, QUEUE, _intent


def _date(value):
    if not isinstance(value, str):
        raise ValueError('NARRATION_GC_RETENTION_INVALID')
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('NARRATION_GC_RETENTION_INVALID')
    return value.timestamp()


def retention_allows(tx, pid, owner, *, now):
    """Known owner only; preserve Trash and existing staged-erasure safeguards."""
    root = tx.get(('projects', pid))
    user = tx.get(('users', owner))
    owners = tx.query_collection(('projects', pid, 'members'), field='role', value='owner')
    if (root is None or user is None or len(owners) != 1 or owners[0].path != ('projects', pid, 'members', owner)
            or owners[0].data.get('user_id') != owner or root.data.get('legal_hold')
            or root.data.get('narration_hold')):
        return False
    lock = tx.get(('account_erasure_locks', owner))
    status = root.data.get('status')
    if lock is not None or user.data.get('status', 'active') != 'active' or status == 'erasure_pending':
        # Do not reinterpret an ordinary account fence as purge authorization.
        rid = lock.data.get('request_id') if lock else None
        if not isinstance(rid, str) or not rid or '/' in rid:
            return False
        closure = tx.get(('account_erasures', rid))
        return bool(closure is not None and closure.data.get('user_id') == owner
                    and root.data.get('_bm_erasure_operation') == rid
                    and closure.data.get('state') == 'closed_retention_pending'
                    and pid in closure.data.get('owned_project_ids', [])
                    and closure.data.get('physical_purge_authorized') is True
                    and _date(closure.data.get('retain_until')) <= now)
    if status == 'active':
        return True
    if status == 'deleted':
        return _date(root.data.get('deleted_at')) + 30 * 86400 <= now
    return False


class GoogleGCQueue:
    def __init__(self, client): self.client = client
    def page(self, *, after, now, limit=25):
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError('NARRATION_GC_PAGE_INVALID')
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = self.client.collection(QUEUE).where(filter=FieldFilter('eligible_at', '<=', now))
        query = query.order_by('eligible_at').order_by('__name__')
        if after:
            try:
                cursor = json.loads(after)
                if (set(cursor) != {'eligible', 'id'} or type(cursor['eligible']) not in (int, float)
                        or not math.isfinite(cursor['eligible']) or not isinstance(cursor['id'], str)
                        or len(cursor['id']) != 32 or any(char not in '0123456789abcdef' for char in cursor['id'])):
                    raise ValueError()
                query = query.start_after({'eligible_at': cursor['eligible'], '__name__': self.client.collection(QUEUE).document(cursor['id'])})
            except (ValueError, TypeError, KeyError):
                raise ValueError('NARRATION_GC_CURSOR_INVALID') from None
        rows = list(query.limit(limit).stream(timeout=10, retry=None))
        result = []
        for row in rows:
            data = row.to_dict()
            if (set(data) != {'v', 'project_id', 'publication', 'owner_user_id', 'eligible_at'}
                    or type(data['v']) is not int or data['v'] != 1 or data['publication'] != row.id
                    or type(data['eligible_at']) not in (int, float) or not math.isfinite(data['eligible_at'])):
                raise HostedValidationError('NARRATION_GC_QUEUE_INVALID')
            require_project_id(data['project_id']); require_user_id(data['owner_user_id'])
            result.append((data, json.dumps({'eligible': data['eligible_at'], 'id': row.id}, separators=(',', ':'))))
        return result


def inspect_object(gc, item, lease):
    """Independent WHOLE-bucket census, including objects outside all intents.

    Unknown/mismatched/orphan objects are a reported blocker, never silently
    erased or used as proof of physical completion. Known attempts cannot be
    resurrected once fenced. No body/KMS read needed for this inventory.
    """
    if is_fence(item):
        return 'fences'
    match = _OBJECT.fullmatch(item.name)
    if match is None:
        return 'orphans'
    pid, attempt, ordinal = match.groups()
    def check(tx):
        gc._check(tx, lease)
        index = tx.get((ATTEMPTS, attempt))
        if (index is None or index.data.get('project_id') != pid or index.data.get('ordinal') != int(ordinal)
                or index.data.get('size') != item.size or index.data.get('sha256') != item.sha256
                or index.data.get('fenced') is not False):
            return 'orphans'
        data = tx.get(_intent(pid, index.data['publication']))
        if data is None or data.data.get('owner_user_id') != index.data.get('owner_user_id'):
            return 'orphans'
        state = data.data.get('state')
        if state == 'collecting':
            gc._validate_collection(dict(data.data), pid, index.data['publication'])
            known = any(part['name'] == item.name for part in data.data['objects'])
        else:
            known = state in {'pending', 'published'} and any(part.get('id') == attempt for part in data.data.get('attempts', []))
        return 'tracked' if known else 'orphans'
    return gc.documents.run_transaction(check)


def run_batch(gc, queue, *, seconds=500, monotonic=time.monotonic):
    if type(seconds) is not int or not 1 <= seconds <= 500:
        raise ValueError('NARRATION_GC_RUNTIME_INVALID')
    deadline = monotonic() + seconds
    counts = dict(collected=0, held=0, tracked=0, fences=0, orphans=0, partial=False)
    lease = gc.acquire()
    queue_done = census_done = False
    try:
        state = gc.checkpoint(lease)
        after = state['intent_after']
        while monotonic() < deadline - 45:
            page = queue.page(after=after, now=gc._time())
            if not page:
                gc.checkpoint(lease, intent_after='')
                queue_done = True
                break
            for data, cursor in page:
                if monotonic() >= deadline - 45:
                    counts['partial'] = True
                    return counts
                done = gc.collect(data['project_id'], data['publication'], lease, deadline=deadline)
                current = gc.documents.get(_intent(data['project_id'], data['publication']))
                if current is not None and current.data.get('state') == 'collecting':
                    counts['partial'] = True
                    return counts  # keep cursor BEFORE this unfinished publication
                counts['collected' if done else 'held'] += 1
                after = cursor
                gc.checkpoint(lease, intent_after=after)
        # Separate resumable census of the ENTIRE bucket, not just DB prefixes.
        # Missing parent/intent/index does not make an object invisible to audit.
        after = state['object_after']
        while monotonic() < deadline - 30:
            gc.checkpoint(lease)
            page, following = gc.objects.inventory_page(after=after)
            for item in page:
                if monotonic() >= deadline - 30:
                    counts['partial'] = True
                    return counts  # next run safely repeats this bounded page
                counts[inspect_object(gc, item, lease)] += 1
            gc.checkpoint(lease, object_after=following)
            if not following:
                census_done = True
                break
            after = following
        counts['partial'] = not (queue_done and census_done)
        return counts
    finally:
        gc.release(lease)


def main():
    import argparse
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm', required=True, choices=['COLLECT_DERIVED_NARRATION_ONLY'])
    parser.parse_args()
    if (os.environ.get('BREADMAKER_OPERATOR_MODE') != 'narration_gc'
            or os.environ.get('BREADMAKER_GCP_PROJECT_ID') != 'breadmaker-prod-511902'
            or os.environ.get('BREADMAKER_NARRATION_BUCKET') != 'breadmaker-prod-511902-breadmaker-narration'):
        raise SystemExit('NARRATION_GC_CONFIGURATION_REQUIRED')
    from hosted.firestore.google_driver import GoogleFirestoreDocumentStore
    from hosted.narration.google_objects import GoogleNarrationObjects
    provider = GoogleFirestoreDocumentStore(project='breadmaker-prod-511902', database='(default)')
    objects = GoogleNarrationObjects(project='breadmaker-prod-511902', bucket=os.environ['BREADMAKER_NARRATION_BUCKET'])
    objects.verify_gc_bucket()  # prove no versioning/soft-delete/lifecycle/public origin first
    gc = NarrationGC(NarrationMaintenanceStore(provider), objects,
        retention_allows=lambda tx, pid, owner: retention_allows(tx, pid, owner, now=time.time()))
    try:
        result = run_batch(gc, GoogleGCQueue(provider._client))
    except Exception:
        # Provider errors can include private object names or payloads. Keep the
        # operational failure observable without emitting the exception body.
        print(json.dumps({'ok': False, 'code': 'NARRATION_GC_FAILED_RESUMABLE'}))
        raise SystemExit(2) from None
    print(json.dumps({'ok': result['orphans'] == 0, **result}, sort_keys=True))
    if result['orphans']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
