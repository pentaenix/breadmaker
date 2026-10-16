from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_MIB=1024*1024
_GIB=1024*_MIB


def _env_int(name:str, default:int, *, minimum:int, maximum:int) -> int:
    raw=str(os.environ.get(name) or '').strip()
    if not raw:return default
    try:value=int(raw)
    except ValueError:raise RuntimeError(f'{name}_INVALID')
    if not minimum<=value<=maximum:raise RuntimeError(f'{name}_OUT_OF_RANGE')
    return value


@dataclass(frozen=True)
class HostedLimits:
    max_projects_per_owner:int
    max_owner_bytes:int
    max_project_bytes:int
    generic_body_bytes:int
    project_package_bytes:int
    project_package_expanded_bytes:int
    breadreview_bytes:int
    document_bytes:int
    document_expanded_bytes:int
    image_bytes:int
    cuts_bytes:int
    writes_per_minute:int
    heavy_per_minute:int

    @classmethod
    def from_environment(cls) -> 'HostedLimits':
        return cls(
            max_projects_per_owner=_env_int('BREADMAKER_HOSTED_MAX_PROJECTS_PER_OWNER',25,minimum=1,maximum=1000),
            max_owner_bytes=_env_int('BREADMAKER_HOSTED_MAX_OWNER_BYTES',8*_GIB,minimum=128*_MIB,maximum=10*1024*_GIB),
            max_project_bytes=_env_int('BREADMAKER_HOSTED_MAX_PROJECT_BYTES',2*_GIB,minimum=64*_MIB,maximum=1024*_GIB),
            generic_body_bytes=_env_int('BREADMAKER_HOSTED_MAX_REQUEST_BODY_BYTES',8*_MIB,minimum=1*_MIB,maximum=256*_MIB),
            project_package_bytes=_env_int('BREADMAKER_HOSTED_MAX_PROJECT_PACKAGE_BYTES',512*_MIB,minimum=16*_MIB,maximum=16*_GIB),
            project_package_expanded_bytes=_env_int('BREADMAKER_HOSTED_MAX_PROJECT_PACKAGE_EXPANDED_BYTES',2*_GIB,minimum=64*_MIB,maximum=64*_GIB),
            breadreview_bytes=_env_int('BREADMAKER_HOSTED_MAX_BREADREVIEW_BYTES',256*_MIB,minimum=8*_MIB,maximum=4*_GIB),
            document_bytes=_env_int('BREADMAKER_HOSTED_MAX_DOCUMENT_BYTES',128*_MIB,minimum=8*_MIB,maximum=4*_GIB),
            document_expanded_bytes=_env_int('BREADMAKER_HOSTED_MAX_DOCUMENT_EXPANDED_BYTES',512*_MIB,minimum=32*_MIB,maximum=8*_GIB),
            image_bytes=_env_int('BREADMAKER_HOSTED_MAX_IMAGE_BYTES',12*_MIB,minimum=1*_MIB,maximum=25*_MIB),
            cuts_bytes=_env_int('BREADMAKER_HOSTED_MAX_CUTS_BYTES',256*_MIB,minimum=8*_MIB,maximum=8*_GIB),
            writes_per_minute=_env_int('BREADMAKER_HOSTED_WRITES_PER_MINUTE',600,minimum=30,maximum=100000),
            heavy_per_minute=_env_int('BREADMAKER_HOSTED_HEAVY_PER_MINUTE',30,minimum=2,maximum=10000),
        )


