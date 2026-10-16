from __future__ import annotations
"""Review lifecycle, scope, state transitions, and aggregate payloads."""
from fastapi import APIRouter, HTTPException
import json
from api.reviews.contracts import ReviewCreate, ReviewReorder, ReviewUpdate
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain as review_domain
from . import markers as review_markers
from . import snapshots as review_snapshots
router=APIRouter()

def _review_payload(con,project_id:int,row) -> dict:
    out=dict(row);rid=str(out['id']);out['scope_ids']=review_domain._review_scope_ids(con,rid,str(out.get('scope_type') or 'manuscript'))
    out['participants']=review_domain._review_participants(con,rid)
    out['questionnaires']=review_domain._review_questionnaires(con,rid)
    out['markers']=review_domain._review_markers(con,rid);out['marker_ids']=[int(row['marker_id']) for row in out['markers']]
    out['snapshot']=review_snapshots._review_snapshot_summary(con,rid)
    out['rounds']=review_snapshots._review_rounds(con,rid)
    out['history']=allrows(con,"SELECT id,event_type,entity_type,entity_id,payload_json,created_at FROM review_events WHERE review_id=? AND event_type IN ('REVIEW_ROUND_STARTED','REVIEW_PAUSED','REVIEW_RESUMED','REVIEW_ROUND_CLOSED','REVIEW_ROUND_REOPENED','REVIEW_ARCHIVED','REVIEW_UNARCHIVED','REVIEW_CHECKPOINT_UPDATED') ORDER BY created_at DESC,id DESC LIMIT 100",(rid,))
    for event in out['history']:
        try:event['payload']=json.loads(event.pop('payload_json') or '{}')
        except:event['payload']={}
    for field in review_domain._REVIEW_PERMISSION_FIELDS: out[field]=bool(out.get(field))
    return out

def _review_rows(con,project_id:int) -> list[dict]:
    return [_review_payload(con,project_id,row) for row in allrows(con,"SELECT * FROM reviews WHERE project_id=? ORDER BY position,id",(project_id,))]

def _normalize_review_scope(con,project_id:int,scope_type:str,scope_ids:list[int]|None) -> tuple[str,list[int]]:
    kind=str(scope_type or '').strip().lower()
    if kind not in review_domain._REVIEW_SCOPE_TYPES:
        raise HTTPException(400,detail="REVIEW_SCOPE_001: Scope must be the entire manuscript, selected Chapters, or selected Scenes.")
    ids=[]
    for raw in scope_ids or []:
        try: value=int(raw)
        except (TypeError,ValueError): raise HTTPException(400,detail="REVIEW_SCOPE_003: Review scope contains an unavailable manuscript item.")
        if value>0 and value not in ids: ids.append(value)
    if kind=='manuscript':
        if ids: raise HTTPException(400,detail="REVIEW_SCOPE_001: Entire-manuscript scope cannot contain selected items.")
        return kind,[]
    if not ids:
        raise HTTPException(400,detail="REVIEW_SCOPE_002: Select at least one Chapter or Scene for this Review scope.")
    placeholders=','.join('?' for _ in ids)
    if kind=='chapters':
        rows=allrows(con,f"SELECT id FROM chapters WHERE project_id=? AND is_implicit=0 AND id IN ({placeholders})",(project_id,*ids))
    else:
        rows=allrows(con,f"SELECT id FROM scenes WHERE project_id=? AND id IN ({placeholders})",(project_id,*ids))
    found={int(row['id']) for row in rows}
    if found!=set(ids): raise HTTPException(400,detail="REVIEW_SCOPE_003: Review scope contains an unavailable manuscript item.")
    return kind,ids

def _replace_review_scope(con,rid:str,scope_type:str,scope_ids:list[int],created_at:str|None=None) -> None:
    con.execute("DELETE FROM review_scope_chapters WHERE review_id=?",(rid,))
    con.execute("DELETE FROM review_scope_scenes WHERE review_id=?",(rid,))
    t=created_at or storage_values.now()
    if scope_type=='chapters':
        con.executemany("INSERT INTO review_scope_chapters(review_id,chapter_id,created_at) VALUES(?,?,?)",[(rid,item,t) for item in scope_ids])
    elif scope_type=='scenes':
        con.executemany("INSERT INTO review_scope_scenes(review_id,scene_id,created_at) VALUES(?,?,?)",[(rid,item,t) for item in scope_ids])

