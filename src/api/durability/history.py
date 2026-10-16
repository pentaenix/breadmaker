from __future__ import annotations

"""Immutable local/remote History HTTP endpoints."""

import sqlite3
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend import diagnostics
from api.shared_contracts import BaseModel
from api.checkpoints.contracts import CheckpointCreate
from api.durability.contracts import HistoryRestoreRequest
from backend.data_access import one, project
from backend.dependencies import feature_call
from backend.environment import runtime
from runtime.native import require_native_history
from backend.history_runtime import queue_remote_history as _queue_remote_history
from backend.request_context import _bound_project, _bound_project_id, _require_hosted_project_management
from revision_store.local_store import RevisionStoreCorruption, RevisionStoreError
from revision_store.restore import HistoryRestoreCorruption, HistoryRestoreError, HistoryRestoreStale, restore_local_revision
from security import content_security
from storage import coordination as storage_coordination
from storage import history as storage_history
from storage import mutations as storage_mutations
from storage import values as storage_values
from workspace.models import WorkspaceError

from . import backups

_capture_book_checkpoint = feature_call('checkpoints.capture', '_capture_book_checkpoint')
_capture_chapter_checkpoint = feature_call('checkpoints.capture', '_capture_chapter_checkpoint')
_checkpoint_compare_rows = feature_call('checkpoints.comparison', '_checkpoint_compare_rows')
_checkpoint_meta = feature_call('checkpoints.lineage', '_checkpoint_meta')
_checkpoint_row = feature_call('checkpoints.lineage', '_checkpoint_row')
_insert_checkpoint = feature_call('checkpoints.capture', '_insert_checkpoint')

router = APIRouter()

def _history_v2_label(revision:dict) -> str:
    summary=revision.get('summary') if isinstance(revision.get('summary'),dict) else {}
    return str(summary.get('label') or 'Automatic History')[:180]

def _history_v2_ui_rows() -> list[dict]:
    rows=runtime.history.list_revisions()
    # Word deltas are presentation metadata only. Generation remains the authority.
    previous_word_count=None
    deltas={}
    for rev in reversed(rows):
        summary=rev.get('summary') if isinstance(rev.get('summary'),dict) else {}
        raw=summary.get('word_count')
        word_count=int(raw) if isinstance(raw,int) and raw>=0 else None
        delta=(word_count-previous_word_count) if word_count is not None and previous_word_count is not None else None
        deltas[str(rev['revision_id'])]=(word_count,delta)
        if word_count is not None: previous_word_count=word_count
    out=[]
    for rev in rows:
        summary=rev.get('summary') if isinstance(rev.get('summary'),dict) else {}
        word_count,word_delta=deltas.get(str(rev['revision_id']),(None,None))
        out.append({
            'id':str(rev['revision_id']),
            'revision_id':str(rev['revision_id']),
            'generation_start':int(rev['generation_start']),
            'generation_end':int(rev['generation_end']),
            'created_at':str(rev.get('created_at') or ''),
            'label':_history_v2_label(rev),
            'cause':str(rev.get('creation_reason') or 'automatic'),
            'is_safety':str(rev.get('creation_reason') or '')=='pre_restore_safety',
            'word_count':word_count,
            'word_delta':word_delta,
            'summary':summary,
        })
    return out

def _history_current_summary() -> dict:
    return storage_history.history_current_summary()

