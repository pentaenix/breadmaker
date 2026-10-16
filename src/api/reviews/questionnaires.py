from __future__ import annotations
"""Reusable questionnaires and their attachment to Review rounds."""
from fastapi import APIRouter, HTTPException
import hashlib
import json
from api.reviews.contracts import QuestionnaireCreate, QuestionnairePopulateIn, QuestionnaireUpdate, ReviewQuestionCreate, ReviewQuestionUpdate, ReviewQuestionnaireAttach
from backend.data_access import allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain as review_domain
from . import snapshots as review_snapshots
router=APIRouter()

@router.get("/api/questionnaires")
def list_questionnaires():
    with storage_mutations.tx() as con:
        p=project(con);return review_domain._questionnaire_rows(con,p['id'])

@router.post("/api/questionnaires")
def create_questionnaire(body:QuestionnaireCreate):
    name=review_domain._review_name(body.name);description=review_domain._review_description(body.description)
    with storage_mutations.tx(operation='questionnaire-create') as con:
        p=project(con);t=storage_values.now();qid=storage_values.uid('questionnaire_')
        if one(con,"SELECT id FROM questionnaires WHERE project_id=? AND name=? COLLATE NOCASE",(p['id'],name)):raise HTTPException(409,detail="QUESTIONNAIRE_001: A questionnaire with that name already exists.")
        con.execute("INSERT INTO questionnaires(id,project_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?,?)",(qid,p['id'],name,description,t,t))
        return one(con,"SELECT * FROM questionnaires WHERE id=?",(qid,))

