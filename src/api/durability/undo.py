from __future__ import annotations

"""Short-lived structural Undo HTTP endpoints."""

from fastapi import APIRouter, HTTPException
from backend.data_access import project
from storage import mutations as storage_mutations
from storage import undo as storage_undo
from . import backups

router = APIRouter()

@router.post("/api/undo/begin")
def undo_begin(label:str='Working action',scope:str='full',scope_id:int|None=None):
    """Begin one short-lived structural Undo transaction.

    Session Undo is deliberately separate from persistent History. Full-project mutable
    state remains the safe default; bounded Canvas/Maps operations may opt into a
    validated table scope so small structural edits do not snapshot unrelated domains.
    """
    with storage_mutations.tx() as con: p=project(con)
    try:return storage_undo.begin_session_undo(p['id'],label[:120] or 'Working action',scope,scope_id)
    except ValueError as exc:raise HTTPException(400,detail=str(exc)) from exc

@router.post("/api/undo/finish/{token}")
def undo_finish(token:str):
    try:
        result=storage_undo.finish_session_undo(token); backups._maybe_external_backup('structural_change'); return result
    except (ValueError,RuntimeError) as exc: raise HTTPException(409,detail=str(exc)) from exc

@router.post("/api/undo/apply/{token}")
def undo_apply(token:str,direction:str='undo'):
    try: return storage_undo.apply_session_undo(token,direction)
    except (ValueError,RuntimeError) as exc: raise HTTPException(409,detail=str(exc)) from exc

@router.post("/api/undo/rollback/{token}")
def undo_rollback(token:str):
    try: return storage_undo.rollback_session_undo(token)
    except (ValueError,RuntimeError) as exc: raise HTTPException(409,detail=str(exc)) from exc