def _history_v2_verified_connection(revision_id:str):
    # Capture workspace identity separately from SQLite's local projects.id. Every
    # portable project DB commonly uses row id=1, so only manifest UUID + root can
    # distinguish books across a concurrent project switch.
    with storage_coordination.project_switch_lock():
        try:
            workspace_project_id,project_root=storage_mutations.configured_project_identity()
            store=require_native_history(runtime).revision_store_for(Path(project_root))
        except (RevisionStoreError,RuntimeError) as exc:
            raise HTTPException(409,detail='History project is not configured safely') from exc
    try:
        verified=store.verify_revision(str(revision_id))
    except RevisionStoreCorruption as exc:
        raise HTTPException(409,detail={'code':'HISTORY_REVISION_CORRUPT','message':'This History state failed integrity verification. Current was not changed.'}) from exc
    except RevisionStoreError as exc:
        raise HTTPException(404,detail='History revision not found') from exc
    with storage_mutations.tx(operation='history_identity_read',track_generation=False) as live:
        p=project(live)
        current_workspace_id,current_root=storage_mutations.configured_project_identity()
    if (
        not p
        or int(verified['revision']['project_id'])!=int(p['id'])
        or current_workspace_id!=workspace_project_id
        or current_root!=project_root
    ):
        raise HTTPException(409,detail='The active project changed while History was being prepared')
    verified=dict(verified)
    verified['workspace_project_id']=workspace_project_id
    verified['project_root']=project_root
    database=Path(verified['root'])/'project.sqlite3'
    con=sqlite3.connect(f'file:{database.as_posix()}?mode=ro&immutable=1',uri=True)
    con.row_factory=sqlite3.Row
    return verified,con

def _history_v2_snapshot_asset_urls(snapshot:dict,revision_id:str) -> dict:
    # History owns immutable image bytes that may no longer exist in Current. Keep
    # comparison identity on the original /api/assets path, but render through a
    # revision-scoped read-only endpoint backed by the verified snapshot object.
    rid=quote(str(revision_id),safe='')
    for block in snapshot.get('blocks',[]) or []:
        if str(block.get('block_type') or '')!='image': continue
        api_path=str(block.get('image_path') or '').strip()
        if not content_security.is_safe_project_asset_path(api_path): continue
        relative=api_path.removeprefix('/api/assets/')
        if not relative: continue
        block['_render_image_path']=f'/api/history/v2/revisions/{rid}/assets/{quote(relative,safe="/")}'
    return snapshot

def _history_v2_snapshot(revision_id:str,scope:str,chapter_id:Optional[int]) -> tuple[dict,dict]:
    verified,con=_history_v2_verified_connection(revision_id)
    try:
        pid=int(verified['revision']['project_id'])
        if scope=='book':
            snapshot=_capture_book_checkpoint(con,pid)
        else:
            if chapter_id is None: raise HTTPException(400,'Choose a Chapter to compare')
            exists=one(con,'SELECT id FROM chapters WHERE id=? AND project_id=?',(chapter_id,pid))
            snapshot=_capture_chapter_checkpoint(con,pid,chapter_id) if exists else {
                'version':1,'scope':'chapter','captured_at':storage_values.now(),'chapter':None,'blocks':[],
                'annotations':[],'comment_threads':[],'comment_messages':[],'scenes':[],
                'include_scenes':True,'include_markup':True,'include_notes':True,
            }
        return verified,snapshot
    finally:
        con.close()

@router.get('/api/history/v2/revisions/{revision_id}/assets/{asset_path:path}')
def history_v2_revision_asset(revision_id:str,asset_path:str):
    verified,con=_history_v2_verified_connection(revision_id)
    con.close()
    root=Path(verified['root']).resolve(); assets=(root/'assets').resolve(); target=(assets/asset_path).resolve()
    try: target.relative_to(assets)
    except ValueError: raise HTTPException(400,detail='HISTORY_ASSET_PATH_INVALID')
    if not target.is_file(): raise HTTPException(404,detail='HISTORY_ASSET_NOT_FOUND')
    return FileResponse(target,headers={'Cache-Control':'private, max-age=31536000, immutable'})

@router.get('/api/history/v2')
def history_v2():
    with storage_mutations.tx(operation='history_v2_list',track_generation=False) as con:
        p=project(con)
        generation=storage_mutations.project_generation(con,p['id']) if p else 0
    return {'revisions':_history_v2_ui_rows(),'current_generation':generation}

