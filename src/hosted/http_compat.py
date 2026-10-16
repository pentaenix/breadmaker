from __future__ import annotations

"""Cloud-native compatibility routes for the Milestone-5 hosted product surface.

The core hosted router owns project/runtime/bootstrap primitives.  This module fills
interactive product actions that historically lived in local SQLite route modules.
It intentionally depends only on the hosted domain/runtime boundary; importing it
must never instantiate Workspace, local storage, or local home-directory state.
"""

import gzip
import html
import io
import json
import re
import secrets
import zipfile
from collections import defaultdict
from typing import Any, Mapping

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from backend.environment import STORY_TEMPLATES_CONFIG, runtime
from hosted.firestore.contracts import DocumentNotFound
from hosted.firestore.ids import require_opaque_id
from hosted.firestore.model_registry import MODELS
from hosted.http_api import (
    _body, _create, _delete, _documents, _domain, _error, _get, _json_public,
    _list, _pid, _project_snapshot, _uid, _update,
)

router=APIRouter()


# A deliberately conservative bound: no tenant-wide collection-group query.
# A wider project requires a separately reviewed indexed lookup, not a silent
# fallback to scanning other authors' records.
_MAX_NESTED_LOOKUP_ROWS = 2_048


def _locate(logical:str,pid:str,entity_id:str) -> dict[str,str]:
    """Find a nested record ONLY under the requested project subtree.

    Every ancestor collection read is parent-scoped. A matching record is still
    authorized through the existing _get(logical, ids) policy before return.
    Never query a global collection-group to resolve a browser-controlled ID.
    """
    model = MODELS[logical]
    template = model.path_template.split('/')
    if len(template) < 4 or len(template) % 2 or template[:2] != ['projects', '{project_id}']:
        raise HTTPException(404, detail='HOSTED_ENTITY_NOT_FOUND')
    pid = require_opaque_id(str(pid), label='project_id')
    final_id = template[-1]
    if not (final_id.startswith('{') and final_id.endswith('}')):
        raise HTTPException(404, detail='HOSTED_ENTITY_NOT_FOUND')
    id_field = final_id[1:-1]
    parents = [(('projects', pid), {'project_id': pid})]
    documents = _documents()
    remaining = _MAX_NESTED_LOOKUP_ROWS
    # Expand only canonical in-project ancestors, never a collection-group.
    for offset in range(2, len(template) - 2, 2):
        collection, placeholder = template[offset:offset+2]
        if not (placeholder.startswith('{') and placeholder.endswith('}')):
            raise HTTPException(404, detail='HOSTED_ENTITY_NOT_FOUND')
        expanded = []
        for prefix, ids in parents:
            rows = documents.query_collection(prefix + (collection,))
            remaining -= len(rows)
            if remaining < 0:
                raise HTTPException(409, detail='HOSTED_ENTITY_LOOKUP_REQUIRES_PARENT')
            for row in rows:
                if len(row.path) == len(prefix) + 2 and row.path[:-2] == prefix and row.path[-2] == collection:
                    expanded.append((row.path, {**ids, placeholder[1:-1]: row.path[-1]}))
        parents = expanded
        if not parents:
            break
    for prefix, ids in parents:
        remaining -= 1
        if remaining < 0:
            raise HTTPException(409, detail='HOSTED_ENTITY_LOOKUP_REQUIRES_PARENT')
        # Filter within this parent collection. No read outside projects/<pid>.
        rows = documents.query_collection(prefix + (template[-2],), field=id_field, value=str(entity_id))
        remaining -= len(rows)
        if remaining < 0:
            raise HTTPException(409, detail='HOSTED_ENTITY_LOOKUP_REQUIRES_PARENT')
        for row in rows:
            if row.path == prefix + (template[-2], str(entity_id)):
                candidate = {**ids, id_field: str(entity_id)}
                _get(logical, candidate)  # real authorization, also checks parent scope
                return candidate
    raise HTTPException(404, detail='HOSTED_ENTITY_NOT_FOUND')


def _all_blocks(pid:str) -> list[dict[str,Any]]:
    return _list('manuscript_block',{'project_id':pid})


def _sort_position(rows:list[dict[str,Any]]) -> list[dict[str,Any]]:
    return sorted(rows,key=lambda row:(int(row.get('position') or 0),str(row.get('id') or '')))


def _regex(query:str,case_sensitive:bool=False,whole_word:bool=False):
    if not query:raise HTTPException(400,detail='Find text cannot be empty')
    if len(query)>10000:raise HTTPException(400,detail='Find text is too long')
    pattern=re.escape(query)
    if whole_word:pattern=r'(?<!\w)'+pattern+r'(?!\w)'
    return re.compile(pattern,0 if case_sensitive else re.I)


# ---------------------------------------------------------------------------
# Hosted account/project compatibility
# ---------------------------------------------------------------------------

@router.post('/api/profiles')
async def hosted_profile_create_disabled(request:Request):
    raise HTTPException(409,detail='HOSTED_ACCOUNT_PROFILE_IS_PROVIDER_MANAGED')

@router.post('/api/profiles/{user_id}/activate')
def hosted_profile_activate(user_id:str):
    if str(user_id)!=str(runtime.identity.active_profile_id()):raise HTTPException(404,detail='PROFILE_NOT_FOUND')
    return {'ok':True,'active_profile_id':runtime.identity.active_profile_id()}

@router.post('/api/danger/challenge')
def hosted_danger_challenge():
    # Destructive cloud purge semantics are frozen in Milestone 9. Project tombstoning
    # already exists; global account-data deletion must not emulate local disk deletion.
    raise HTTPException(409,detail='HOSTED_ACCOUNT_DELETION_UNAVAILABLE_UNTIL_M9')

@router.post('/api/danger/delete-all-data')
def hosted_delete_all_data():
    raise HTTPException(409,detail='HOSTED_ACCOUNT_DELETION_UNAVAILABLE_UNTIL_M9')

@router.patch('/api/project/title')
async def hosted_project_title(request:Request):
    body=await _body(request);title=str(body.get('title') or '').strip()[:240]
    if not title:raise HTTPException(400,detail='PROJECT_TITLE_REQUIRED')
    row=runtime.projects.rename(_pid(request),title)
    return {'ok':True,'title':row.get('name') or row.get('title') or title}


# ---------------------------------------------------------------------------
# Find / replace
# ---------------------------------------------------------------------------

@router.post('/api/find')
async def hosted_find(request:Request):
    body=await _body(request);pid=_pid(request)
    query=str(body.get('query') or '')
    rx=_regex(query,bool(body.get('case_sensitive')),bool(body.get('whole_word')))
    scope=str(body.get('scope') or 'document');chapter_id=body.get('chapter_id');scene_id=body.get('scene_id');tab_id=body.get('tab_id')
    blocks=_all_blocks(pid)
    if scope=='chapter' and chapter_id:blocks=[b for b in blocks if str(b.get('chapter_id'))==str(chapter_id)]
    elif scope=='scene' and scene_id:blocks=[b for b in blocks if str(b.get('scene_id'))==str(scene_id)]
    else:
        if tab_id is None:
            tabs=_list('document_tab',{'project_id':pid});primary=next((t for t in tabs if t.get('is_manuscript')),tabs[0] if tabs else None);tab_id=primary.get('id') if primary else None
        if tab_id is not None:blocks=[b for b in blocks if str(b.get('tab_id'))==str(tab_id)]
    chapters={str(c.get('id')):c for c in _list('chapter',{'project_id':pid})};scenes={str(s.get('id')):s for s in _list('scene',{'project_id':pid})}
    hits=[]
    for block in _sort_position(blocks):
        text=str(block.get('text_content') or '')
        for match in rx.finditer(text):
            chapter=chapters.get(str(block.get('chapter_id')),{});scene=scenes.get(str(block.get('scene_id')),{})
            hits.append({'block_id':block.get('id'),'chapter_id':block.get('chapter_id'),'chapter_title':'' if chapter.get('is_implicit') else str(chapter.get('title') or ''),'scene_title':str(scene.get('title') or ''),'start':match.start(),'end':match.end(),'match':match.group(0),'context':text[max(0,match.start()-45):min(len(text),match.end()+70)]})
            if len(hits)>=5000:return {'count':len(hits),'hits':hits}
    return {'count':len(hits),'hits':hits}

@router.post('/api/replace')
async def hosted_replace(request:Request):
    body=await _body(request);pid=_pid(request);query=str(body.get('query') or '');replacement=str(body.get('replacement') or '')
    if len(replacement)>100000:raise HTTPException(400,detail='Find/replace text is too long')
    rx=_regex(query,bool(body.get('case_sensitive')),bool(body.get('whole_word')))
    scope=str(body.get('scope') or 'document');blocks=_all_blocks(pid)
    if scope=='chapter' and body.get('chapter_id'):blocks=[b for b in blocks if str(b.get('chapter_id'))==str(body.get('chapter_id'))]
    elif scope=='scene' and body.get('scene_id'):blocks=[b for b in blocks if str(b.get('scene_id'))==str(body.get('scene_id'))]
    else:
        tab_id=body.get('tab_id')
        if tab_id is None:
            tabs=_list('document_tab',{'project_id':pid});primary=next((t for t in tabs if t.get('is_manuscript')),tabs[0] if tabs else None);tab_id=primary.get('id') if primary else None
        if tab_id is not None:blocks=[b for b in blocks if str(b.get('tab_id'))==str(tab_id)]
    affected=[];total=0
    for block in blocks:
        text=str(block.get('text_content') or '');count=len(rx.findall(text))
        if count:affected.append({'block_id':block.get('id'),'chapter_id':block.get('chapter_id'),'count':count,'context':text[:160]});total+=count
    if body.get('preview'):return {'count':total,'affected':affected[:1000]}
    for item in affected:
        current=_get('manuscript_block',{'project_id':pid,'block_id':str(item['block_id'])});old=str(current.get('text_content') or '');new=rx.sub(lambda _m:replacement,old)
        # Preserve inline markup only when a plain-text replacement does not make that
        # representation stale. Hosted M8 can add richer DOM-aware replacement if needed.
        content_html=str(current.get('content_html') or '')
        if content_html:
            content_html=rx.sub(lambda _m:html.escape(replacement),content_html)
        else:content_html=html.escape(new)
        _update('manuscript_block',{'project_id':pid,'block_id':str(item['block_id'])},{'text_content':new,'content_html':content_html})
    return {'ok':True,'count':total}


# ---------------------------------------------------------------------------
# Markup / comments / references
# ---------------------------------------------------------------------------

@router.get('/api/icon-markers')
def hosted_icon_markers(request:Request):return _list('icon_marker',{'project_id':_pid(request)})
@router.post('/api/icon-markers')
async def hosted_icon_marker_create(request:Request):return _create('icon_marker',{'project_id':_pid(request)},await _body(request))
@router.patch('/api/icon-markers/{mid}')
async def hosted_icon_marker_update(mid:str,request:Request):return _update('icon_marker',{'project_id':_pid(request),'icon_marker_id':mid},await _body(request))
@router.delete('/api/icon-markers/{mid}')
def hosted_icon_marker_delete(mid:str,request:Request):return _delete('icon_marker',{'project_id':_pid(request),'icon_marker_id':mid})

@router.patch('/api/labels/{lid}')
async def hosted_label_update(lid:str,request:Request):return _update('markup_label',{'project_id':_pid(request),'label_id':lid},await _body(request))
@router.get('/api/labels/{lid}/usage')
def hosted_label_usage(lid:str,request:Request):
    pid=_pid(request);rows=_list('annotation',{'project_id':pid},field='label_id',value=lid);return {'label_id':lid,'usage':len(rows)}
@router.delete('/api/labels/{lid}')
def hosted_label_delete(lid:str,request:Request):return _delete('markup_label',{'project_id':_pid(request),'label_id':lid})
@router.post('/api/annotations/clear-highlights')
def hosted_clear_highlights(request:Request):
    pid=_pid(request);removed=0
    for row in list(_list('annotation',{'project_id':pid})):
        if str(row.get('annotation_type') or row.get('type') or '')=='highlight':
            _delete('annotation',{'project_id':pid,'annotation_id':str(row.get('id'))});removed+=1
    return {'ok':True,'removed':removed}

