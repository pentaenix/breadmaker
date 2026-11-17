"""Finite owner queries for a known set of active project roots.

The name bounds are prefixes, not exact parent filters. Nested or malformed
matching paths invalidate the WHOLE batch; never discard them as irrelevant.
No fallback query, credential construction, content read or write.
"""
import json
from .ids import require_project_id, require_user_id

OWNER_FIELDS = ('user_id', 'role', 'created_at')
OWNER_BATCH_SIZE = 20
MAX_OWNER_BYTES = 8 * 1024 * 1024


def project_ids(values):
    if type(values) not in (list, tuple) or len(values) > 100 or len(set(values)) != len(values):
        raise ValueError('WORD_OWNER_BATCH_SCOPE')
    return sorted(require_project_id(pid) for pid in values)


def owner_query(client, values):
    from google.cloud.firestore_v1.base_query import And, Or, FieldFilter
    pids = project_ids(values)
    if not 0 < len(pids) <= OWNER_BATCH_SIZE:
        raise ValueError('WORD_OWNER_BATCH_SCOPE')
    prefixes = [And([
        FieldFilter('__name__', '>=', client.document('projects/' + pid)),
        FieldFilter('__name__', '<', client.document('projects/' + pid + '\uf8ff')),
    ]) for pid in pids]
    # Exactly one owner per root is required. N+1 is a saturation sentinel,
    # including duplicate/foreign rows; fewer rows cannot hide a missing owner.
    return client.collection_group('members').where(filter=And([
        FieldFilter('role', '==', 'owner'), Or(prefixes),
    ])).select(list(OWNER_FIELDS)).limit(len(pids) + 1)


def validate_owner_rows(values, rows):
    pids = project_ids(values)
    found = {pid: [] for pid in pids}
    size = 0
    for path, data in rows:
        path = tuple(path)
        if (len(path) != 4 or path[0] != 'projects' or path[1] not in found
                or path[2] != 'members' or type(data) is not dict
                or set(data) != set(OWNER_FIELDS) or data.get('role') != 'owner'
                or path[3] != require_user_id(data.get('user_id'))):
            raise ValueError('WORD_OWNER_BATCH_UNVERIFIED')
        size += len(json.dumps(data, separators=(',', ':'), allow_nan=False).encode())
        if size > MAX_OWNER_BYTES:
            raise ValueError('WORD_OWNER_BATCH_BYTES')
        found[path[1]].append((path, data))
        if len(found[path[1]]) != 1:
            raise ValueError('WORD_OWNER_BATCH_UNVERIFIED')
    if any(len(owners) != 1 for owners in found.values()):
        raise ValueError('WORD_OWNER_BATCH_INCOMPLETE')
    return found


def read_owner_batch(client, values, *, transaction, remaining):
    """One projected stream; the caller supplies its existing bounded deadline."""
    pids = project_ids(values)
    stream = owner_query(client, pids).stream(transaction=transaction,
        retry=None, timeout=min(2, remaining()))
    rows = []; size = 0
    try:
        for row in stream:
            remaining()
            if len(rows) >= len(pids) + 1:
                raise ValueError('WORD_OWNER_BATCH_PROVIDER_LIMIT')
            size += len(json.dumps(row.to_dict() or {}, separators=(',', ':'), allow_nan=False).encode())
            if size > MAX_OWNER_BYTES:
                raise ValueError('WORD_OWNER_BATCH_BYTES')
            rows.append(row)
    finally:
        close = getattr(stream, 'close', None)
        if close is not None:
            close()
    remaining()
    validate_owner_rows(pids, [(row.reference.path.split('/'), row.to_dict() or {}) for row in rows])
    return rows
