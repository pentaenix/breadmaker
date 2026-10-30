from __future__ import annotations

"""Annotations API/domain routes.

Extracted mechanically from the pre-10.1 composition module. Route behavior and
function names are intentionally unchanged; dependencies are declared explicitly below; cross-feature calls resolve to their
canonical owner lazily to avoid import cycles.
"""

from typing import Optional
import json

from fastapi import APIRouter

from backend.dependencies import feature_call
from backend.environment import runtime
from fastapi import HTTPException
from storage import mutations as storage_mutations
from storage import values as storage_values
from api.shared_contracts import BaseModel
from .contracts import AnnotationBatch, AnnotationClear, AnnotationItem, AnnotationUpdate, LabelCreate, EditingSearchRequest
from .editing_lexicon import PASSES
from .editing_search import find_matches
from .marker_colors import recommend_marker_color
from .editing_descriptions import DESCRIPTIONS, LEGACY_DESCRIPTION
from backend.content import (
    VALID_ANNOTATION_TYPES,
    normalize_color,
)
from backend.data_access import (
    _delete_polymorphic_references,
    allrows,
    one,
    project,
)

# Explicit composition-root dependencies.

router = APIRouter()

MAX_EDITING_CHARACTERS=5_000_000
MAX_EDITING_BLOCK_CHARACTERS=100_000
MAX_EDITING_MATCHES=20_000
EDITING_REGISTRY='editing-search-markers/v1'

def _editing_registry(con,pid):
    row=one(con,'SELECT value_json FROM ui_state WHERE project_id=? AND key=?',(pid,EDITING_REGISTRY))
    try:
        value=json.loads(row['value_json']) if row else {}
        return value if isinstance(value,dict) else {}
    except (ValueError,TypeError):return {}

def _editing_marker(con,pid,kind,registry):
    saved=registry.get(kind)
    if isinstance(saved,dict) and type(saved.get('id')) is int and isinstance(saved.get('created_at'),str):
        marker=one(con,'SELECT * FROM markup_labels WHERE project_id=? AND id=? AND created_at=?',(pid,saved.get('id'),saved.get('created_at')))
        if marker:return marker
    # Ownership survives a renamed marker; names are only a fallback for reuse.
    marker=one(con,"SELECT l.* FROM markup_labels l JOIN annotations a ON a.label_id=l.id WHERE l.project_id=? AND a.project_id=? AND a.annotation_type='highlight' AND a.body=? ORDER BY a.id LIMIT 1",(pid,pid,'editing-search/v1:'+kind))
    return marker or one(con,'SELECT * FROM markup_labels WHERE project_id=? AND lower(trim(name))=lower(?)',(pid,PASSES[kind][0]))

def _remember_editing_marker(con,pid,kind,marker,registry):
    registry={key:value for key,value in registry.items() if key in PASSES}
    registry[kind]={'id':marker['id'],'created_at':marker['created_at']}
    con.execute('INSERT INTO ui_state(project_id,key,value_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(project_id,key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at',(pid,EDITING_REGISTRY,json.dumps(registry),storage_values.now()))

@router.get('/api/annotations/editing-search')
def editing_search_status(tab_id:int):
    if runtime.mode!='local':raise HTTPException(404)
    with storage_mutations.tx() as con:
        pid=project(con)['id']
        if not one(con,'SELECT id FROM document_tabs WHERE id=? AND project_id=?',(tab_id,pid)):raise HTTPException(404)
        registry=_editing_registry(con,pid);results={}
        for kind in PASSES:
            marker=_editing_marker(con,pid,kind,registry)
            count=con.execute("SELECT COUNT(*) FROM annotations WHERE project_id=? AND label_id=? AND annotation_type='highlight' AND body=? AND block_id IN (SELECT id FROM manuscript_blocks WHERE project_id=? AND tab_id=?)",(pid,marker['id'] if marker else None,'editing-search/v1:'+kind,pid,tab_id)).fetchone()[0]
            results[kind]={'marker':marker,'matches':count}
        return {'searches':results}