@router.get("/api/reviews")
def list_reviews():
    with storage_mutations.tx() as con:
        p=project(con)
        return _review_rows(con,p['id'])

@router.post("/api/reviews")
def create_review(body:ReviewCreate):
    name=review_domain._review_name(body.name)
    with storage_mutations.tx(operation='review-create') as con:
        p=project(con); scope_type,scope_ids=_normalize_review_scope(con,p['id'],body.scope_type,body.scope_ids); marker_ids=review_markers._normalize_review_marker_ids(con,p['id'],body.marker_ids); t=storage_values.now(); rid=storage_values.uid('review_')
        permissions=review_domain._normalize_review_permissions(body.permission_read,body.permission_comment,body.permission_suggest,body.permission_feedback)
        visibility=str(body.reviewer_visibility or 'private').strip().lower()
        if visibility not in {'private','shared'}: raise HTTPException(400,detail="REVIEW_VISIBILITY_001: Reviewer visibility must be private or shared.")
        description=review_domain._review_description(body.description);position=review_domain._review_next_position(con,p['id'])
        con.execute("INSERT INTO reviews(id,project_id,name,description,position,status,reviewer_visibility,scope_type,permission_read,permission_comment,permission_suggest,permission_feedback,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(rid,p['id'],name,description,position,'draft',visibility,scope_type,*permissions.values(),t,t))
        _replace_review_scope(con,rid,scope_type,scope_ids,t)
        review_markers._replace_review_markers(con,rid,marker_ids,t)
        return _review_payload(con,p['id'],one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,p['id'])))

@router.post('/api/reviews/reorder')
def reorder_reviews(body:ReviewReorder):
    ids=[str(item) for item in body.ids]
    if not ids or len(ids)!=len(set(ids)): raise HTTPException(400,detail='REVIEW_REORDER_001: Invalid Review order.')
    with storage_mutations.tx(operation='review-reorder') as con:
        p=project(con);existing=[str(row['id']) for row in allrows(con,"SELECT id FROM reviews WHERE project_id=? ORDER BY position,id",(p['id'],))]
        if len(ids)!=len(existing) or set(ids)!=set(existing): raise HTTPException(400,detail='REVIEW_REORDER_002: Review order must include every Review in the project.')
        t=storage_values.now()
        for position,rid in enumerate(ids): con.execute("UPDATE reviews SET position=?,updated_at=? WHERE id=? AND project_id=?",(position,t,rid,p['id']))
        return {'ok':True,'ids':ids}

@router.get("/api/reviews/{rid}")
def get_review(rid:str):
    with storage_mutations.tx() as con:
        p=project(con); row=one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,p['id']))
        if not row: raise HTTPException(404,detail="REVIEW_002: Review not found.")
        return _review_payload(con,p['id'],row)

