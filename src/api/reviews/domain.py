from __future__ import annotations
"""Shared Review-domain primitives with no Review-route ownership."""
from fastapi import HTTPException
import hashlib
import json
import re
from auth import identity as identity_service
from api.reviews.contracts import ReviewSelectionIn
from backend.data_access import allrows, one
from backend.environment import runtime
from backend.request_context import _bound_project_id
from backend.portable_review_runtime import current_portable_review_path
from storage import values as storage_values

_REVIEW_SCOPE_TYPES={'manuscript','chapters','scenes'}

_REVIEW_PERMISSION_FIELDS=('permission_read','permission_comment','permission_suggest','permission_feedback')

def _review_name(value:str) -> str:
    clean=(value or '').strip()[:120]
    if not clean:
        raise HTTPException(400,detail="REVIEW_001: Review name cannot be empty.")
    return clean

def _review_description(value:str) -> str:
    return (value or '').strip()[:4000]

def _review_next_position(con,project_id:int) -> int:
    row=one(con,"SELECT COALESCE(MAX(position),-1)+1 position FROM reviews WHERE project_id=?",(project_id,)) or {}
    return max(0,int(row.get('position') or 0))

def _review_numbered_name(existing_names:list[str],base_name:str) -> str:
    base=_review_name(base_name); pattern=re.compile(rf'^{re.escape(base)}(?:\s+(\d+))?$',re.IGNORECASE)
    highest=0
    for existing in existing_names:
        match=pattern.match(str(existing).strip())
        if not match: continue
        highest=max(highest,int(match.group(1) or 1))
    return base if highest==0 else _review_name(f'{base} {highest+1}')

def _review_participant_name(value:str) -> str:
    clean=(value or '').strip()[:120]
    if not clean:
        raise HTTPException(400,detail="REVIEW_PARTICIPANT_001: Participant name cannot be empty.")
    return clean

def _review_participants(con,rid:str) -> list[dict]:
    return allrows(con,"SELECT id,review_id,display_name,status,started_at,completed_at,created_at,updated_at FROM review_participants WHERE review_id=? ORDER BY created_at,id",(rid,))

def _review_event(con,rid:str,event_type:str,*,participant_id:str|None=None,entity_type:str='',entity_id:str='',payload:dict|None=None,created_at:str|None=None):
    t=created_at or storage_values.now();event_payload=dict(payload or {})
    if participant_id is None and current_portable_review_path() is None:
        try:actor=runtime.identity.current_actor(_bound_project_id() or None)
        except identity_service.AuthorizationError as exc:
            raise HTTPException(403,detail='This profile is not a member of the active project.') from exc
        event_payload.setdefault('actor',actor.event_snapshot())
    con.execute("INSERT INTO review_events(id,review_id,participant_id,event_type,entity_type,entity_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)",(storage_values.uid('review_event_'),rid,participant_id,event_type,entity_type,entity_id,json.dumps(event_payload,sort_keys=True,separators=(',',':'),ensure_ascii=False),t))

def _review_current_bookmark(con,rid:str,participant_id:str,snapshot_id:str|None) -> dict|None:
    if not snapshot_id:return None
    rows=allrows(con,"SELECT event_type,payload_json FROM review_events WHERE review_id=? AND participant_id=? AND event_type IN ('BOOKMARK_SET','BOOKMARK_CLEARED') ORDER BY created_at DESC,id DESC LIMIT 32",(rid,participant_id))
    for row in rows:
        try:payload=json.loads(row.get('payload_json') or '{}')
        except Exception:continue
        if str(payload.get('snapshot_id') or '')!=str(snapshot_id):continue
        if str(row.get('event_type') or '')=='BOOKMARK_CLEARED':return None
        selection=payload.get('selection') if isinstance(payload.get('selection'),dict) else None
        return selection
    return None

def _review_participant_for_write(con,rid:str,participant_id:str,*,code_prefix:str='REVIEW_PARTICIPANT') -> dict:
    participant=one(con,"SELECT * FROM review_participants WHERE id=? AND review_id=?",(participant_id,rid))
    if not participant: raise HTTPException(404,detail="REVIEW_PARTICIPANT_004: Participant does not belong to this Review.")
    status=str(participant.get('status') or 'not_started')
    if status=='revoked': raise HTTPException(409,detail=f"{code_prefix}_REVOKED: This participant's Review access has been revoked.")
    return participant

def _review_for_current_project(con,project_id:int,rid:str):
    row=one(con,"SELECT * FROM reviews WHERE id=? AND project_id=?",(rid,project_id))
    if not row: raise HTTPException(404,detail="REVIEW_002: Review not found.")
    return row

def _questionnaire_rows(con,project_id:int) -> list[dict]:
    rows=allrows(con,"SELECT * FROM questionnaires WHERE project_id=? ORDER BY name COLLATE NOCASE,id",(project_id,))
    for row in rows:
        row['questions']=allrows(con,"SELECT * FROM questionnaire_questions WHERE questionnaire_id=? ORDER BY position,id",(row['id'],))
    return rows

