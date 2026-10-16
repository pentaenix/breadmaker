from __future__ import annotations
"""Frozen Review rounds and checkpoint-backed snapshot services."""
import hashlib
import json
from fastapi import HTTPException
from backend.data_access import allrows, one
from backend.dependencies import feature_call
from storage import assets as storage_assets
from storage import values as storage_values
from . import domain as review_domain
_capture_book_checkpoint = feature_call('checkpoints.capture', '_capture_book_checkpoint')
_capture_chapter_checkpoint = feature_call('checkpoints.capture', '_capture_chapter_checkpoint')
_checkpoint_row = feature_call('checkpoints.lineage', '_checkpoint_row')
_checkpoint_word_count = feature_call('checkpoints.capture', '_checkpoint_word_count')
_encode_checkpoint_snapshot = feature_call('checkpoints.capture', '_encode_checkpoint_snapshot')
_insert_checkpoint = feature_call('checkpoints.capture', '_insert_checkpoint')

def _review_snapshot_summary(con,rid:str,snapshot_id:str|None=None) -> dict|None:
    where="rs.review_id=?";args=[rid]
    if snapshot_id is not None:where+=" AND rs.id=?";args.append(snapshot_id)
    row=one(con,f"""SELECT rs.*,
      (SELECT COUNT(*) FROM review_snapshot_chapters c WHERE c.snapshot_id=rs.id) chapter_count,
      (SELECT COUNT(*) FROM review_snapshot_scenes s WHERE s.snapshot_id=rs.id) scene_count,
      (SELECT COUNT(*) FROM review_snapshot_blocks b WHERE b.snapshot_id=rs.id) block_count,
      (SELECT COUNT(*) FROM review_snapshot_questions q WHERE q.snapshot_id=rs.id) question_count,
      (SELECT COUNT(*) FROM review_snapshot_markers m WHERE m.snapshot_id=rs.id) marker_count
      FROM review_snapshots rs WHERE {where} ORDER BY CASE rs.status WHEN 'open' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,rs.round_number DESC LIMIT 1""",tuple(args))
    if not row:return None
    for field in review_domain._REVIEW_PERMISSION_FIELDS: row[field]=bool(row.get(field))
    row['checkpoints']=_review_checkpoint_rows(con,str(row['id']))
    row['checkpoint_ids']=[int(link['checkpoint_id']) for link in row['checkpoints'] if link.get('checkpoint_id') is not None]
    row['checkpoint_count']=len(row['checkpoints'])
    state=_review_checkpoint_update_state(con,rid,row)
    row['checkpoint_update_allowed']=state['allowed']
    row['checkpoint_update_blocker']=state['blocker']
    row['checkpoint_update_inline_count']=state['inline_count']
    row['checkpoint_update_bookmark_count']=state['bookmark_count']
    row['checkpoint_update_portable_exported']=state['portable_exported']
    return row

def _review_rounds(con,rid:str) -> list[dict]:
    rows=allrows(con,"""SELECT rs.*,
      (SELECT COUNT(*) FROM review_snapshot_blocks b WHERE b.snapshot_id=rs.id) block_count,
      (SELECT COUNT(*) FROM review_snapshot_questions q WHERE q.snapshot_id=rs.id) question_count,
      (SELECT COUNT(*) FROM review_highlights h JOIN review_anchors a ON a.id=h.anchor_id AND a.review_id=h.review_id WHERE h.review_id=rs.review_id AND a.snapshot_id=rs.id) highlight_count,
      (SELECT COUNT(*) FROM review_comments c JOIN review_anchors a ON a.id=c.anchor_id AND a.review_id=c.review_id WHERE c.review_id=rs.review_id AND a.snapshot_id=rs.id) comment_count,
      (SELECT COUNT(*) FROM review_suggestions s JOIN review_anchors a ON a.id=s.anchor_id AND a.review_id=s.review_id WHERE s.review_id=rs.review_id AND a.snapshot_id=rs.id) suggestion_count,
      (SELECT COUNT(*) FROM review_question_answers a WHERE a.review_id=rs.review_id AND a.snapshot_id=rs.id) answer_count,
      (SELECT COUNT(*) FROM review_general_feedback g WHERE g.review_id=rs.review_id AND g.snapshot_id=rs.id AND TRIM(COALESCE(g.body,''))<>'') general_feedback_count
      FROM review_snapshots rs WHERE rs.review_id=? ORDER BY rs.round_number DESC""",(rid,))
    for row in rows:
        for field in review_domain._REVIEW_PERMISSION_FIELDS:row[field]=bool(row.get(field))
        row['checkpoints']=_review_checkpoint_rows(con,str(row['id']))
        row['checkpoint_ids']=[int(link['checkpoint_id']) for link in row['checkpoints'] if link.get('checkpoint_id') is not None]
        row['checkpoint_count']=len(row['checkpoints'])
    return rows

