from __future__ import annotations
"""Review feedback, annotations, suggestions, and writer-side actions."""
from fastapi import APIRouter, HTTPException
from typing import Optional
import json
from bs4 import BeautifulSoup, NavigableString
from api.reviews.contracts import ReviewCommentCreate, ReviewFeedbackStatusUpdate, ReviewFeedbackSubmit, ReviewHighlightCreate, ReviewSuggestionCreate, ReviewTaskCreate
from backend.content import _ALLOWED_INLINE, _html_from_text, sanitize_inline_html
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from backend.dependencies import feature_call
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain as review_domain
from . import snapshots as review_snapshots
router=APIRouter()
_detach_map_anchors_for_removed_manuscript = feature_call('maps.anchors', '_detach_map_anchors_for_removed_manuscript')
_normalize_image_rows = feature_call('manuscript.images', '_normalize_image_rows')
_split_inline_html = feature_call('durability.cuts', '_split_inline_html')
reanchor_block_annotations = feature_call('manuscript.anchors', 'reanchor_block_annotations')
reanchor_block_bookmark = feature_call('manuscript.anchors', 'reanchor_block_bookmark')
rehome_deleted_block_refs = feature_call('manuscript.anchors', 'rehome_deleted_block_refs')
repair_stale_annotations = feature_call('manuscript.anchors', 'repair_stale_annotations')
repair_stale_bookmark = feature_call('manuscript.anchors', 'repair_stale_bookmark')

def _review_questions(con,rid:str,snapshot_id:str|None=None) -> list[dict]:
    snapshot=review_snapshots._review_snapshot_summary(con,rid,snapshot_id)
    if not snapshot:return []
    return allrows(con,"SELECT id,snapshot_id,source_questionnaire_id,source_question_id,questionnaire_name,position,prompt,required,created_at FROM review_snapshot_questions WHERE snapshot_id=? ORDER BY position,id",(snapshot['id'],))

def _review_participant_feedback_form(con,rid:str,participant_id:str,snapshot_id:str|None=None) -> dict:
    snapshot=review_snapshots._review_snapshot_summary(con,rid,snapshot_id)
    if not snapshot:return {'questions':[],'answers':[],'general_feedback':'','saved':False,'snapshot_id':None}
    sid=str(snapshot['id']);questions=_review_questions(con,rid,sid)
    answers=allrows(con,"SELECT id,review_id,snapshot_id,question_id,participant_id,answer_text,created_at,updated_at FROM review_question_answers WHERE review_id=? AND snapshot_id=? AND participant_id=?",(rid,sid,participant_id))
    by_question={str(row['question_id']):row for row in answers}
    general=one(con,"SELECT id,review_id,snapshot_id,participant_id,body,created_at,updated_at FROM review_general_feedback WHERE review_id=? AND snapshot_id=? AND participant_id=?",(rid,sid,participant_id))
    return {'snapshot_id':sid,'round_number':snapshot.get('round_number'),'questions':questions,'answers':[{'question_id':q['id'],'answer_text':str((by_question.get(str(q['id'])) or {}).get('answer_text') or '')} for q in questions],'general_feedback':str((general or {}).get('body') or ''),'saved':bool(general or answers)}

def _review_feedback_form_access(con,project_id:int,rid:str,participant_id:str) -> tuple[dict,dict,dict]:
    review=review_domain._review_for_current_project(con,project_id,rid)
    if str(review.get('status') or '') not in {'open','paused'}:
        raise HTTPException(409,detail="REVIEW_FORM_001: This Review does not have an active round.")
    if str(review.get('status') or '')=='paused':
        raise HTTPException(409,detail="REVIEW_FORM_003: This Review round is paused.")
    participant=review_domain._review_participant_for_write(con,rid,participant_id,code_prefix='REVIEW_FORM')
    snapshot=review_snapshots._review_snapshot_summary(con,rid)
    if not snapshot: raise HTTPException(409,detail="REVIEW_SNAPSHOT_001: Review has not been started.")
    if not bool(snapshot.get('permission_feedback')):
        raise HTTPException(403,detail="REVIEW_FORM_002: Feedback permission is not enabled for this Review.")
    return review,participant,snapshot