def _review_questionnaires(con,rid:str) -> list[dict]:
    rows=allrows(con,"""SELECT rq.id link_id,rq.position,rq.created_at,q.*
      FROM review_questionnaires rq JOIN questionnaires q ON q.id=rq.questionnaire_id
      WHERE rq.review_id=? ORDER BY rq.position,rq.id""",(rid,))
    for row in rows:
        row['questions']=allrows(con,"SELECT * FROM questionnaire_questions WHERE questionnaire_id=? ORDER BY position,id",(row['id'],))
    return rows

def _review_selection_values(blocks:list[dict],snapshot_id:str,selection:ReviewSelectionIn) -> dict:
    sid=str(snapshot_id); start_id=str(selection.start_block_id or ''); end_id=str(selection.end_block_id or '')
    by_id={str(row['source_block_id']):row for row in blocks}
    start=by_id.get(start_id); end=by_id.get(end_id)
    if not start or not end:
        raise HTTPException(400,detail="REVIEW_ANCHOR_001: Selection is outside the frozen Review content.")
    start_ordinal=int(start['ordinal']); end_ordinal=int(end['ordinal'])
    if start_ordinal>end_ordinal:
        raise HTTPException(400,detail="REVIEW_ANCHOR_002: Selection order is invalid.")
    start_text=str(start.get('text_content') or ''); end_text=str(end.get('text_content') or '')
    so=int(selection.start_offset); eo=int(selection.end_offset)
    if so<0 or eo<0 or so>len(start_text) or eo>len(end_text):
        raise HTTPException(400,detail="REVIEW_ANCHOR_003: Selection offsets are outside the frozen Review text.")
    if start_ordinal==end_ordinal and so>=eo:
        raise HTTPException(400,detail="REVIEW_ANCHOR_004: Select some Review text first.")
    selected=[]
    for row in sorted(blocks,key=lambda x:(int(x.get('ordinal') or 0),str(x.get('source_block_id') or ''))):
        ordinal=int(row['ordinal'])
        if ordinal<start_ordinal or ordinal>end_ordinal: continue
        text=str(row.get('text_content') or '')
        left=so if ordinal==start_ordinal else 0
        right=eo if ordinal==end_ordinal else len(text)
        selected.append(text[left:right])
    selected_text='\n'.join(selected)
    if not selected_text.strip(): raise HTTPException(400,detail="REVIEW_ANCHOR_004: Select some Review text first.")
    prefix=start_text[max(0,so-96):so]; suffix=end_text[eo:eo+96]
    context_hash=hashlib.sha256(json.dumps({'snapshot_id':sid,'start_block_id':start_id,'end_block_id':end_id,'start_offset':so,'end_offset':eo,'selected_text':selected_text,'prefix':prefix,'suffix':suffix},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()
    return {'snapshot_id':sid,'start_block_id':start_id,'end_block_id':end_id,'start_offset':so,'end_offset':eo,'selected_text':selected_text,'prefix_context':prefix,'suffix_context':suffix,'context_hash':context_hash}

def _review_anchor_from_selection(con,rid:str,snapshot:dict,selection:ReviewSelectionIn) -> dict:
    sid=str(snapshot['id'])
    blocks=allrows(con,"SELECT source_block_id,ordinal,text_content FROM review_snapshot_blocks WHERE snapshot_id=? ORDER BY ordinal",(sid,))
    values=_review_selection_values(blocks,sid,selection)
    aid=storage_values.uid('review_anchor_'); t=storage_values.now()
    con.execute("INSERT INTO review_anchors(id,review_id,snapshot_id,start_block_id,end_block_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,context_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(aid,rid,values['snapshot_id'],values['start_block_id'],values['end_block_id'],values['start_offset'],values['end_offset'],values['selected_text'],values['prefix_context'],values['suffix_context'],values['context_hash'],t))
    return one(con,"SELECT * FROM review_anchors WHERE id=? AND review_id=?",(aid,rid))

def _normalize_review_permissions(permission_read=False,permission_comment=False,permission_suggest=False,permission_feedback=False) -> dict:
    read=bool(permission_read); comment=bool(permission_comment); suggest=bool(permission_suggest); feedback=bool(permission_feedback)
    if comment or suggest: read=True
    return {
        'permission_read':1 if read else 0,
        'permission_comment':1 if comment else 0,
        'permission_suggest':1 if suggest else 0,
        'permission_feedback':1 if feedback else 0,
    }

def _review_scope_ids(con,rid:str,scope_type:str) -> list[int]:
    if scope_type=='chapters':
        return [int(row['id']) for row in allrows(con,"""SELECT c.id FROM review_scope_chapters rs JOIN chapters c ON c.id=rs.chapter_id JOIN document_tabs t ON t.id=c.tab_id WHERE rs.review_id=? ORDER BY t.position,c.position,c.id""",(rid,))]
    if scope_type=='scenes':
        return [int(row['id']) for row in allrows(con,"""SELECT s.id FROM review_scope_scenes rs JOIN scenes s ON s.id=rs.scene_id LEFT JOIN document_tabs t ON t.id=s.tab_id WHERE rs.review_id=? ORDER BY COALESCE(t.position,0),s.position,s.id""",(rid,))]
    return []

def _review_markers(con,rid:str) -> list[dict]:
    return allrows(con,"""SELECT ml.id marker_id,ml.name,ml.description,ml.color,rm.position
      FROM review_markers rm JOIN review_marker_labels ml ON ml.id=rm.marker_id
      WHERE rm.review_id=? ORDER BY rm.position,rm.id""",(rid,))
