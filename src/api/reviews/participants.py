from __future__ import annotations
"""Writer-side Review participant administration."""
from fastapi import APIRouter, HTTPException
from api.reviews.contracts import ReviewParticipantCreate, ReviewParticipantStatusUpdate, ReviewParticipantUpdate, ReviewParticipantsPopulate
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain as review_domain
router=APIRouter()

@router.post("/api/reviews/{rid}/participants")
def create_review_participant(rid:str,body:ReviewParticipantCreate):
    display_name=review_domain._review_participant_name(body.display_name)
    with storage_mutations.tx(operation='review-participant-create') as con:
        p=project(con); review_domain._review_for_current_project(con,p['id'],rid)
        if any(str(row.get('display_name') or '').casefold()==display_name.casefold() for row in review_domain._review_participants(con,rid)):
            raise HTTPException(409,detail="REVIEW_PARTICIPANT_002: A participant with that name already exists in this Review.")
        t=storage_values.now(); participant_id=storage_values.uid('review_participant_')
        con.execute("INSERT INTO review_participants(id,review_id,display_name,created_at,updated_at) VALUES(?,?,?,?,?)",(participant_id,rid,display_name,t,t))
        return one(con,"SELECT id,review_id,display_name,status,started_at,completed_at,created_at,updated_at FROM review_participants WHERE id=?",(participant_id,))

@router.post('/api/reviews/{rid}/participants/populate')
def populate_review_participants(rid:str,body:ReviewParticipantsPopulate):
    source_id=str(body.source_review_id or '')
    if source_id==rid: raise HTTPException(400,detail='REVIEW_PARTICIPANT_006: Choose a different Review to reuse participants from.')
    with storage_mutations.tx(operation='review-participants-populate') as con:
        p=project(con);review_domain._review_for_current_project(con,p['id'],rid);review_domain._review_for_current_project(con,p['id'],source_id);existing={str(row.get('display_name') or '').casefold() for row in review_domain._review_participants(con,rid)};created=[];t=storage_values.now()
        for source in review_domain._review_participants(con,source_id):
            name=str(source.get('display_name') or '').strip()
            if not name or name.casefold() in existing: continue
            pid=storage_values.uid('review_participant_');con.execute("INSERT INTO review_participants(id,review_id,display_name,status,created_at,updated_at) VALUES(?,?,?,'not_started',?,?)",(pid,rid,name,t,t));created.append(one(con,"SELECT id,review_id,display_name,status,started_at,completed_at,created_at,updated_at FROM review_participants WHERE id=?",(pid,)));existing.add(name.casefold())
        return {'created':created,'count':len(created)}

@router.patch("/api/review-participants/{participant_id}")
def update_review_participant(participant_id:str,body:ReviewParticipantUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'display_name' not in vals: raise HTTPException(400,detail="REVIEW_PARTICIPANT_003: No participant changes were provided.")
    display_name=review_domain._review_participant_name(vals.get('display_name') or '')
    with storage_mutations.tx(operation='review-participant-update') as con:
        p=project(con)
        current=one(con,"SELECT rp.* FROM review_participants rp JOIN reviews r ON r.id=rp.review_id WHERE rp.id=? AND r.project_id=?",(participant_id,p['id']))
        if not current: raise HTTPException(404,detail="REVIEW_PARTICIPANT_004: Participant not found.")
        if any(str(row.get('display_name') or '').casefold()==display_name.casefold() and str(row.get('id'))!=participant_id for row in review_domain._review_participants(con,str(current['review_id']))):
            raise HTTPException(409,detail="REVIEW_PARTICIPANT_002: A participant with that name already exists in this Review.")
        con.execute("UPDATE review_participants SET display_name=?,updated_at=? WHERE id=?",(display_name,storage_values.now(),participant_id))
        return one(con,"SELECT id,review_id,display_name,status,started_at,completed_at,created_at,updated_at FROM review_participants WHERE id=?",(participant_id,))

@router.patch("/api/review-participants/{participant_id}/status")
def update_review_participant_status(participant_id:str,body:ReviewParticipantStatusUpdate):
    requested=str(body.status or '').strip().lower()
    if requested not in {'not_started','in_progress','completed','revoked'}: raise HTTPException(400,detail="REVIEW_PARTICIPANT_LIFECYCLE_004: Invalid participant status.")
    with storage_mutations.tx(operation='review-participant-status') as con:
        p=project(con); participant=one(con,"SELECT rp.*,r.status review_status FROM review_participants rp JOIN reviews r ON r.id=rp.review_id WHERE rp.id=? AND r.project_id=?",(participant_id,p['id']))
        if not participant: raise HTTPException(404,detail="REVIEW_PARTICIPANT_004: Participant does not belong to this project.")
        current=str(participant.get('status') or 'not_started'); rid=str(participant['review_id']); t=storage_values.now()
        if requested==current: return one(con,"SELECT id,review_id,display_name,status,started_at,completed_at,created_at,updated_at FROM review_participants WHERE id=?",(participant_id,))
        started=participant.get('started_at'); completed=participant.get('completed_at')
        if requested=='in_progress': started=started or t; completed=None
        elif requested=='completed': started=started or t; completed=t
        elif requested=='not_started': started=None; completed=None
        elif requested=='revoked': completed=completed
        con.execute("UPDATE review_participants SET status=?,started_at=?,completed_at=?,updated_at=? WHERE id=?",(requested,started,completed,t,participant_id))
        review_domain._review_event(con,rid,'PARTICIPANT_STATUS_CHANGED',participant_id=participant_id,entity_type='participant',entity_id=participant_id,payload={'from':current,'to':requested},created_at=t)
        return one(con,"SELECT id,review_id,display_name,status,started_at,completed_at,created_at,updated_at FROM review_participants WHERE id=?",(participant_id,))

@router.delete("/api/review-participants/{participant_id}")
def delete_review_participant(participant_id:str):
    with storage_mutations.tx(operation='review-participant-delete') as con:
        p=project(con)
        current=one(con,"SELECT rp.id,rp.review_id FROM review_participants rp JOIN reviews r ON r.id=rp.review_id WHERE rp.id=? AND r.project_id=?",(participant_id,p['id']))
        if not current: raise HTTPException(404,detail="REVIEW_PARTICIPANT_004: Participant not found.")
        groups={'review_highlight':[r['id'] for r in allrows(con,"SELECT id FROM review_highlights WHERE participant_id=?",(participant_id,))],'review_comment':[r['id'] for r in allrows(con,"SELECT id FROM review_comments WHERE participant_id=?",(participant_id,))],'review_suggestion':[r['id'] for r in allrows(con,"SELECT id FROM review_suggestions WHERE participant_id=?",(participant_id,))],'review_answer':[r['id'] for r in allrows(con,"SELECT id FROM review_question_answers WHERE participant_id=?",(participant_id,))],'review_general_feedback':[r['id'] for r in allrows(con,"SELECT id FROM review_general_feedback WHERE participant_id=?",(participant_id,))]}
        _delete_polymorphic_references(con,p['id'],groups)
        con.execute("DELETE FROM review_participants WHERE id=?",(participant_id,))
    return {'ok':True}