@router.get('/api/comments/{tid}')
def hosted_comment_get(tid:str,request:Request):
    pid=_pid(request);return {'thread':_get('comment_thread',{'project_id':pid,'thread_id':tid}),'messages':_list('comment_message',{'project_id':pid,'thread_id':tid})}
@router.post('/api/comments/{tid}/delete')
def hosted_comment_delete_action(tid:str,request:Request):return _delete('comment_thread',{'project_id':_pid(request),'thread_id':tid})
@router.patch('/api/comment-messages/{mid}')
async def hosted_comment_message_update(mid:str,request:Request):
    pid=_pid(request);ids=_locate('comment_message',pid,mid);return _update('comment_message',ids,await _body(request))

@router.get('/api/references')
def hosted_references(request:Request,source_type:str|None=None,source_id:str|None=None,target_type:str|None=None,target_id:str|None=None):
    rows=_list('cross_feature_reference',{'project_id':_pid(request)})
    for field,value in (('source_type',source_type),('source_id',source_id),('target_type',target_type),('target_id',target_id)):
        if value is not None:rows=[r for r in rows if str(r.get(field))==str(value)]
    return rows
@router.post('/api/references')
async def hosted_reference_create(request:Request):return _create('cross_feature_reference',{'project_id':_pid(request)},await _body(request))
@router.delete('/api/references/{rid}')
def hosted_reference_delete(rid:str,request:Request):return _delete('cross_feature_reference',{'project_id':_pid(request),'reference_id':rid})


# ---------------------------------------------------------------------------
# Cuts / tasks
# ---------------------------------------------------------------------------

@router.get('/api/cuts')
def hosted_cuts(request:Request):return _list('cut',{'project_id':_pid(request)})
@router.post('/api/cuts')
async def hosted_cut_create(request:Request):return _create('cut',{'project_id':_pid(request)},await _body(request))
@router.patch('/api/cuts/{cid}')
async def hosted_cut_update(cid:str,request:Request):return _update('cut',{'project_id':_pid(request),'cut_id':cid},await _body(request))
@router.delete('/api/cuts/{cid}')
def hosted_cut_delete(cid:str,request:Request):return _delete('cut',{'project_id':_pid(request),'cut_id':cid})
@router.post('/api/cuts/{cid}/insert')
async def hosted_cut_insert(cid:str,request:Request):
    pid=_pid(request);body=await _body(request);cut=_get('cut',{'project_id':pid,'cut_id':cid})
    chapter_id=str(body.get('chapter_id') or cut.get('source_chapter_id') or '')
    if not chapter_id:raise HTTPException(400,detail='CUT_INSERT_CHAPTER_REQUIRED')
    tab_id=str(body.get('tab_id') or '')
    if not tab_id:
        chapter=_get('chapter',{'project_id':pid,'chapter_id':chapter_id});tab_id=str(chapter.get('tab_id') or '')
    blocks=_sort_position(_list('manuscript_block',{'project_id':pid},field='chapter_id',value=chapter_id))
    position=len(blocks)
    anchor=str(body.get('block_id') or body.get('after_block_id') or '')
    if anchor:
        for index,row in enumerate(blocks):
            if str(row.get('id'))==anchor:position=index+1;break
    for row in reversed(blocks[position:]):_update('manuscript_block',{'project_id':pid,'block_id':str(row.get('id'))},{'position':int(row.get('position') or 0)+1})
    block=_create('manuscript_block',{'project_id':pid},{'tab_id':tab_id,'chapter_id':chapter_id,'position':position,'block_type':'paragraph','semantic_style':'body','content_html':str(cut.get('content_html') or ''),'text_content':str(cut.get('text_content') or '')})
    return {'ok':True,'block':block}
@router.get('/api/cuts/export')
def hosted_cuts_export(request:Request):
    payload=json.dumps({'version':1,'cuts':_list('cut',{'project_id':_pid(request)})},ensure_ascii=False,sort_keys=True).encode('utf-8')
    return Response(payload,media_type='application/json',headers={'Content-Disposition':'attachment; filename=cuts.json','Cache-Control':'private, no-store'})
@router.post('/api/cuts/import')
async def hosted_cuts_import(request:Request,file:UploadFile|None=File(default=None)):
    if file is not None:raw=await file.read();value=json.loads(raw.decode('utf-8'))
    else:value=await _body(request)
    items=value.get('cuts') if isinstance(value,dict) else value
    if not isinstance(items,list):raise HTTPException(400,detail='CUT_IMPORT_INVALID')
    saved=[_create('cut',{'project_id':_pid(request)},item) for item in items if isinstance(item,dict)]
    return {'ok':True,'imported':len(saved),'cuts':saved}

@router.patch('/api/task-milestones/{mid}')
async def hosted_milestone_update(mid:str,request:Request):
    pid=_pid(request);return _update('task_milestone',_locate('task_milestone',pid,mid),await _body(request))
@router.delete('/api/task-milestones/{mid}')
def hosted_milestone_delete(mid:str,request:Request):return _delete('task_milestone',_locate('task_milestone',_pid(request),mid))
@router.post('/api/tasks/{tid}/duplicate')
def hosted_task_duplicate(tid:str,request:Request):
    pid=_pid(request);source=_get('task',{'project_id':pid,'task_id':tid});payload={k:v for k,v in source.items() if k not in {'id','task_id','generation','created_at','updated_at'}};payload['title']=str(payload.get('title') or 'Task')+' Copy';created=_create('task',{'project_id':pid},payload)
    for milestone in _list('task_milestone',{'project_id':pid,'task_id':tid}):_create('task_milestone',{'project_id':pid,'task_id':str(created['id'])},{k:v for k,v in milestone.items() if k not in {'id','milestone_id','generation','created_at','updated_at'}})
    return created
@router.post('/api/tasks/{tid}/milestones/reorder')
async def hosted_milestone_reorder(tid:str,request:Request):
    body=await _body(request);order=body.get('ids') or body.get('milestone_ids') or body.get('order') or []
    if not isinstance(order,list):raise HTTPException(400,detail='MILESTONE_ORDER_INVALID')
    for pos,mid in enumerate(order):_update('task_milestone',{'project_id':_pid(request),'task_id':tid,'milestone_id':str(mid)},{'position':pos})
    return {'ok':True}


# ---------------------------------------------------------------------------
# Chapters / Scenes
# ---------------------------------------------------------------------------

@router.post('/api/chapters/reorder')
async def hosted_chapter_reorder(request:Request):
    body=await _body(request);order=body.get('ids') or body.get('chapter_ids') or body.get('order') or []
    if not isinstance(order,list):raise HTTPException(400,detail='CHAPTER_ORDER_INVALID')
    for pos,cid in enumerate(order):_update('chapter',{'project_id':_pid(request),'chapter_id':str(cid)},{'position':pos})
    return {'ok':True}
@router.post('/api/chapters/{cid}/dissolve')
def hosted_chapter_dissolve(cid:str,request:Request):
    # Hosted canonical state keeps blocks independent. Dissolve removes only the visible
    # boundary metadata after moving blocks to the nearest sibling chapter.
    pid=_pid(request);chapters=_sort_position(_list('chapter',{'project_id':pid}));idx=next((i for i,c in enumerate(chapters) if str(c.get('id'))==cid),-1)
    if idx<0:raise HTTPException(404,detail='CHAPTER_NOT_FOUND')
    target=(chapters[idx-1] if idx>0 else chapters[idx+1] if idx+1<len(chapters) else None)
    if target is None:return {'ok':True,'chapter':chapters[idx]}
    target_id=str(target.get('id'))
    for block in _list('manuscript_block',{'project_id':pid},field='chapter_id',value=cid):_update('manuscript_block',{'project_id':pid,'block_id':str(block.get('id'))},{'chapter_id':target_id})
    _delete('chapter',{'project_id':pid,'chapter_id':cid});return {'ok':True,'chapter_id':target_id}
@router.delete('/api/chapters/{cid}/permanent')
def hosted_chapter_permanent(cid:str,request:Request):
    pid=_pid(request)
    for block in list(_list('manuscript_block',{'project_id':pid},field='chapter_id',value=cid)):_delete('manuscript_block',{'project_id':pid,'block_id':str(block.get('id'))})
    return _delete('chapter',{'project_id':pid,'chapter_id':cid})
@router.post('/api/chapters/{cid}/split')
async def hosted_chapter_split(cid:str,request:Request):
    pid=_pid(request);body=await _body(request);chapter=_get('chapter',{'project_id':pid,'chapter_id':cid});blocks=_sort_position(_list('manuscript_block',{'project_id':pid},field='chapter_id',value=cid));at=str(body.get('block_id') or body.get('at_block_id') or '');index=next((i for i,b in enumerate(blocks) if str(b.get('id'))==at),len(blocks));new=_create('chapter',{'project_id':pid},{'tab_id':chapter.get('tab_id'),'title':str(body.get('title') or 'New Chapter'),'position':int(chapter.get('position') or 0)+1})
    for pos,block in enumerate(blocks[index:]):_update('manuscript_block',{'project_id':pid,'block_id':str(block.get('id'))},{'chapter_id':new['id'],'position':pos})
    return {'chapter':new}

@router.put('/api/scenes/{sid}/blocks')
async def hosted_scene_blocks(sid:str,request:Request):
    pid=_pid(request);body=await _body(request);ids=body.get('block_ids') or body.get('ids') or []
    if not isinstance(ids,list):raise HTTPException(400,detail='SCENE_BLOCKS_INVALID')
    for block in _all_blocks(pid):
        if str(block.get('scene_id') or '')==sid and str(block.get('id')) not in {str(x) for x in ids}:_update('manuscript_block',{'project_id':pid,'block_id':str(block.get('id'))},{'scene_id':None})
    for bid in ids:_update('manuscript_block',{'project_id':pid,'block_id':str(bid)},{'scene_id':sid})
    return {'ok':True}
@router.post('/api/scenes/{sid}/extend')
async def hosted_scene_extend(sid:str,request:Request):
    body=await _body(request);bid=str(body.get('block_id') or '');
    if bid:_update('manuscript_block',{'project_id':_pid(request),'block_id':bid},{'scene_id':sid})
    return {'ok':True}
@router.post('/api/scenes/{sid}/remove-blocks')
async def hosted_scene_remove_blocks(sid:str,request:Request):
    body=await _body(request);ids=body.get('block_ids') or body.get('ids') or []
    for bid in ids:_update('manuscript_block',{'project_id':_pid(request),'block_id':str(bid)},{'scene_id':None})
    return {'ok':True}
@router.post('/api/scenes/{sid}/move')
async def hosted_scene_move(sid:str,request:Request):return _update('scene',{'project_id':_pid(request),'scene_id':sid},await _body(request))
@router.post('/api/scenes/{sid}/cut')
def hosted_scene_cut(sid:str,request:Request):
    pid=_pid(request);scene=_get('scene',{'project_id':pid,'scene_id':sid});blocks=[b for b in _all_blocks(pid) if str(b.get('scene_id') or '')==sid];content='\n'.join(str(b.get('text_content') or '') for b in blocks);return _create('cut',{'project_id':pid},{'title':str(scene.get('title') or 'Scene'),'text_content':content,'content_html':'<p>'+html.escape(content).replace('\n','</p><p>')+'</p>','source_scene_id':sid})
@router.get('/api/scenes/{scene_id}/map-memberships')
def hosted_scene_map_memberships(scene_id:str,request:Request):
    refs=[r for r in _list('cross_feature_reference',{'project_id':_pid(request)}) if str(r.get('target_type'))=='scene' and str(r.get('target_id'))==scene_id]
    return {'memberships':refs}


# ---------------------------------------------------------------------------
# Questionnaires and review marker library
# ---------------------------------------------------------------------------

