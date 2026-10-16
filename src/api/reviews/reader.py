from __future__ import annotations
"""Reviewer-facing participant read/session routes."""
from fastapi import APIRouter, HTTPException
from api.reviews.contracts import ReviewHighlightCreate
from backend.data_access import one, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain as review_domain
from . import feedback as review_feedback
from . import snapshots as review_snapshots
router=APIRouter()

@router.get("/api/reviews/{rid}/participants")
def list_review_participants(rid:str):
    with storage_mutations.tx() as con:
        p=project(con); review_domain._review_for_current_project(con,p['id'],rid)
        return review_domain._review_participants(con,rid)

@router.get("/api/reviews/{rid}/participants/{participant_id}/reader")
def review_participant_reader(rid:str,participant_id:str):
    with storage_mutations.tx() as con:
        p=project(con); review=review_domain._review_for_current_project(con,p['id'],rid)
        if str(review.get('status') or '')!='open':
            raise HTTPException(409,detail="REVIEW_READER_001: This Review is not open.")
        participant=one(con,"SELECT id,review_id,display_name,status,started_at,completed_at,created_at,updated_at FROM review_participants WHERE id=? AND review_id=?",(participant_id,rid))
        if not participant:
            raise HTTPException(404,detail="REVIEW_PARTICIPANT_004: Participant does not belong to this Review.")
        if str(participant.get('status') or '')=='revoked':
            raise HTTPException(403,detail="REVIEW_READER_003: This participant's Review access has been revoked.")
        snapshot_summary=review_snapshots._review_snapshot_summary(con,rid)
        if not snapshot_summary:
            raise HTTPException(409,detail="REVIEW_SNAPSHOT_001: Review has not been started.")
        can_read=bool(snapshot_summary.get('permission_read')); can_feedback=bool(snapshot_summary.get('permission_feedback'))
        if not can_read and not can_feedback:
            raise HTTPException(403,detail="REVIEW_READER_002: This Review does not expose a participant-facing capability.")
        snapshot=review_snapshots._review_snapshot_payload(con,rid) if can_read else snapshot_summary
        if can_read:
            visible_feedback=review_feedback._review_feedback_rows(con,rid,None if str(review.get('reviewer_visibility') or 'private')=='shared' else participant_id)
            feedback={key:visible_feedback[key] for key in ('highlights','comments','suggestions')}
        else:
            feedback={'highlights':[],'comments':[],'suggestions':[]}
        return {"review":{"id":rid,"name":str(review.get('name') or ''),"reviewer_visibility":str(review.get('reviewer_visibility') or 'private')},"participant":participant,"snapshot":snapshot,"feedback":feedback,"form":review_feedback._review_participant_feedback_form(con,rid,participant_id) if can_feedback else None,"bookmark":review_domain._review_current_bookmark(con,rid,participant_id,str(snapshot_summary.get('id') or '')) if can_read else None}

@router.post("/api/reviews/{rid}/participants/{participant_id}/begin")
def begin_review_participant(rid:str,participant_id:str):
    with storage_mutations.tx(operation='review-participant-begin') as con:
        p=project(con); review=review_domain._review_for_current_project(con,p['id'],rid)
        if str(review.get('status') or '')!='open': raise HTTPException(409,detail="REVIEW_PARTICIPANT_LIFECYCLE_001: Review must be Open.")
        participant=one(con,"SELECT * FROM review_participants WHERE id=? AND review_id=?",(participant_id,rid))
        if not participant: raise HTTPException(404,detail="REVIEW_PARTICIPANT_004: Participant does not belong to this Review.")
        status=str(participant.get('status') or 'not_started')
        if status=='revoked': raise HTTPException(409,detail="REVIEW_PARTICIPANT_LIFECYCLE_002: Revoked participants cannot enter Reviewer Mode.")
        if status in {'not_started','completed'}:
            t=storage_values.now(); con.execute("UPDATE review_participants SET status='in_progress',started_at=COALESCE(started_at,?),completed_at=NULL,updated_at=? WHERE id=?",(t,t,participant_id)); review_domain._review_event(con,rid,'PARTICIPANT_STARTED',participant_id=participant_id,entity_type='participant',entity_id=participant_id,created_at=t)
        return one(con,"SELECT id,review_id,display_name,status,started_at,completed_at,created_at,updated_at FROM review_participants WHERE id=?",(participant_id,))

