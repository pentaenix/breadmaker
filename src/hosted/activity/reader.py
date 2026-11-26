"""Bounded numeric-only reader. No project queries, content reads or decryption."""
from datetime import datetime, timezone, timedelta
from copy import deepcopy
import re
import time

from . import records as r

ROOT_FIELDS = ['status', 'created_at', '_bm_erasure_operation', r.FIELD]
DAY_FIELDS = list(r.blank(1_700_000_000, ('synthetic', 1)))
MAX_ACCOUNTS = 200
MAX_ROWS = 6000
MAX_OBSERVATIONS = 5000


class Unavailable(ValueError):
    def __init__(self): super().__init__('ACTIVITY_UNAVAILABLE')


def iso(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _stream(query, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Unavailable()
    rows = query.stream(timeout=min(3, remaining), retry=None)
    try:
        return list(rows)
    finally:
        close = getattr(rows, 'close', None)
        if close: close()


def _iterator(query, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise Unavailable()
    rows = query.stream(timeout=min(3, remaining), retry=None)
    try:
        yield from rows
    finally:
        close = getattr(rows, 'close', None)
        if close: close()


def _locks(client, uids, deadline):
    if not uids: return set()
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise Unavailable()
    rows = client.get_all([client.document('account_erasure_locks/' + uid) for uid in sorted(uids)],
                          field_paths=['user_id'], timeout=min(3, remaining), retry=None)
    try:
        return {row.reference.path.split('/')[1] for row in rows if row.exists}
    finally:
        close = getattr(rows, 'close', None)
        if close: close()


def _roots(rows):
    if len(rows) > MAX_ACCOUNTS:
        raise Unavailable()
    roots = {}
    for row in rows:
        path = row.reference.path.split('/')
        if len(path) != 2 or path[0] != 'users' or not re.fullmatch(r'usr_[0-9a-f]{32}', path[1]):
            raise Unavailable()
        roots[path[1]] = row.to_dict()
    return roots


def _grant(data):
    from types import SimpleNamespace
    return r.eligible(SimpleNamespace(data=data)) if data else None


def collect(client, *, started_at, policy_reader, deadline, now=None, history_cache=None):
    """Current consent/exclusion fences surround a non-atomic observation read.

One fixed root metadata listing and one fixed numeric collection-group listing.
Overflow, corruption or a changed grant makes the entire observation unknown.
No customer identifiers leave this function.
"""
    now = int(time.time() if now is None else now)
    started = datetime.fromisoformat(started_at.replace('Z', '+00:00')).timestamp()
    if not 0 < started <= now:
        raise Unavailable()
    cutoff = max(r.day_key(now - (r.RETENTION_DAYS - 1) * 86400), r.day_key(started))
    policy = policy_reader.load_policy(deadline=deadline)
    roots = _roots(_stream(client.collection('users').select(ROOT_FIELDS).limit(MAX_ACCOUNTS + 1), deadline))
    excluded = {(entry.hosted_user_id, entry.hosted_created_at) for entry in policy.entries}
    locks = _locks(client, roots, deadline)
    grants = {uid: _grant(data) for uid, data in roots.items() if uid not in locks and _grant(data) is not None}
    included = {uid: grant for uid, grant in grants.items() if (uid, grant[0]) not in excluded}
    from google.cloud.firestore_v1.base_query import FieldFilter
    # Only settled days older than yesterday may be reused. Native transactions
    # cannot remain in flight for a day; today/yesterday are always read again.
    recent = r.day_key(now - 86400)
    cache_key = r.digest([started_at, cutoff, recent, sorted(included.items())])
    reusable = (history_cache is not None and history_cache.get('key') == cache_key
                and history_cache.get('until', 0) > time.monotonic())
    buckets = deepcopy(history_cache['buckets']) if reusable else {}
    closed_tasks = deepcopy(history_cache['tasks']) if reusable else {}
    tasks, online = {}, set()
    observations = 0
    query = client.collection_group(r.DAYS).where(filter=FieldFilter('day', '>=', max(cutoff, recent) if reusable else cutoff))
    rows = _iterator(query.select(DAY_FIELDS).limit(MAX_ROWS + 1), deadline) if included else iter(())
    try:
        for index, row in enumerate(rows):
            if index >= MAX_ROWS: raise Unavailable()
            path = row.reference.path.split('/')
            if len(path) != 4 or path[0] != 'users' or path[2] != r.DAYS:
                raise Unavailable()
            uid = path[1]
            if uid not in included:
                continue
            raw = row.to_dict()
            if (raw.get('account_created_at'), raw.get('consent_revision')) != included[uid]:
                continue
            if (set(raw) != set(DAY_FIELDS) or raw.get('day') != path[3]
                    or not cutoff <= path[3] <= r.day_key(now)):
                raise Unavailable()
            midnight = int(datetime.fromisoformat(path[3]).replace(tzinfo=timezone.utc).timestamp())
            data = r.load(raw, midnight, included[uid])
            if not data['complete']:
                raise Unavailable()
            expiry = raw['expires_at']
            if (not isinstance(expiry, datetime) or expiry.tzinfo is None
                    or expiry.timestamp() != midnight + r.RETENTION_DAYS * 86400):
                raise Unavailable()
            if expiry.timestamp() <= now:
                continue  # TTL purge is asynchronous; expired data is never used.
            if (data['last_seen'] and not midnight <= data['last_seen'] <= min(now, midnight + 86399)
                    or path[3] == r.day_key(now) and any(m > now % 86400 // 60 for m in data['minutes'])):
                raise Unavailable()
            bucket = buckets.setdefault(path[3], dict(day=path[3], foreground_account_minutes=0,
                    participating=set(), counts=dict.fromkeys(r.COUNTERS, 0)))
            bucket['foreground_account_minutes'] += len(data['minutes'])
            if data['minutes']: bucket['participating'].add(uid)
            for key, value in data['counts'].items():
                bucket['counts'][key] += value
            if 0 <= now - data['last_seen'] <= 90:
                online.add(uid)
            observations += len(data['tasks'])
            if observations > MAX_OBSERVATIONS:
                raise Unavailable()
            for key, fact in data['tasks'].items():
                if (not re.fullmatch('[0-9a-f]{64}', key) or type(fact) is not dict
                        or set(fact) != {'state', 'generation', 'observed_ns'}
                        or fact['state'] not in (*r.STATES, 'unknown', None)
                        or type(fact['generation']) is not int or fact['generation'] < 1
                        or type(fact['observed_ns']) is not int
                        or not midnight * 10**9 <= fact['observed_ns'] < (min(now, midnight + 86399) + 1) * 10**9):
                    raise Unavailable()
                target = closed_tasks if path[3] < recent else tasks
                previous = target.get(key)
                order = lambda value: (value['generation'], value['observed_ns'])
                if previous is None or order(fact) > order(previous):
                    target[key] = fact
    finally:
        close = getattr(rows, 'close', None)
        if close: close()
    for key, fact in closed_tasks.items():
        if key not in tasks or (fact['generation'], fact['observed_ns']) > (tasks[key]['generation'], tasks[key]['observed_ns']):
            tasks[key] = fact
    if len(tasks) > MAX_OBSERVATIONS: raise Unavailable()
    # Recheck only metadata, including a concurrent consent withdrawal/closure.
    if roots:
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise Unavailable()
        rechecked = client.get_all([client.document('users/' + uid) for uid in sorted(roots)],
                                  field_paths=ROOT_FIELDS, timeout=min(3, remaining), retry=None)
        try:
            current = {row.reference.path.split('/')[1]: row.to_dict() for row in rechecked if row.exists}
        finally:
            close = getattr(rechecked, 'close', None)
            if close: close()
        if current != roots:
            raise Unavailable()
    if _locks(client, roots, deadline) != locks: raise Unavailable()
    if policy_reader.load_policy(deadline=deadline) != policy or time.monotonic() >= deadline:
        raise Unavailable()
    if history_cache is not None:
        history_cache.update(key=cache_key, until=time.monotonic()+3600,
            buckets=deepcopy({day: value for day, value in buckets.items() if day < recent}), tasks=deepcopy(closed_tasks))
    series = []
    cursor = datetime.fromisoformat(cutoff).replace(tzinfo=timezone.utc)
    today = datetime.fromtimestamp(now, timezone.utc).date()
    while cursor.date() <= today:
        day = cursor.date().isoformat()
        bucket = buckets.get(day, dict(foreground_account_minutes=0, participating=set(), counts=dict.fromkeys(r.COUNTERS, 0)))
        series.append(dict(day=day, foreground_account_minutes=bucket['foreground_account_minutes'],
                           participating_accounts=len(bucket['participating']), **bucket['counts']))
        cursor += timedelta(days=1)
    states = dict.fromkeys((*r.STATES, 'unknown'), 0)
    for task in tasks.values():
        if task['state'] is not None:
            states[task['state']] += 1
    return dict(schema_version=1, source=r.SOURCE, state='available', as_of=iso(now),
                collection_started_at=started_at, retained_from=cutoff, expires_in_seconds=90,
                coverage='current_consent_observed_only', consistency='non_atomic_observation',
                opted_in_accounts=len(grants), included_accounts=len(included),
                excluded_accounts=len(grants) - len(included), concurrent_accounts=len(online),
                series=series, task_states=states)