def _review_snapshot_payload(con,rid:str,snapshot_id:str|None=None) -> dict|None:
    summary=_review_snapshot_summary(con,rid,snapshot_id)
    if not summary:return None
    sid=str(summary['id'])
    summary['chapters']=allrows(con,"SELECT source_chapter_id,source_tab_id,ordinal,position,title,is_implicit,description FROM review_snapshot_chapters WHERE snapshot_id=? ORDER BY ordinal,id",(sid,))
    summary['scenes']=allrows(con,"SELECT source_scene_id,source_tab_id,ordinal,position,title,description FROM review_snapshot_scenes WHERE snapshot_id=? ORDER BY ordinal,id",(sid,))
    summary['blocks']=allrows(con,"SELECT source_block_id,source_tab_id,source_chapter_id,source_scene_id,ordinal,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash FROM review_snapshot_blocks WHERE snapshot_id=? ORDER BY ordinal,id",(sid,))
    summary['questions']=allrows(con,"SELECT id,source_questionnaire_id,source_question_id,questionnaire_name,position,prompt,required FROM review_snapshot_questions WHERE snapshot_id=? ORDER BY position,id",(sid,))
    summary['markers']=allrows(con,"SELECT source_marker_id,position,name,description,color FROM review_snapshot_markers WHERE snapshot_id=? ORDER BY position,id",(sid,))
    return summary