@router.post("/api/questionnaires/populate")
def populate_questionnaire(body:QuestionnairePopulateIn):
    base_name=review_domain._review_name(body.name);description=review_domain._review_description(body.description)
    questions=[str(prompt or '').strip()[:2000] for prompt in (body.questions or []) if str(prompt or '').strip()]
    if not questions:raise HTTPException(400,detail="QUESTIONNAIRE_POPULATE_001: Template must contain at least one question.")
    with storage_mutations.tx(operation='questionnaire-populate') as con:
        p=project(con);candidate=base_name;suffix=2
        while one(con,"SELECT id FROM questionnaires WHERE project_id=? AND name=? COLLATE NOCASE",(p['id'],candidate)):
            candidate=f"{base_name} {suffix}";suffix+=1
        t=storage_values.now();qid=storage_values.uid('questionnaire_')
        con.execute("INSERT INTO questionnaires(id,project_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?,?)",(qid,p['id'],candidate,description,t,t))
        for position,prompt in enumerate(questions):
            question_id=storage_values.uid('questionnaire_question_')
            con.execute("INSERT INTO questionnaire_questions(id,questionnaire_id,position,prompt,required,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(question_id,qid,position,prompt,0,t,t))
        return {'questionnaire':one(con,"SELECT * FROM questionnaires WHERE id=?",(qid,)),'questions':allrows(con,"SELECT * FROM questionnaire_questions WHERE questionnaire_id=? ORDER BY position,id",(qid,))}

@router.patch("/api/questionnaires/{questionnaire_id}")
def update_questionnaire(questionnaire_id:str,body:QuestionnaireUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'name' in vals:vals['name']=review_domain._review_name(vals.get('name') or '')
    if 'description' in vals:vals['description']=review_domain._review_description(vals.get('description') or '')
    with storage_mutations.tx(operation='questionnaire-update') as con:
        p=project(con);current=one(con,"SELECT * FROM questionnaires WHERE id=? AND project_id=?",(questionnaire_id,p['id']))
        if not current:raise HTTPException(404,detail="QUESTIONNAIRE_002: Questionnaire not found.")
        if 'name' in vals and one(con,"SELECT id FROM questionnaires WHERE project_id=? AND name=? COLLATE NOCASE AND id<>?",(p['id'],vals['name'],questionnaire_id)):raise HTTPException(409,detail="QUESTIONNAIRE_001: A questionnaire with that name already exists.")
        if vals:
            sets=', '.join(f"{key}=?" for key in vals)+",updated_at=?";con.execute(f"UPDATE questionnaires SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),questionnaire_id))
        return one(con,"SELECT * FROM questionnaires WHERE id=?",(questionnaire_id,))

@router.delete("/api/questionnaires/{questionnaire_id}")
def delete_questionnaire(questionnaire_id:str):
    with storage_mutations.tx(operation='questionnaire-delete') as con:
        p=project(con);row=one(con,"SELECT id FROM questionnaires WHERE id=? AND project_id=?",(questionnaire_id,p['id']))
        if not row:raise HTTPException(404,detail="QUESTIONNAIRE_002: Questionnaire not found.")
        con.execute("DELETE FROM questionnaires WHERE id=?",(questionnaire_id,))
    return {'ok':True}

@router.post("/api/questionnaires/{questionnaire_id}/questions")
def create_questionnaire_question(questionnaire_id:str,body:ReviewQuestionCreate):
    prompt=(body.prompt or '').strip()[:2000]
    if not prompt:raise HTTPException(400,detail="REVIEW_QUESTION_001: Question cannot be empty.")
    with storage_mutations.tx(operation='questionnaire-question-create') as con:
        p=project(con);questionnaire=one(con,"SELECT id FROM questionnaires WHERE id=? AND project_id=?",(questionnaire_id,p['id']))
        if not questionnaire:raise HTTPException(404,detail="QUESTIONNAIRE_002: Questionnaire not found.")
        position=int((one(con,"SELECT COALESCE(MAX(position),-1)+1 position FROM questionnaire_questions WHERE questionnaire_id=?",(questionnaire_id,)) or {}).get('position') or 0);qid=storage_values.uid('questionnaire_question_');t=storage_values.now()
        con.execute("INSERT INTO questionnaire_questions(id,questionnaire_id,position,prompt,required,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(qid,questionnaire_id,position,prompt,1 if body.required else 0,t,t));return one(con,"SELECT * FROM questionnaire_questions WHERE id=?",(qid,))

@router.patch("/api/questionnaire-questions/{question_id}")
def update_questionnaire_question(question_id:str,body:ReviewQuestionUpdate):
    vals=body.model_dump(exclude_unset=True)
    with storage_mutations.tx(operation='questionnaire-question-update') as con:
        p=project(con);current=one(con,"SELECT qq.* FROM questionnaire_questions qq JOIN questionnaires q ON q.id=qq.questionnaire_id WHERE qq.id=? AND q.project_id=?",(question_id,p['id']))
        if not current:raise HTTPException(404,detail="REVIEW_QUESTION_005: Question not found.")
        prompt=str(vals.get('prompt',current.get('prompt') or '')).strip()[:2000]
        if not prompt:raise HTTPException(400,detail="REVIEW_QUESTION_001: Question cannot be empty.")
        required=1 if bool(vals.get('required',current.get('required'))) else 0
        con.execute("UPDATE questionnaire_questions SET prompt=?,required=?,updated_at=? WHERE id=?",(prompt,required,storage_values.now(),question_id));return one(con,"SELECT * FROM questionnaire_questions WHERE id=?",(question_id,))

@router.delete("/api/questionnaire-questions/{question_id}")
def delete_questionnaire_question(question_id:str):
    with storage_mutations.tx(operation='questionnaire-question-delete') as con:
        p=project(con);current=one(con,"SELECT qq.id FROM questionnaire_questions qq JOIN questionnaires q ON q.id=qq.questionnaire_id WHERE qq.id=? AND q.project_id=?",(question_id,p['id']))
        if not current:raise HTTPException(404,detail="REVIEW_QUESTION_005: Question not found.")
        con.execute("DELETE FROM questionnaire_questions WHERE id=?",(question_id,))
    return {'ok':True}

def _recompute_review_snapshot_content_hash(con,rid:str,snapshot_id:str) -> None:
    payload=review_snapshots._review_snapshot_payload(con,rid,snapshot_id)
    if not payload:return
    permissions={field:bool(payload.get(field)) for field in review_domain._REVIEW_PERMISSION_FIELDS}
    content_hash=storage_values.review_snapshot_content_hash(
        rid,int(payload.get('source_generation') or 0),str(payload.get('scope_type') or 'manuscript'),permissions,
        payload.get('chapters') or [],payload.get('scenes') or [],payload.get('blocks') or [],payload.get('questions') or [],payload.get('markers') or [],
    )
    con.execute("UPDATE review_snapshots SET content_hash=? WHERE id=?",(content_hash,snapshot_id))

def _active_review_snapshot_for_questionnaire_change(con,rid:str) -> dict|None:
    snapshot=review_snapshots._review_snapshot_summary(con,rid)
    if not snapshot or str(snapshot.get('status') or '') not in {'open','paused'}:return None
    return snapshot

def _sync_questionnaire_into_active_snapshot(con,rid:str,questionnaire_id:str) -> int:
    snapshot=_active_review_snapshot_for_questionnaire_change(con,rid)
    if not snapshot:return 0
    sid=str(snapshot['id'])
    if one(con,"SELECT id FROM review_snapshot_questions WHERE snapshot_id=? AND source_questionnaire_id=? LIMIT 1",(sid,questionnaire_id)):
        if not bool(snapshot.get('permission_feedback')):
            con.execute("UPDATE review_snapshots SET permission_feedback=1 WHERE id=?",(sid,));_recompute_review_snapshot_content_hash(con,rid,sid)
        return 0
    questionnaire=one(con,"SELECT id,name FROM questionnaires WHERE id=?",(questionnaire_id,))
    if not questionnaire:return 0
    questions=allrows(con,"SELECT id,prompt,required FROM questionnaire_questions WHERE questionnaire_id=? ORDER BY position,id",(questionnaire_id,))
    position=int((one(con,"SELECT COALESCE(MAX(position),-1)+1 position FROM review_snapshot_questions WHERE snapshot_id=?",(sid,)) or {}).get('position') or 0)
    t=storage_values.now()
    for offset,question in enumerate(questions):
        con.execute("INSERT INTO review_snapshot_questions(id,snapshot_id,source_questionnaire_id,source_question_id,questionnaire_name,position,prompt,required,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(storage_values.uid('review_snapshot_question_'),sid,questionnaire_id,str(question['id']),str(questionnaire.get('name') or ''),position+offset,str(question.get('prompt') or ''),1 if question.get('required') else 0,t))
    con.execute("UPDATE review_snapshots SET permission_feedback=1 WHERE id=?",(sid,))
    _recompute_review_snapshot_content_hash(con,rid,sid)
    return len(questions)

def _remove_questionnaire_from_active_snapshot(con,rid:str,questionnaire_id:str) -> int:
    snapshot=_active_review_snapshot_for_questionnaire_change(con,rid)
    if not snapshot:return 0
    sid=str(snapshot['id'])
    rows=allrows(con,"SELECT id FROM review_snapshot_questions WHERE snapshot_id=? AND source_questionnaire_id=?",(sid,questionnaire_id))
    if not rows:return 0
    ids=[str(row['id']) for row in rows];marks=','.join('?' for _ in ids)
    answered=int((one(con,f"SELECT COUNT(*) n FROM review_question_answers WHERE snapshot_id=? AND question_id IN ({marks})",(sid,*ids)) or {}).get('n') or 0)
    if answered:
        raise HTTPException(409,detail="REVIEW_QUESTIONNAIRE_004: This Questionnaire already has responses in the active Review round.")
    con.execute("DELETE FROM review_snapshot_questions WHERE snapshot_id=? AND source_questionnaire_id=?",(sid,questionnaire_id))
    remaining=allrows(con,"SELECT id FROM review_snapshot_questions WHERE snapshot_id=? ORDER BY position,id",(sid,))
    for position,row in enumerate(remaining):con.execute("UPDATE review_snapshot_questions SET position=? WHERE id=?",(position,row['id']))
    _recompute_review_snapshot_content_hash(con,rid,sid)
    return len(rows)

@router.post("/api/reviews/{rid}/questionnaires")
def attach_review_questionnaire(rid:str,body:ReviewQuestionnaireAttach):
    with storage_mutations.tx(operation='review-questionnaire-attach') as con:
        p=project(con);review=review_domain._review_for_current_project(con,p['id'],rid);qid=str(body.questionnaire_id or '')
        if not one(con,"SELECT id FROM questionnaires WHERE id=? AND project_id=?",(qid,p['id'])):raise HTTPException(404,detail="QUESTIONNAIRE_002: Questionnaire not found.")
        existing=one(con,"SELECT id FROM review_questionnaires WHERE review_id=? AND questionnaire_id=?",(rid,qid))
        lid=str(existing['id']) if existing else storage_values.uid('review_questionnaire_')
        if not existing:
            position=int((one(con,"SELECT COALESCE(MAX(position),-1)+1 position FROM review_questionnaires WHERE review_id=?",(rid,)) or {}).get('position') or 0)
            con.execute("INSERT INTO review_questionnaires(id,review_id,questionnaire_id,position,created_at) VALUES(?,?,?,?,?)",(lid,rid,qid,position,storage_values.now()))
        # A Questionnaire is itself a Feedback surface. Keep the Review and the active round in sync so
        # attaching after Start Review is immediately visible to participants.
        if not bool(review.get('permission_feedback')):
            con.execute("UPDATE reviews SET permission_feedback=1,updated_at=? WHERE id=?",(storage_values.now(),rid))
        frozen_count=_sync_questionnaire_into_active_snapshot(con,rid,qid)
        return {'ok':True,'id':lid,'duplicate':bool(existing),'active_round_questions_added':frozen_count}

@router.delete("/api/reviews/{rid}/questionnaires/{questionnaire_id}")
def detach_review_questionnaire(rid:str,questionnaire_id:str):
    with storage_mutations.tx(operation='review-questionnaire-detach') as con:
        p=project(con);review_domain._review_for_current_project(con,p['id'],rid)
        _remove_questionnaire_from_active_snapshot(con,rid,questionnaire_id)
        con.execute("DELETE FROM review_questionnaires WHERE review_id=? AND questionnaire_id=?",(rid,questionnaire_id))
    return {'ok':True}