def _review_feedback_rows(con,rid:str,participant_id:Optional[str]=None,snapshot_id:Optional[str]=None) -> dict:
    participant_clause=' AND h.participant_id=?' if participant_id else ''
    snapshot_clause=' AND a.snapshot_id=?' if snapshot_id else ''
    params=tuple([rid]+([participant_id] if participant_id else [])+([snapshot_id] if snapshot_id else []))
    highlights=allrows(con,f"""SELECT h.id,h.review_id,h.participant_id,rp.display_name participant_name,h.anchor_id,h.tag,h.status,h.created_at,h.updated_at,
      a.snapshot_id,rs.round_number,a.start_block_id,a.end_block_id,a.start_offset,a.end_offset,a.selected_text,a.prefix_context,a.suffix_context
      FROM review_highlights h JOIN review_participants rp ON rp.id=h.participant_id AND rp.review_id=h.review_id
      JOIN review_anchors a ON a.id=h.anchor_id AND a.review_id=h.review_id
      JOIN review_snapshots rs ON rs.id=a.snapshot_id
      WHERE h.review_id=?{participant_clause}{snapshot_clause} ORDER BY h.created_at,h.id""",params)
    for item in highlights:
        marker=one(con,"SELECT name,color FROM review_snapshot_markers WHERE snapshot_id=? AND name=? COLLATE NOCASE",(item.get('snapshot_id'),str(item.get('tag') or ''))) if item.get('tag') else None
        item['marker_name']=str((marker or {}).get('name') or item.get('tag') or '')
        item['marker_color']=str((marker or {}).get('color') or '#f2c94c')
    participant_clause=' AND c.participant_id=?' if participant_id else ''
    comments=allrows(con,f"""SELECT c.id,c.review_id,c.participant_id,rp.display_name participant_name,c.anchor_id,c.body,c.status,c.created_at,c.updated_at,
      a.snapshot_id,rs.round_number,a.start_block_id,a.end_block_id,a.start_offset,a.end_offset,a.selected_text,a.prefix_context,a.suffix_context
      FROM review_comments c JOIN review_participants rp ON rp.id=c.participant_id AND rp.review_id=c.review_id
      JOIN review_anchors a ON a.id=c.anchor_id AND a.review_id=c.review_id
      JOIN review_snapshots rs ON rs.id=a.snapshot_id
      WHERE c.review_id=?{participant_clause}{snapshot_clause} ORDER BY c.created_at,c.id""",params)
    participant_clause=' AND s.participant_id=?' if participant_id else ''
    suggestions=allrows(con,f"""SELECT s.id,s.review_id,s.participant_id,rp.display_name participant_name,s.anchor_id,s.operation,s.original_text,s.proposed_text,s.status,s.applied_generation,s.created_at,s.updated_at,s.resolved_at,
      a.snapshot_id,rs.round_number,a.start_block_id,a.end_block_id,a.start_offset,a.end_offset,a.selected_text,a.prefix_context,a.suffix_context
      FROM review_suggestions s JOIN review_participants rp ON rp.id=s.participant_id AND rp.review_id=s.review_id
      JOIN review_anchors a ON a.id=s.anchor_id AND a.review_id=s.review_id
      JOIN review_snapshots rs ON rs.id=a.snapshot_id
      WHERE s.review_id=?{participant_clause}{snapshot_clause} ORDER BY s.created_at,s.id""",params)
    for target_type,rows in (('review_highlight',highlights),('review_comment',comments),('review_suggestion',suggestions)):
        for item in rows:
            item['task_count']=int((one(con,"SELECT COUNT(*) count FROM references_map WHERE source_type='task' AND target_type=? AND target_id=?",(target_type,str(item['id']))) or {}).get('count') or 0)
    clause=' AND a.participant_id=?' if participant_id else ''
    snapshot_clause=' AND a.snapshot_id=?' if snapshot_id else ''
    params=tuple([rid]+([participant_id] if participant_id else [])+([snapshot_id] if snapshot_id else []))
    answers=allrows(con,f"""SELECT a.id,a.review_id,a.snapshot_id,a.question_id,a.participant_id,rp.display_name participant_name,a.answer_text,a.created_at,a.updated_at,
      q.prompt,q.questionnaire_name,rs.round_number
      FROM review_question_answers a JOIN review_participants rp ON rp.id=a.participant_id AND rp.review_id=a.review_id
      JOIN review_snapshot_questions q ON q.id=a.question_id JOIN review_snapshots rs ON rs.id=a.snapshot_id
      WHERE a.review_id=?{clause}{snapshot_clause} ORDER BY rs.round_number,a.updated_at,a.id""",params)
    clause=' AND g.participant_id=?' if participant_id else ''
    snapshot_clause=' AND g.snapshot_id=?' if snapshot_id else ''
    params=tuple([rid]+([participant_id] if participant_id else [])+([snapshot_id] if snapshot_id else []))
    generals=allrows(con,f"""SELECT g.id,g.review_id,g.snapshot_id,g.participant_id,rp.display_name participant_name,g.body,g.created_at,g.updated_at,rs.round_number
      FROM review_general_feedback g JOIN review_participants rp ON rp.id=g.participant_id AND rp.review_id=g.review_id
      JOIN review_snapshots rs ON rs.id=g.snapshot_id
      WHERE g.review_id=?{clause}{snapshot_clause} ORDER BY rs.round_number,g.updated_at,g.id""",params)
    grouped={}
    for row in answers:
        key=(str(row['snapshot_id']),str(row['participant_id']));entry=grouped.setdefault(key,{'snapshot_id':row['snapshot_id'],'round_number':row.get('round_number'),'participant_id':str(row['participant_id']),'participant_name':row['participant_name'],'answers':[],'general_feedback':'','updated_at':row.get('updated_at'),'created_at':row.get('created_at')})
        answer_id=str(row.get('id') or '')
        task_count=int((one(con,"SELECT COUNT(*) count FROM references_map WHERE source_type='task' AND target_type='review_answer' AND target_id=?",(answer_id,)) or {}).get('count') or 0)
        entry['answers'].append({'id':answer_id,'question_id':row['question_id'],'questionnaire_name':str(row.get('questionnaire_name') or ''),'prompt':str(row.get('prompt') or ''),'answer_text':str(row.get('answer_text') or ''),'task_count':task_count,'updated_at':row.get('updated_at')})
        entry['updated_at']=max(str(entry.get('updated_at') or ''),str(row.get('updated_at') or ''))
    for general in generals:
        key=(str(general['snapshot_id']),str(general['participant_id']));entry=grouped.setdefault(key,{'snapshot_id':general['snapshot_id'],'round_number':general.get('round_number'),'participant_id':str(general['participant_id']),'participant_name':general['participant_name'],'answers':[],'general_feedback':'','updated_at':general.get('updated_at'),'created_at':general.get('created_at')})
        general_id=str(general.get('id') or '')
        entry['general_feedback']=str(general.get('body') or '');entry['general_feedback_id']=general_id;entry['general_feedback_task_count']=int((one(con,"SELECT COUNT(*) count FROM references_map WHERE source_type='task' AND target_type='review_general_feedback' AND target_id=?",(general_id,)) or {}).get('count') or 0);entry['updated_at']=max(str(entry.get('updated_at') or ''),str(general.get('updated_at') or ''))
    forms=sorted(grouped.values(),key=lambda row:(int(row.get('round_number') or 0),str(row.get('updated_at') or '')))
    return {'highlights':highlights,'comments':comments,'suggestions':suggestions,'feedback_forms':forms}

