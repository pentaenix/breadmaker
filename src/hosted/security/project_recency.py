"""Default-off project modification recency, never engagement or writing activity.

Only existing active root status, generation and server-managed updated_at are
projected. No child records, content, identities, daily event records or writes.
Membership, rename and other non-writing changes can update this timestamp.
Historical activity cannot be reconstructed: only each current root's last change
is measured. No runtime composition or expanded data access is enabled here.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from hosted.firestore.ids import require_project_id
from .inventory import (_utc, _utc_now, _stamp, _count, _active_status_filter,
                        MAX_COUNT, MAX_AGE_SECONDS, MAX_READ_SECONDS)

SOURCE = 'cloud_project_recency_v1'
MAX_PROJECTS = 100
RPC_SECONDS = 5.0
FIELDS = ('current_cloud_projects', 'projects_modified_24h', 'projects_modified_7d', 'projects_modified_30d')


@dataclass(frozen=True)
class ProjectRecencySnapshot:
    start: datetime
    end: datetime
    values: Mapping[str, int] | None = field(default=None, repr=False)

    def __post_init__(self):
        if _utc(self.end) < _utc(self.start):
            raise ValueError('PROJECT_RECENCY_WINDOW_INVALID')
        if self.values is not None:
            if set(self.values) != set(FIELDS):
                raise ValueError('PROJECT_RECENCY_FIELDS_INVALID')
            for value in self.values.values():
                _count(value)
            if not (0 <= self.values['projects_modified_24h'] <= self.values['projects_modified_7d']
                    <= self.values['projects_modified_30d'] <= self.values['current_cloud_projects'] <= MAX_PROJECTS):
                raise ValueError('PROJECT_RECENCY_TOTALS_INVALID')
            object.__setattr__(self, 'values', MappingProxyType(dict(self.values)))

    def json_bytes(self, *, now=None):
        now = _utc(_utc_now() if now is None else now)
        known = (self.values is not None and self.end <= now
                 and (now-self.end).total_seconds() <= MAX_AGE_SECONDS
                 and (self.end-self.start).total_seconds() <= MAX_READ_SECONDS)
        return json.dumps({
            'schema_version': 1, 'source': SOURCE, 'as_of': _stamp(self.end),
            'coverage': {'start': _stamp(self.start), 'end': _stamp(self.end),
                         'completeness': 'complete' if known else 'unknown',
                         'consistency': 'non_atomic'},
            'metrics': {key: self.values[key] if known else None for key in FIELDS},
        }, separators=(',', ':'), allow_nan=False).encode('utf-8')


class GoogleFirestoreProjectRecencyProducer:
    """Injected client only; default off. Four bounded root RPCs maximum.

    Two projections and two COUNT timestamp fences. Missing/invalid timestamps,
    cap overflow, generation/population changes and failures mean unknown.
    """
    def __init__(self, *, client=None, enabled=False, utc_now=_utc_now,
                 monotonic=time.monotonic):
        if type(enabled) is not bool:
            raise ValueError('PROJECT_RECENCY_ENABLE_INVALID')
        self.client, self.enabled = client, enabled
        self.now, self.clock = utc_now, monotonic

    def _roots(self, timeout):
        query = (self.client.collection('projects').where(filter=_active_status_filter())
                 .select(['status', 'generation', 'updated_at']).limit(MAX_PROJECTS + 1))
        stream = query.stream(retry=None, timeout=timeout())
        roots, times = {}, []
        try:
            for snapshot in stream:
                timeout()
                path = snapshot.reference.path.split('/')
                if len(path) != 2 or path[0] != 'projects' or not snapshot.exists:
                    raise ValueError('PROJECT_RECENCY_ROOT_INVALID')
                pid = require_project_id(path[1])
                data = snapshot.to_dict()
                if (set(data) != {'status', 'generation', 'updated_at'} or data['status'] != 'active'
                        or type(data['generation']) is not int
                        or not 1 <= data['generation'] <= MAX_COUNT
                        or pid in roots or len(roots) >= MAX_PROJECTS):
                    raise ValueError('PROJECT_RECENCY_ROOT_INVALID')
                stamp = data['updated_at']
                if type(stamp) is not str or len(stamp) > 40:
                    raise ValueError('PROJECT_RECENCY_TIMESTAMP_INVALID')
                updated = _utc(datetime.fromisoformat(stamp.replace('Z', '+00:00')))
                roots[pid] = (data['generation'], updated)
                times.append(_utc(snapshot.read_time))
        finally:
            stream.close()
        query = (self.client.collection('projects').where(filter=_active_status_filter())
                 .limit(MAX_PROJECTS + 1))
        total, observed = self._aggregate(query, timeout)
        if total != len(roots):
            raise ValueError('PROJECT_RECENCY_ROOT_CHANGED')
        times.append(observed)
        return roots, times

    def _aggregate(self, query, timeout):
        stream = query.count(alias='total').stream(retry=None, timeout=timeout())
        try:
            iterator, missing = iter(stream), object()
            row = next(iterator, missing)
            if not isinstance(row, (list, tuple)) or len(row) != 1 or row[0].alias != 'total':
                raise ValueError('PROJECT_RECENCY_AGGREGATE_INVALID')
            value = row[0].value
            value = _count(0 if type(value) is float and value == 0.0 else value)
            if value > MAX_PROJECTS:
                raise ValueError('PROJECT_RECENCY_RECORD_LIMIT')
            observed = _utc(row[0].read_time)
            if next(iterator, missing) is not missing:
                raise ValueError('PROJECT_RECENCY_AGGREGATE_INVALID')
            return value, observed
        finally:
            stream.close()

    def collect(self, *, deadline=None):
        start = _utc(self.now())
        if not self.enabled or self.client is None:
            return ProjectRecencySnapshot(start, start)
        begun, values = self.clock(), None
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ValueError('PROJECT_RECENCY_DEADLINE_INVALID')
        until = min(begun + MAX_READ_SECONDS, deadline) if deadline is not None else begun + MAX_READ_SECONDS
        def timeout():
            current = self.clock()
            if not begun <= current < until:
                raise ValueError('PROJECT_RECENCY_DEADLINE')
            return min(RPC_SECONDS, until - current)
        try:
            roots, times = self._roots(timeout)
            totals = dict.fromkeys(FIELDS, 0)
            totals['current_cloud_projects'] = len(roots)
            # Freeze the window at collection start, not a moving query clock.
            for generation, updated in roots.values():
                age = (start - updated).total_seconds()
                if age < 0:
                    raise ValueError('PROJECT_RECENCY_FUTURE_TIMESTAMP')
                for key, seconds in (('projects_modified_24h', 86400),
                                     ('projects_modified_7d', 604800),
                                     ('projects_modified_30d', 2592000)):
                    totals[key] += int(age <= seconds)
            after, after_times = self._roots(timeout)
            times.extend(after_times)
            timeout()
            end = _utc(self.now())
            if (roots == after and start <= end
                    and (end-start).total_seconds() <= MAX_READ_SECONDS
                    and all(start <= stamp <= end for stamp in times)):
                values = totals
        except Exception:
            # No exception text, identifiers, payloads, retries or last-good data.
            end = _utc(self.now())
        return ProjectRecencySnapshot(start, end, values)