@router.patch("/api/reviews/{rid}")
def update_review(rid:str,body:ReviewUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'name' in vals: vals['name']=review_domain._review_name(vals.get('name') or '')
    if 'description' in vals: vals['description']=review_domain._review_description(vals.get('description') or '')
    requested_scope_type=vals.pop('scope_type',None) if 'scope_type' in vals else None
    requested_scope_ids=vals.pop('scope_ids',None) if 'scope_ids' in vals else None
    requested_marker_ids=vals.pop('marker_ids',None) if 'marker_ids' in vals else None
    with storage_mutations.tx(operation='review-update') as con:
        p=project(con); current=one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,p['id']))
        if not current: raise HTTPException(404,detail="REVIEW_002: Review not found.")
        if 'reviewer_visibility' in vals:
            vals['reviewer_visibility']=str(vals.get('reviewer_visibility') or '').strip().lower()
            if vals['reviewer_visibility'] not in {'private','shared'}: raise HTTPException(400,detail="REVIEW_VISIBILITY_001: Reviewer visibility must be private or shared.")
        if any(field in vals for field in review_domain._REVIEW_PERMISSION_FIELDS):
            merged={field:vals.get(field,current.get(field)) for field in review_domain._REVIEW_PERMISSION_FIELDS}
            vals.update(review_domain._normalize_review_permissions(**merged))
        scope_changed=requested_scope_type is not None or requested_scope_ids is not None
        if scope_changed:
            scope_type=requested_scope_type or str(current.get('scope_type') or 'manuscript')
            if requested_scope_ids is None:
                requested_scope_ids=review_domain._review_scope_ids(con,rid,str(current.get('scope_type') or 'manuscript')) if scope_type==str(current.get('scope_type') or 'manuscript') else []
            scope_type,scope_ids=_normalize_review_scope(con,p['id'],scope_type,requested_scope_ids)
            vals['scope_type']=scope_type
        marker_ids=review_markers._normalize_review_marker_ids(con,p['id'],requested_marker_ids) if requested_marker_ids is not None else None
        if vals:
            sets=', '.join(f"{key}=?" for key in vals)+",updated_at=?"
            con.execute(f"UPDATE reviews SET {sets} WHERE id=? AND project_id=?",(*vals.values(),storage_values.now(),rid,p['id']))
        if scope_changed: _replace_review_scope(con,rid,vals['scope_type'],scope_ids)
        if marker_ids is not None:
            previous_marker_ids=[int(row['marker_id']) for row in review_domain._review_markers(con,rid)]
            for removed_id in [mid for mid in previous_marker_ids if mid not in marker_ids]:review_markers._remove_review_marker_from_active_snapshot(con,rid,removed_id)
            review_markers._replace_review_markers(con,rid,marker_ids)
            for added_id in [mid for mid in marker_ids if mid not in previous_marker_ids]:review_markers._sync_review_marker_into_active_snapshot(con,rid,added_id)
        row=one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,p['id']))
        return _review_payload(con,p['id'],row)

@router.post('/api/reviews/{rid}/duplicate')
def duplicate_review(rid:str):
    with storage_mutations.tx(operation='review-duplicate') as con:
        p=project(con);source=review_domain._review_for_current_project(con,p['id'],rid);base=(str(source.get('name') or 'Review')+' Copy')[:120];name=base;n=2
        while one(con,"SELECT id FROM reviews WHERE project_id=? AND name=? COLLATE NOCASE",(p['id'],name)):
            suffix=f' {n}';name=(base[:120-len(suffix)]+suffix);n+=1
        t=storage_values.now();new_id=storage_values.uid('review_');position=review_domain._review_next_position(con,p['id'])
        con.execute("INSERT INTO reviews(id,project_id,name,description,position,status,reviewer_visibility,scope_type,permission_read,permission_comment,permission_suggest,permission_feedback,created_at,updated_at) VALUES(?,?,?,?,?,'draft',?,?,?,?,?,?,?,?)",(new_id,p['id'],name,str(source.get('description') or ''),position,str(source.get('reviewer_visibility') or 'private'),str(source.get('scope_type') or 'manuscript'),int(source.get('permission_read') or 0),int(source.get('permission_comment') or 0),int(source.get('permission_suggest') or 0),int(source.get('permission_feedback') or 0),t,t))
        for chapter_id in review_domain._review_scope_ids(con,rid,'chapters'):
            con.execute("INSERT INTO review_scope_chapters(review_id,chapter_id,created_at) VALUES(?,?,?)",(new_id,chapter_id,t))
        for scene_id in review_domain._review_scope_ids(con,rid,'scenes'):
            con.execute("INSERT INTO review_scope_scenes(review_id,scene_id,created_at) VALUES(?,?,?)",(new_id,scene_id,t))
        review_markers._replace_review_markers(con,new_id,[int(row['marker_id']) for row in review_domain._review_markers(con,rid)],t)
        for participant in review_domain._review_participants(con,rid):
            con.execute("INSERT INTO review_participants(id,review_id,display_name,status,created_at,updated_at) VALUES(?,?,?,'not_started',?,?)",(storage_values.uid('review_participant_'),new_id,participant['display_name'],t,t))
        for link in allrows(con,"SELECT questionnaire_id,position FROM review_questionnaires WHERE review_id=? ORDER BY position,id",(rid,)):
            con.execute("INSERT INTO review_questionnaires(id,review_id,questionnaire_id,position,created_at) VALUES(?,?,?,?,?)",(storage_values.uid('review_questionnaire_'),new_id,link['questionnaire_id'],int(link.get('position') or 0),t))
        return _review_payload(con,p['id'],one(con,"SELECT * FROM reviews WHERE id=?",(new_id,)))