def _review_feedback_access(con,project_id:int,rid:str,participant_id:str) -> tuple[dict,dict,dict]:
    review=review_domain._review_for_current_project(con,project_id,rid)
    if str(review.get('status') or '')!='open':
        raise HTTPException(409,detail="REVIEW_FEEDBACK_001: This Review has not been started.")
    participant=review_domain._review_participant_for_write(con,rid,participant_id,code_prefix='REVIEW_FEEDBACK')
    snapshot=review_snapshots._review_snapshot_payload(con,rid)
    if not snapshot: raise HTTPException(409,detail="REVIEW_SNAPSHOT_001: Review has not been started.")
    if not bool(snapshot.get('permission_comment')):
        raise HTTPException(403,detail="REVIEW_FEEDBACK_002: Comment + Highlight permission is not enabled for this Review.")
    return review,participant,snapshot

def _review_suggestion_access(con,project_id:int,rid:str,participant_id:str) -> tuple[dict,dict,dict]:
    review=review_domain._review_for_current_project(con,project_id,rid)
    if str(review.get('status') or '')!='open':
        raise HTTPException(409,detail="REVIEW_SUGGEST_001: This Review has not been started.")
    participant=review_domain._review_participant_for_write(con,rid,participant_id,code_prefix='REVIEW_SUGGEST')
    snapshot=review_snapshots._review_snapshot_payload(con,rid)
    if not snapshot: raise HTTPException(409,detail="REVIEW_SNAPSHOT_001: Review has not been started.")
    if not bool(snapshot.get('permission_suggest')):
        raise HTTPException(403,detail="REVIEW_SUGGEST_002: Suggest permission is not enabled for this Review.")
    return review,participant,snapshot