def _review_snapshot_source(con,project_id:int,review:dict) -> tuple[list[dict],list[dict],list[dict]]:
    scope_type=str(review.get('scope_type') or 'manuscript')
    scope_ids=review_domain._review_scope_ids(con,str(review['id']),scope_type)
    chapter_ids:list[int]=[]
    scene_ids:list[int]=[]
    if scope_type=='manuscript':
        chapters=allrows(con,"""SELECT c.id source_chapter_id,c.tab_id source_tab_id,c.position,c.title,c.description,c.is_implicit
          FROM chapters c JOIN document_tabs t ON t.id=c.tab_id
          WHERE c.project_id=? AND t.is_manuscript=1 ORDER BY t.position,c.position,c.id""",(project_id,))
        chapter_ids=[int(row['source_chapter_id']) for row in chapters]
        blocks=allrows(con,"""SELECT b.id source_block_id,b.tab_id source_tab_id,b.chapter_id source_chapter_id,b.scene_id source_scene_id,b.position,b.block_type,b.semantic_style,b.content_html,b.text_content,b.image_path,b.image_width,b.image_footer,b.image_description,b.image_row_id,b.content_hash
          FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id JOIN document_tabs t ON t.id=c.tab_id
          WHERE b.project_id=? AND t.is_manuscript=1 ORDER BY t.position,c.position,b.position,b.id""",(project_id,))
        scene_ids=[]
        for row in blocks:
            if row.get('source_scene_id') is not None and int(row['source_scene_id']) not in scene_ids:scene_ids.append(int(row['source_scene_id']))
    elif scope_type=='chapters':
        marks=','.join('?' for _ in scope_ids)
        chapters=allrows(con,f"""SELECT c.id source_chapter_id,c.tab_id source_tab_id,c.position,c.title,c.description,c.is_implicit
          FROM chapters c JOIN document_tabs t ON t.id=c.tab_id
          WHERE c.project_id=? AND c.id IN ({marks}) ORDER BY t.position,c.position,c.id""",(project_id,*scope_ids))
        chapter_ids=[int(row['source_chapter_id']) for row in chapters]
        blocks=allrows(con,f"""SELECT b.id source_block_id,b.tab_id source_tab_id,b.chapter_id source_chapter_id,b.scene_id source_scene_id,b.position,b.block_type,b.semantic_style,b.content_html,b.text_content,b.image_path,b.image_width,b.image_footer,b.image_description,b.image_row_id,b.content_hash
          FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id JOIN document_tabs t ON t.id=c.tab_id
          WHERE b.project_id=? AND b.chapter_id IN ({marks}) ORDER BY t.position,c.position,b.position,b.id""",(project_id,*scope_ids))
        scene_ids=[]
        for row in blocks:
            if row.get('source_scene_id') is not None and int(row['source_scene_id']) not in scene_ids:scene_ids.append(int(row['source_scene_id']))
    else:
        marks=','.join('?' for _ in scope_ids)
        blocks=allrows(con,f"""SELECT b.id source_block_id,b.tab_id source_tab_id,b.chapter_id source_chapter_id,b.scene_id source_scene_id,b.position,b.block_type,b.semantic_style,b.content_html,b.text_content,b.image_path,b.image_width,b.image_footer,b.image_description,b.image_row_id,b.content_hash
          FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id JOIN document_tabs t ON t.id=c.tab_id
          WHERE b.project_id=? AND b.scene_id IN ({marks}) AND t.is_manuscript=1 ORDER BY t.position,c.position,b.position,b.id""",(project_id,*scope_ids))
        if not blocks:raise HTTPException(409,detail="REVIEW_SNAPSHOT_003: Selected Review scope has no manuscript content to freeze.")
        for row in blocks:
            cid=int(row['source_chapter_id'])
            if cid not in chapter_ids:chapter_ids.append(cid)
        cmarks=','.join('?' for _ in chapter_ids)
        chapters=allrows(con,f"""SELECT c.id source_chapter_id,c.tab_id source_tab_id,c.position,c.title,c.description,c.is_implicit
          FROM chapters c JOIN document_tabs t ON t.id=c.tab_id
          WHERE c.project_id=? AND c.id IN ({cmarks}) ORDER BY t.position,c.position,c.id""",(project_id,*chapter_ids))
        scene_ids=list(scope_ids)
    scenes=[]
    if scene_ids:
        smarks=','.join('?' for _ in scene_ids)
        scenes=allrows(con,f"""SELECT s.id source_scene_id,s.tab_id source_tab_id,s.position,s.title,s.description
          FROM scenes s WHERE s.project_id=? AND s.id IN ({smarks}) ORDER BY s.tab_id,s.position,s.id""",(project_id,*scene_ids))
    for index,row in enumerate(chapters):row['ordinal']=index
    for index,row in enumerate(scenes):row['ordinal']=index
    for index,row in enumerate(blocks):row['ordinal']=index
    return chapters,scenes,blocks

def _review_checkpoint_rows(con,snapshot_id:str) -> list[dict]:
    rows=allrows(con,"""SELECT rsc.id relation_id,rsc.snapshot_id,rsc.checkpoint_id,rsc.ordinal,rsc.source_chapter_id,rsc.created_at,
      c.scope,c.name,c.description,c.source_chapter_title,c.word_count,c.updated_at checkpoint_updated_at
      FROM review_snapshot_checkpoints rsc LEFT JOIN checkpoints c ON c.id=rsc.checkpoint_id
      WHERE rsc.snapshot_id=? ORDER BY rsc.ordinal,rsc.id""",(snapshot_id,))
    return rows