class HostedRateLimiter:
    """Instance-local best-effort traffic shedding; NEVER distributed authority.

    Multiple Cloud Run replicas may each have independent limiter buckets. This
    limiter must not be relied on for global anti-abuse, authorization, transaction
    idempotency, or credential security. M10/M12 require upstream traffic controls.

    Identity buckets are hard-bounded as well as time-bounded. A distributed source
    spray must not turn the abuse-control structure itself into an unbounded memory sink.
    When every bucket is still active at capacity, a new identity fails closed.
    """
    def __init__(self, *, max_buckets:int=8192, now=time.monotonic):
        capacity=int(max_buckets)
        if capacity<16 or capacity>65536:raise RuntimeError('HOSTED_RATE_LIMIT_BUCKET_CAPACITY_INVALID')
        self.max_buckets=capacity
        self._now=now
        self._lock=threading.RLock()
        self._events:dict[tuple[str,str],deque[float]]={}
        self._next_prune_at=0.0

    def _prune_locked(self, cutoff:float, *, max_remove:int|None=None) -> None:
        removed=0
        for event_key,event_rows in list(self._events.items()):
            while event_rows and event_rows[0]<=cutoff:event_rows.popleft()
            if event_rows:continue
            self._events.pop(event_key,None);removed+=1
            if max_remove is not None and removed>=max_remove:return

    def _prune_if_due_locked(self, cutoff:float, now:float) -> None:
        # A source-address spray must not make every rejected request scan thousands of
        # buckets. Full cleanup is therefore rate-limited independently of request rate.
        if now<self._next_prune_at:return
        self._prune_locked(cutoff)
        self._next_prune_at=now+5.0

    def allow(self, identity:str, bucket:str, *, limit:int, period_seconds:float=60.0) -> tuple[bool,int]:
        key=(str(identity or 'anonymous'),str(bucket));now=float(self._now());cutoff=now-period_seconds
        with self._lock:
            rows=self._events.get(key)
            if rows is None:
                if len(self._events)>=self.max_buckets:
                    self._prune_if_due_locked(cutoff,now)
                    if len(self._events)>=self.max_buckets:
                        return False,max(1,int(period_seconds+0.999))
                elif len(self._events)>self.max_buckets//2:
                    self._prune_if_due_locked(cutoff,now)
                rows=deque();self._events[key]=rows
            else:
                while rows and rows[0]<=cutoff:rows.popleft()
            if len(rows)>=limit:
                retry=max(1,int(period_seconds-(now-rows[0])+0.999)) if rows else 1
                return False,retry
            rows.append(now)
            return True,0


HEAVY_EXACT=frozenset({
    '/api/projects/import','/api/data/export','/api/export/pdf','/api/import','/api/import/new-project',
    '/api/import/tabs','/api/portable-review/open-temporary','/api/reviews/import-breadreview','/api/cuts/import',
})
HEAVY_SUFFIXES=('/export','/restore-as-project','/apply')


def is_heavy_request(path:str,method:str) -> bool:
    path=str(path or '');method=str(method or '').upper()
    if path in HEAVY_EXACT:return True
    if any(path.endswith(suffix) for suffix in HEAVY_SUFFIXES):return True
    if method in {'POST','PUT','PATCH'} and ('/backups/' in path or '/history/' in path):return True
    return False


def body_limit_for_path(path:str,limits:HostedLimits) -> int:
    path=str(path or '')
    if path=='/api/projects/import':return limits.project_package_bytes+_MIB
    if path in {'/api/portable-review/open-temporary','/api/reviews/import-breadreview'}:return limits.breadreview_bytes+_MIB
    if path in {'/api/import','/api/import/new-project','/api/import/tabs'}:return limits.document_bytes+_MIB
    if path=='/api/assets':return limits.image_bytes+_MIB
    if path=='/api/cuts/import':return limits.cuts_bytes+_MIB
    return limits.generic_body_bytes


def directory_size(root:Path, *, stop_after:int|None=None) -> int:
    """Return regular-file bytes beneath root without following symlinks."""
    total=0;root=Path(root)
    if not root.exists():return 0
    for base,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if not (Path(base)/name).is_symlink()]
        for name in files:
            path=Path(base)/name
            try:
                if path.is_symlink() or not path.is_file():continue
                total+=int(path.stat().st_size)
            except OSError:continue
            if stop_after is not None and total>stop_after:return total
    return total


def owned_projects(rows:Iterable[dict],owner_user_id:str) -> list[dict]:
    uid=str(owner_user_id or '')
    return [dict(row) for row in rows if str(row.get('owner_user_id') or '')==uid]