@router.patch('/api/questionnaires/{questionnaire_id}')
async def hosted_questionnaire_update(questionnaire_id:str,request:Request):return _update('questionnaire',{'project_id':_pid(request),'questionnaire_id':questionnaire_id},await _body(request))
@router.delete('/api/questionnaires/{questionnaire_id}')
def hosted_questionnaire_delete(questionnaire_id:str,request:Request):return _delete('questionnaire',{'project_id':_pid(request),'questionnaire_id':questionnaire_id})
@router.post('/api/questionnaires/{questionnaire_id}/questions')
async def hosted_question_create(questionnaire_id:str,request:Request):return _create('questionnaire_question',{'project_id':_pid(request),'questionnaire_id':questionnaire_id},await _body(request))
@router.patch('/api/questionnaire-questions/{question_id}')
async def hosted_question_update(question_id:str,request:Request):return _update('questionnaire_question',_locate('questionnaire_question',_pid(request),question_id),await _body(request))
@router.delete('/api/questionnaire-questions/{question_id}')
def hosted_question_delete(question_id:str,request:Request):return _delete('questionnaire_question',_locate('questionnaire_question',_pid(request),question_id))
@router.post('/api/questionnaires/populate')
async def hosted_questionnaire_populate(request:Request):
    body=await _body(request);items=body.get('questionnaires') if isinstance(body.get('questionnaires'),list) else []
    return {'questionnaires':[_create('questionnaire',{'project_id':_pid(request)},item) for item in items if isinstance(item,dict)]}

@router.get('/api/review-markers')
def hosted_review_markers(request:Request):return _list('review_marker_label',{'project_id':_pid(request)})
@router.post('/api/review-markers')
async def hosted_review_marker_create(request:Request):return _create('review_marker_label',{'project_id':_pid(request)},await _body(request))
@router.patch('/api/review-markers/{marker_id}')
async def hosted_review_marker_update(marker_id:str,request:Request):return _update('review_marker_label',{'project_id':_pid(request),'marker_label_id':marker_id},await _body(request))
@router.delete('/api/review-markers/{marker_id}')
def hosted_review_marker_delete(marker_id:str,request:Request):return _delete('review_marker_label',{'project_id':_pid(request),'marker_label_id':marker_id})
@router.post('/api/review-markers/populate')
async def hosted_review_marker_populate(request:Request):
    body=await _body(request);items=body.get('markers') if isinstance(body.get('markers'),list) else []
    return {'markers':[_create('review_marker_label',{'project_id':_pid(request)},item) for item in items if isinstance(item,dict)]}
@router.post('/api/reviews/{rid}/markers/{marker_id}')
def hosted_review_marker_attach(rid:str,marker_id:str,request:Request):
    return _create('review_marker_assignment',{'project_id':_pid(request),'review_id':rid},{'marker_label_id':marker_id},entity_id=marker_id)
@router.delete('/api/reviews/{rid}/markers/{marker_id}')
def hosted_review_marker_detach(rid:str,marker_id:str,request:Request):return _delete('review_marker_assignment',{'project_id':_pid(request),'review_id':rid,'review_marker_id':marker_id})

@router.get('/api/reviews/{rid}/participants')
def hosted_review_participants(rid:str,request:Request):return _list('review_participant',{'project_id':_pid(request),'review_id':rid})
@router.patch('/api/review-participants/{participant_id}')
async def hosted_review_participant_update(participant_id:str,request:Request):return _update('review_participant',_locate('review_participant',_pid(request),participant_id),await _body(request))
@router.patch('/api/review-participants/{participant_id}/status')
async def hosted_review_participant_status(participant_id:str,request:Request):return _update('review_participant',_locate('review_participant',_pid(request),participant_id),await _body(request))
@router.delete('/api/review-participants/{participant_id}')
def hosted_review_participant_delete(participant_id:str,request:Request):return _delete('review_participant',_locate('review_participant',_pid(request),participant_id))

@router.post('/api/reviews/{rid}/archive')
def hosted_review_archive(rid:str,request:Request):return _update('review',{'project_id':_pid(request),'review_id':rid},{'status':'archived'})
@router.post('/api/reviews/{rid}/unarchive')
def hosted_review_unarchive(rid:str,request:Request):return _update('review',{'project_id':_pid(request),'review_id':rid},{'status':'draft'})
@router.post('/api/reviews/{rid}/pause')
def hosted_review_pause(rid:str,request:Request):return _update('review',{'project_id':_pid(request),'review_id':rid},{'status':'paused'})
@router.post('/api/reviews/{rid}/resume')
def hosted_review_resume(rid:str,request:Request):return _update('review',{'project_id':_pid(request),'review_id':rid},{'status':'active'})
@router.post('/api/reviews/{rid}/start')
def hosted_review_start(rid:str,request:Request):
    pid=_pid(request);snapshot=_create('review_snapshot',{'project_id':pid,'review_id':rid},{'snapshot':_project_snapshot(pid),'status':'active'});review=_update('review',{'project_id':pid,'review_id':rid},{'status':'active','active_snapshot_id':snapshot['id']});return {'review':review,'snapshot':snapshot}
@router.post('/api/reviews/{rid}/duplicate')
def hosted_review_duplicate(rid:str,request:Request):
    pid=_pid(request);source=_get('review',{'project_id':pid,'review_id':rid});payload={k:v for k,v in source.items() if k not in {'id','review_id','generation','created_at','updated_at','active_snapshot_id'}};payload['name']=str(payload.get('name') or 'Review')+' Copy';return _create('review',{'project_id':pid},payload)
@router.post('/api/reviews/reorder')
async def hosted_review_reorder(request:Request):
    body=await _body(request);order=body.get('ids') or body.get('review_ids') or body.get('order') or []
    for pos,rid in enumerate(order if isinstance(order,list) else []):_update('review',{'project_id':_pid(request),'review_id':str(rid)},{'position':pos})
    return {'ok':True}
@router.get('/api/reviews/{rid}/snapshot')
def hosted_review_snapshot(rid:str,request:Request):
    snapshots=_list('review_snapshot',{'project_id':_pid(request),'review_id':rid});return snapshots[-1] if snapshots else None
@router.get('/api/reviews/{rid}/manuscript-round')
def hosted_review_round(rid:str,request:Request):return hosted_review_snapshot(rid,request)
@router.get('/api/reviews/{rid}/feedback')
def hosted_review_feedback(rid:str,request:Request):
    pid=_pid(request);return {'comments':_list('review_comment',{'project_id':pid,'review_id':rid}),'highlights':_list('review_highlight',{'project_id':pid,'review_id':rid}),'suggestions':_list('review_suggestion',{'project_id':pid,'review_id':rid}),'feedback':_list('review_general_feedback',{'project_id':pid,'review_id':rid})}


# ---------------------------------------------------------------------------
# Checkpoints / history / transient undo
# ---------------------------------------------------------------------------

@router.patch('/api/checkpoints/{cid}')
async def hosted_checkpoint_update(cid:str,request:Request):
    # Hosted checkpoints are immutable snapshots. Metadata edits are represented as a
    # replacement checkpoint, never mutation of the immutable snapshot pointer.
    current=_get('checkpoint',{'project_id':_pid(request),'checkpoint_id':cid});body=await _body(request);payload={**current,**body};payload.pop('id',None);payload.pop('checkpoint_id',None);return _create('checkpoint',{'project_id':_pid(request)},payload)
@router.delete('/api/checkpoints/{cid}')
def hosted_checkpoint_delete(cid:str,request:Request):
    pid=_pid(request)
    try:_domain().delete_immutable_pointer('checkpoint',{'project_id':pid,'checkpoint_id':cid},user_id=_uid())
    except Exception as exc:raise _error(exc)
    return {'ok':True,'checkpoint_id':cid,'object_disposition':'orphan_retention_candidate'}
@router.get('/api/checkpoints/compare/versions')
def hosted_checkpoint_compare(request:Request):return {'checkpoints':_list('checkpoint',{'project_id':_pid(request)})}
@router.post('/api/checkpoints/{cid}/preflight')
def hosted_checkpoint_preflight(cid:str,request:Request):return {'ok':True,'checkpoint':_get('checkpoint',{'project_id':_pid(request),'checkpoint_id':cid})}
@router.post('/api/checkpoints/{cid}/apply')
def hosted_checkpoint_apply(cid:str,request:Request):
    raise HTTPException(409,detail='HOSTED_CHECKPOINT_RESTORE_REQUIRES_M8_JOB_PROTOCOL')

@router.get('/api/history/retention')
def hosted_history_retention():return runtime.history.retention_settings()
@router.put('/api/history/retention')
async def hosted_history_retention_put(request:Request):
    body=await _body(request);return runtime.history.update_retention(str(body.get('value') or body.get('policy') or ''))
@router.post('/api/history/sync')
async def hosted_history_sync(request:Request):
    body=await _body(request);return runtime.history.sync_remote(_pid(request),body.get('revision_id'))
@router.post('/api/history/gc')
def hosted_history_gc(request:Request):return runtime.history.run_maintenance(_pid(request))
@router.post('/api/history/repair-local')
def hosted_history_repair():raise HTTPException(409,detail='HOSTED_LOCAL_REPAIR_UNAVAILABLE')
@router.post('/api/history/edit-baseline')
def hosted_history_edit_baseline():return {'ok':True,'mode':'hosted','baseline':'authoritative'}

# Structural undo is deliberately working-session state at M5. Tokens only record the
# project generation boundary; losing the process loses undo availability but cannot
# roll committed canonical state backward or corrupt it. Full horizontally safe undo is
# revisited in M8.
_UNDO:dict[str,dict[str,Any]]={}
@router.post('/api/undo/begin')
def hosted_undo_begin(request:Request,label:str='',scope:str='full',scope_id:str|None=None):
    pid=_pid(request);token='undo_'+secrets.token_hex(16);_UNDO[token]={'project_id':pid,'user_id':_uid(),'generation':runtime.projects.get(pid).get('generation'),'label':label,'scope':scope,'scope_id':scope_id};return {'token':token}
@router.post('/api/undo/finish/{token}')
def hosted_undo_finish(token:str,request:Request):
    row=_UNDO.get(token)
    if not row or row['project_id']!=_pid(request) or row['user_id']!=_uid():raise HTTPException(404,detail='UNDO_TOKEN_NOT_FOUND')
    changed=int(runtime.projects.get(row['project_id']).get('generation') or 0)!=int(row.get('generation') or 0);row['changed']=changed;return {'ok':True,'changed':changed}
@router.post('/api/undo/apply/{token}')
def hosted_undo_apply(token:str,request:Request,direction:str='undo'):
    raise HTTPException(409,detail='HOSTED_UNDO_RESTORE_UNAVAILABLE_UNTIL_M8')
@router.post('/api/undo/rollback/{token}')
def hosted_undo_rollback(token:str,request:Request):
    # A failed multi-request action cannot be silently rolled back from process memory.
    # Surface the limitation rather than claiming rollback succeeded.
    raise HTTPException(409,detail='HOSTED_UNDO_ROLLBACK_UNAVAILABLE_UNTIL_M8')


# ---------------------------------------------------------------------------
# Explicit later-milestone capability boundaries
# ---------------------------------------------------------------------------

@router.post('/api/recovery-backups/update')
def hosted_recovery_backup_update():raise HTTPException(409,detail='HOSTED_BACKUP_UNAVAILABLE_UNTIL_M9')
@router.get('/api/recovery/status')
def hosted_recovery_status(request:Request):return {'ok':True,'mode':'hosted','project_id':_pid(request),'recovery_required':False}


# ---------------------------------------------------------------------------
# Canvas compatibility
# ---------------------------------------------------------------------------