def _review_checkpoint_specs(con,project_id:int,review:dict,chapters:list[dict],round_number:int) -> list[dict]:
    scope_type=str(review.get('scope_type') or 'manuscript')
    base=f"{str(review.get('name') or 'Review')} · R{round_number}"
    # Full-manuscript Reviews always preserve a Book checkpoint. Scoped Reviews use
    # one Chapter checkpoint per covered Chapter up to three Chapters; larger scopes
    # fall back to one Book checkpoint.
    if scope_type=='manuscript' or len(chapters)>3:
        return [{'snapshot':_capture_book_checkpoint(con,project_id),'name':base,'source_chapter_id':None}]
    if not chapters:
        raise HTTPException(409,detail="REVIEW_CHECKPOINT_008: Review scope does not contain a Chapter that can be checkpointed.")
    specs=[]
    for row in chapters:
        chapter_id=int(row['source_chapter_id'])
        title=str(row.get('title') or f'Chapter {chapter_id}')
        specs.append({'snapshot':_capture_chapter_checkpoint(con,project_id,chapter_id),'name':f"{base} · {title}",'source_chapter_id':chapter_id})
    return specs

def _create_review_snapshot(con,project_row:dict,review:dict) -> dict:
    rid=str(review['id']);current=str(review.get('status') or 'draft')
    if current=='archived':raise HTTPException(409,detail="REVIEW_ROUND_003: Restore this Review before starting a new round.")
    chapters,scenes,blocks=_review_snapshot_source(con,int(project_row['id']),review)
    attached=review_domain._review_questionnaires(con,rid) if bool(review.get('permission_feedback')) else []
    frozen_questions=[];position=0
    for questionnaire in attached:
        for question in questionnaire.get('questions') or []:
            frozen_questions.append({'source_questionnaire_id':str(questionnaire['id']),'source_question_id':str(question['id']),'questionnaire_name':str(questionnaire.get('name') or ''),'position':position,'prompt':str(question.get('prompt') or ''),'required':bool(question.get('required'))});position+=1
    frozen_markers=[{'source_marker_id':int(row['marker_id']),'position':int(row.get('position') or 0),'name':str(row.get('name') or ''),'description':str(row.get('description') or ''),'color':str(row.get('color') or '')} for row in review_domain._review_markers(con,rid)] if bool(review.get('permission_comment')) else []
    source_generation=int(project_row.get('project_generation') or 0);scope_type=str(review.get('scope_type') or 'manuscript');permissions={field:bool(review.get(field)) for field in review_domain._REVIEW_PERMISSION_FIELDS}
    content_hash=storage_values.review_snapshot_content_hash(rid,source_generation,scope_type,permissions,chapters,scenes,blocks,frozen_questions,frozen_markers)
    round_number=int((one(con,"SELECT COALESCE(MAX(round_number),0)+1 n FROM review_snapshots WHERE review_id=?",(rid,)) or {}).get('n') or 1)
    checkpoint_specs=_review_checkpoint_specs(con,int(project_row['id']),review,chapters,round_number)
    sid=storage_values.uid('review_snapshot_');t=storage_values.now();db_permissions={field:int(value) for field,value in permissions.items()}
    # Starting another round is intentionally non-destructive. Any currently live/paused
    # round becomes Closed history, but it can be reopened later and keeps all feedback/checkpoints.
    for active in allrows(con,"SELECT id,round_number,status FROM review_snapshots WHERE review_id=? AND status IN ('open','paused') ORDER BY round_number",(rid,)):
        con.execute("UPDATE review_snapshots SET status='closed',closed_at=? WHERE id=?",(t,active['id']))
        review_domain._review_event(con,rid,'REVIEW_ROUND_CLOSED',entity_type='snapshot',entity_id=str(active['id']),payload={'round_number':active.get('round_number'),'from':active.get('status'),'to':'closed','reason':'new_round'},created_at=t)
    con.execute("INSERT INTO review_snapshots(id,review_id,round_number,status,source_generation,scope_type,permission_read,permission_comment,permission_suggest,permission_feedback,content_hash,created_at) VALUES(?,?,?, 'open',?,?,?,?,?,?,?,?)",(sid,rid,round_number,source_generation,scope_type,db_permissions['permission_read'],db_permissions['permission_comment'],db_permissions['permission_suggest'],db_permissions['permission_feedback'],content_hash,t))
    checkpoint_ids=[]
    for ordinal,spec in enumerate(checkpoint_specs):
        checkpoint_id=_insert_checkpoint(con,int(project_row['id']),spec['snapshot'],spec['name'],'')
        checkpoint_ids.append(checkpoint_id)
        con.execute("INSERT INTO review_snapshot_checkpoints(snapshot_id,checkpoint_id,ordinal,source_chapter_id,created_at) VALUES(?,?,?,?,?)",(sid,checkpoint_id,ordinal,spec.get('source_chapter_id'),t))
    con.executemany("INSERT INTO review_snapshot_chapters(snapshot_id,source_chapter_id,source_tab_id,ordinal,position,title,is_implicit,description,created_at) VALUES(?,?,?,?,?,?,?,?,?)",[(sid,int(row['source_chapter_id']),row.get('source_tab_id'),int(row['ordinal']),int(row.get('position') or 0),str(row.get('title') or ''),int(row.get('is_implicit') or 0),str(row.get('description') or ''),t) for row in chapters])
    con.executemany("INSERT INTO review_snapshot_scenes(snapshot_id,source_scene_id,source_tab_id,ordinal,position,title,description,created_at) VALUES(?,?,?,?,?,?,?,?)",[(sid,int(row['source_scene_id']),row.get('source_tab_id'),int(row['ordinal']),int(row.get('position') or 0),str(row.get('title') or ''),str(row.get('description') or ''),t) for row in scenes])
    con.executemany("INSERT INTO review_snapshot_blocks(snapshot_id,source_block_id,source_tab_id,source_chapter_id,source_scene_id,ordinal,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[(sid,str(row['source_block_id']),row.get('source_tab_id'),int(row['source_chapter_id']),row.get('source_scene_id'),int(row['ordinal']),float(row.get('position') or 0),str(row.get('block_type') or 'paragraph'),str(row.get('semantic_style') or 'body'),str(row.get('content_html') or ''),str(row.get('text_content') or ''),str(row.get('image_path') or ''),max(5,min(100,int(row.get('image_width') or 100))),str(row.get('image_footer') or ''),str(row.get('image_description') or ''),str(row.get('image_row_id') or ''),str(row.get('content_hash') or ''),t) for row in blocks])
    con.executemany("INSERT INTO review_snapshot_questions(id,snapshot_id,source_questionnaire_id,source_question_id,questionnaire_name,position,prompt,required,created_at) VALUES(?,?,?,?,?,?,?,?,?)",[(storage_values.uid('review_snapshot_question_'),sid,row['source_questionnaire_id'],row['source_question_id'],row['questionnaire_name'],row['position'],row['prompt'],1 if row.get('required') else 0,t) for row in frozen_questions])
    con.executemany("INSERT INTO review_snapshot_markers(snapshot_id,source_marker_id,position,name,description,color,created_at) VALUES(?,?,?,?,?,?,?)",[(sid,row['source_marker_id'],row['position'],row['name'],row['description'],row['color'],t) for row in frozen_markers])
    con.execute("UPDATE reviews SET status='open',started_at=COALESCE(started_at,?),closed_at=NULL,updated_at=? WHERE id=?",(t,t,rid))
    con.execute("UPDATE review_participants SET status=CASE WHEN status='revoked' THEN 'revoked' ELSE 'not_started' END,started_at=NULL,completed_at=NULL,updated_at=? WHERE review_id=?",(t,rid))
    review_domain._review_event(con,rid,'REVIEW_ROUND_STARTED',entity_type='snapshot',entity_id=sid,payload={'round_number':round_number,'source_generation':source_generation,'snapshot_id':sid,'checkpoint_ids':checkpoint_ids},created_at=t)
    return _review_snapshot_payload(con,rid,sid)

def _review_checkpoint_update_state(con,rid:str,snapshot:dict|None) -> dict:
    if not snapshot:
        return {'allowed':False,'blocker':'missing','inline_count':0,'bookmark_count':0,'portable_exported':False}
    sid=str(snapshot.get('id') or '')
    links=_review_checkpoint_rows(con,sid)
    if not links or any(row.get('checkpoint_id') is None or not row.get('scope') for row in links):
        return {'allowed':False,'blocker':'missing','inline_count':0,'bookmark_count':0,'portable_exported':False}
    # Chapter checkpoints must still point at live Chapters. Manual Checkpoint deletion
    # or source deletion makes the baseline incomplete rather than silently replacing it.
    project_row=one(con,"SELECT r.project_id FROM reviews r WHERE r.id=?",(rid,)) or {}
    pid=int(project_row.get('project_id') or 0)
    for row in links:
        source_chapter_id=row.get('source_chapter_id')
        if source_chapter_id is not None and not one(con,"SELECT id FROM chapters WHERE id=? AND project_id=?",(int(source_chapter_id),pid)):
            return {'allowed':False,'blocker':'missing','inline_count':0,'bookmark_count':0,'portable_exported':False}
    inline_count=int((one(con,"SELECT COUNT(*) n FROM review_anchors WHERE review_id=? AND snapshot_id=?",(rid,sid)) or {}).get('n') or 0)
    bookmark_count=0
    for participant in review_domain._review_participants(con,rid):
        if review_domain._review_current_bookmark(con,rid,str(participant['id']),sid): bookmark_count+=1
    portable_exported=False
    for event in allrows(con,"SELECT payload_json FROM review_events WHERE review_id=? AND event_type='BREADREVIEW_EXPORTED' ORDER BY created_at DESC,id DESC",(rid,)):
        try:payload=json.loads(event.get('payload_json') or '{}')
        except Exception:continue
        if str(payload.get('snapshot_id') or '')==sid:
            portable_exported=True;break
    scope_changed=False
    review=one(con,"SELECT scope_type FROM reviews WHERE id=?",(rid,)) or {}
    snapshot_scope=str(snapshot.get('scope_type') or 'manuscript');current_scope=str(review.get('scope_type') or 'manuscript')
    if snapshot_scope!=current_scope:
        scope_changed=True
    elif snapshot_scope=='chapters':
        frozen={int(row['source_chapter_id']) for row in allrows(con,"SELECT source_chapter_id FROM review_snapshot_chapters WHERE snapshot_id=?",(sid,))}
        scope_changed=frozen!=set(review_domain._review_scope_ids(con,rid,'chapters'))
    elif snapshot_scope=='scenes':
        frozen={int(row['source_scene_id']) for row in allrows(con,"SELECT source_scene_id FROM review_snapshot_scenes WHERE snapshot_id=?",(sid,))}
        current_scenes=set(review_domain._review_scope_ids(con,rid,'scenes'))
        scope_changed=frozen!=current_scenes
        if not scope_changed:
            frozen_chapters={int(row['source_chapter_id']) for row in allrows(con,"SELECT source_chapter_id FROM review_snapshot_chapters WHERE snapshot_id=?",(sid,))}
            current_chapters=set()
            if current_scenes:
                marks=','.join('?' for _ in current_scenes)
                current_chapters={int(row['chapter_id']) for row in allrows(con,f"SELECT DISTINCT chapter_id FROM manuscript_blocks WHERE project_id=? AND scene_id IN ({marks}) AND chapter_id IS NOT NULL",(pid,*sorted(current_scenes)))}
            scope_changed=frozen_chapters!=current_chapters
    blocker=''
    if inline_count: blocker='inline_feedback'
    elif bookmark_count: blocker='bookmarks'
    elif portable_exported: blocker='portable_export'
    elif scope_changed: blocker='scope_changed'
    return {'allowed':not blocker,'blocker':blocker,'inline_count':inline_count,'bookmark_count':bookmark_count,'portable_exported':portable_exported,'scope_changed':scope_changed}

def _replace_checkpoint_snapshot(con,pid:int,checkpoint_id:int,snapshot:dict) -> None:
    row=_checkpoint_row(con,pid,int(checkpoint_id))
    blob,digest=_encode_checkpoint_snapshot(snapshot);t=storage_values.now();tab=snapshot.get('tab') or {};chapter=snapshot.get('chapter') or {}
    con.execute("DELETE FROM checkpoint_asset_refs WHERE checkpoint_id=?",(int(checkpoint_id),))
    con.execute("""UPDATE checkpoints SET scope=?,source_tab_id=?,source_tab_title=?,source_tab_created_at=?,source_chapter_id=?,source_chapter_title=?,source_chapter_created_at=?,source_chapter_position=?,current_source_chapter_id=?,current_source_chapter_created_at=?,snapshot_blob=?,snapshot_hash=?,word_count=?,include_scenes=?,include_markup=?,include_notes=?,updated_at=? WHERE id=? AND project_id=?""",
      (snapshot['scope'],tab.get('id'),str(tab.get('title') or ''),str(tab.get('created_at') or ''),chapter.get('id'),str(chapter.get('title') or ''),str(chapter.get('created_at') or ''),chapter.get('position'),chapter.get('id'),str(chapter.get('created_at') or ''),blob,digest,_checkpoint_word_count(snapshot),int(bool(snapshot.get('include_scenes',True))),int(bool(snapshot.get('include_markup',True))),int(bool(snapshot.get('include_notes',True))),t,int(checkpoint_id),pid))
    storage_assets.capture_checkpoint_assets(con,int(checkpoint_id),snapshot)
    storage_assets.cleanup_checkpoint_assets(con)

def _refresh_review_snapshot_manuscript(con,project_row:dict,review:dict,snapshot:dict) -> dict:
    rid=str(review['id']);sid=str(snapshot['id'])
    state=_review_checkpoint_update_state(con,rid,snapshot)
    if not state['allowed']:
        codes={'inline_feedback':'REVIEW_CHECKPOINT_003','bookmarks':'REVIEW_CHECKPOINT_004','portable_export':'REVIEW_CHECKPOINT_005','missing':'REVIEW_CHECKPOINT_006','scope_changed':'REVIEW_CHECKPOINT_007'}
        messages={'inline_feedback':'This Review round already contains inline feedback.','bookmarks':'This Review round contains reviewer bookmarks.','portable_export':'A portable copy of this Review round has already been exported.','missing':'This Review round is not linked to a Checkpoint.','scope_changed':'This Review round scope has changed since it was started.'}
        raise HTTPException(409,detail=f"{codes.get(state['blocker'],'REVIEW_CHECKPOINT_006')}: {messages.get(state['blocker'],'This Review Checkpoint cannot be updated.')}")
    chapters,scenes,blocks=_review_snapshot_source(con,int(project_row['id']),review)
    frozen_questions=allrows(con,"SELECT id,source_questionnaire_id,source_question_id,questionnaire_name,position,prompt,required FROM review_snapshot_questions WHERE snapshot_id=? ORDER BY position,id",(sid,))
    source_generation=int(project_row.get('project_generation') or 0);scope_type=str(snapshot.get('scope_type') or review.get('scope_type') or 'manuscript');permissions={field:bool(snapshot.get(field)) for field in review_domain._REVIEW_PERMISSION_FIELDS}
    hash_questions=[{'source_questionnaire_id':str(q.get('source_questionnaire_id') or ''),'source_question_id':str(q.get('source_question_id') or ''),'questionnaire_name':str(q.get('questionnaire_name') or ''),'position':int(q.get('position') or 0),'prompt':str(q.get('prompt') or ''),'required':bool(q.get('required'))} for q in frozen_questions]
    frozen_markers=allrows(con,"SELECT source_marker_id,position,name,description,color FROM review_snapshot_markers WHERE snapshot_id=? ORDER BY position,id",(sid,))
    content_hash=storage_values.review_snapshot_content_hash(rid,source_generation,scope_type,permissions,chapters,scenes,blocks,hash_questions,frozen_markers);t=storage_values.now()
    con.execute("DELETE FROM review_snapshot_chapters WHERE snapshot_id=?",(sid,));con.execute("DELETE FROM review_snapshot_scenes WHERE snapshot_id=?",(sid,));con.execute("DELETE FROM review_snapshot_blocks WHERE snapshot_id=?",(sid,))
    con.executemany("INSERT INTO review_snapshot_chapters(snapshot_id,source_chapter_id,source_tab_id,ordinal,position,title,is_implicit,description,created_at) VALUES(?,?,?,?,?,?,?,?,?)",[(sid,int(row['source_chapter_id']),row.get('source_tab_id'),int(row['ordinal']),int(row.get('position') or 0),str(row.get('title') or ''),int(row.get('is_implicit') or 0),str(row.get('description') or ''),t) for row in chapters])
    con.executemany("INSERT INTO review_snapshot_scenes(snapshot_id,source_scene_id,source_tab_id,ordinal,position,title,description,created_at) VALUES(?,?,?,?,?,?,?,?)",[(sid,int(row['source_scene_id']),row.get('source_tab_id'),int(row['ordinal']),int(row.get('position') or 0),str(row.get('title') or ''),str(row.get('description') or ''),t) for row in scenes])
    con.executemany("INSERT INTO review_snapshot_blocks(snapshot_id,source_block_id,source_tab_id,source_chapter_id,source_scene_id,ordinal,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[(sid,str(row['source_block_id']),row.get('source_tab_id'),int(row['source_chapter_id']),row.get('source_scene_id'),int(row['ordinal']),float(row.get('position') or 0),str(row.get('block_type') or 'paragraph'),str(row.get('semantic_style') or 'body'),str(row.get('content_html') or ''),str(row.get('text_content') or ''),str(row.get('image_path') or ''),max(5,min(100,int(row.get('image_width') or 100))),str(row.get('image_footer') or ''),str(row.get('image_description') or ''),str(row.get('image_row_id') or ''),str(row.get('content_hash') or ''),t) for row in blocks])
    con.execute("UPDATE review_snapshots SET source_generation=?,content_hash=? WHERE id=?",(source_generation,content_hash,sid))
    checkpoint_ids=[]
    for link in _review_checkpoint_rows(con,sid):
        checkpoint_id=int(link['checkpoint_id']);source_chapter_id=link.get('source_chapter_id')
        checkpoint_snapshot=_capture_book_checkpoint(con,int(project_row['id'])) if source_chapter_id is None else _capture_chapter_checkpoint(con,int(project_row['id']),int(source_chapter_id))
        _replace_checkpoint_snapshot(con,int(project_row['id']),checkpoint_id,checkpoint_snapshot)
        checkpoint_ids.append(checkpoint_id)
    review_domain._review_event(con,rid,'REVIEW_CHECKPOINT_UPDATED',entity_type='snapshot',entity_id=sid,payload={'round_number':int(snapshot.get('round_number') or 1),'snapshot_id':sid,'checkpoint_ids':checkpoint_ids,'source_generation':source_generation},created_at=t)
    return _review_snapshot_payload(con,rid,sid)
