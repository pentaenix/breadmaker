"""Default-off, content-free current structural inventory.

Only explicit active project roots are eligible. A bounded projection reads only
status and generation; fixed child COUNT aggregates never read author fields.
Retired, deleted, missing-status and unpublished/staged roots are excluded.
Counts mean extant root records (including archived tasks), not lifetime creation
or activity. No nested nodes, history, packed blocks, text or bodies are read.
Existing projects need no content backfill for these existence counts. Writing
size and activity are unknown until their own authoritative producers exist.

Both root scans must match, and every project generation must be unchanged.
This detects generation-advancing mutation during collection, not low-level
writer bypasses. Runtime must retain the canonical action-journal wrapper. It is
not an atomic fleet
snapshot. No cache, writes, credentials, scheduler or runtime activation.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from hosted.firestore.ids import require_project_id
from .inventory import (_utc, _utc_now, _stamp, _count, _active_status_filter,
                        MAX_COUNT, MAX_AGE_SECONDS, MAX_READ_SECONDS)

SOURCE = 'cloud_product_inventory_v1'
MAX_PROJECTS = 100
MAX_RECORDS_PER_COLLECTION = 10_000
RPC_SECONDS = 5.0
COLLECTIONS = MappingProxyType({
    'current_chapters': 'chapters', 'current_scenes': 'scenes',
    'current_canvases': 'canvases', 'current_tasks': 'tasks',
    'current_story_maps': 'story_maps', 'current_reviews': 'reviews',
})
FIELDS = ('current_cloud_projects', *COLLECTIONS)


@dataclass(frozen=True)
class ProductInventorySnapshot:
    start: datetime
    end: datetime
    values: Mapping[str, int] | None = field(default=None, repr=False)

    def __post_init__(self):
        if _utc(self.end) < _utc(self.start):
            raise ValueError('PRODUCT_INVENTORY_WINDOW_INVALID')
        if self.values is not None:
            if set(self.values) != set(FIELDS):
                raise ValueError('PRODUCT_INVENTORY_FIELDS_INVALID')
            for value in self.values.values():
                _count(value)
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


class GoogleFirestoreProductInventoryProducer:
    """Injected authorized client only. Disabled does not contact dependencies.

    Two bounded root queries, two root COUNTs and six child aggregates per eligible project; at most
    100 projects and 60 seconds. Larger fleets explicitly return unknown rather
    than publishing a partial total. Collection names cannot be supplied by a
    client. No query_collection_group or document body fallback exists.
    """
    def __init__(self, *, client=None, enabled=False, utc_now=_utc_now,
                 monotonic=time.monotonic):
        if type(enabled) is not bool:
            raise ValueError('PRODUCT_INVENTORY_ENABLE_INVALID')
        self.client, self.enabled = client, enabled
        self.now, self.clock = utc_now, monotonic

    def _roots(self, timeout):
        query = (self.client.collection('projects').where(filter=_active_status_filter())
                 .select(['status', 'generation']).limit(MAX_PROJECTS + 1))
        stream = query.stream(retry=None, timeout=timeout())
        roots, times = {}, []
        try:
            for snapshot in stream:
                timeout()
                path = snapshot.reference.path.split('/')
                if len(path) != 2 or path[0] != 'projects' or not snapshot.exists:
                    raise ValueError('PRODUCT_INVENTORY_ROOT_INVALID')
                pid = require_project_id(path[1])
                data = snapshot.to_dict()
                if (set(data) != {'status', 'generation'} or data['status'] != 'active'
                        or type(data['generation']) is not int
                        or not 1 <= data['generation'] <= MAX_COUNT
                        or pid in roots or len(roots) >= MAX_PROJECTS):
                    raise ValueError('PRODUCT_INVENTORY_ROOT_INVALID')
                roots[pid] = data['generation']
                times.append(_utc(snapshot.read_time))
        finally:
            stream.close()
        query = (self.client.collection('projects').where(filter=_active_status_filter())
                 .limit(MAX_PROJECTS + 1))
        total, observed = self._aggregate(query, timeout)
        if total != len(roots):
            raise ValueError('PRODUCT_INVENTORY_ROOT_CHANGED')
        times.append(observed)
        return roots, times

    def _count(self, pid, collection, timeout):
        query = self.client.collection(f'projects/{pid}/{collection}').limit(MAX_RECORDS_PER_COLLECTION + 1)
        return self._aggregate(query, timeout)

    def _aggregate(self, query, timeout):
        stream = query.count(alias='total').stream(retry=None, timeout=timeout())
        try:
            iterator, missing = iter(stream), object()
            row = next(iterator, missing)
            if not isinstance(row, (list, tuple)) or len(row) != 1 or row[0].alias != 'total':
                raise ValueError('PRODUCT_INVENTORY_AGGREGATE_INVALID')
            value = row[0].value
            value = _count(0 if type(value) is float and value == 0.0 else value)
            if value > MAX_RECORDS_PER_COLLECTION:
                raise ValueError('PRODUCT_INVENTORY_RECORD_LIMIT')
            observed = _utc(row[0].read_time)
            if next(iterator, missing) is not missing:
                raise ValueError('PRODUCT_INVENTORY_AGGREGATE_INVALID')
            return value, observed
        finally:
            stream.close()

    def collect(self):
        start = _utc(self.now())
        if not self.enabled or self.client is None:
            return ProductInventorySnapshot(start, start)
        begun, values = self.clock(), None
        def timeout():
            elapsed = self.clock() - begun
            if not 0 <= elapsed < MAX_READ_SECONDS:
                raise ValueError('PRODUCT_INVENTORY_DEADLINE')
            return min(RPC_SECONDS, MAX_READ_SECONDS - elapsed)
        try:
            roots, times = self._roots(timeout)
            totals = dict.fromkeys(FIELDS, 0)
            totals['current_cloud_projects'] = len(roots)
            for pid in roots:
                for metric, collection in COLLECTIONS.items():
                    count, observed = self._count(pid, collection, timeout)
                    totals[metric] = _count(totals[metric] + count)
                    times.append(observed)
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
        return ProductInventorySnapshot(start, end, values)