@router.post("/api/reviews/{rid}/start")
def start_review(rid:str):
    with storage_mutations.tx(operation='review-round-start') as con:
        p=project(con); review=one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,p['id']))
        if not review:raise HTTPException(404,detail="REVIEW_002: Review not found.")
        review_snapshots._create_review_snapshot(con,p,review)
        row=one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,p['id']))
        return _review_payload(con,p['id'],row)

@router.post("/api/reviews/{rid}/checkpoint/update")
def update_review_checkpoint(rid:str):
    with storage_mutations.tx(operation='review-checkpoint-update') as con:
        p=project(con);review=review_domain._review_for_current_project(con,p['id'],rid)
        if str(review.get('status') or '')!='paused':
            raise HTTPException(409,detail="REVIEW_CHECKPOINT_001: Pause this Review before updating its Checkpoint.")
        snapshot=review_snapshots._review_snapshot_summary(con,rid)
        if not snapshot:raise HTTPException(409,detail="REVIEW_CHECKPOINT_002: This Review has no active round.")
        review_snapshots._refresh_review_snapshot_manuscript(con,p,review,snapshot)
        return _review_payload(con,p['id'],one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,p['id'])))

def _sync_review_container_status(con,project_id:int,rid:str) -> dict:
    review=review_domain._review_for_current_project(con,project_id,rid)
    if str(review.get('status') or '')=='archived':
        return _review_payload(con,project_id,review)
    open_round=one(con,"SELECT id FROM review_snapshots WHERE review_id=? AND status='open' ORDER BY round_number DESC LIMIT 1",(rid,))
    paused_round=one(con,"SELECT id FROM review_snapshots WHERE review_id=? AND status='paused' ORDER BY round_number DESC LIMIT 1",(rid,))
    has_round=one(con,"SELECT id FROM review_snapshots WHERE review_id=? LIMIT 1",(rid,))
    if open_round: target='open';closed_at=None
    elif paused_round: target='paused';closed_at=None
    elif has_round: target='closed';closed_at=storage_values.now()
    else: target='draft';closed_at=None
    con.execute("UPDATE reviews SET status=?,closed_at=?,updated_at=? WHERE id=?",(target,closed_at,storage_values.now(),rid))
    return _review_payload(con,project_id,one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,project_id)))

def _set_review_round_status(con,project_id:int,rid:str,expected:set[str],target:str,event_type:str):
    review=review_domain._review_for_current_project(con,project_id,rid);current=str(review.get('status') or 'draft')
    if current=='archived':raise HTTPException(409,detail="REVIEW_LIFECYCLE_003: Restore this Review before changing a round.")
    snapshot=review_snapshots._review_snapshot_summary(con,rid)
    if not snapshot:raise HTTPException(409,detail="REVIEW_SNAPSHOT_001: Review has no round to update.")
    snapshot_status=str(snapshot.get('status') or '')
    if snapshot_status not in expected:raise HTTPException(409,detail=f"REVIEW_LIFECYCLE_001: Cannot move a {snapshot_status} round to {target}.")
    t=storage_values.now()
    if target=='paused':con.execute("UPDATE review_snapshots SET status='paused',paused_at=? WHERE id=?",(t,snapshot['id']))
    elif target=='open':con.execute("UPDATE review_snapshots SET status='open',paused_at=NULL,closed_at=NULL WHERE id=?",(snapshot['id'],))
    review_domain._review_event(con,rid,event_type,entity_type='snapshot',entity_id=str(snapshot['id']),payload={'round_number':snapshot.get('round_number'),'from':snapshot_status,'to':target},created_at=t)
    return _sync_review_container_status(con,project_id,rid)