@router.put("/api/reviews/{rid}/participants/{participant_id}/feedback")
def save_review_feedback(rid:str,participant_id:str,body:ReviewFeedbackSubmit):
    with storage_mutations.tx(operation='review-feedback-save') as con:
        p=project(con); _review,participant,snapshot=_review_feedback_form_access(con,p['id'],rid,participant_id)
        questions=_review_questions(con,rid,str(snapshot['id'])); by_id={str(q['id']):q for q in questions}
        submitted={}
        for answer in body.answers:
            qid=str(answer.question_id or '')
            if qid in submitted: raise HTTPException(400,detail="REVIEW_FORM_003: A Feedback question was submitted more than once.")
            if qid not in by_id: raise HTTPException(400,detail="REVIEW_FORM_004: Feedback contains an unavailable question.")
            submitted[qid]=(answer.answer_text or '').strip()[:120000]
        t=storage_values.now()
        for question in questions:
            qid=str(question['id']); answer=submitted.get(qid,'')
            existing=one(con,"SELECT id FROM review_question_answers WHERE snapshot_id=? AND question_id=? AND participant_id=?",(snapshot['id'],qid,participant_id))
            if existing:
                con.execute("UPDATE review_question_answers SET answer_text=?,updated_at=? WHERE id=?",(answer,t,existing['id']))
            else:
                con.execute("INSERT INTO review_question_answers(id,review_id,snapshot_id,question_id,participant_id,answer_text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(storage_values.uid('review_answer_'),rid,snapshot['id'],qid,participant_id,answer,t,t))
        general=(body.general_feedback or '').strip()[:240000]
        existing_general=one(con,"SELECT id FROM review_general_feedback WHERE review_id=? AND snapshot_id=? AND participant_id=?",(rid,snapshot['id'],participant_id))
        if existing_general:
            con.execute("UPDATE review_general_feedback SET body=?,updated_at=? WHERE id=?",(general,t,existing_general['id']))
        else:
            con.execute("INSERT INTO review_general_feedback(id,review_id,snapshot_id,participant_id,body,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(storage_values.uid('review_general_'),rid,snapshot['id'],participant_id,general,t,t))
        con.execute("INSERT INTO review_events(id,review_id,participant_id,event_type,entity_type,entity_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)",(storage_values.uid('review_event_'),rid,participant['id'],'FEEDBACK_SAVED','feedback',participant['id'],json.dumps({'question_ids':[str(q['id']) for q in questions],'general_feedback_present':bool(general)},sort_keys=True,separators=(',',':')),t))
        return _review_participant_feedback_form(con,rid,participant_id)

@router.post("/api/reviews/{rid}/participants/{participant_id}/highlights")
def create_review_highlight(rid:str,participant_id:str,body:ReviewHighlightCreate):
    with storage_mutations.tx(operation='review-highlight-create') as con:
        p=project(con); _review,participant,snapshot=_review_feedback_access(con,p['id'],rid,participant_id)
        marker=one(con,"SELECT source_marker_id,name,color FROM review_snapshot_markers WHERE snapshot_id=? AND source_marker_id=?",(snapshot['id'],int(body.marker_id or 0)))
        if not marker:raise HTTPException(400,detail='REVIEW_HIGHLIGHT_001: Choose an available Review marker.')
        anchor=review_domain._review_anchor_from_selection(con,rid,snapshot,body.selection); t=storage_values.now(); hid=storage_values.uid('review_highlight_');tag=str(marker.get('name') or '')[:240]
        con.execute("INSERT INTO review_highlights(id,review_id,participant_id,anchor_id,tag,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(hid,rid,participant['id'],anchor['id'],tag,t,t))
        con.execute("INSERT INTO review_events(id,review_id,participant_id,event_type,entity_type,entity_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)",(storage_values.uid('review_event_'),rid,participant['id'],'HIGHLIGHT_CREATED','highlight',hid,json.dumps({'anchor_id':anchor['id'],'marker_id':int(marker['source_marker_id']),'marker_name':tag},sort_keys=True,separators=(',',':')),t))
        row=one(con,"SELECT h.*,a.snapshot_id,a.selected_text,a.start_block_id,a.end_block_id,a.start_offset,a.end_offset FROM review_highlights h JOIN review_anchors a ON a.id=h.anchor_id WHERE h.id=?",(hid,));row['marker_name']=tag;row['marker_color']=str(marker.get('color') or '#f2c94c');return row

@router.post("/api/reviews/{rid}/participants/{participant_id}/comments")
def create_review_comment(rid:str,participant_id:str,body:ReviewCommentCreate):
    text=(body.body or '').strip()[:12000]
    if not text: raise HTTPException(400,detail="REVIEW_COMMENT_001: Comment cannot be empty.")
    with storage_mutations.tx(operation='review-comment-create') as con:
        p=project(con); _review,participant,snapshot=_review_feedback_access(con,p['id'],rid,participant_id)
        anchor=review_domain._review_anchor_from_selection(con,rid,snapshot,body.selection); t=storage_values.now(); cid=storage_values.uid('review_comment_')
        con.execute("INSERT INTO review_comments(id,review_id,participant_id,anchor_id,body,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(cid,rid,participant['id'],anchor['id'],text,t,t))
        con.execute("INSERT INTO review_events(id,review_id,participant_id,event_type,entity_type,entity_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)",(storage_values.uid('review_event_'),rid,participant['id'],'COMMENT_CREATED','comment',cid,json.dumps({'anchor_id':anchor['id'],'body':text},sort_keys=True,separators=(',',':'),ensure_ascii=False),t))
        return one(con,"SELECT c.*,a.selected_text,a.start_block_id,a.end_block_id,a.start_offset,a.end_offset FROM review_comments c JOIN review_anchors a ON a.id=c.anchor_id WHERE c.id=?",(cid,))

@router.post("/api/reviews/{rid}/participants/{participant_id}/suggestions")
def create_review_suggestion(rid:str,participant_id:str,body:ReviewSuggestionCreate):
    operation=str(body.operation or '').strip().lower()
    if operation not in {'insert','delete','replace'}:
        raise HTTPException(400,detail="REVIEW_SUGGEST_003: Suggestion operation must be insert, delete, or replace.")
    proposed=str(body.proposed_text or '')
    if len(proposed)>120000:
        raise HTTPException(400,detail="REVIEW_SUGGEST_004: Suggested text is too long.")
    if operation in {'insert','replace'} and not proposed.strip():
        raise HTTPException(400,detail="REVIEW_SUGGEST_005: This suggestion needs proposed text.")
    if operation=='delete': proposed=''
    with storage_mutations.tx(operation='review-suggestion-create') as con:
        p=project(con); review,participant,snapshot=_review_suggestion_access(con,p['id'],rid,participant_id)
        anchor=review_domain._review_anchor_from_selection(con,rid,snapshot,body.selection); sid=storage_values.uid('review_suggestion_'); t=storage_values.now()
        original=str(anchor.get('selected_text') or '')
        con.execute("INSERT INTO review_suggestions(id,review_id,participant_id,anchor_id,operation,original_text,proposed_text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(sid,rid,participant['id'],anchor['id'],operation,original,proposed,t,t))
        con.execute("INSERT INTO review_events(id,review_id,participant_id,event_type,entity_type,entity_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)",(storage_values.uid('review_event_'),rid,participant['id'],'SUGGESTION_CREATED','suggestion',sid,json.dumps({'anchor_id':anchor['id'],'operation':operation,'original_text':original,'proposed_text':proposed},sort_keys=True,separators=(',',':'),ensure_ascii=False),t))
        return one(con,"SELECT s.*,a.selected_text,a.start_block_id,a.end_block_id,a.start_offset,a.end_offset FROM review_suggestions s JOIN review_anchors a ON a.id=s.anchor_id WHERE s.id=?",(sid,))

def _review_item_table(kind:str) -> tuple[str,str]:
    normalized=str(kind or '').strip().lower()
    mapping={'highlight':('review_highlights','review_highlight'),'comment':('review_comments','review_comment'),'suggestion':('review_suggestions','review_suggestion'),'answer':('review_question_answers','review_answer'),'general':('review_general_feedback','review_general_feedback')}
    if normalized not in mapping: raise HTTPException(400,detail="REVIEW_PROCESS_001: Unsupported Review item type.")
    return mapping[normalized]

def _review_item_for_writer(con,project_id:int,rid:str,kind:str,item_id:str) -> tuple[dict,dict,str,str]:
    review=review_domain._review_for_current_project(con,project_id,rid); table,target_type=_review_item_table(kind)
    item=one(con,f"SELECT * FROM {table} WHERE id=? AND review_id=?",(item_id,rid))
    if not item: raise HTTPException(404,detail="REVIEW_PROCESS_002: Review item not found.")
    return review,item,table,target_type

@router.patch("/api/reviews/{rid}/feedback/{kind}/{item_id}/status")
def update_review_feedback_item_status(rid:str,kind:str,item_id:str,body:ReviewFeedbackStatusUpdate):
    requested=str(body.status or '').strip().lower()
    if requested not in {'open','resolved'}: raise HTTPException(400,detail="REVIEW_PROCESS_003: Status must be open or resolved.")
    if kind not in {'highlight','comment','suggestion'}: raise HTTPException(400,detail="REVIEW_PROCESS_004: Only inline Review feedback can be resolved here.")
    with storage_mutations.tx(operation='review-feedback-status') as con:
        p=project(con); _review,item,table,_target_type=_review_item_for_writer(con,p['id'],rid,kind,item_id); current=str(item.get('status') or 'open')
        if kind=='suggestion' and current not in {'open','resolved'}: raise HTTPException(409,detail='REVIEW_PROCESS_005: Accepted or rejected Suggestions cannot be reopened from the overview.')
        if current==requested: return one(con,f"SELECT * FROM {table} WHERE id=?",(item_id,))
        t=storage_values.now(); resolved_at=t if requested=='resolved' else None
        con.execute(f"UPDATE {table} SET status=?,resolved_at=?,updated_at=? WHERE id=?",(requested,resolved_at,t,item_id))
        review_domain._review_event(con,rid,'FEEDBACK_RESOLVED' if requested=='resolved' else 'FEEDBACK_REOPENED',entity_type=kind,entity_id=item_id,payload={'from':current,'to':requested},created_at=t)
        return one(con,f"SELECT * FROM {table} WHERE id=?",(item_id,))

@router.post("/api/reviews/{rid}/feedback/{kind}/{item_id}/task")
def create_task_from_review_feedback(rid:str,kind:str,item_id:str,body:ReviewTaskCreate):
    title=(body.title or '').strip()[:500]
    if not title: raise HTTPException(400,detail="TASK_001: Task cannot be empty")
    description=(body.description or '')[:12000]
    with storage_mutations.tx(operation='review-feedback-task') as con:
        p=project(con); _review,item,_table,target_type=_review_item_for_writer(con,p['id'],rid,kind,item_id); t=storage_values.now()
        archived_from=None; tid=con.execute("INSERT INTO tasks(project_id,title,description,status,archived_from_status,marker_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(p['id'],title,description,'open',archived_from,None,t,t)).lastrowid
        excerpt=''
        if kind=='comment': excerpt=str(item.get('body') or '')
        elif kind=='suggestion': excerpt=str(item.get('proposed_text') or item.get('original_text') or '')
        elif kind=='answer': excerpt=str(item.get('answer_text') or '')
        elif kind=='general': excerpt=str(item.get('body') or '')
        if not excerpt and item.get('anchor_id'):
            anchor=one(con,"SELECT selected_text FROM review_anchors WHERE id=? AND review_id=?",(item.get('anchor_id'),rid)); excerpt=str((anchor or {}).get('selected_text') or '')
        con.execute("INSERT INTO references_map(project_id,source_type,source_id,target_type,target_id,excerpt,relationship,created_at) VALUES(?,?,?,?,?,?,?,?)",(p['id'],'task',str(tid),target_type,str(item_id),excerpt[:2000],'Review feedback',t))
        anchor=one(con,"SELECT a.*,rs.round_number FROM review_anchors a JOIN review_snapshots rs ON rs.id=a.snapshot_id WHERE a.id=? AND a.review_id=?",(item.get('anchor_id'),rid)) if item.get('anchor_id') else None
        manuscript_reference=None
        if anchor:
            start=one(con,"SELECT id,chapter_id FROM manuscript_blocks WHERE project_id=? AND id=?",(p['id'],anchor.get('start_block_id')));end=one(con,"SELECT id,chapter_id FROM manuscript_blocks WHERE project_id=? AND id=?",(p['id'],anchor.get('end_block_id')))
            if start and end:
                con.execute("INSERT INTO references_map(project_id,source_type,source_id,target_type,target_id,start_block_id,end_block_id,start_offset,end_offset,excerpt,relationship,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(p['id'],'task',str(tid),'manuscript_range','range',anchor['start_block_id'],anchor['end_block_id'],int(anchor.get('start_offset') or 0),int(anchor.get('end_offset') or 0),str(anchor.get('selected_text') or '')[:2000],f'Review round {int(anchor.get("round_number") or 1)}',t))
                manuscript_reference=one(con,"SELECT * FROM references_map WHERE project_id=? AND source_type='task' AND source_id=? AND target_type='manuscript_range' ORDER BY id DESC LIMIT 1",(p['id'],str(tid)))
        if kind=='suggestion' and str(item.get('status') or '')=='open': con.execute("UPDATE review_suggestions SET status='task_created',updated_at=? WHERE id=?",(t,item_id))
        review_domain._review_event(con,rid,'TASK_CREATED',entity_type=kind,entity_id=item_id,payload={'task_id':tid,'manuscript_linked':bool(manuscript_reference)},created_at=t)
        return {'task':one(con,"SELECT * FROM tasks WHERE id=?",(tid,)),'reference':one(con,"SELECT * FROM references_map WHERE project_id=? AND source_type='task' AND source_id=? AND target_type=? AND target_id=? ORDER BY id DESC LIMIT 1",(p['id'],str(tid),target_type,str(item_id))),'manuscript_reference':manuscript_reference}

@router.get("/api/review-items/{kind}/{item_id}")
def get_review_item_context(kind:str,item_id:str):
    with storage_mutations.tx() as con:
        p=project(con); table,target_type=_review_item_table(kind)
        if kind in {'answer','general'}:
            row=one(con,f"SELECT r.project_id,i.review_id,i.id,NULL anchor_id FROM {table} i JOIN reviews r ON r.id=i.review_id WHERE i.id=? AND r.project_id=?",(item_id,p['id']))
        else:
            row=one(con,f"SELECT r.project_id,i.review_id,i.id,i.anchor_id FROM {table} i JOIN reviews r ON r.id=i.review_id WHERE i.id=? AND r.project_id=?",(item_id,p['id']))
        if not row: raise HTTPException(404,detail="REVIEW_PROCESS_002: Review item not found.")
        anchor=one(con,"SELECT a.*,rs.round_number FROM review_anchors a JOIN review_snapshots rs ON rs.id=a.snapshot_id WHERE a.id=? AND a.review_id=?",(row.get('anchor_id'),row['review_id'])) if row.get('anchor_id') else None
        current=None
        if anchor:
            start=one(con,"SELECT id,chapter_id FROM manuscript_blocks WHERE project_id=? AND id=?",(p['id'],anchor.get('start_block_id')));end=one(con,"SELECT id,chapter_id FROM manuscript_blocks WHERE project_id=? AND id=?",(p['id'],anchor.get('end_block_id')))
            if start and end: current={'target_type':'manuscript_range','target_id':'range','start_block_id':anchor['start_block_id'],'end_block_id':anchor['end_block_id'],'start_offset':int(anchor.get('start_offset') or 0),'end_offset':int(anchor.get('end_offset') or 0),'excerpt':str(anchor.get('selected_text') or ''),'target_start_chapter_id':int(start['chapter_id']),'target_end_chapter_id':int(end['chapter_id']),'target_missing':False}
        return {'review_id':row['review_id'],'item_id':row['id'],'kind':kind,'target_type':target_type,'round_number':int((anchor or {}).get('round_number') or 0),'snapshot_id':(anchor or {}).get('snapshot_id'),'manuscript_reference':current}

def _review_suggestion_for_writer(con,project_id:int,rid:str,suggestion_id:str) -> tuple[dict,dict,dict]:
    review=review_domain._review_for_current_project(con,project_id,rid)
    suggestion=one(con,"""SELECT s.*,a.snapshot_id,a.start_block_id,a.end_block_id,a.start_offset,a.end_offset,a.selected_text
      FROM review_suggestions s JOIN review_anchors a ON a.id=s.anchor_id AND a.review_id=s.review_id
      WHERE s.id=? AND s.review_id=?""",(suggestion_id,rid))
    if not suggestion: raise HTTPException(404,detail="REVIEW_SUGGEST_006: Suggestion not found.")
    snapshot_block=one(con,"SELECT * FROM review_snapshot_blocks WHERE snapshot_id=? AND source_block_id=?",(suggestion['snapshot_id'],suggestion['start_block_id']))
    return review,suggestion,snapshot_block

def _replace_inline_html_range(fragment:str,start:int,end:int,replacement:str) -> str:
    left,rest=_split_inline_html(fragment,start); selected,right=_split_inline_html(rest,max(0,end-start))
    replacement_html=_html_from_text(replacement)
    # If the replaced range is entirely inside one inline formatting stack, keep that
    # stack around the proposed text. Existing surrounding formatting is never flattened.
    if replacement and selected:
        soup=BeautifulSoup(selected,'html.parser'); first=next((node for node in soup.descendants if isinstance(node,NavigableString) and str(node)),None)
        if first is not None:
            wrappers=[]; parent=first.parent
            while parent is not None and getattr(parent,'name',None) in _ALLOWED_INLINE-{'br'}:
                wrappers.append(parent.name); parent=parent.parent
            for tag in wrappers: replacement_html=f'<{tag}>{replacement_html}</{tag}>'
    return left+replacement_html+right

@router.post("/api/reviews/{rid}/suggestions/{suggestion_id}/reject")
def reject_review_suggestion(rid:str,suggestion_id:str):
    with storage_mutations.tx(operation='review-suggestion-reject') as con:
        p=project(con); _review,suggestion,_snapshot_block=_review_suggestion_for_writer(con,p['id'],rid,suggestion_id)
        if str(suggestion.get('status') or '')!='open': raise HTTPException(409,detail="REVIEW_SUGGEST_007: Only open suggestions can be rejected.")
        t=storage_values.now(); con.execute("UPDATE review_suggestions SET status='rejected',updated_at=?,resolved_at=? WHERE id=?",(t,t,suggestion_id))
        review_domain._review_event(con,rid,'SUGGESTION_REJECTED',entity_type='suggestion',entity_id=suggestion_id,created_at=t)
        return one(con,"SELECT * FROM review_suggestions WHERE id=?",(suggestion_id,))

@router.post("/api/reviews/{rid}/suggestions/{suggestion_id}/accept")
def accept_review_suggestion(rid:str,suggestion_id:str):
    with storage_mutations.tx(operation='review-suggestion-accept') as con:
        p=project(con); _review,suggestion,_snapshot_block=_review_suggestion_for_writer(con,p['id'],rid,suggestion_id)
        if str(suggestion.get('status') or '')!='open': raise HTTPException(409,detail="REVIEW_SUGGEST_008: Only open suggestions can be accepted.")

        snapshot_rows=allrows(con,"SELECT * FROM review_snapshot_blocks WHERE snapshot_id=? ORDER BY ordinal,source_block_id",(suggestion['snapshot_id'],))
        snapshot_by_id={str(row['source_block_id']):row for row in snapshot_rows}
        start_snapshot=snapshot_by_id.get(str(suggestion['start_block_id'])); end_snapshot=snapshot_by_id.get(str(suggestion['end_block_id']))
        if not start_snapshot or not end_snapshot:
            raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_002: The original manuscript block is no longer available. The suggestion was not applied.")
        start_ordinal=int(start_snapshot['ordinal']); end_ordinal=int(end_snapshot['ordinal'])
        if start_ordinal>end_ordinal:
            raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_003: The Review selection is no longer structurally valid. The suggestion was not applied.")
        span=[row for row in snapshot_rows if start_ordinal<=int(row['ordinal'])<=end_ordinal]
        if not span or any(str(row.get('block_type') or 'paragraph')!='paragraph' for row in span):
            raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_004: This suggestion includes non-text manuscript content and cannot be applied automatically.")

        block_ids=[str(row['source_block_id']) for row in span]
        marks=','.join('?' for _ in block_ids)
        live_rows=allrows(con,f"SELECT * FROM manuscript_blocks WHERE project_id=? AND id IN ({marks})",(p['id'],*block_ids))
        live_by_id={str(row['id']):row for row in live_rows}
        if any(block_id not in live_by_id for block_id in block_ids):
            raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_002: The original manuscript block is no longer available. The suggestion was not applied.")
        chapter_ids={int(live_by_id[block_id]['chapter_id']) for block_id in block_ids}
        if len(chapter_ids)!=1:
            raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_005: This suggestion crosses Chapter boundaries. Apply it within one Chapter at a time.")
        chapter_id=next(iter(chapter_ids))
        chapter=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(chapter_id,p['id']))
        if not chapter: raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_002: The original manuscript Chapter is no longer available. The suggestion was not applied.")

        live_order=allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(chapter_id,))
        order_ids=[str(row['id']) for row in live_order]
        try:
            first_index=order_ids.index(block_ids[0]); last_index=order_ids.index(block_ids[-1])
        except ValueError:
            raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_002: The original manuscript block is no longer available. The suggestion was not applied.")
        if order_ids[first_index:last_index+1]!=block_ids:
            raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_003: The manuscript structure changed after this Review started. The suggestion was not applied automatically.")

        selected=[]
        so=int(suggestion.get('start_offset') or 0); eo=int(suggestion.get('end_offset') or 0)
        for index,snapshot_row in enumerate(span):
            live=live_by_id[str(snapshot_row['source_block_id'])]
            if int(live.get('chapter_id') or 0)!=int(snapshot_row.get('source_chapter_id') or 0) or int(live.get('tab_id') or 0)!=int(snapshot_row.get('source_tab_id') or 0):
                raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_003: The manuscript structure changed after this Review started. The suggestion was not applied automatically.")
            if str(live.get('content_hash') or '')!=str(snapshot_row.get('content_hash') or '') or str(live.get('text_content') or '')!=str(snapshot_row.get('text_content') or ''):
                raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_003: The manuscript changed after this Review started. The suggestion was not applied automatically.")
            text=str(live.get('text_content') or '')
            left=so if index==0 else 0; right=eo if index==len(span)-1 else len(text)
            if left<0 or right<left or right>len(text):
                raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_003: The Review selection no longer matches the manuscript. The suggestion was not applied automatically.")
            selected.append(text[left:right])
        if '\n'.join(selected)!=str(suggestion.get('original_text') or suggestion.get('selected_text') or ''):
            raise HTTPException(409,detail="REVIEW_SUGGEST_APPLY_003: The manuscript changed after this Review started. The suggestion was not applied automatically.")

        operation=str(suggestion.get('operation') or '')
        replacement=str(suggestion.get('proposed_text') or '')
        t=storage_values.now(); changed_ids=[]; deleting=[]

        def update_text_block(row:dict,new_text:str,new_html:str):
            new_html=sanitize_inline_html(new_html,new_text)
            reanchor_block_annotations(con,str(row['id']),new_text); reanchor_block_bookmark(con,str(row['id']),new_text)
            con.execute("UPDATE manuscript_blocks SET text_content=?,content_html=?,content_hash=?,updated_at=? WHERE id=?",(new_text,new_html,storage_values.block_content_hash(new_html,new_text,''),t,row['id']))
            changed_ids.append(str(row['id']))

        if len(span)==1:
            live=live_by_id[block_ids[0]]; old_text=str(live.get('text_content') or '')
            if operation=='insert': apply_start=apply_end=eo
            elif operation=='delete': apply_start,apply_end,replacement=so,eo,''
            else: apply_start,apply_end=so,eo
            new_text=old_text[:apply_start]+replacement+old_text[apply_end:]
            original_html=str(live.get('content_html') or _html_from_text(old_text))
            update_text_block(live,new_text,_replace_inline_html_range(original_html,apply_start,apply_end,replacement))
        elif operation=='insert':
            # A multi-block Insert is anchored by the selection, but it does not replace it.
            # Insert at the frozen end point and leave every selected paragraph intact.
            live=live_by_id[block_ids[-1]]; old_text=str(live.get('text_content') or '')
            original_html=str(live.get('content_html') or _html_from_text(old_text))
            new_text=old_text[:eo]+replacement+old_text[eo:]
            update_text_block(live,new_text,_replace_inline_html_range(original_html,eo,eo,replacement))
        else:
            # Apply a range edit across paragraph blocks without flattening structural
            # boundaries that were not fully selected. The replacement belongs at the
            # selection start; fully consumed following blocks disappear; an unselected
            # tail in the final block stays in that original block/style/Scene.
            if operation=='delete': replacement=''
            start_live=live_by_id[block_ids[0]]; start_text=str(start_live.get('text_content') or '')
            start_html=str(start_live.get('content_html') or _html_from_text(start_text))
            start_new=start_text[:so]+replacement
            start_selected_fully=(so==0)
            if start_new or not start_selected_fully:
                update_text_block(start_live,start_new,_replace_inline_html_range(start_html,so,len(start_text),replacement))
            else:
                deleting.append(start_live)

            for block_id in block_ids[1:-1]: deleting.append(live_by_id[block_id])

            end_live=live_by_id[block_ids[-1]]; end_text=str(end_live.get('text_content') or '')
            end_new=end_text[eo:]
            if end_new:
                end_html=str(end_live.get('content_html') or _html_from_text(end_text))
                update_text_block(end_live,end_new,_replace_inline_html_range(end_html,0,eo,''))
            else:
                deleting.append(end_live)

            # A Chapter must retain editable prose. If the selected span covered every
            # block and deletion would empty it, keep the start block as a blank paragraph.
            deleting_ids={str(row['id']) for row in deleting}
            surviving=one(con,f"SELECT id FROM manuscript_blocks WHERE chapter_id=? AND id NOT IN ({','.join('?' for _ in deleting_ids)}) LIMIT 1",(chapter_id,*deleting_ids)) if deleting_ids else {'id':'kept'}
            if not surviving and str(start_live['id']) in deleting_ids:
                deleting=[row for row in deleting if str(row['id'])!=str(start_live['id'])]
                update_text_block(start_live,'','')

        deleting_ids=[str(row['id']) for row in deleting]
        removed_scene_ids=[]
        if deleting_ids:
            candidate_scene_ids=sorted({int(row['scene_id']) for row in deleting if row.get('scene_id') is not None})
            marks_delete=','.join('?' for _ in deleting_ids)
            for scene_id in candidate_scene_ids:
                surviving=one(con,f"SELECT id FROM manuscript_blocks WHERE scene_id=? AND id NOT IN ({marks_delete}) LIMIT 1",(scene_id,*deleting_ids))
                if not surviving: removed_scene_ids.append(scene_id)
            if removed_scene_ids:
                _detach_map_anchors_for_removed_manuscript(con,p['id'],scene_ids=removed_scene_ids,block_ids=deleting_ids)
                _delete_polymorphic_references(con,p['id'],{'scene':removed_scene_ids})
            for row in deleting:
                rehome_deleted_block_refs(con,row,chapter_id)
                con.execute("DELETE FROM manuscript_blocks WHERE id=?",(row['id'],))
            for scene_id in removed_scene_ids: con.execute("DELETE FROM scenes WHERE id=?",(scene_id,))

        for position,row in enumerate(allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(chapter_id,))):
            con.execute("UPDATE manuscript_blocks SET position=? WHERE id=?",(position,row['id']))
        repair_stale_annotations(con,chapter_id); repair_stale_bookmark(con,chapter_id); _normalize_image_rows(con,chapter_id,t)
        con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,chapter_id)); con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,p['id']))
        storage_invariants.validate_project_invariants(con,p['id'])
        generation=int(one(con,"SELECT project_generation FROM projects WHERE id=?",(p['id'],))['project_generation'])+1
        con.execute("UPDATE review_suggestions SET status='accepted',applied_generation=?,updated_at=?,resolved_at=? WHERE id=?",(generation,t,t,suggestion_id))
        applied_ids=[block_id for block_id in block_ids if block_id not in set(deleting_ids)]
        review_domain._review_event(con,rid,'SUGGESTION_ACCEPTED',entity_type='suggestion',entity_id=suggestion_id,payload={'applied_block_ids':applied_ids,'deleted_block_ids':deleting_ids,'operation':operation},created_at=t)
        return {'ok':True,'suggestion':one(con,"SELECT * FROM review_suggestions WHERE id=?",(suggestion_id,)),'block_ids':applied_ids,'deleted_block_ids':deleting_ids,'chapter_id':chapter_id}