@router.get('/api/history/v2/revisions/{revision_id}/compare-current')
def history_v2_compare_current(revision_id:str,scope:str='book',chapter_id:Optional[int]=None):
    if scope not in {'chapter','book'}: raise HTTPException(400,'History comparison scope must be chapter or book')
    verified,historical=_history_v2_snapshot(revision_id,scope,chapter_id)
    with storage_mutations.tx(operation='history_v2_compare_current',track_generation=False) as con:
        p=project(con)
        current_workspace_id,current_root=storage_mutations.configured_project_identity()
        if (not p or int(p['id'])!=int(verified['revision']['project_id'])
            or current_workspace_id!=str(verified.get('workspace_project_id') or '')
            or current_root!=str(verified.get('project_root') or '')):
            raise HTTPException(409,'The active project changed while History comparison was being prepared')
        if scope=='chapter':
            if chapter_id is None or not one(con,'SELECT id FROM chapters WHERE id=? AND project_id=?',(chapter_id,p['id'])):
                raise HTTPException(409,'Current Chapter is no longer available')
            current=_capture_chapter_checkpoint(con,p['id'],chapter_id)
        else:
            current=_capture_book_checkpoint(con,p['id'])
        current_generation=storage_mutations.project_generation(con,p['id'])
    _history_v2_snapshot_asset_urls(historical,revision_id)
    result=_checkpoint_compare_rows(historical,current)
    rev=verified['revision']
    result.update({
        'revision':{'id':str(rev['revision_id']),'label':_history_v2_label(rev),'created_at':rev.get('created_at'),'cause':rev.get('creation_reason') or 'automatic','generation_end':int(rev['generation_end'])},
        'scope':scope,'current_generation':current_generation,
    })
    return result

@router.post('/api/history/v2/revisions/{revision_id}/checkpoint')
def history_v2_save_as_checkpoint(revision_id:str,body:CheckpointCreate):
    scope=(body.scope or '').strip().lower()
    if scope not in {'chapter','book'}: raise HTTPException(400,'Checkpoint scope must be chapter or book')
    if scope=='chapter' and body.chapter_id is None: raise HTTPException(400,'Choose a chapter for this checkpoint')
    verified,historical=_history_v2_snapshot(revision_id,scope,body.chapter_id)
    if scope=='chapter' and not historical.get('chapter'):
        raise HTTPException(409,'That Chapter did not exist in the selected History state')
    with storage_mutations.tx(operation='history_v2_save_checkpoint') as con:
        p=project(con)
        current_workspace_id,current_root=storage_mutations.configured_project_identity()
        if (not p or int(p['id'])!=int(verified['revision']['project_id'])
            or current_workspace_id!=str(verified.get('workspace_project_id') or '')
            or current_root!=str(verified.get('project_root') or '')):
            raise HTTPException(409,'The active project changed while this History Checkpoint was being prepared')
        default_name=_history_v2_label(verified['revision'])
        cid=_insert_checkpoint(con,p['id'],historical,body.name or default_name,body.description)
        result=_checkpoint_meta(con,_checkpoint_row(con,p['id'],cid))
    return result

