from __future__ import annotations

"""Per-project browser editing leases.

Leases protect one project from competing stale browser editors. They are coordination,
not authentication, and intentionally remain process-local.
"""

import threading
import time
from typing import Optional

_CLIENT_LEASE_LOCK=threading.Lock()
_CLIENT_LEASES: dict[str,dict[str,object]]={}
_CLIENT_LEASE_WAITERS: dict[str,dict[str,float]]={}
_CLIENT_LEASE_SECONDS=90.0
# A healthy single editor may renew indefinitely. Once another editor is waiting, the
# current holder gets a bounded continuous tenure so a forgotten/hostile tab cannot
# monopolize a shared project forever. These are coordination limits, not auth TTLs.
_CLIENT_LEASE_MAX_CONTINUOUS_SECONDS=300.0
_CLIENT_LEASE_WAIT_GRACE_SECONDS=5.0

def _prune_client_leases(now_mono:float|None=None) -> None:
    now_mono=time.monotonic() if now_mono is None else now_mono
    expired=[pid for pid,item in _CLIENT_LEASES.items() if now_mono-float(item.get('seen',0))>=_CLIENT_LEASE_SECONDS]
    for pid in expired:
        _CLIENT_LEASES.pop(pid,None);_CLIENT_LEASE_WAITERS.pop(pid,None)

def _claim_client_lease(project_id:str,client_id:str,*,actor_id:str='',actor_role:str='') -> bool:
    pid=str(project_id or '')
    if not pid:return True
    now_mono=time.monotonic();cid=str(client_id or '')
    with _CLIENT_LEASE_LOCK:
        _prune_client_leases(now_mono)
        current=_CLIENT_LEASES.get(pid)
        if not current:
            _CLIENT_LEASES[pid]={'client_id':cid,'actor_id':str(actor_id or ''),'actor_role':str(actor_role or ''),'acquired':now_mono,'seen':now_mono}
            _CLIENT_LEASE_WAITERS.pop(pid,None);return True
        if str(current.get('client_id') or '')==cid:
            waiters=_CLIENT_LEASE_WAITERS.get(pid) or {}
            foreign_waiting=any(waiter!=cid for waiter in waiters)
            held=now_mono-float(current.get('acquired',now_mono))
            if foreign_waiting and held>=_CLIENT_LEASE_MAX_CONTINUOUS_SECONDS:
                # Stop extending the monopolized lease. The next waiting editor can
                # acquire immediately; this request receives the normal 409/reload path.
                _CLIENT_LEASES.pop(pid,None);return False
            current['seen']=now_mono;return True
        waiters=_CLIENT_LEASE_WAITERS.setdefault(pid,{})
        first=waiters.setdefault(cid,now_mono)
        held=now_mono-float(current.get('acquired',now_mono))
        waited=now_mono-float(first)
        if held>=_CLIENT_LEASE_MAX_CONTINUOUS_SECONDS and waited>=_CLIENT_LEASE_WAIT_GRACE_SECONDS:
            _CLIENT_LEASES[pid]={'client_id':cid,'actor_id':str(actor_id or ''),'actor_role':str(actor_role or ''),'acquired':now_mono,'seen':now_mono}
            _CLIENT_LEASE_WAITERS.pop(pid,None);return True
        return False

def _active_foreign_client(project_id:str,client_id:Optional[str]) -> bool:
    pid=str(project_id or '')
    if not pid:return False
    now_mono=time.monotonic()
    with _CLIENT_LEASE_LOCK:
        _prune_client_leases(now_mono)
        current=_CLIENT_LEASES.get(pid)
        return bool(current and str(current.get('client_id') or '')!=str(client_id or ''))

def _release_client_lease(project_id:str,client_id:str):
    pid=str(project_id or '')
    if not pid:return
    with _CLIENT_LEASE_LOCK:
        current=_CLIENT_LEASES.get(pid)
        if current and str(current.get('client_id') or '')==str(client_id or ''):
            _CLIENT_LEASES.pop(pid,None);_CLIENT_LEASE_WAITERS.pop(pid,None)

def clear_project(project_id: str) -> None:
    pid=str(project_id or '')
    with _CLIENT_LEASE_LOCK:
        _CLIENT_LEASES.pop(pid,None)
        _CLIENT_LEASE_WAITERS.pop(pid,None)

def clear_all() -> None:
    with _CLIENT_LEASE_LOCK:
        _CLIENT_LEASES.clear()
        _CLIENT_LEASE_WAITERS.clear()