@router.post('/api/annotations/editing-search')
def editing_search(body:EditingSearchRequest):
    """One native, atomic editing pass; existing markers/Undo own persistence."""
    if runtime.mode!='local':raise HTTPException(404)
    name,_=PASSES[body.scan];stamp='editing-search/v1:'+body.scan
    with storage_mutations.tx() as con:
        pid=project(con)['id']
        if not one(con,'SELECT id FROM document_tabs WHERE id=? AND project_id=?',(body.tab_id,pid)):
            raise HTTPException(404,'EDITING_SEARCH_DOCUMENT_MISSING')
        registry=_editing_registry(con,pid)
        marker=_editing_marker(con,pid,body.scan,registry)
        if body.action in {'clear','remove'}:
            if marker is None:return {'marker':None,'matches':0,'added':0,'removed':0}
            _remember_editing_marker(con,pid,body.scan,marker,registry)
            if body.action=='remove':
                removed=con.execute("DELETE FROM annotations WHERE project_id=? AND label_id=? AND annotation_type='highlight'",(pid,marker['id'])).rowcount
                con.execute('DELETE FROM markup_labels WHERE project_id=? AND id=?',(pid,marker['id']))
                return {'marker':None,'matches':0,'added':0,'removed':removed}
            removed=con.execute("DELETE FROM annotations WHERE project_id=? AND label_id=? AND annotation_type='highlight' AND body=? AND block_id IN (SELECT id FROM manuscript_blocks WHERE project_id=? AND tab_id=?)",(pid,marker['id'],stamp,pid,body.tab_id)).rowcount
            return {'marker':marker,'matches':0,'added':0,'removed':removed}
        size,largest=con.execute("SELECT COALESCE(SUM(length(text_content)),0),COALESCE(MAX(length(text_content)),0) FROM manuscript_blocks WHERE project_id=? AND tab_id=? AND block_type='paragraph'",(pid,body.tab_id)).fetchone()
        if size>MAX_EDITING_CHARACTERS or largest>MAX_EDITING_BLOCK_CHARACTERS:raise HTTPException(413,'EDITING_SEARCH_TOO_LARGE')
        rows=allrows(con,"SELECT b.id,b.text_content FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.project_id=? AND b.tab_id=? AND b.block_type='paragraph' ORDER BY c.position,b.position,b.id",(pid,body.tab_id))
        matches=[]
        for row in rows:
            text=row['text_content'] or ''
            for start,end in find_matches(text,body.scan):
                matches.append((row['id'],start,end,text[start:end],text[max(0,start-45):start],text[end:end+45]))
                if len(matches)>MAX_EDITING_MATCHES:raise HTTPException(413,'EDITING_SEARCH_TOO_MANY_MATCHES')
        if marker is None and not matches:return {'marker':None,'matches':0,'added':0,'removed':0}
        now=storage_values.now()
        if marker is None:
            color=recommend_marker_color([row['color'] for row in allrows(con,'SELECT color FROM markup_labels WHERE project_id=?',(pid,))])
            lid=con.execute('INSERT INTO markup_labels(project_id,name,description,color,created_at,updated_at) VALUES(?,?,?,?,?,?)',(pid,name,DESCRIPTIONS[body.scan],color,now,now)).lastrowid
            marker=one(con,'SELECT * FROM markup_labels WHERE id=?',(lid,))
        elif marker['description']==LEGACY_DESCRIPTION and one(con,'SELECT id FROM annotations WHERE project_id=? AND label_id=? AND body=? LIMIT 1',(pid,marker['id'],stamp)):
            # Refresh only our exact old boilerplate, never user-written copy.
            con.execute('UPDATE markup_labels SET description=?,updated_at=? WHERE id=? AND project_id=?',(DESCRIPTIONS[body.scan],now,marker['id'],pid))
            marker=one(con,'SELECT * FROM markup_labels WHERE id=?',(marker['id'],))
        lid=marker['id']
        _remember_editing_marker(con,pid,body.scan,marker,registry)
        old=allrows(con,"SELECT * FROM annotations WHERE project_id=? AND label_id=? AND annotation_type='highlight' AND block_id IN (SELECT id FROM manuscript_blocks WHERE project_id=? AND tab_id=?)",(pid,lid,pid,body.tab_id))
        wanted={item[:4] for item in matches}
        existing={(a['block_id'],a['start_offset'],a['end_offset'],a['selected_text']) for a in old if a['body']!=stamp and not a['stale']}
        removed=0
        for annotation in old:
            key=(annotation['block_id'],annotation['start_offset'],annotation['end_offset'],annotation['selected_text'])
            owned=annotation['body']==stamp
            if owned and (body.action=='recheck' or key not in wanted or annotation['stale'] or key in existing):
                con.execute('DELETE FROM annotations WHERE id=? AND project_id=?',(annotation['id'],pid));removed+=1
            elif not annotation['stale']:existing.add(key)
        added=0
        for block,start,end,selected,prefix,suffix in matches:
            if (block,start,end,selected) in existing:continue
            con.execute("INSERT INTO annotations(project_id,block_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,body,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(pid,block,'highlight',lid,start,end,selected,prefix,suffix,stamp,'open',now,now))
            added+=1
        return {'marker':marker,'matches':len(matches),'added':added,'removed':removed}

# ---------------- Editing markers / annotations ----------------
@router.post("/api/labels")
def create_label(body:LabelCreate):
    color=normalize_color(body.color,False)
    name=body.name.strip()
    description=(body.description or '').strip()[:12000]
    if not name: raise HTTPException(400,"Marker needs a name.")
    with storage_mutations.tx() as con:
        p=project(con); t=storage_values.now()
        if one(con,"SELECT id FROM markup_labels WHERE project_id=? AND lower(name)=lower(?)",(p['id'],name)):
            raise HTTPException(409,f"A marker named “{name}” already exists.")
        if one(con,"SELECT id FROM markup_labels WHERE project_id=? AND lower(color)=lower(?)",(p['id'],color)):
            raise HTTPException(409,"That color is already used by another marker.")
        lid=con.execute("INSERT INTO markup_labels(project_id,name,description,color,created_at,updated_at) VALUES(?,?,?,?,?,?)",(p['id'],name,description,color,t,t)).lastrowid
        return one(con,"SELECT * FROM markup_labels WHERE id=?",(lid,))

class LabelUpdate(BaseModel): name:Optional[str]=None; color:Optional[str]=None; description:Optional[str]=None
@router.patch("/api/labels/{lid}")
def update_label(lid:int,body:LabelUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'color' in vals: vals['color']=normalize_color(vals['color'],False)
    if 'description' in vals: vals['description']=(vals['description'] or '').strip()[:12000]
    if 'name' in vals:
        vals['name']=(vals['name'] or '').strip()
        if not vals['name']: raise HTTPException(400,"Marker needs a name.")
    if vals:
        with storage_mutations.tx() as con:
            p=project(con)
            if not one(con,"SELECT id FROM markup_labels WHERE id=? AND project_id=?",(lid,p['id'])): raise HTTPException(404,"Marker not found")
            if 'name' in vals and one(con,"SELECT id FROM markup_labels WHERE project_id=? AND id<>? AND lower(name)=lower(?)",(p['id'],lid,vals['name'])):
                raise HTTPException(409,f"A marker named “{vals['name']}” already exists.")
            if 'color' in vals and one(con,"SELECT id FROM markup_labels WHERE project_id=? AND id<>? AND lower(color)=lower(?)",(p['id'],lid,vals['color'])):
                raise HTTPException(409,"That color is already used by another marker.")
            sets=', '.join(f"{k}=?" for k in vals)+", updated_at=?"
            con.execute(f"UPDATE markup_labels SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),lid))
            # Named color markers are the canonical canvas color meaning.
            # Nodes using the marker follow color edits automatically; removing
            # the marker still preserves the node's last concrete color via FK SET NULL.
            if 'color' in vals:
                con.execute("UPDATE canvas_nodes SET color=?,updated_at=? WHERE label_id=?",(vals['color'],storage_values.now(),lid))
                con.execute("UPDATE canvas_edges SET color=? WHERE marker_id=?",(vals['color'],lid))
    return {"ok":True}

@router.get("/api/labels/{lid}/usage")
def label_usage(lid:int):
    with storage_mutations.tx() as con:
        p=project(con)
        if not one(con,"SELECT id FROM markup_labels WHERE id=? AND project_id=?",(lid,p['id'])): raise HTTPException(404,"Marker not found")
        annotations=con.execute("SELECT COUNT(*) FROM annotations WHERE project_id=? AND label_id=?",(p['id'],lid)).fetchone()[0]
        tasks=con.execute("SELECT COUNT(*) FROM tasks WHERE project_id=? AND marker_id=?",(p['id'],lid)).fetchone()[0]
        nodes=con.execute("SELECT COUNT(*) FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE c.project_id=? AND n.label_id=?",(p['id'],lid)).fetchone()[0]
        edges=con.execute("SELECT COUNT(*) FROM canvas_edges e JOIN canvases c ON c.id=e.canvas_id WHERE c.project_id=? AND e.marker_id=?",(p['id'],lid)).fetchone()[0]
        return {"annotations":annotations,"tasks":tasks,"nodes":nodes,"edges":edges,"total":annotations+tasks+nodes+edges}

@router.delete("/api/labels/{lid}")
def delete_label(lid:int):
    with storage_mutations.tx() as con:
        p=project(con)
        if not one(con,"SELECT id FROM markup_labels WHERE id=? AND project_id=?",(lid,p['id'])): raise HTTPException(404,"Marker not found")
        con.execute("DELETE FROM markup_labels WHERE id=?",(lid,))
    return {"ok":True}

def _validate_annotation_items(con,pid:int,items:list[AnnotationItem],*,label:str='Annotation'):
    checked=[]
    for x in items:
        if bool(x.block_id)==bool(x.chapter_id):
            raise HTTPException(400,f'{label} must anchor to exactly one manuscript block or Chapter title')
        if x.block_id:
            target=one(con,"SELECT id,project_id,text_content FROM manuscript_blocks WHERE id=?",(x.block_id,))
            if not target or int(target['project_id'])!=int(pid): raise HTTPException(409,f'A selected manuscript block changed before the {label.lower()} could be saved')
            text=target['text_content'] or ''
        else:
            target=one(con,"SELECT id,project_id,title,is_implicit FROM chapters WHERE id=?",(int(x.chapter_id),))
            if not target or int(target['project_id'])!=int(pid) or int(target.get('is_implicit') or 0): raise HTTPException(409,f'A selected Chapter title changed before the {label.lower()} could be saved')
            text=target['title'] or ''
        if x.start_offset<0 or x.end_offset<x.start_offset or x.end_offset>len(text): raise HTTPException(400,f'{label} range is outside its manuscript target')
        checked.append((x,target,text))
    if not checked: raise HTTPException(400,f'Select manuscript text before adding {label.lower()}')
    return checked

def _validate_comment_items(con,pid:int,items:list[AnnotationItem]):
    return _validate_annotation_items(con,pid,items,label='Note')

def _clean_comment_body(value) -> str:
    return str(value or '').replace('\r\n','\n').replace('\r','\n').strip()

def _create_comment_thread(con,pid:int,items:list[AnnotationItem],body:str):
    text=_clean_comment_body(body)
    if not text: raise HTTPException(400,'Note cannot be empty')
    if len(text)>12000: raise HTTPException(400,'Note is too long')
    checked=_validate_comment_items(con,pid,items); t=storage_values.now()
    tid=con.execute("INSERT INTO comment_threads(project_id,status,created_at,updated_at) VALUES(?,?,?,?)",(pid,'open',t,t)).lastrowid
    mid=con.execute("INSERT INTO comment_messages(project_id,thread_id,body,created_at,updated_at) VALUES(?,?,?,?,?)",(pid,tid,text,t,t)).lastrowid
    ids=[]
    for x,_,_ in checked:
        aid=con.execute("INSERT INTO annotations(project_id,block_id,chapter_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,thread_id,body,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(pid,x.block_id,x.chapter_id,'comment',None,x.start_offset,x.end_offset,x.selected_text,x.prefix_context,x.suffix_context,tid,text,'open',t,t)).lastrowid
        ids.append(aid)
    return {"id":tid,"thread_id":tid,"message_id":mid,"ids":ids}

def _comment_payload(con,pid:int,thread:dict) -> dict:
    tid=int(thread['id'])
    anchors=allrows(con,"""SELECT a.id,a.block_id,a.chapter_id title_chapter_id,a.start_offset,a.end_offset,a.selected_text,a.prefix_context,a.suffix_context,a.stale,a.status,
        COALESCE(a.chapter_id,b.chapter_id) chapter_id,b.scene_id,CASE WHEN a.chapter_id IS NOT NULL THEN -1 ELSE b.position END block_position,
        c.position chapter_position,CASE WHEN c.is_implicit=1 THEN '' ELSE c.title END chapter_title,s.title scene_title,dt.position tab_position,
        CASE WHEN a.chapter_id IS NOT NULL THEN 1 ELSE 0 END chapter_title_anchor
        FROM annotations a LEFT JOIN manuscript_blocks b ON b.id=a.block_id LEFT JOIN chapters c ON c.id=COALESCE(a.chapter_id,b.chapter_id)
        LEFT JOIN scenes s ON s.id=b.scene_id LEFT JOIN document_tabs dt ON dt.id=c.tab_id
        WHERE a.project_id=? AND a.thread_id=? ORDER BY COALESCE(dt.position,0),COALESCE(c.position,0),CASE WHEN a.chapter_id IS NOT NULL THEN -1 ELSE b.position END,a.start_offset,a.id""",(pid,tid))
    messages=allrows(con,"SELECT id,body,created_at,updated_at FROM comment_messages WHERE project_id=? AND thread_id=? ORDER BY id",(pid,tid))
    first=messages[0]['body'] if messages else ''
    excerpt=' … '.join((a.get('selected_text') or '').strip() for a in anchors if (a.get('selected_text') or '').strip())
    anchor=anchors[0] if anchors else {}
    return {**thread,"anchors":anchors,"messages":messages,"first_message":first,"reply_count":max(0,len(messages)-1),"excerpt":excerpt[:600],
            "chapter_id":anchor.get('chapter_id'),"chapter_title":anchor.get('chapter_title') or '',"scene_title":anchor.get('scene_title') or '',
            "stale":bool(anchors) and all(bool(a.get('stale')) for a in anchors)}

@router.post("/api/annotations/batch")
def create_annotations(body:AnnotationBatch):
    if body.annotation_type not in VALID_ANNOTATION_TYPES: raise HTTPException(400,'Unknown annotation type')
    with storage_mutations.tx() as con:
        p=project(con)
        if body.annotation_type=='comment':
            return _create_comment_thread(con,p['id'],body.items,body.body)
        ids=[]; t=storage_values.now()
        if body.label_id is not None and not one(con,"SELECT id FROM markup_labels WHERE id=? AND project_id=?",(body.label_id,p['id'])): raise HTTPException(400,"Highlighter no longer exists")
        checked=_validate_annotation_items(con,p['id'],body.items,label='Annotation')
        for x,_,_ in checked:
            aid=con.execute("INSERT INTO annotations(project_id,block_id,chapter_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,body,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(p['id'],x.block_id,x.chapter_id,body.annotation_type,body.label_id,x.start_offset,x.end_offset,x.selected_text,x.prefix_context,x.suffix_context,body.body,'open',t,t)).lastrowid; ids.append(aid)
    return {"ids":ids}

@router.post("/api/annotations/clear-highlights")
def clear_highlights(body:AnnotationClear):
    """Remove highlight annotations only from selected prose or Chapter-title ranges."""
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; t=storage_values.now(); grouped={}
        checked=_validate_annotation_items(con,pid,body.items,label='Marker clear')
        for item,target,text in checked:
            key=('block',str(item.block_id)) if item.block_id else ('chapter',int(item.chapter_id))
            start=max(0,min(int(item.start_offset),len(text))); end=max(start,min(int(item.end_offset),len(text)))
            if end>start: grouped.setdefault(key,{'text':text,'ranges':[]})['ranges'].append((start,end))
        changed=0; deleted=[]
        for (kind,target_id),entry in grouped.items():
            ranges=sorted(entry['ranges']); merged=[]
            for start,end in ranges:
                if merged and start<=merged[-1][1]: merged[-1]=(merged[-1][0],max(merged[-1][1],end))
                else: merged.append((start,end))
            if kind=='block':
                rows=allrows(con,"SELECT * FROM annotations WHERE project_id=? AND block_id=? AND chapter_id IS NULL AND annotation_type='highlight' AND label_id IS NOT NULL",(pid,target_id))
            else:
                rows=allrows(con,"SELECT * FROM annotations WHERE project_id=? AND chapter_id=? AND block_id IS NULL AND annotation_type='highlight' AND label_id IS NOT NULL",(pid,target_id))
            text=entry['text']
            for row in rows:
                segments=[(int(row['start_offset']),int(row['end_offset']))]
                for clear_start,clear_end in merged:
                    next_segments=[]
                    for seg_start,seg_end in segments:
                        if clear_end<=seg_start or clear_start>=seg_end:
                            next_segments.append((seg_start,seg_end)); continue
                        if seg_start<clear_start: next_segments.append((seg_start,min(seg_end,clear_start)))
                        if seg_end>clear_end: next_segments.append((max(seg_start,clear_end),seg_end))
                    segments=[segment for segment in next_segments if segment[1]>segment[0]]
                original=(int(row['start_offset']),int(row['end_offset']))
                if segments==[original]: continue
                changed+=1
                if not segments:
                    con.execute("DELETE FROM annotations WHERE id=?",(row['id'],)); deleted.append(int(row['id'])); continue
                def values(start,end):
                    return (start,end,text[start:end],text[max(0,start-45):start],text[end:end+45])
                first=segments[0]; vals=values(*first)
                con.execute("UPDATE annotations SET start_offset=?,end_offset=?,selected_text=?,prefix_context=?,suffix_context=?,stale=0,updated_at=? WHERE id=?",(*vals,t,row['id']))
                for seg_start,seg_end in segments[1:]:
                    vals=values(seg_start,seg_end)
                    con.execute("INSERT INTO annotations(project_id,block_id,chapter_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,thread_id,body,status,stale,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(pid,row.get('block_id'),row.get('chapter_id'),'highlight',row['label_id'],*vals,row.get('thread_id'),row.get('body') or '',row.get('status') or 'open',0,row.get('created_at') or t,t))
        if deleted: _delete_polymorphic_references(con,pid,{'annotation':deleted})
        return {'changed':changed}

@router.patch("/api/annotations/{aid}")
def update_annotation(aid:int,body:AnnotationUpdate):
    vals=body.model_dump(exclude_unset=True); allowed={'status','body','label_id'}; vals={k:v for k,v in vals.items() if k in allowed}
    if 'status' in vals and vals['status'] not in {'open','resolved'}: raise HTTPException(400,"Annotation status must be open or resolved")
    if vals:
        with storage_mutations.tx() as con:
            p=project(con)
            if not one(con,"SELECT id FROM annotations WHERE id=? AND project_id=?",(aid,p['id'])): raise HTTPException(404,"Annotation not found")
            if vals.get('label_id') is not None and not one(con,"SELECT id FROM markup_labels WHERE id=? AND project_id=?",(vals['label_id'],p['id'])): raise HTTPException(400,"Highlighter no longer exists")
            sets=', '.join(f"{k}=?" for k in vals)+", updated_at=?"; con.execute(f"UPDATE annotations SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),aid))
    return {"ok":True}

@router.get("/api/annotations")
def list_annotations(label_id:Optional[int]=None,status:str='open'):
    with storage_mutations.tx() as con:
        p=project(con); q="SELECT a.*,l.name label_name,l.color label_color,COALESCE(a.chapter_id,b.chapter_id) resolved_chapter_id,CASE WHEN c.is_implicit=1 THEN '' ELSE c.title END chapter_title,s.title scene_title FROM annotations a LEFT JOIN markup_labels l ON l.id=a.label_id LEFT JOIN manuscript_blocks b ON b.id=a.block_id LEFT JOIN chapters c ON c.id=COALESCE(a.chapter_id,b.chapter_id) LEFT JOIN scenes s ON s.id=b.scene_id WHERE a.project_id=?"; args=[p['id']]
        if label_id is not None: q+=' AND a.label_id=?'; args.append(label_id)
        if status!='all': q+=' AND a.status=?'; args.append(status)
        q+=' ORDER BY c.position,CASE WHEN a.chapter_id IS NOT NULL THEN -1 ELSE b.position END,a.start_offset'
        rows=allrows(con,q,args)
        for row in rows: row['chapter_id']=row.pop('resolved_chapter_id',None)
        return rows