@router.post('/api/history/v2/revisions/{revision_id}/restore')
def history_v2_restore(revision_id:str,body:Optional[HistoryRestoreRequest]=None):
    _require_hosted_project_management()
    try:
        # Bind the complete restore preparation to one active project identity.
        # Slow materialization/verification then runs without holding the switch lock,
        # while the final canonical CAS still rejects a project switch or newer edit.
        active=_bound_project()
        if not active:raise HistoryRestoreError('HISTORY_RESTORE_PROJECT_NOT_CONFIGURED')
        remote_history_project_id=str(active['id'])
        project_root=Path(str(active['path'])).resolve()
        token=storage_mutations.capture_mutation_token('history_restore')
        listed=next((row for row in require_native_history(runtime).revision_store_for(project_root).list_revisions() if str(row.get('revision_id'))==str(revision_id)),None)
        label=_history_v2_label(listed or {})
        result=restore_local_revision(
            revision_id,
            expected_generation=body.expected_generation if body else None,
            verified_backup=lambda: backups.verified_full_backup('before_history_restore',project_root=project_root),
            label=label,
            project_root=project_root,
            mutation_token=token,
        )
    except HistoryRestoreStale as exc:
        raise HTTPException(409,detail={'code':exc.code,'message':'The manuscript changed while this History restore was being prepared. Nothing was replaced; review the latest Current state and try again.'}) from exc
    except HistoryRestoreCorruption as exc:
        raise HTTPException(409,detail={'code':exc.code,'message':'This History state could not be verified safely. Current was not changed.'}) from exc
    except HistoryRestoreError as exc:
        raise HTTPException(500,detail={'code':exc.code,'message':'Breadmaker could not complete the History restore safely. Current was not changed.'}) from exc
    payload=result.as_dict()
    if result.history_landmark_error:
        diagnostics.log_event('LOCAL_HISTORY_RESTORE_002','History restore committed safely, but its post-restore History landmark could not be published.',level='WARNING',context={'revision_id':revision_id,'generation':result.current_generation},error=RuntimeError(result.history_landmark_error),data_safe=True)
    try:_queue_remote_history(remote_history_project_id,None)
    except Exception:pass
    backups._maybe_external_backup('history_restore')
    return payload

@router.post('/api/autocommit')
def auto_commit(label:str='Automatic History',source:str='continuous'):
    local_revision=None
    try:
        local_revision=storage_history.capture_local_revision(label=label[:180] or 'Automatic History',creation_reason=source[:80] or 'continuous',summary=_history_current_summary())
    except Exception as exc:
        diagnostics.log_event('LOCAL_HISTORY_001','Local Revision History capture failed without changing Current or Recovery Backups.',level='WARNING',error=exc,data_safe=True)
    if local_revision:
        try:_queue_remote_history(str(local_revision.get('workspace_project_id') or ''),str(local_revision.get('revision_id') or '') or None)
        except Exception:pass
    backups._maybe_external_backup('automatic_history')
    return {'id':str(local_revision.get('revision_id') or '') if local_revision else None,'local_revision_id':str(local_revision.get('revision_id') or '') if local_revision else None}

@router.post('/api/history/sync')
def history_remote_sync():
    _require_hosted_project_management()
    project_id=_bound_project_id()
    if not project_id:raise HTTPException(409,'No project context')
    return runtime.history.sync_remote(project_id)

class HistoryRepairRequest(BaseModel):
    destination_id:str
    revision_id:Optional[str]=None

class HistoryRetentionUpdate(BaseModel):
    value:str

@router.get('/api/history/retention')
def history_retention_get():
    _require_hosted_project_management()
    try:return runtime.history.retention_settings()
    except WorkspaceError as exc:raise HTTPException(409,str(exc)) from exc

@router.put('/api/history/retention')
def history_retention_put(body:HistoryRetentionUpdate):
    _require_hosted_project_management()
    try:
        result=runtime.history.update_retention(body.value)
        active_id=_bound_project_id()
        if active_id:_queue_remote_history(active_id,None)
        return result
    except WorkspaceError as exc:raise HTTPException(409,str(exc)) from exc

@router.post('/api/history/gc')
def history_gc_run():
    _require_hosted_project_management()
    project_id=_bound_project_id()
    if not project_id:raise HTTPException(409,'No project context')
    # Explicit maintenance barrier for diagnostics/support. Normal GC stays in the
    # background History worker and never blocks Current commits.
    return runtime.history.run_maintenance(project_id)

@router.post('/api/history/repair-local')
def history_remote_repair(body:HistoryRepairRequest):
    _require_hosted_project_management()
    project_id=_bound_project_id()
    if not project_id:raise HTTPException(409,'No project context')
    try:return runtime.history.repair_local_from_destination(body.destination_id,project_id=project_id,revision_id=body.revision_id)
    except WorkspaceError as exc:raise HTTPException(409,str(exc)) from exc