@router.put("/api/reviews/{rid}/participants/{participant_id}/bookmark")
def set_review_participant_bookmark(rid:str,participant_id:str,body:ReviewHighlightCreate):
    with storage_mutations.tx(operation='review-participant-bookmark-set') as con:
        p=project(con);review=review_domain._review_for_current_project(con,p['id'],rid);participant=review_domain._review_participant_for_write(con,rid,participant_id,code_prefix='REVIEW_BOOKMARK');snapshot=review_snapshots._review_snapshot_payload(con,rid)
        if not snapshot or not bool(snapshot.get('permission_read')):raise HTTPException(403,detail="REVIEW_BOOKMARK_001: Reading permission is required to bookmark Review text.")
        selection=review_domain._review_selection_values(list(snapshot.get('blocks') or []),str(snapshot['id']),body.selection);t=storage_values.now();bookmark={k:selection[k] for k in ('start_block_id','end_block_id','start_offset','end_offset')}
        review_domain._review_event(con,rid,'BOOKMARK_SET',participant_id=participant_id,entity_type='bookmark',entity_id=participant_id,payload={'snapshot_id':snapshot['id'],'selection':bookmark},created_at=t);return {'bookmark':bookmark}

@router.delete("/api/reviews/{rid}/participants/{participant_id}/bookmark")
def clear_review_participant_bookmark(rid:str,participant_id:str):
    with storage_mutations.tx(operation='review-participant-bookmark-clear') as con:
        p=project(con);review_domain._review_for_current_project(con,p['id'],rid);review_domain._review_participant_for_write(con,rid,participant_id,code_prefix='REVIEW_BOOKMARK');snapshot=review_snapshots._review_snapshot_summary(con,rid)
        if not snapshot:raise HTTPException(409,detail="REVIEW_BOOKMARK_002: Review has no active round.")
        t=storage_values.now();review_domain._review_event(con,rid,'BOOKMARK_CLEARED',participant_id=participant_id,entity_type='bookmark',entity_id=participant_id,payload={'snapshot_id':snapshot['id']},created_at=t);return {'ok':True}

@router.post("/api/reviews/{rid}/participants/{participant_id}/complete")
def complete_review_participant(rid:str,participant_id:str):
    with storage_mutations.tx(operation='review-participant-complete') as con:
        p=project(con); review=review_domain._review_for_current_project(con,p['id'],rid)
        if str(review.get('status') or '')!='open': raise HTTPException(409,detail="REVIEW_PARTICIPANT_LIFECYCLE_001: Review must be Open.")
        participant=one(con,"SELECT * FROM review_participants WHERE id=? AND review_id=?",(participant_id,rid))
        if not participant: raise HTTPException(404,detail="REVIEW_PARTICIPANT_004: Participant does not belong to this Review.")
        if str(participant.get('status') or '')=='revoked': raise HTTPException(409,detail="REVIEW_PARTICIPANT_LIFECYCLE_002: Revoked participants cannot complete a Review.")
        t=storage_values.now(); con.execute("UPDATE review_participants SET status='completed',started_at=COALESCE(started_at,?),completed_at=?,updated_at=? WHERE id=?",(t,t,t,participant_id)); review_domain._review_event(con,rid,'PARTICIPANT_COMPLETED',participant_id=participant_id,entity_type='participant',entity_id=participant_id,created_at=t)
        return one(con,"SELECT id,review_id,display_name,status,started_at,completed_at,created_at,updated_at FROM review_participants WHERE id=?",(participant_id,))

@router.get("/api/reviews/{rid}/feedback")
def review_feedback_for_writer(rid:str):
    with storage_mutations.tx() as con:
        p=project(con); review_domain._review_for_current_project(con,p['id'],rid)
        return review_feedback._review_feedback_rows(con,rid)

@router.get("/api/reviews/{rid}/manuscript-round")
def review_manuscript_round(rid:str,snapshot_id:str):
    with storage_mutations.tx() as con:
        p=project(con); review_domain._review_for_current_project(con,p['id'],rid)
        snapshot=review_snapshots._review_snapshot_payload(con,rid,snapshot_id)
        if not snapshot: raise HTTPException(404,detail="REVIEW_SNAPSHOT_002: Review round not found.")
        return {'snapshot':snapshot,'feedback':review_feedback._review_feedback_rows(con,rid,snapshot_id=snapshot_id)}
