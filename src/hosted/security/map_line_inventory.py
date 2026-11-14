"""Default-off current map-line counts; no map titles, line fields or items.

Only active projects and existing map parents contribute. This is non-atomic:
matching projections detect observable generation/parent changes, not every
possible concurrent or out-of-band write. No runtime hook, cache or writes.
"""
from __future__ import annotations
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping
from hosted.firestore.ids import require_opaque_id
from .inventory import _utc, _utc_now, _stamp, _count, MAX_AGE_SECONDS, MAX_READ_SECONDS
from .product_inventory import GoogleFirestoreProductInventoryProducer, MAX_PROJECTS, RPC_SECONDS

SOURCE = 'cloud_map_line_inventory_v1'
MAX_MAPS = 100  # fleet-wide, not per project
FIELDS = ('current_cloud_projects', 'current_story_maps', 'current_map_lines')

@dataclass(frozen=True)
class MapLineInventorySnapshot:
    start: datetime
    end: datetime
    values: Mapping[str, int] | None = field(default=None, repr=False)

    def __post_init__(self):
        if _utc(self.end) < _utc(self.start):
            raise ValueError('MAP_LINE_WINDOW_INVALID')
        if self.values is not None:
            if set(self.values) != set(FIELDS):
                raise ValueError('MAP_LINE_FIELDS_INVALID')
            for value in self.values.values(): _count(value)
            if (self.values['current_cloud_projects'] > MAX_PROJECTS
                    or self.values['current_story_maps'] > MAX_MAPS
                    or self.values['current_map_lines'] > self.values['current_story_maps'] * 10000
                    or not self.values['current_cloud_projects'] and self.values['current_story_maps']):
                raise ValueError('MAP_LINE_TOTALS_INVALID')
            object.__setattr__(self, 'values', MappingProxyType(dict(self.values)))

    def json_bytes(self, *, now=None):
        now = _utc(_utc_now() if now is None else now)
        known = (self.values is not None and self.end <= now
                 and (now-self.end).total_seconds() <= MAX_AGE_SECONDS
                 and (self.end-self.start).total_seconds() <= MAX_READ_SECONDS)
        return json.dumps({'schema_version':1,'source':SOURCE,'as_of':_stamp(self.end),
            'coverage':{'start':_stamp(self.start),'end':_stamp(self.end),
                        'completeness':'complete' if known else 'unknown','consistency':'non_atomic'},
            'metrics':{key:self.values[key] if known else None for key in FIELDS}},
            separators=(',', ':'), allow_nan=False).encode('utf-8')

class GoogleFirestoreMapLineInventoryProducer(GoogleFirestoreProductInventoryProducer):
    """Reuse reviewed active-root and fixed COUNT validators; default remains off.

    Max504RPC:4root+4/project+1/map, <=100projects/100maps fleet-wide.
    Parent select([]) requests names only; it never falls back to full documents.
    """
    def _parents(self, roots, timeout):
        parents, times = {}, []
        for pid in roots:
            path = f'projects/{pid}/story_maps'
            # Shrinking query limit bounds provider work, not merely iteration.
            remaining = MAX_MAPS - len(parents)
            stream = self.client.collection(path).select([]).limit(remaining+1).stream(
                retry=None, timeout=timeout())
            seen = set()
            try:
                for snapshot in stream:
                    timeout()
                    pieces = snapshot.reference.path.split('/')
                    if (len(pieces) != 4 or '/'.join(pieces[:3]) != path
                            or not snapshot.exists or snapshot.to_dict() != {}):
                        raise ValueError('MAP_LINE_PARENT_INVALID')
                    mid = require_opaque_id(pieces[3], label='map_id')
                    if mid in seen or len(parents) >= MAX_MAPS:
                        raise ValueError('MAP_LINE_PARENT_LIMIT')
                    seen.add(mid)
                    parents[(pid, mid)] = True
                    times.append(_utc(snapshot.read_time))
            finally:
                stream.close()
            total, observed = self._aggregate(self.client.collection(path).limit(MAX_MAPS+1), timeout)
            if total != len(seen): raise ValueError('MAP_LINE_PARENT_CHANGED')
            times.append(observed)  # authoritative evidence even for an empty parent query
        return parents, times

    def collect(self, *, deadline=None):
        start = _utc(self.now())
        if not self.enabled or self.client is None:
            return MapLineInventorySnapshot(start,start)
        begun, values = self.clock(), None
        if deadline is not None and (type(deadline) not in (int,float) or not math.isfinite(deadline)):
            raise ValueError('MAP_LINE_DEADLINE_INVALID')
        until = min(begun+MAX_READ_SECONDS,deadline) if deadline is not None else begun+MAX_READ_SECONDS
        def timeout():
            current=self.clock()
            if not begun <= current < until: raise ValueError('MAP_LINE_DEADLINE')
            return min(RPC_SECONDS,until-current)
        try:
            roots,times=self._roots(timeout)
            parents,parent_times=self._parents(roots,timeout);times.extend(parent_times)
            lines=0
            for pid,mid in parents:
                count,observed=self._aggregate(self.client.collection(
                    f'projects/{pid}/story_maps/{mid}/lines').limit(10001),timeout)
                lines=_count(lines+count);times.append(observed)
            after_parents,parent_times=self._parents(roots,timeout);times.extend(parent_times)
            after,after_times=self._roots(timeout);times.extend(after_times)
            timeout();end=_utc(self.now())
            if (roots==after and parents==after_parents and start<=end
                    and (end-start).total_seconds()<=MAX_READ_SECONDS
                    and all(start<=stamp<=end for stamp in times)):
                values=dict(zip(FIELDS,(len(roots),len(parents),lines)))
        except Exception:
            end=_utc(self.now())
        return MapLineInventorySnapshot(start,end,values)