def _close_review_round(con,project_id:int,rid:str,snapshot_id:str) -> dict:
    review=review_domain._review_for_current_project(con,project_id,rid)
    if str(review.get('status') or '')=='archived':raise HTTPException(409,detail="REVIEW_LIFECYCLE_003: Restore this Review before changing a round.")
    snapshot=review_snapshots._review_snapshot_summary(con,rid,snapshot_id)
    if not snapshot:raise HTTPException(404,detail="REVIEW_SNAPSHOT_001: Review round was not found.")
    current=str(snapshot.get('status') or '')
    if current=='closed':return _sync_review_container_status(con,project_id,rid)
    if current not in {'open','paused'}:raise HTTPException(409,detail=f"REVIEW_LIFECYCLE_001: Cannot close a {current} round.")
    t=storage_values.now();con.execute("UPDATE review_snapshots SET status='closed',closed_at=? WHERE id=?",(t,snapshot_id))
    review_domain._review_event(con,rid,'REVIEW_ROUND_CLOSED',entity_type='snapshot',entity_id=snapshot_id,payload={'round_number':snapshot.get('round_number'),'from':current,'to':'closed'},created_at=t)
    return _sync_review_container_status(con,project_id,rid)

def _reopen_review_round(con,project_id:int,rid:str,snapshot_id:str) -> dict:
    review=review_domain._review_for_current_project(con,project_id,rid)
    if str(review.get('status') or '')=='archived':raise HTTPException(409,detail="REVIEW_LIFECYCLE_003: Restore this Review before reopening a round.")
    target=review_snapshots._review_snapshot_summary(con,rid,snapshot_id)
    if not target:raise HTTPException(404,detail="REVIEW_SNAPSHOT_001: Review round was not found.")
    target_status=str(target.get('status') or '')
    if target_status=='open':return _sync_review_container_status(con,project_id,rid)
    if target_status not in {'closed','paused'}:raise HTTPException(409,detail=f"REVIEW_LIFECYCLE_001: Cannot reopen a {target_status} round.")
    t=storage_values.now()
    # One round is live in-app at a time. Switching back to an older round pauses the
    # previously live round instead of destroying it; it can be resumed/reopened later.
    for active in allrows(con,"SELECT id,round_number FROM review_snapshots WHERE review_id=? AND status='open' AND id<>?",(rid,snapshot_id)):
        con.execute("UPDATE review_snapshots SET status='paused',paused_at=? WHERE id=?",(t,active['id']))
        review_domain._review_event(con,rid,'REVIEW_PAUSED',entity_type='snapshot',entity_id=str(active['id']),payload={'round_number':active.get('round_number'),'from':'open','to':'paused','reason':'round_switch'},created_at=t)
    con.execute("UPDATE review_snapshots SET status='open',paused_at=NULL,closed_at=NULL WHERE id=?",(snapshot_id,))
    review_domain._review_event(con,rid,'REVIEW_ROUND_REOPENED',entity_type='snapshot',entity_id=snapshot_id,payload={'round_number':target.get('round_number'),'from':target_status,'to':'open'},created_at=t)
    return _sync_review_container_status(con,project_id,rid)

@router.post("/api/reviews/{rid}/pause")
def pause_review(rid:str):
    with storage_mutations.tx(operation='review-pause') as con:
        p=project(con);return _set_review_round_status(con,p['id'],rid,{'open'},'paused','REVIEW_PAUSED')

@router.post("/api/reviews/{rid}/resume")
def resume_review(rid:str):
    with storage_mutations.tx(operation='review-resume') as con:
        p=project(con);return _set_review_round_status(con,p['id'],rid,{'paused'},'open','REVIEW_RESUMED')

