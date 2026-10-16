from __future__ import annotations

"""Checkpoint preflight and destructive apply HTTP boundary."""

import re

from fastapi import APIRouter, HTTPException

from api.checkpoints.contracts import CheckpointApply
from backend.data_access import one, project
from backend.dependencies import feature_call
from backend.history_runtime import commit
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import undo as storage_undo
from . import capture, lineage, restore

repair_orphaned_bookmark = feature_call('manuscript.anchors', 'repair_orphaned_bookmark')
sync_developmental_canvases = feature_call('canvas.developmental', 'sync_developmental_canvases')

router = APIRouter()

def _checkpoint_apply_core(con,pid:int,row:dict,snap:dict,body:CheckpointApply,report:dict)->dict:
    meta=lineage._checkpoint_meta(con,row)
    if snap['scope']=='book':
        ids=restore._restore_book_snapshot(con,pid,snap,report=report); result={'ok':True,'scope':'book','mode':'replace_book','chapter_ids':ids}
    elif body.add_as_chapter:
        new_id=restore._restore_chapter_snapshot(con,pid,snap,force_new=True,clone_nested=True,report=report)
        if not meta.get('source_exists'): lineage._checkpoint_rebind_chapter_lineage(con,pid,row,new_id)
        result={'ok':True,'scope':'chapter','mode':'add_chapter','chapter_id':new_id}
    else:
        target_id=int(body.target_chapter_id or 0); target=one(con,"SELECT id,title,created_at FROM chapters WHERE id=? AND project_id=?",(target_id,pid)) if target_id else None
        if not target: raise HTTPException(400,"Choose the live Chapter that should receive this Checkpoint")
        same_lineage=bool(meta.get('source_exists') and int(meta.get('current_chapter_id') or 0)==target_id)
        new_id=restore._restore_chapter_snapshot(con,pid,snap,target_chapter_id=target_id,preserve_target_title=not same_lineage,clone_nested=not same_lineage,report=report)
        if same_lineage: lineage._checkpoint_rebind_chapter_lineage(con,pid,row,new_id)
        result={'ok':True,'scope':'chapter','mode':'replace_chapter','chapter_id':new_id}
    repair_orphaned_bookmark(con,pid)
    errors=storage_invariants.project_invariant_errors(con,pid)
    for error in errors:
        if error.startswith('Scene '):
            m=re.match(r'Scene\s+(\d+)',error); scene=one(con,"SELECT title FROM scenes WHERE id=? AND project_id=?",(int(m.group(1)),pid)) if m else None; name=str((scene or {}).get('title') or f"Scene {m.group(1) if m else ''}").strip()
            report.setdefault('hard_conflicts',[]).append(f'Restoring this Checkpoint would make Scene “{name}” disconnected or structurally invalid. Adjust that Scene first.')
        else:
            report.setdefault('hard_conflicts',[]).append(error)
    return result


def _checkpoint_preflight_payload(cid:int,body:CheckpointApply)->dict:
    with storage_mutations.preview_tx() as con:
        p=project(con); pid=p['id']; row=lineage._checkpoint_row(con,pid,cid); snap=capture._decode_checkpoint_snapshot(con,row); meta=lineage._checkpoint_meta(con,row)
        if snap['scope']=='chapter' and not body.add_as_chapter:
            target_id=int(body.target_chapter_id or 0)
            if not target_id or not one(con,"SELECT id FROM chapters WHERE id=? AND project_id=?",(target_id,pid)): raise HTTPException(400,"Choose the live Chapter that should receive this Checkpoint")
        token=storage_undo.project_state_fingerprint(con,pid); report={'losses':[],'renames':[],'notices':[],'hard_conflicts':[]}
        result=_checkpoint_apply_core(con,pid,row,snap,body,report)
        # Normalize duplicate notices created by multi-Chapter book restoration.
        report['notices']=list(dict.fromkeys(report.get('notices') or [])); unique_renames=[]; seen=set()
        for item in report.get('renames') or []:
            key=(item.get('kind'),item.get('from'),item.get('to'))
            if key not in seen: seen.add(key); unique_renames.append(item)
        report['renames']=unique_renames
        report.update({'checkpoint_id':cid,'display_name':meta['display_name'],'preflight_token':token,'requires_confirmation':bool(report['losses'] or report['renames'] or report['notices']),'can_apply':not bool(report['hard_conflicts']),'result_preview':result})
        return report


@router.post("/api/checkpoints/{cid}/preflight")
def preflight_checkpoint(cid:int,body:CheckpointApply):
    return _checkpoint_preflight_payload(cid,body)


@router.post("/api/checkpoints/{cid}/apply")
def apply_checkpoint(cid:int,body:CheckpointApply):
    preflight=_checkpoint_preflight_payload(cid,body)
    if preflight['hard_conflicts']:
        raise HTTPException(409,detail={'code':'CHECKPOINT_HARD_CONFLICT','message':preflight['hard_conflicts'][0],'preflight':preflight})
    if preflight['requires_confirmation'] and not body.confirm_conflicts:
        raise HTTPException(409,detail={'code':'CHECKPOINT_CONFIRM_REQUIRED','message':'This Checkpoint needs your approval before current editorial work is changed.','preflight':preflight})
    if body.preflight_token and str(body.preflight_token)!=str(preflight['preflight_token']):
        raise HTTPException(409,detail={'code':'CHECKPOINT_PREFLIGHT_STALE','message':'The manuscript changed after this Checkpoint was reviewed. Review the updated changes before applying it.','preflight':preflight})
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; current_token=storage_undo.project_state_fingerprint(con,pid)
        if current_token!=preflight['preflight_token']:
            raise HTTPException(409,detail={'code':'CHECKPOINT_PREFLIGHT_STALE','message':'The manuscript changed after this Checkpoint was reviewed. Review it again before applying.'})
        row=lineage._checkpoint_row(con,pid,cid); snap=capture._decode_checkpoint_snapshot(con,row); meta=lineage._checkpoint_meta(con,row); display=meta['display_name']
    history_id=commit(f'Before applying checkpoint “{display}”','safety',False)
    with storage_mutations.tx() as con:
        # History is independent of Current and does not change the preflight fingerprint.
        if storage_undo.project_state_fingerprint(con,pid)!=preflight['preflight_token']:
            raise HTTPException(409,detail={'code':'CHECKPOINT_PREFLIGHT_STALE','message':'The manuscript changed before the Checkpoint could be applied. Review it again.'})
        row=lineage._checkpoint_row(con,pid,cid); snap=capture._decode_checkpoint_snapshot(con,row); report={'losses':[],'renames':[],'notices':[],'hard_conflicts':[]}; result=_checkpoint_apply_core(con,pid,row,snap,body,report)
        if report['hard_conflicts']: raise HTTPException(409,detail={'code':'CHECKPOINT_HARD_CONFLICT','message':report['hard_conflicts'][0]})
        storage_invariants.validate_project_invariants(con,pid); result['history_revision_id']=history_id; result['reconciliation']=report
    sync_developmental_canvases(pid); return result