def _story_template_catalog_raw() -> dict[str,Any]:
    try:data=json.loads(STORY_TEMPLATES_CONFIG.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as exc:raise HTTPException(500,detail=f'STORY_TEMPLATE_CONFIG_INVALID:{exc}') from exc
    if data.get('version')!=1 or not isinstance(data.get('categories'),list):raise HTTPException(500,detail='STORY_TEMPLATE_CONFIG_INVALID')
    return data


def _story_template(template_id:str) -> dict[str,Any]:
    for category in _story_template_catalog_raw()['categories']:
        for template in category.get('templates') or []:
            if str(template.get('id') or '')==str(template_id):return template
    raise HTTPException(404,detail='STORY_TEMPLATE_NOT_FOUND')


def _story_source_items(pid:str,template:Mapping[str,Any]) -> list[dict[str,Any]]:
    source=str(template.get('source') or '')
    if source=='project.chapters':
        rows=[row for row in _list('chapter',{'project_id':pid}) if not bool(row.get('is_implicit'))]
        return [{'title':str(row.get('title') or 'Chapter'),'body':'','chapter_id':row.get('id'),'anchor_type':'chapter','anchor_id':row.get('id')} for row in _sort_position(rows)]
    if source=='project.scenes':
        rows=_sort_position(_list('scene',{'project_id':pid}))
        return [{'title':str(row.get('title') or 'Scene'),'body':str(row.get('description') or ''),'scene_id':row.get('id'),'anchor_type':'scene','anchor_id':row.get('id')} for row in rows]
    return [dict(item) for item in template.get('items') or [] if isinstance(item,dict)]


def _story_catalog_for_project(pid:str,*,map_only:bool=False) -> dict[str,Any]:
    config=_story_template_catalog_raw();categories=[]
    for category in config['categories']:
        templates=[]
        for template in category.get('templates') or []:
            if map_only and not bool(template.get('map_compatible')):continue
            source=template.get('source');count=len(_story_source_items(pid,template))
            templates.append({'id':template.get('id'),'label':template.get('label'),'description':str(template.get('description') or '')[:1000],'source':source,'item_count':count})
        if templates:categories.append({'id':category.get('id'),'label':category.get('label'),'templates':templates})
    return {'version':config['version'],'categories':categories}


@router.get('/api/canvas-populate')
def hosted_canvas_populate_catalog(request:Request):return _story_catalog_for_project(_pid(request))

@router.post('/api/canvases/{cid}/populate')
async def hosted_canvas_populate(cid:str,request:Request):
    body=await _body(request);pid=_pid(request)
    template_id=str(body.get('template_id') or '')
    if not template_id:
        # Retain the M5A low-level payload for API compatibility with any early hosted client.
        nodes=body.get('nodes') or [];edges=body.get('edges') or [];created_nodes=[];created_edges=[]
        if isinstance(nodes,list):
            for item in nodes:
                if isinstance(item,dict):created_nodes.append(_create('canvas_node',{'project_id':pid,'canvas_id':cid},item))
        if isinstance(edges,list):
            for item in edges:
                if isinstance(item,dict):created_edges.append(_create('canvas_edge',{'project_id':pid,'canvas_id':cid},item))
        return {'ok':True,'nodes':created_nodes,'edges':created_edges}
    template=_story_template(template_id);items=_story_source_items(pid,template)
    if not items:raise HTTPException(400,detail=f"Nothing is available for {template.get('label') or template_id} yet")
    if len(items)>500:raise HTTPException(400,detail='Canvas Populate is limited to 500 nodes per operation')
    node_defaults=dict(template.get('node') or {});layout=dict(template.get('layout') or {});columns=max(1,min(20,int(layout.get('columns') or 4)))
    gap_x=float(layout.get('gap_x') or 44);gap_y=float(layout.get('gap_y') or 44);origin_x=max(0.0,float(body.get('x') or 0));origin_y=max(0.0,float(body.get('y') or 0))
    default_w=float(node_defaults.get('w') or 220);default_h=float(node_defaults.get('h') or 125);created=[]
    for index,item in enumerate(items):
        override=dict(item.get('node') or {});node={**node_defaults,**override};column=index%columns;row=index//columns
        payload={
            'title':str(item.get('title') or '')[:1000],'body':str(item.get('body') or '')[:12000],
            'shape':str(node.get('shape') or 'rect'),'node_type':str(node.get('node_type') or ('chapter' if item.get('chapter_id') else 'scene' if item.get('scene_id') else 'free')),
            'chapter_id':item.get('chapter_id'),'scene_id':item.get('scene_id'),'w':float(node.get('w') or default_w),'h':float(node.get('h') or default_h),
            'x':origin_x+column*(default_w+gap_x),'y':origin_y+row*(default_h+gap_y),'position':index,
        }
        created.append(_create('canvas_node',{'project_id':pid,'canvas_id':cid},payload))
    edges=[];connect=dict(template.get('connect') or {})
    if connect.get('mode')=='sequence':
        for left,right in zip(created,created[1:]):
            edges.append(_create('canvas_edge',{'project_id':pid,'canvas_id':cid},{'from_node_id':left.get('id'),'to_node_id':right.get('id'),'from_port':'right','to_port':'left','route_style':connect.get('route_style','straight'),'end_style':connect.get('end_style','arrow'),'position':len(edges)}))
    return {'template_id':template_id,'label':template.get('label'),'nodes':created,'edges':edges,'layout':{'type':'grid','columns':columns},'origin':{'x':origin_x,'y':origin_y}}

@router.patch('/api/nodes/{nid}')
async def hosted_canvas_node_update(nid:str,request:Request):return _update('canvas_node',_locate('canvas_node',_pid(request),nid),await _body(request))
@router.delete('/api/nodes/{nid}')
def hosted_canvas_node_delete(nid:str,request:Request):return _delete('canvas_node',_locate('canvas_node',_pid(request),nid))
@router.patch('/api/edges/{eid}')
async def hosted_canvas_edge_update(eid:str,request:Request):return _update('canvas_edge',_locate('canvas_edge',_pid(request),eid),await _body(request))
@router.delete('/api/edges/{eid}')
def hosted_canvas_edge_delete(eid:str,request:Request):return _delete('canvas_edge',_locate('canvas_edge',_pid(request),eid))

@router.patch('/api/canvases/{cid}/nodes/positions')
async def hosted_canvas_node_positions(cid:str,request:Request):
    body=await _body(request);rows=body.get('nodes') or body.get('positions') or []
    if not isinstance(rows,list):raise HTTPException(400,detail='CANVAS_POSITIONS_INVALID')
    saved=[]
    for item in rows:
        if not isinstance(item,dict):continue
        nid=str(item.get('id') or item.get('node_id') or '')
        if not nid:continue
        saved.append(_update('canvas_node',{'project_id':_pid(request),'canvas_id':cid,'node_id':nid},{k:v for k,v in item.items() if k not in {'id','node_id'}}))
    return {'ok':True,'nodes':saved}

@router.post('/api/canvases/{cid}/nodes/delete-batch')
async def hosted_canvas_delete_batch(cid:str,request:Request):
    body=await _body(request);ids=body.get('ids') or body.get('node_ids') or []
    removed=0
    for nid in ids if isinstance(ids,list) else []:
        try:_delete('canvas_node',{'project_id':_pid(request),'canvas_id':cid,'node_id':str(nid)});removed+=1
        except HTTPException as exc:
            if exc.status_code!=404:raise
    return {'ok':True,'removed':removed}

@router.post('/api/canvases/reorder')
async def hosted_canvas_reorder(request:Request):
    body=await _body(request);order=body.get('ids') or body.get('canvas_ids') or body.get('order') or []
    for pos,cid in enumerate(order if isinstance(order,list) else []):_update('canvas',{'project_id':_pid(request),'canvas_id':str(cid)},{'position':pos})
    return {'ok':True}


def _canvas_version_payload(pid:str,cid:str) -> dict[str,Any]:
    return {'nodes':_list('canvas_node',{'project_id':pid,'canvas_id':cid}),'edges':_list('canvas_edge',{'project_id':pid,'canvas_id':cid})}

@router.get('/api/canvases/{cid}/versions')
def hosted_canvas_versions(cid:str,request:Request):
    rows=_list('canvas_version',{'project_id':_pid(request),'canvas_id':cid});return [row for row in rows if str(row.get('status') or '')!='deleted']

@router.get('/api/canvases/{cid}/versions/{vid}')
def hosted_canvas_version_detail(cid:str,vid:str,request:Request):
    pid=_pid(request);version=_get('canvas_version',{'project_id':pid,'canvas_id':cid,'version_id':vid});items=_list('canvas_version_item',{'project_id':pid,'canvas_id':cid,'version_id':vid})
    return {'version':version,'items':items}

def _create_canvas_version(pid:str,cid:str,name:str) -> dict[str,Any]:
    snapshot=_canvas_version_payload(pid,cid)
    version=_create('canvas_version',{'project_id':pid,'canvas_id':cid},{'name':str(name or 'Version'),'status':'active','item_count':len(snapshot['nodes'])+len(snapshot['edges'])})
    vid=str(version['id'])
    for kind,rows in snapshot.items():
        singular='node' if kind=='nodes' else 'edge'
        for row in rows:
            payload={k:v for k,v in row.items() if k not in {'generation','created_at','updated_at'}}
            _create('canvas_version_item',{'project_id':pid,'canvas_id':cid,'version_id':vid},{'kind':singular,'source_id':row.get('id'),'payload':payload})
    return version

@router.post('/api/canvases/{cid}/versions')
async def hosted_canvas_version_create(cid:str,request:Request):
    body=await _body(request)
    return _create_canvas_version(_pid(request),cid,str(body.get('name') or 'Version'))

@router.patch('/api/canvases/{cid}/versions/{vid}')
async def hosted_canvas_version_rename(cid:str,vid:str,request:Request):return _update('canvas_version',{'project_id':_pid(request),'canvas_id':cid,'version_id':vid},await _body(request))

@router.post('/api/canvases/{cid}/versions/{vid}/update')
async def hosted_canvas_version_refresh(cid:str,vid:str,request:Request):
    pid=_pid(request);current=_get('canvas_version',{'project_id':pid,'canvas_id':cid,'version_id':vid});body=await _body(request)
    name=str(body.get('name') or (str(current.get('name') or 'Version')+' (updated)'))
    return _create_canvas_version(pid,cid,name)

@router.post('/api/canvases/{cid}/versions/{vid}/activate')
def hosted_canvas_version_activate(cid:str,vid:str,request:Request):
    pid=_pid(request);items=_list('canvas_version_item',{'project_id':pid,'canvas_id':cid,'version_id':vid});
    # Snapshot activation is a bounded best-effort reconciliation over canonical CAS.
    for item in items:
        kind=str(item.get('kind') or '');payload=item.get('payload') if isinstance(item.get('payload'),dict) else {};source_id=str(item.get('source_id') or payload.get('id') or '')
        if kind=='node' and source_id:
            try:_update('canvas_node',{'project_id':pid,'canvas_id':cid,'node_id':source_id},payload)
            except HTTPException as exc:
                if exc.status_code==404:_create('canvas_node',{'project_id':pid,'canvas_id':cid},payload,entity_id=source_id)
                else:raise
        if kind=='edge' and source_id:
            try:_update('canvas_edge',{'project_id':pid,'canvas_id':cid,'edge_id':source_id},payload)
            except HTTPException as exc:
                if exc.status_code==404:_create('canvas_edge',{'project_id':pid,'canvas_id':cid},payload,entity_id=source_id)
                else:raise
    return {'ok':True,'version_id':vid}

@router.delete('/api/canvases/{cid}/versions/{vid}')
def hosted_canvas_version_delete(cid:str,vid:str,request:Request):return _update('canvas_version',{'project_id':_pid(request),'canvas_id':cid,'version_id':vid},{'status':'deleted'})
@router.delete('/api/canvases/{cid}/versions/{vid}/items/{item_id}')
def hosted_canvas_version_item_delete(cid:str,vid:str,item_id:str,request:Request):raise HTTPException(409,detail='HOSTED_CANVAS_VERSION_ITEM_IMMUTABLE')

@router.post('/api/canvases/{cid}/developmental-layout')
async def hosted_canvas_developmental_layout(cid:str,request:Request):return {'ok':True,'canvas':_get('canvas',{'project_id':_pid(request),'canvas_id':cid}),'layout':await _body(request)}
@router.post('/api/canvases/{cid}/developmental-promote/{node_id}')
def hosted_canvas_developmental_promote(cid:str,node_id:str,request:Request):return _update('canvas_node',{'project_id':_pid(request),'canvas_id':cid,'node_id':node_id},{'developmental':False,'promoted':True})
@router.post('/api/developmental/preflight')
async def hosted_developmental_preflight(request:Request):return {'ok':True,'plan':await _body(request)}
@router.post('/api/developmental/apply')
async def hosted_developmental_apply(request:Request):return {'ok':True,'applied':True,'plan':await _body(request)}


# ---------------------------------------------------------------------------
# Story-map compatibility
# ---------------------------------------------------------------------------

@router.patch('/api/map-lines/{lid}')
async def hosted_map_line_update(lid:str,request:Request):return _update('map_line',_locate('map_line',_pid(request),lid),await _body(request))
@router.delete('/api/map-lines/{lid}')
def hosted_map_line_delete(lid:str,request:Request):return _delete('map_line',_locate('map_line',_pid(request),lid))
@router.patch('/api/map-items/{iid}')
async def hosted_map_item_update(iid:str,request:Request):return _update('map_item',_locate('map_item',_pid(request),iid),await _body(request))
@router.delete('/api/map-items/{iid}')
def hosted_map_item_delete(iid:str,request:Request):return _delete('map_item',_locate('map_item',_pid(request),iid))
@router.patch('/api/map-junctions/{jid}')
async def hosted_map_junction_update(jid:str,request:Request):return _update('map_junction',_locate('map_junction',_pid(request),jid),await _body(request))
@router.delete('/api/map-junctions/{jid}')
def hosted_map_junction_delete(jid:str,request:Request):return _delete('map_junction',_locate('map_junction',_pid(request),jid))

@router.post('/api/maps/reorder')
async def hosted_maps_reorder(request:Request):
    body=await _body(request);order=body.get('ids') or body.get('map_ids') or body.get('order') or []
    for pos,mid in enumerate(order if isinstance(order,list) else []):_update('story_map',{'project_id':_pid(request),'map_id':str(mid)},{'position':pos})
    return {'ok':True}
@router.post('/api/maps/{mid}/lines/reorder')
async def hosted_map_lines_reorder(mid:str,request:Request):
    body=await _body(request);order=body.get('ids') or body.get('line_ids') or body.get('order') or []
    for pos,lid in enumerate(order if isinstance(order,list) else []):_update('map_line',{'project_id':_pid(request),'map_id':mid,'line_id':str(lid)},{'position':pos})
    return {'ok':True}
@router.post('/api/maps/{mid}/duplicate')
def hosted_map_duplicate(mid:str,request:Request):
    pid=_pid(request);source=_get('story_map',{'project_id':pid,'map_id':mid});payload={k:v for k,v in source.items() if k not in {'id','map_id','generation','created_at','updated_at'}};payload['title']=str(payload.get('title') or payload.get('name') or 'Map')+' Copy';new=_create('story_map',{'project_id':pid},payload)
    for line in _list('map_line',{'project_id':pid,'map_id':mid}):
        line_payload={k:v for k,v in line.items() if k not in {'id','line_id','generation','created_at','updated_at'}};new_line=_create('map_line',{'project_id':pid,'map_id':str(new['id'])},line_payload)
        for item in _list('map_item',{'project_id':pid,'map_id':mid,'line_id':str(line['id'])}):_create('map_item',{'project_id':pid,'map_id':str(new['id']),'line_id':str(new_line['id'])},{k:v for k,v in item.items() if k not in {'id','item_id','generation','created_at','updated_at'}})
    return new
@router.post('/api/maps/{mid}/auto-arrange')
def hosted_map_auto_arrange(mid:str,request:Request):
    pid=_pid(request);_get('story_map',{'project_id':pid,'map_id':mid});changed=0
    for line in _sort_position(_list('map_line',{'project_id':pid,'map_id':mid})):
        lid=str(line.get('id'));items=_sort_position(_list('map_item',{'project_id':pid,'map_id':mid,'line_id':lid}));count=len(items)
        for index,item in enumerate(items):
            start=.5 if count<=1 else .05+.90*index/(count-1);patch={'position':index,'schematic_start':start,'label_pinned':False,'label_offset':0}
            if str(item.get('item_type') or item.get('map_type') or 'stop')=='span':
                width=.12
                try:
                    planned_start=float(item.get('planned_start'));planned_end=float(item.get('planned_end'));width=max(.06,min(.30,abs(planned_end-planned_start)))
                except (TypeError,ValueError):pass
                patch['schematic_start']=min(start,.97-width);patch['schematic_end']=patch['schematic_start']+width
            else:patch['schematic_end']=None
            _update('map_item',{'project_id':pid,'map_id':mid,'line_id':lid,'item_id':str(item.get('id'))},patch);changed+=1
    return {'ok':True,'map_id':mid,'changed':changed}
@router.post('/api/map-lines/{lid}/sync')
def hosted_map_line_sync(lid:str,request:Request):return {'ok':True,'line':_get('map_line',_locate('map_line',_pid(request),lid))}
@router.get('/api/map-populate')
def hosted_map_populate_catalog(request:Request):return _story_catalog_for_project(_pid(request),map_only=True)

def _map_item_payload(item:Mapping[str,Any],index:int,count:int) -> dict[str,Any]:
    start=item.get('map_start');end=item.get('map_end');kind=str(item.get('map_type') or 'stop')
    if start is None:start=.5 if count<=1 else .05+.90*index/(count-1)
    payload={'position':index,'title':str(item.get('title') or '')[:1000],'description':str(item.get('body') or item.get('description') or '')[:12000],'item_type':kind,'schematic_start':float(start),'schematic_end':float(end) if end is not None else None}
    if item.get('anchor_type') and item.get('anchor_id'):
        payload['start_anchor_type']=item.get('anchor_type');payload['start_anchor_id']=item.get('anchor_id')
        if kind=='span':payload['end_anchor_type']=item.get('anchor_type');payload['end_anchor_id']=item.get('anchor_id')
    return payload

@router.post('/api/maps/{mid}/populate')
async def hosted_map_populate(mid:str,request:Request):
    body=await _body(request);pid=_pid(request);template_id=str(body.get('template_id') or '')
    if not template_id:
        lines=body.get('lines') or [];saved=[]
        for line in lines if isinstance(lines,list) else []:
            if isinstance(line,dict):saved.append(_create('map_line',{'project_id':pid,'map_id':mid},line))
        return {'ok':True,'lines':saved}
    template=_story_template(template_id)
    if not bool(template.get('map_compatible')):raise HTTPException(400,detail='MAP_TEMPLATE_NOT_COMPATIBLE')
    items=_story_source_items(pid,template)
    if not items:raise HTTPException(400,detail=f"Nothing is available for {template.get('label') or template_id} yet")
    existing=_list('map_line',{'project_id':pid,'map_id':mid});position=max([int(row.get('position') or 0) for row in existing],default=-1)+1
    line=_create('map_line',{'project_id':pid,'map_id':mid},{'position':position,'name':str(template.get('map_line_name') or template.get('label') or 'Line'),'title':str(template.get('map_line_name') or template.get('label') or 'Line'),'description':str(template.get('description') or '')[:12000],'provenance':'source','source_template_id':template_id,'layout_locked':True})
    lid=str(line.get('id'));saved=[]
    for index,item in enumerate(items):saved.append(_create('map_item',{'project_id':pid,'map_id':mid,'line_id':lid},_map_item_payload(item,index,len(items))))
    return {'map_id':mid,'line_id':lid,'line_name':line.get('name') or line.get('title'),'item_ids':[row.get('id') for row in saved],'item_count':len(saved),'label':template.get('label')}
@router.post('/api/map-lines/{lid}/populate')
async def hosted_map_line_populate(lid:str,request:Request):
    pid=_pid(request);ids=_locate('map_line',pid,lid);body=await _body(request);template_id=str(body.get('template_id') or '')
    if template_id:
        template=_story_template(template_id)
        if not bool(template.get('map_compatible')):raise HTTPException(400,detail='MAP_TEMPLATE_NOT_COMPATIBLE')
        items=_story_source_items(pid,template)
        for current in _list('map_item',{'project_id':pid,'map_id':ids['map_id'],'line_id':lid}):_delete('map_item',{'project_id':pid,'map_id':ids['map_id'],'line_id':lid,'item_id':str(current.get('id'))})
        saved=[_create('map_item',{'project_id':pid,'map_id':ids['map_id'],'line_id':lid},_map_item_payload(item,index,len(items))) for index,item in enumerate(items)]
        _update('map_line',ids,{'provenance':'source','source_template_id':template_id,'description':str(template.get('description') or '')[:12000]})
        return {'line_id':lid,'item_ids':[row.get('id') for row in saved],'item_count':len(saved),'label':template.get('label')}
    items=body.get('items') or [];saved=[]
    for item in items if isinstance(items,list) else []:
        if isinstance(item,dict):saved.append(_create('map_item',{'project_id':pid,'map_id':ids['map_id'],'line_id':lid},item))
    return {'ok':True,'items':saved}
@router.post('/api/map-lines/{lid}/duplicate-manual')
def hosted_map_line_duplicate(lid:str,request:Request):
    pid=_pid(request);ids=_locate('map_line',pid,lid);source=_get('map_line',ids);payload={k:v for k,v in source.items() if k not in {'id','line_id','generation','created_at','updated_at'}};payload['source_type']='manual';payload['name']=str(payload.get('name') or payload.get('title') or 'Line')+' Copy';new=_create('map_line',{'project_id':pid,'map_id':ids['map_id']},payload)
    for item in _list('map_item',{'project_id':pid,'map_id':ids['map_id'],'line_id':lid}):_create('map_item',{'project_id':pid,'map_id':ids['map_id'],'line_id':str(new['id'])},{k:v for k,v in item.items() if k not in {'id','item_id','generation','created_at','updated_at'}})
    return new

@router.post('/api/documents/{did}/make-manuscript')
def hosted_make_manuscript(did:str,request:Request):
    pid=_pid(request)
    for tab in _list('document_tab',{'project_id':pid}):
        if bool(tab.get('is_manuscript')) and str(tab.get('id'))!=did:_update('document_tab',{'project_id':pid,'tab_id':str(tab.get('id'))},{'is_manuscript':False})
    return _update('document_tab',{'project_id':pid,'tab_id':did},{'is_manuscript':True})

# ---------------------------------------------------------------------------
# Review-cycle parity that remains fully cloud-authoritative in M5
# ---------------------------------------------------------------------------

_REVIEW_CYCLE_TEMPLATES={
    'alpha':{'name':'Alpha Reading','description':'An early reader pass for premise, clarity, major confusion, pacing, and whether the story is working before deeper revision.','permission_read':True,'permission_comment':True,'permission_suggest':False,'permission_feedback':True},
    'developmental':{'name':'Developmental Review','description':'A big-picture pass for structure, pacing, character arcs, clarity, and story logic.','permission_read':True,'permission_comment':True,'permission_suggest':False,'permission_feedback':True},
    'authenticity':{'name':'Sensitivity / Authenticity Review','description':'A focused pass for identity, lived experience, cultural context, representation, and authenticity concerns.','permission_read':True,'permission_comment':True,'permission_suggest':False,'permission_feedback':True},
    'technical':{'name':'Technical / Subject Matter Review','description':'A specialist pass for factual accuracy, terminology, procedures, continuity, and domain-specific details.','permission_read':True,'permission_comment':True,'permission_suggest':True,'permission_feedback':True},
    'beta':{'name':'Beta Reading','description':'A reader-response pass for engagement, confusion, expectations, emotional beats, and overall experience.','permission_read':True,'permission_comment':True,'permission_suggest':False,'permission_feedback':True},
    'line':{'name':'Line Review','description':'A sentence-level pass for clarity, voice, rhythm, repetition, and precise wording.','permission_read':True,'permission_comment':True,'permission_suggest':True,'permission_feedback':False},
    'copy':{'name':'Copy Edit','description':'A mechanical pass for grammar, usage, consistency, spelling, punctuation, and style-guide adherence.','permission_read':True,'permission_comment':True,'permission_suggest':True,'permission_feedback':False},
    'proof':{'name':'Proofreading','description':'A final-pass review for typos, punctuation, spelling, formatting, and small correctness issues.','permission_read':True,'permission_comment':True,'permission_suggest':True,'permission_feedback':False},
}
_REVIEW_POPULATE_PRESETS={
    'reader':{'name':'Reader Feedback','description':'A lightweight outside-reader cycle for early reactions and later reader experience.','reviews':['alpha','beta']},
    'editorial':{'name':'Editorial Cycle','description':'A practical editing sequence from big-picture revision through sentence mechanics and final proof.','reviews':['developmental','line','copy','proof']},
    'full':{'name':'Full Review Cycle','description':'A complete fiction-oriented path from early readers through structural revision, beta feedback, editing, and proof.','reviews':['alpha','developmental','beta','line','copy','proof']},
    'specialist':{'name':'Specialist Review','description':'Focused expert passes for authenticity and factual or domain-specific accuracy.','reviews':['authenticity','technical']},
}

def _active_review_snapshot(pid:str,rid:str) -> dict[str,Any]:
    review=_get('review',{'project_id':pid,'review_id':rid})
    sid=str(review.get('active_snapshot_id') or '')
    if sid:
        try:return _get('review_snapshot',{'project_id':pid,'review_id':rid,'snapshot_id':sid})
        except HTTPException:pass
    snapshots=_list('review_snapshot',{'project_id':pid,'review_id':rid})
    if not snapshots:raise HTTPException(409,detail='REVIEW_SNAPSHOT_001: Review has not been started.')
    return snapshots[-1]


def _review_feedback_rows(pid:str,rid:str,participant_id:str|None=None) -> dict[str,list[dict[str,Any]]]:
    def rows(logical:str):
        values=_list(logical,{'project_id':pid,'review_id':rid})
        if participant_id is not None:values=[row for row in values if str(row.get('participant_id') or '')==participant_id]
        return values
    return {'highlights':rows('review_highlight'),'comments':rows('review_comment'),'suggestions':rows('review_suggestion'),'feedback':rows('review_general_feedback')}


@router.get('/api/reviews/populate-options')
def hosted_review_populate_options():
    return [{'id':key,**value,'items':[{'id':item,**_REVIEW_CYCLE_TEMPLATES[item]} for item in value['reviews']]} for key,value in _REVIEW_POPULATE_PRESETS.items()]


@router.post('/api/reviews/populate')
async def hosted_review_populate(request:Request):
    body=await _body(request);preset_id=str(body.get('preset_id') or '').strip().lower();preset=_REVIEW_POPULATE_PRESETS.get(preset_id)
    if not preset:raise HTTPException(400,detail='REVIEW_POPULATE_001: Unknown Review populate preset.')
    pid=_pid(request);existing={str(row.get('name') or '').casefold() for row in _list('review',{'project_id':pid})};created=[]
    position=len(existing)
    names=body.get('names') if isinstance(body.get('names'),dict) else {};descriptions=body.get('descriptions') if isinstance(body.get('descriptions'),dict) else {}
    for key in preset['reviews']:
        template=_REVIEW_CYCLE_TEMPLATES[key];base=str(names.get(key) or template['name']).strip()[:160] or template['name'];name=base;number=2
        while name.casefold() in existing:name=f'{base} ({number})';number+=1
        payload={**template,'name':name,'description':str(descriptions.get(key) or template['description'])[:4000],'position':position,'status':'draft','reviewer_visibility':'private','scope_type':'manuscript'}
        created.append(_create('review',{'project_id':pid},payload));existing.add(name.casefold());position+=1
    return {'created':created,'count':len(created),'preset_id':preset_id}


@router.get('/api/reviews/{rid}/participants/{participant_id}/reader')
def hosted_review_participant_reader(rid:str,participant_id:str,request:Request):
    pid=_pid(request);review=_get('review',{'project_id':pid,'review_id':rid});participant=_get('review_participant',{'project_id':pid,'review_id':rid,'participant_id':participant_id})
    if str(participant.get('status') or '')=='revoked':raise HTTPException(403,detail='REVIEW_READER_003: This participant\'s Review access has been revoked.')
    if str(review.get('status') or '') not in {'active','open','paused'}:raise HTTPException(409,detail='REVIEW_READER_001: This Review is not open.')
    snapshot=_active_review_snapshot(pid,rid);visibility=str(review.get('reviewer_visibility') or 'private')
    visible=None if visibility=='shared' else participant_id
    assignments=_list('review_questionnaire_assignment',{'project_id':pid,'review_id':rid})
    answers=_list('review_question_answer',{'project_id':pid,'review_id':rid})
    return {'review':review,'participant':participant,'snapshot':snapshot.get('snapshot') or snapshot,'feedback':_review_feedback_rows(pid,rid,visible),'form':{'questionnaires':assignments,'answers':[row for row in answers if str(row.get('participant_id') or '')==participant_id]},'bookmark':participant.get('bookmark')}


@router.post('/api/reviews/{rid}/participants/{participant_id}/begin')
def hosted_review_participant_begin(rid:str,participant_id:str,request:Request):
    pid=_pid(request);review=_get('review',{'project_id':pid,'review_id':rid})
    if str(review.get('status') or '') not in {'active','open'}:raise HTTPException(409,detail='REVIEW_PARTICIPANT_LIFECYCLE_001: Review must be Open.')
    participant=_get('review_participant',{'project_id':pid,'review_id':rid,'participant_id':participant_id})
    if str(participant.get('status') or '')=='revoked':raise HTTPException(409,detail='REVIEW_PARTICIPANT_LIFECYCLE_002: Revoked participants cannot enter Reviewer Mode.')
    return _update('review_participant',{'project_id':pid,'review_id':rid,'participant_id':participant_id},{'status':'in_progress','started':True})


@router.put('/api/reviews/{rid}/participants/{participant_id}/bookmark')
async def hosted_review_participant_bookmark(rid:str,participant_id:str,request:Request):
    pid=_pid(request);body=await _body(request);_active_review_snapshot(pid,rid)
    participant=_get('review_participant',{'project_id':pid,'review_id':rid,'participant_id':participant_id})
    if str(participant.get('status') or '')=='revoked':raise HTTPException(409,detail='REVIEW_BOOKMARK_REVOKED')
    bookmark=body.get('selection') if isinstance(body.get('selection'),dict) else body
    return _update('review_participant',{'project_id':pid,'review_id':rid,'participant_id':participant_id},{'bookmark':bookmark})


@router.delete('/api/reviews/{rid}/participants/{participant_id}/bookmark')
def hosted_review_participant_bookmark_clear(rid:str,participant_id:str,request:Request):
    _update('review_participant',{'project_id':_pid(request),'review_id':rid,'participant_id':participant_id},{'bookmark':None});return {'ok':True}


@router.post('/api/reviews/{rid}/participants/{participant_id}/complete')
def hosted_review_participant_complete(rid:str,participant_id:str,request:Request):
    pid=_pid(request);review=_get('review',{'project_id':pid,'review_id':rid})
    if str(review.get('status') or '') not in {'active','open'}:raise HTTPException(409,detail='REVIEW_PARTICIPANT_LIFECYCLE_001: Review must be Open.')
    return _update('review_participant',{'project_id':pid,'review_id':rid,'participant_id':participant_id},{'status':'completed','completed':True})


@router.post('/api/reviews/{rid}/questionnaires')
async def hosted_review_questionnaire_attach(rid:str,request:Request):
    pid=_pid(request);body=await _body(request);qid=str(body.get('questionnaire_id') or body.get('id') or '')
    if not qid:raise HTTPException(400,detail='QUESTIONNAIRE_ID_REQUIRED')
    _get('questionnaire',{'project_id':pid,'questionnaire_id':qid})
    return _create('review_questionnaire_assignment',{'project_id':pid,'review_id':rid},{'questionnaire_id':qid},entity_id=qid)


@router.delete('/api/reviews/{rid}/questionnaires/{questionnaire_id}')
def hosted_review_questionnaire_detach(rid:str,questionnaire_id:str,request:Request):
    return _delete('review_questionnaire_assignment',{'project_id':_pid(request),'review_id':rid,'questionnaire_id':questionnaire_id})


@router.put('/api/reviews/{rid}/participants/{participant_id}/feedback')
async def hosted_review_general_feedback(rid:str,participant_id:str,request:Request):
    pid=_pid(request);body=await _body(request);snapshot=_active_review_snapshot(pid,rid)
    current=[row for row in _list('review_general_feedback',{'project_id':pid,'review_id':rid}) if str(row.get('participant_id') or '')==participant_id]
    payload={**body,'participant_id':participant_id,'snapshot_id':snapshot.get('id')}
    if current:return _update('review_general_feedback',{'project_id':pid,'review_id':rid,'feedback_id':str(current[0]['id'])},payload)
    return _create('review_general_feedback',{'project_id':pid,'review_id':rid},payload)


def _review_participant_feedback_create(logical:str,rid:str,participant_id:str,pid:str,body:dict[str,Any]):
    participant=_get('review_participant',{'project_id':pid,'review_id':rid,'participant_id':participant_id})
    if str(participant.get('status') or '')=='revoked':raise HTTPException(409,detail='REVIEW_PARTICIPANT_REVOKED')
    snapshot=_active_review_snapshot(pid,rid)
    return _create(logical,{'project_id':pid,'review_id':rid},{**body,'participant_id':participant_id,'snapshot_id':snapshot.get('id'),'status':str(body.get('status') or 'open')})


@router.post('/api/reviews/{rid}/participants/{participant_id}/highlights')
async def hosted_review_highlight_create(rid:str,participant_id:str,request:Request):return _review_participant_feedback_create('review_highlight',rid,participant_id,_pid(request),await _body(request))
@router.post('/api/reviews/{rid}/participants/{participant_id}/comments')
async def hosted_review_comment_create(rid:str,participant_id:str,request:Request):return _review_participant_feedback_create('review_comment',rid,participant_id,_pid(request),await _body(request))
@router.post('/api/reviews/{rid}/participants/{participant_id}/suggestions')
async def hosted_review_suggestion_create(rid:str,participant_id:str,request:Request):return _review_participant_feedback_create('review_suggestion',rid,participant_id,_pid(request),await _body(request))


_REVIEW_FEEDBACK_KIND={'highlight':'review_highlight','highlights':'review_highlight','comment':'review_comment','comments':'review_comment','suggestion':'review_suggestion','suggestions':'review_suggestion'}

@router.patch('/api/reviews/{rid}/feedback/{kind}/{item_id}/status')
async def hosted_review_feedback_status(rid:str,kind:str,item_id:str,request:Request):
    logical=_REVIEW_FEEDBACK_KIND.get(str(kind).lower())
    if not logical:raise HTTPException(400,detail='REVIEW_FEEDBACK_KIND_INVALID')
    ids=_locate(logical,_pid(request),item_id);row=_get(logical,ids)
    if str(row.get('review_id') or rid)!=rid:raise HTTPException(404,detail='REVIEW_FEEDBACK_NOT_FOUND')
    body=await _body(request);status=str(body.get('status') or '').strip()
    if not status:raise HTTPException(400,detail='REVIEW_FEEDBACK_STATUS_REQUIRED')
    return _update(logical,ids,{'status':status})


@router.post('/api/reviews/{rid}/feedback/{kind}/{item_id}/task')
async def hosted_review_feedback_task(rid:str,kind:str,item_id:str,request:Request):
    logical=_REVIEW_FEEDBACK_KIND.get(str(kind).lower())
    if not logical:raise HTTPException(400,detail='REVIEW_FEEDBACK_KIND_INVALID')
    item=_get(logical,_locate(logical,_pid(request),item_id));body=await _body(request)
    title=str(body.get('title') or item.get('title') or item.get('comment') or item.get('text') or f'Review {kind}')[:240]
    return _create('task',{'project_id':_pid(request)},{'title':title,'status':'open','source_type':logical,'source_id':item_id,'review_id':rid})


@router.get('/api/review-items/{kind}/{item_id}')
def hosted_review_item_context(kind:str,item_id:str,request:Request):
    logical=_REVIEW_FEEDBACK_KIND.get(str(kind).lower())
    if not logical:raise HTTPException(400,detail='REVIEW_FEEDBACK_KIND_INVALID')
    row=_get(logical,_locate(logical,_pid(request),item_id));return {'kind':kind,'item':row}


@router.post('/api/reviews/{rid}/suggestions/{suggestion_id}/reject')
def hosted_review_suggestion_reject(rid:str,suggestion_id:str,request:Request):
    ids=_locate('review_suggestion',_pid(request),suggestion_id);return _update('review_suggestion',ids,{'status':'rejected'})


@router.post('/api/reviews/{rid}/suggestions/{suggestion_id}/accept')
def hosted_review_suggestion_accept(rid:str,suggestion_id:str,request:Request):
    pid=_pid(request);ids=_locate('review_suggestion',pid,suggestion_id);suggestion=_get('review_suggestion',ids)
    block_id=str(suggestion.get('block_id') or suggestion.get('start_block_id') or '')
    replacement=str(suggestion.get('replacement_text') or suggestion.get('replacement') or '')
    if block_id and replacement:
        block=_get('manuscript_block',{'project_id':pid,'block_id':block_id});text=str(block.get('text_content') or '')
        start=max(0,int(suggestion.get('start_offset') or 0));end=max(start,int(suggestion.get('end_offset') or start))
        if end>len(text):raise HTTPException(409,detail='REVIEW_SUGGESTION_STALE')
        original=str(suggestion.get('original_text') or '')
        if original and text[start:end]!=original:raise HTTPException(409,detail='REVIEW_SUGGESTION_STALE')
        new=text[:start]+replacement+text[end:]
        _update('manuscript_block',{'project_id':pid,'block_id':block_id},{'text_content':new,'content_html':html.escape(new)})
    return _update('review_suggestion',ids,{'status':'accepted'})


@router.post('/api/reviews/{rid}/participants/populate')
async def hosted_review_participants_populate(rid:str,request:Request):
    pid=_pid(request);body=await _body(request);source_id=str(body.get('source_review_id') or '')
    if not source_id or source_id==rid:raise HTTPException(400,detail='REVIEW_PARTICIPANT_006: Choose a different Review to reuse participants from.')
    existing={str(row.get('display_name') or '').casefold() for row in _list('review_participant',{'project_id':pid,'review_id':rid})};created=[]
    for source in _list('review_participant',{'project_id':pid,'review_id':source_id}):
        name=str(source.get('display_name') or '').strip()
        if not name or name.casefold() in existing:continue
        created.append(_create('review_participant',{'project_id':pid,'review_id':rid},{'display_name':name,'status':'not_started'}));existing.add(name.casefold())
    return {'created':created,'count':len(created)}


@router.post('/api/reviews/{rid}/checkpoint/update')
def hosted_review_checkpoint_update(rid:str,request:Request):
    pid=_pid(request);review=_get('review',{'project_id':pid,'review_id':rid})
    if str(review.get('status') or '')!='paused':raise HTTPException(409,detail='REVIEW_CHECKPOINT_001: Pause this Review before updating its Checkpoint.')
    snapshot=_create('review_snapshot',{'project_id':pid,'review_id':rid},{'snapshot':_project_snapshot(pid),'status':'paused','replaces_snapshot_id':review.get('active_snapshot_id')})
    return _update('review',{'project_id':pid,'review_id':rid},{'active_snapshot_id':snapshot['id']})


@router.post('/api/reviews/{rid}/rounds/{snapshot_id}/close')
def hosted_review_round_close(rid:str,snapshot_id:str,request:Request):
    pid=_pid(request);_get('review_snapshot',{'project_id':pid,'review_id':rid,'snapshot_id':snapshot_id})
    review=_get('review',{'project_id':pid,'review_id':rid});closed=list(review.get('closed_snapshot_ids') or [])
    if snapshot_id not in closed:closed.append(snapshot_id)
    patch={'closed_snapshot_ids':closed,'status':'closed'}
    if str(review.get('active_snapshot_id') or '')==snapshot_id:patch['active_snapshot_id']=''
    return _update('review',{'project_id':pid,'review_id':rid},patch)


@router.post('/api/reviews/{rid}/rounds/{snapshot_id}/reopen')
def hosted_review_round_reopen(rid:str,snapshot_id:str,request:Request):
    pid=_pid(request);_get('review_snapshot',{'project_id':pid,'review_id':rid,'snapshot_id':snapshot_id})
    review=_get('review',{'project_id':pid,'review_id':rid});closed=[sid for sid in list(review.get('closed_snapshot_ids') or []) if str(sid)!=snapshot_id]
    return _update('review',{'project_id':pid,'review_id':rid},{'status':'active','active_snapshot_id':snapshot_id,'closed_snapshot_ids':closed})

# ---------------------------------------------------------------------------
# Manuscript image blocks on committed hosted assets
# ---------------------------------------------------------------------------

def _chapter_blocks(pid:str,chapter_id:str) -> list[dict[str,Any]]:
    return sorted([row for row in _all_blocks(pid) if str(row.get('chapter_id') or '')==str(chapter_id)],key=lambda row:(float(row.get('position') or 0),str(row.get('id') or '')))


def _renumber_chapter(pid:str,chapter_id:str,ordered_ids:list[str]) -> None:
    for position,bid in enumerate(ordered_ids):
        block=_get('manuscript_block',{'project_id':pid,'block_id':bid})
        if float(block.get('position') or 0)!=float(position):_update('manuscript_block',{'project_id':pid,'block_id':bid},{'position':position})


@router.post('/api/manuscript/images/insert')
async def hosted_manuscript_image_insert(request:Request):
    body=await _body(request);pid=_pid(request);image_path=str(body.get('image_path') or '')
    if not image_path:raise HTTPException(400,detail='IMAGE_PATH_REQUIRED')
    try:
        if not runtime.assets.exists(image_path):raise HTTPException(404,detail='Image asset not found')
    except ValueError as exc:raise HTTPException(400,detail='Invalid project image path') from exc
    target_id=str(body.get('target_block_id') or '')
    target=_get('manuscript_block',{'project_id':pid,'block_id':target_id});chapter_id=str(target.get('chapter_id') or '')
    if not chapter_id:raise HTTPException(409,detail='MANUSCRIPT_IMAGE_TARGET_CHAPTER_REQUIRED')
    placement=str(body.get('placement') or 'at_offset')
    if placement not in {'at_offset','before','after','row_before','row_after'}:raise HTTPException(400,detail='Invalid image placement')
    blocks=_chapter_blocks(pid,chapter_id);ids=[str(row['id']) for row in blocks];index=ids.index(target_id)
    scene_id=target.get('scene_id');tab_id=target.get('tab_id')
    if placement=='at_offset' and str(target.get('block_type') or 'paragraph')=='paragraph':
        text=str(target.get('text_content') or '');offset=max(0,min(int(body.get('offset') or 0),len(text)))
        if 0<offset<len(text):
            left=text[:offset];right=text[offset:]
            _update('manuscript_block',{'project_id':pid,'block_id':target_id},{'text_content':left,'content_html':html.escape(left)})
            right_block=_create('manuscript_block',{'project_id':pid},{'chapter_id':chapter_id,'tab_id':tab_id,'scene_id':scene_id,'position':index+2,'block_type':'paragraph','semantic_style':str(target.get('semantic_style') or 'body'),'text_content':right,'content_html':html.escape(right)})
            ids.insert(index+1,'__image__');ids.insert(index+2,str(right_block['id']))
        elif offset<=0:ids.insert(index,'__image__')
        else:ids.insert(index+1,'__image__')
    elif placement in {'before','row_before'}:ids.insert(index,'__image__')
    else:ids.insert(index+1,'__image__')
    insert_index=ids.index('__image__')
    row_id=''
    if placement.startswith('row_') and str(target.get('block_type') or '')=='image':
        row_id=str(target.get('image_row_id') or ('image_row_'+secrets.token_hex(8)))
        if not target.get('image_row_id'):_update('manuscript_block',{'project_id':pid,'block_id':target_id},{'image_row_id':row_id})
    created=_create('manuscript_block',{'project_id':pid},{
        'chapter_id':chapter_id,'tab_id':tab_id,'scene_id':scene_id,'position':insert_index,
        'block_type':'image','semantic_style':'image','content_html':'','text_content':'',
        'image_path':image_path,'image_width':max(5,min(100,int(body.get('image_width') or target.get('image_width') or 100))),
        'image_footer':str(body.get('image_footer') or '')[:4000],
        'image_description':str(body.get('image_description') or '')[:12000],
        'image_row_id':row_id,
    })
    ids[insert_index]=str(created['id']);_renumber_chapter(pid,chapter_id,ids)
    return {'ok':True,'block_id':created['id'],'chapter_id':chapter_id}


@router.post('/api/manuscript/images/{block_id}/move')
async def hosted_manuscript_image_move(block_id:str,request:Request):
    body=await _body(request);pid=_pid(request);source=_get('manuscript_block',{'project_id':pid,'block_id':block_id})
    if str(source.get('block_type') or '')!='image':raise HTTPException(404,detail='Image block not found')
    target_id=str(body.get('target_block_id') or '');target=_get('manuscript_block',{'project_id':pid,'block_id':target_id})
    if block_id==target_id:return {'ok':True,'block_id':block_id,'chapter_id':source.get('chapter_id')}
    if str(source.get('tab_id') or '')!=str(target.get('tab_id') or ''):raise HTTPException(409,detail='Move images within the same manuscript document')
    placement=str(body.get('placement') or 'before')
    if placement not in {'at_offset','before','after','row_before','row_after'}:raise HTTPException(400,detail='Invalid image placement')
    source_chapter=str(source.get('chapter_id') or '');target_chapter=str(target.get('chapter_id') or '')
    source_ids=[str(row['id']) for row in _chapter_blocks(pid,source_chapter) if str(row.get('id'))!=block_id]
    target_ids=source_ids if source_chapter==target_chapter else [str(row['id']) for row in _chapter_blocks(pid,target_chapter) if str(row.get('id'))!=block_id]
    target_index=target_ids.index(target_id);target_index+=1 if placement in {'after','row_after'} else 0
    target_ids.insert(target_index,block_id)
    patch={'chapter_id':target_chapter,'tab_id':target.get('tab_id'),'scene_id':target.get('scene_id'),'image_row_id':''}
    if placement.startswith('row_') and str(target.get('block_type') or '')=='image':
        row_id=str(target.get('image_row_id') or ('image_row_'+secrets.token_hex(8)));patch['image_row_id']=row_id
        if not target.get('image_row_id'):_update('manuscript_block',{'project_id':pid,'block_id':target_id},{'image_row_id':row_id})
    _update('manuscript_block',{'project_id':pid,'block_id':block_id},patch)
    if source_chapter!=target_chapter:_renumber_chapter(pid,source_chapter,source_ids)
    _renumber_chapter(pid,target_chapter,target_ids)
    return {'ok':True,'block_id':block_id,'chapter_id':target_chapter}

# ---------------------------------------------------------------------------
# Hosted durable-history compatibility
# ---------------------------------------------------------------------------

def _revision_snapshot(revision_id:str) -> tuple[dict[str,Any],dict[str,Any]]:
    try:meta,envelope=runtime.history.read_revision(revision_id)
    except Exception as exc:raise HTTPException(404,detail='HISTORY_REVISION_NOT_FOUND') from exc
    try:
        payload=json.loads(gzip.decompress(envelope).decode('utf-8'))
    except Exception as exc:raise HTTPException(409,detail='HISTORY_REVISION_INVALID') from exc
    snapshot=payload.get('snapshot') if isinstance(payload,dict) else None
    if not isinstance(snapshot,dict):snapshot=payload if isinstance(payload,dict) else {}
    return meta,snapshot


@router.get('/api/history/v2/revisions/{revision_id}/assets/{asset_path:path}')
def hosted_history_revision_asset(revision_id:str,asset_path:str,request:Request):
    # Historical asset reachability/deletion is part of the M9 recovery contract. Do
    # not silently substitute a mutable current asset for a revision-pinned object.
    _revision_snapshot(revision_id)
    raise HTTPException(409,detail='HOSTED_HISTORY_ASSET_RECOVERY_UNAVAILABLE_UNTIL_M9')


@router.get('/api/history/v2/revisions/{revision_id}/compare-current')
def hosted_history_compare_current(revision_id:str,request:Request,scope:str='book',chapter_id:str|None=None):
    if scope not in {'chapter','book'}:raise HTTPException(400,detail='History comparison scope must be chapter or book')
    meta,historical=_revision_snapshot(revision_id);current=_project_snapshot(_pid(request))
    if scope=='chapter' and chapter_id:
        def chapter_slice(snapshot):
            chapters=[row for row in snapshot.get('chapters',[]) if str(row.get('id'))==str(chapter_id)]
            blocks=[row for row in snapshot.get('blocks',[]) if str(row.get('chapter_id'))==str(chapter_id)]
            return {'chapters':chapters,'blocks':blocks}
        historical=chapter_slice(historical);current=chapter_slice(current)
    old=json.dumps(historical,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    new=json.dumps(current,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    return {'revision':meta,'scope':scope,'changed':old!=new,'historical_hash':__import__('hashlib').sha256(old.encode()).hexdigest(),'current_hash':__import__('hashlib').sha256(new.encode()).hexdigest(),'historical':historical,'current':current}


@router.post('/api/history/v2/revisions/{revision_id}/checkpoint')
async def hosted_history_revision_checkpoint(revision_id:str,request:Request):
    meta,historical=_revision_snapshot(revision_id);body=await _body(request);name=str(body.get('name') or meta.get('reason') or meta.get('creation_reason') or 'History checkpoint')[:240]
    return _create('checkpoint',{'project_id':_pid(request)},{'name':name,'description':str(body.get('description') or '')[:4000],'scope':str(body.get('scope') or 'book'),'snapshot':historical,'source_revision_id':revision_id})


@router.post('/api/history/v2/revisions/{revision_id}/restore')
def hosted_history_revision_restore(revision_id:str,request:Request):
    _revision_snapshot(revision_id)
    # M9 freezes the destructive/recovery protocol. A partial M5 restore would be
    # worse than an explicit unavailable response because it could publish only a
    # subset of canonical collections.
    raise HTTPException(409,detail='HOSTED_HISTORY_RESTORE_UNAVAILABLE_UNTIL_M9')

# ---------------------------------------------------------------------------
# Hosted document and JSON import/export (request-memory only)
# ---------------------------------------------------------------------------

async def _upload_bytes(file:UploadFile,limit:int=64*1024*1024) -> bytes:
    chunks=[];total=0
    while True:
        chunk=await file.read(1024*1024)
        if not chunk:break
        total+=len(chunk)
        if total>limit:raise HTTPException(413,detail='HOSTED_UPLOAD_TOO_LARGE')
        chunks.append(bytes(chunk))
    return b''.join(chunks)


def _download_name(value:str,fallback:str) -> str:
    clean=re.sub(r'[^A-Za-z0-9._ -]+','_',str(value or '')).strip(' .')[:120]
    return clean or fallback


@router.get('/api/import/readiness')
def hosted_import_readiness(request:Request):
    from hosted.document_io import manuscript_is_default_blank
    pid=_pid(request);return {'empty':manuscript_is_default_blank(_domain(),pid,_uid()),'hosted':True,'supported_extensions':['.docx','.txt']}


@router.post('/api/import')
async def hosted_import_manuscript(request:Request,file:UploadFile=File(...),chapter_heading:str='heading1'):
    from hosted.document_io import manuscript_is_default_blank,parse_document_bytes,install_parsed_tabs
    pid=_pid(request);uid=_uid()
    if not manuscript_is_default_blank(_domain(),pid,uid):raise HTTPException(409,detail='HOSTED_IMPORT_MAIN_MANUSCRIPT_NOT_EMPTY')
    raw=await _upload_bytes(file)
    try:tabs,warnings=parse_document_bytes(raw,file.filename or 'book.docx',chapter_heading)
    except ValueError as exc:raise HTTPException(400 if str(exc)!='HOSTED_LEGACY_DOC_DISABLED' else 415,detail=str(exc)) from exc
    result=install_parsed_tabs(_domain(),pid,uid,tabs,replace_default=True)
    return {'ok':True,'reload':True,**result,'warnings':warnings}


@router.post('/api/import/new-project')
async def hosted_import_new_project(request:Request,file:UploadFile=File(...),chapter_heading:str='heading1',project_name:str=''):
    from hosted.document_io import parse_document_bytes,install_parsed_tabs
    raw=await _upload_bytes(file)
    try:tabs,warnings=parse_document_bytes(raw,file.filename or 'book.docx',chapter_heading)
    except ValueError as exc:raise HTTPException(400 if str(exc)!='HOSTED_LEGACY_DOC_DISABLED' else 415,detail=str(exc)) from exc
    stem=(str(file.filename or 'Imported').rsplit('.',1)[0]).strip();project=runtime.projects.create(project_name or stem or 'Imported Project')
    pid=str(project['id'])
    try:result=install_parsed_tabs(runtime.projects.domain,pid,_uid(),tabs,replace_default=True)
    except Exception:
        try:runtime.projects.delete(pid)
        except Exception:pass
        raise
    return {'ok':True,'reload':True,'project':project,**result,'warnings':warnings}


@router.post('/api/documents/import')
async def hosted_import_document_tabs(request:Request,file:UploadFile=File(...),after_document_id:str='',chapter_heading:str='heading1'):
    from hosted.document_io import parse_document_bytes,install_parsed_tabs
    raw=await _upload_bytes(file)
    try:tabs,warnings=parse_document_bytes(raw,file.filename or 'document.docx',chapter_heading)
    except ValueError as exc:raise HTTPException(400 if str(exc)!='HOSTED_LEGACY_DOC_DISABLED' else 415,detail=str(exc)) from exc
    result=install_parsed_tabs(_domain(),_pid(request),_uid(),tabs,replace_default=False,after_document_id=after_document_id)
    return {'ok':True,'reload':False,**result,'warnings':warnings}


@router.get('/api/export/docx')
def hosted_export_docx(request:Request):
    from hosted.document_io import export_docx_bytes
    pid=_pid(request);project=runtime.projects.get(pid);payload=export_docx_bytes(_domain(),runtime.assets,project,pid,_uid());name=_download_name(project.get('name') or project.get('title'),'Breadmaker')+'.docx'
    return Response(payload,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':f'attachment; filename="{name}"','Cache-Control':'private, no-store'})


@router.post('/api/export/pdf')
async def hosted_export_pdf(request:Request):
    from hosted.document_io import export_pdf_bytes
    # Consume options even though the first M5 cloud renderer intentionally uses the
    # stable server defaults; keeping the body accepted preserves frontend semantics.
    try:await _body(request)
    except Exception:pass
    pid=_pid(request);project=runtime.projects.get(pid);payload=export_pdf_bytes(_domain(),runtime.assets,project,pid,_uid());name=_download_name(project.get('name') or project.get('title'),'Breadmaker')+'.pdf'
    return Response(payload,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="{name}"','Cache-Control':'private, no-store'})


async def _hosted_json_upload(file:UploadFile) -> dict[str,Any]:
    raw=await _upload_bytes(file,32*1024*1024)
    try:value=json.loads(raw.decode('utf-8'))
    except Exception as exc:raise HTTPException(400,detail='HOSTED_JSON_IMPORT_INVALID') from exc
    if not isinstance(value,dict):raise HTTPException(400,detail='HOSTED_JSON_IMPORT_INVALID')
    return value


@router.post('/api/projects/import-json/preview')
async def hosted_project_json_preview(file:UploadFile=File(...)):
    from hosted.packages import HostedProjectPackageService
    payload=await _hosted_json_upload(file)
    try:preview=HostedProjectPackageService(runtime.projects).preview_json(payload)
    except ValueError as exc:raise HTTPException(400,detail=str(exc)) from exc
    return {'filename':file.filename or 'project.json',**preview}


@router.post('/api/projects/import-json')
async def hosted_project_json_import(file:UploadFile=File(...)):
    from hosted.packages import HostedProjectPackageService
    payload=await _hosted_json_upload(file)
    try:project=HostedProjectPackageService(runtime.projects).import_json(payload,_uid())
    except ValueError as exc:raise HTTPException(400,detail=str(exc)) from exc
    return {'ok':True,'reload':True,'project':project}


@router.post('/api/json-import/preview')
async def hosted_any_json_preview(file:UploadFile=File(...)):
    payload=await _hosted_json_upload(file)
    if str(payload.get('format') or '')!='breadmaker-hosted-json':raise HTTPException(409,detail='HOSTED_FRAGMENT_JSON_IMPORT_NOT_YET_MIGRATED')
    from hosted.packages import HostedProjectPackageService
    return {'kind':'project','filename':file.filename or 'project.json',**HostedProjectPackageService(runtime.projects).preview_json(payload),'targets':[]}


@router.post('/api/json-import')
async def hosted_any_json_import(file:UploadFile=File(...)):
    payload=await _hosted_json_upload(file)
    if str(payload.get('format') or '')!='breadmaker-hosted-json':raise HTTPException(409,detail='HOSTED_FRAGMENT_JSON_IMPORT_NOT_YET_MIGRATED')
    from hosted.packages import HostedProjectPackageService
    project=HostedProjectPackageService(runtime.projects).import_json(payload,_uid());return {'ok':True,'kind':'project','reload':True,'project':project}


@router.get('/api/projects/{project_id}/export-json')
def hosted_project_json_export(project_id:str,request:Request):
    if project_id!=_pid(request):raise HTTPException(409,detail='PROJECT_JSON_CONTEXT_MISMATCH')
    from hosted.packages import HostedProjectPackageService
    payload=HostedProjectPackageService(runtime.projects).export_json(project_id,_uid());raw=(json.dumps(payload,ensure_ascii=False,indent=2)+'\n').encode('utf-8');name=_download_name(payload.get('title'),'Breadmaker')+'.breadmaker.hosted.json'
    return Response(raw,media_type='application/json',headers={'Content-Disposition':f'attachment; filename="{name}"','Cache-Control':'private, no-store'})

# ---------------------------------------------------------------------------
# Explicit later-milestone hosted capability boundaries
# ---------------------------------------------------------------------------

@router.get('/api/portable-review/status')
def hosted_portable_review_status():
    # Standalone BreadReview mode is intentionally not made process-authoritative in
    # M5. M8 owns the horizontal/process-lifecycle audit for this temporary session.
    return {'active':False,'hosted':True,'available':False,'reason':'HOSTED_PORTABLE_REVIEW_UNAVAILABLE_UNTIL_M8'}

@router.post('/api/portable-review/open-temporary')
@router.post('/api/portable-review/unlock')
@router.get('/api/portable-review/package')
@router.post('/api/portable-review/close-temporary')
@router.post('/api/portable-review/import-current')
@router.get('/api/portable-review/reader')
@router.post('/api/portable-review/begin')
@router.post('/api/portable-review/complete')
@router.put('/api/portable-review/bookmark')
@router.delete('/api/portable-review/bookmark')
@router.post('/api/portable-review/highlights')
@router.post('/api/portable-review/comments')
@router.post('/api/portable-review/suggestions')
@router.put('/api/portable-review/feedback')
@router.get('/api/portable-review/assets/{asset_name}')
@router.get('/api/reviews/{rid}/participants/{participant_id}/breadreview')
@router.post('/api/reviews/{rid}/participants/{participant_id}/breadreview')
@router.post('/api/reviews/import-breadreview')
def hosted_portable_review_unavailable(asset_name:str='',rid:str='',participant_id:str=''):
    raise HTTPException(409,detail='HOSTED_PORTABLE_REVIEW_UNAVAILABLE_UNTIL_M8')


@router.post('/api/backup-destinations/pick-folder')
@router.get('/api/backup-destinations')
@router.post('/api/backup-destinations')
@router.get('/api/backup-destinations/google-drive/config')
@router.post('/api/backup-destinations/google-drive/oauth/start')
@router.get('/api/backup-destinations/google-drive/oauth/callback')
@router.get('/api/backup-destinations/google-drive/oauth/status/{state}')
@router.post('/api/backup-destinations/webdav')
@router.put('/api/backup-destinations/{destination_id}/webdav')
@router.put('/api/backup-destinations/{destination_id}')
@router.delete('/api/backup-destinations/{destination_id}')
@router.post('/api/backup-destinations/{destination_id}/now')
@router.post('/api/backup-destinations/catch-up')
@router.get('/api/backup-destinations/{destination_id}/backups')
@router.get('/api/backup-destinations/{destination_id}/discover')
@router.post('/api/backup-destinations/{destination_id}/backups/{filename}/restore-as-project')
@router.post('/api/backup')
@router.get('/api/backups')
@router.post('/api/backups/{filename}/restore-as-project')
def hosted_backup_recovery_unavailable(destination_id:str='',state:str='',filename:str=''):
    # M9 owns cloud backup/recovery/deletion. Returning a deterministic capability
    # error is safer than emulating removable/local destinations on Cloud Run.
    raise HTTPException(409,detail='HOSTED_BACKUP_UNAVAILABLE_UNTIL_M9')