@router.post("/api/reviews/{rid}/rounds/{snapshot_id}/close")
def close_review_round(rid:str,snapshot_id:str):
    with storage_mutations.tx(operation='review-round-close') as con:
        p=project(con);return _close_review_round(con,p['id'],rid,snapshot_id)

@router.post("/api/reviews/{rid}/rounds/{snapshot_id}/reopen")
def reopen_review_round(rid:str,snapshot_id:str):
    with storage_mutations.tx(operation='review-round-reopen') as con:
        p=project(con);return _reopen_review_round(con,p['id'],rid,snapshot_id)

@router.post("/api/reviews/{rid}/archive")
def archive_review(rid:str):
    with storage_mutations.tx(operation='review-archive') as con:
        p=project(con);review=review_domain._review_for_current_project(con,p['id'],rid);current=str(review.get('status') or 'draft')
        if current in {'open','paused'}:raise HTTPException(409,detail="REVIEW_LIFECYCLE_002: Close the live Review round before archiving this Review.")
        t=storage_values.now();con.execute("UPDATE reviews SET status='archived',updated_at=? WHERE id=?",(t,rid));review_domain._review_event(con,rid,'REVIEW_ARCHIVED',entity_type='review',entity_id=rid,payload={'from':current,'to':'archived'},created_at=t)
        return _review_payload(con,p['id'],one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,p['id'])))

@router.post("/api/reviews/{rid}/unarchive")
def unarchive_review(rid:str):
    with storage_mutations.tx(operation='review-unarchive') as con:
        p=project(con);review=review_domain._review_for_current_project(con,p['id'],rid)
        if str(review.get('status') or '')!='archived':raise HTTPException(409,detail="REVIEW_LIFECYCLE_001: Review is not archived.")
        has_round=bool(review_snapshots._review_snapshot_summary(con,rid));target='closed' if has_round else 'draft';t=storage_values.now();con.execute("UPDATE reviews SET status=?,closed_at=?,updated_at=? WHERE id=?",(target,t if has_round else None,t,rid));review_domain._review_event(con,rid,'REVIEW_UNARCHIVED',entity_type='review',entity_id=rid,payload={'to':target},created_at=t)
        return _review_payload(con,p['id'],one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,p['id'])))

@router.get("/api/reviews/{rid}/snapshot")
def get_review_snapshot(rid:str,snapshot_id:str|None=None):
    with storage_mutations.tx() as con:
        p=project(con); review_domain._review_for_current_project(con,p['id'],rid)
        snapshot=review_snapshots._review_snapshot_payload(con,rid,snapshot_id)
        if not snapshot:raise HTTPException(404,detail="REVIEW_SNAPSHOT_001: Review round was not found.")
        return snapshot

@router.delete("/api/reviews/{rid}")
def delete_review(rid:str):
    with storage_mutations.tx(operation='review-delete') as con:
        p=project(con)
        if not one(con,"SELECT id FROM reviews WHERE id=? AND project_id=?",(rid,p['id'])):
            raise HTTPException(404,detail="REVIEW_002: Review not found.")
        groups={'review_highlight':[r['id'] for r in allrows(con,"SELECT id FROM review_highlights WHERE review_id=?",(rid,))],'review_comment':[r['id'] for r in allrows(con,"SELECT id FROM review_comments WHERE review_id=?",(rid,))],'review_suggestion':[r['id'] for r in allrows(con,"SELECT id FROM review_suggestions WHERE review_id=?",(rid,))],'review_answer':[r['id'] for r in allrows(con,"SELECT id FROM review_question_answers WHERE review_id=?",(rid,))],'review_general_feedback':[r['id'] for r in allrows(con,"SELECT id FROM review_general_feedback WHERE review_id=?",(rid,))]}
        _delete_polymorphic_references(con,p['id'],groups)
        con.execute("DELETE FROM reviews WHERE id=? AND project_id=?",(rid,p['id']))
    return {"ok":True}
