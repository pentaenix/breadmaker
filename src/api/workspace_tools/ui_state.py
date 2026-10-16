from __future__ import annotations
import json
from fastapi import APIRouter, HTTPException
from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import values as storage_values
from backend.data_access import project
from backend.environment import runtime
from api.workspace_tools.contracts import UIStateIn

router=APIRouter()

def _ui_state_storage_key(key:str) -> str:
    value=str(key or '')
    if len(value)>120:raise HTTPException(400)
    if runtime.sessions.requires_authentication:
        current=runtime.context.current()
        if current is None or not current.actor.member_id:raise HTTPException(409,detail='PROJECT_CONTEXT_REQUIRED')
        # UI preferences belong to the collaborator, not to every member of the project.
        # member_id is project-scoped and already safe to persist in portable Review
        # history; never embed the installation/account usr_ id into project content.
        return f'member:{current.actor.member_id}:{value}'
    return value

@router.get("/api/ui-state")
def get_ui_state_batch(keys:str=''):
    """Read a bounded set of collaborator-scoped UI preferences in one transaction.

    Startup previously opened one SQLite transaction and one HTTP request per preference,
    which made reload latency scale with the number of workspace surfaces.  The batch
    endpoint preserves the same member/project scoping as the single-key API while
    allowing boot to hydrate navigation state atomically.
    """
    requested=[]
    for raw in str(keys or '').split(','):
        key=raw.strip()
        if key and key not in requested:requested.append(key)
    if len(requested)>64:raise HTTPException(400,detail='UI_STATE_002: Too many UI state keys requested.')
    if not requested:return {"values":{}}
    storage_keys=[_ui_state_storage_key(key) for key in requested]
    marks=','.join('?' for _ in storage_keys)
    with storage_database.read_tx() as con:
        p=project(con)
        rows=con.execute(f"SELECT key,value_json FROM ui_state WHERE project_id=? AND key IN ({marks})",(p['id'],*storage_keys)).fetchall()
    by_storage={str(row['key']):row['value_json'] for row in rows}
    values={}
    for key,storage_key in zip(requested,storage_keys):
        raw=by_storage.get(storage_key)
        if raw is None:values[key]=None;continue
        try:values[key]=json.loads(raw)
        except Exception:values[key]=None
    return {"values":values}

@router.get("/api/ui-state/{key}")
def get_ui_state(key:str):
    storage_key=_ui_state_storage_key(key)
    with storage_database.read_tx() as con:
        p=project(con); row=con.execute("SELECT value_json FROM ui_state WHERE project_id=? AND key=?",(p['id'],storage_key)).fetchone()
        if not row: return {"value":None}
        try: value=json.loads(row[0])
        except Exception: value=None
        return {"value":value}

@router.put("/api/ui-state/{key}")
def put_ui_state(key:str, body:UIStateIn):
    storage_key=_ui_state_storage_key(key)
    raw=json.dumps(body.value,ensure_ascii=False,separators=(',',':'))
    if len(raw)>100000: raise HTTPException(413,detail="UI_STATE_001: UI state is too large.")
    with storage_mutations.tx() as con:
        p=project(con); con.execute("INSERT INTO ui_state(project_id,key,value_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(project_id,key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",(p['id'],storage_key,raw,storage_values.now()))
    return {"ok":True}
