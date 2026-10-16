from __future__ import annotations

"""Manuscript projection, Map anchors, and read projection services."""

import re
import threading
from typing import Optional

from fastapi import HTTPException
from backend.dependencies import feature_call
from backend.data_access import allrows, one
from storage import context as storage_context
from storage import values as storage_values
from . import domain

_MAP_CHRONOLOGY_CACHE:dict[tuple,dict]={}
_MAP_CHRONOLOGY_CACHE_LOCK=threading.RLock()
_MAP_CHRONOLOGY_CACHE_MAX=8

_describe_ref = feature_call('workspace_tools.references', '_describe_ref')
_entity_exists = feature_call('workspace_tools.references', '_entity_exists')

def _word_units(text:str)->int:
    return max(1,len(re.findall(r"\b[\w’'-]+\b",text or "",flags=re.UNICODE)))

def _manuscript_chronology_signature(con,pid:int,tab_id:int)->tuple:
    # MAX(updated_at) intentionally makes this independent from Map-only project
    # generations: changing geometry must not force a full manuscript reprojection.
    ch=con.execute("SELECT COUNT(*),COALESCE(MAX(updated_at),'') FROM chapters WHERE project_id=? AND tab_id=?",(pid,tab_id)).fetchone()
    bl=con.execute("SELECT COUNT(*),COALESCE(MAX(updated_at),'') FROM manuscript_blocks WHERE project_id=? AND tab_id=?",(pid,tab_id)).fetchone()
    sc=con.execute("SELECT COUNT(*),COALESCE(MAX(updated_at),'') FROM scenes WHERE project_id=? AND tab_id=?",(pid,tab_id)).fetchone()
    root=str(storage_context.current_project_root() or '')
    return (root,int(tab_id),int(ch[0] or 0),str(ch[1] or ''),int(bl[0] or 0),str(bl[1] or ''),int(sc[0] or 0),str(sc[1] or ''))

def _manuscript_chronology(con,pid:int)->dict:
    """Project the primary manuscript onto a 0..1 axis using prose length.

    The derived axis is cached by manuscript-specific update signatures. Map-only
    edits therefore reuse the projection instead of regex-scanning the entire book on
    every open, drag, Populate, or follow-up refresh.
    """
    tab=one(con,"SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(pid,))
    if not tab:return {"chapters":[],"scenes":[],"block_positions":{},"chapter_ranges":{},"scene_ranges":{},"chapter_names":{},"scene_names":{},"total_words":0,"total_units":1.0}
    signature=_manuscript_chronology_signature(con,pid,int(tab['id']))
    with _MAP_CHRONOLOGY_CACHE_LOCK:
        cached=_MAP_CHRONOLOGY_CACHE.get(signature)
        if cached is not None:return cached
    chapters=allrows(con,"SELECT id,title,position,is_implicit FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab['id']))
    chapter_ids={c['id'] for c in chapters};chapter_names={int(c['id']):str(c.get('title') or 'Chapter') for c in chapters};by_chapter={c['id']:[] for c in chapters}
    blocks=allrows(con,"SELECT id,text_content,scene_id,position,chapter_id,block_type FROM manuscript_blocks WHERE project_id=? AND tab_id=? ORDER BY chapter_id,position,id",(pid,tab['id']))
    weights={}
    for block in blocks:
        if block['chapter_id'] not in chapter_ids:continue
        weight=_word_units(block.get('text_content') or '') if block.get('block_type')!='image' else 40
        weights[block['id']]=weight;by_chapter[block['chapter_id']].append(block)
    total_words=sum(weights.values());total_units=float(total_words or 1)
    cursor=0.0;block_positions={};chapter_ranges={};scene_ranges={};chapter_axis=[]
    for chapter in chapters:
        rows=by_chapter[chapter['id']];start=cursor/total_units
        for block in rows:
            weight=float(weights.get(block['id'],1));bstart=cursor/total_units;bend=min(1.0,(cursor+weight)/total_units)
            block_positions[block['id']]={"start":bstart,"end":bend,"weight":weight,"text_length":len(block.get('text_content') or ''),"chapter_id":chapter['id'],"scene_id":block.get('scene_id')}
            if block.get('scene_id') is not None:
                sid=int(block['scene_id']);current=scene_ranges.get(sid)
                scene_ranges[sid]={"start":min(current['start'],bstart) if current else bstart,"end":max(current['end'],bend) if current else bend,"chapter_id":chapter['id'] if not current else current['chapter_id']}
            cursor+=weight
        if not rows:cursor+=1.0
        end=min(1.0,cursor/total_units);chapter_ranges[chapter['id']]={"start":start,"end":max(start,end)}
        if not chapter.get('is_implicit'):chapter_axis.append({"id":chapter['id'],"title":chapter['title'],"position":start,"end_position":max(start,end)})
    scene_names={int(r['id']):str(r.get('title') or 'Scene') for r in allrows(con,"SELECT id,title FROM scenes WHERE project_id=?",(pid,))}
    scene_axis=[{"id":sid,"title":scene_names.get(sid,"Scene"),"position":rng['start'],"end_position":rng['end'],"chapter_id":rng['chapter_id']} for sid,rng in scene_ranges.items()]
    scene_axis.sort(key=lambda x:(x['position'],x['id']))
    result={"chapters":chapter_axis,"scenes":scene_axis,"block_positions":block_positions,"chapter_ranges":chapter_ranges,"scene_ranges":scene_ranges,"chapter_names":chapter_names,"scene_names":scene_names,"total_words":total_words,"total_units":total_units}
    with _MAP_CHRONOLOGY_CACHE_LOCK:
        # Remove stale signatures for this same project/tab, then cap process memory.
        root,tab_id=signature[0],signature[1]
        for key in list(_MAP_CHRONOLOGY_CACHE):
            if key[0]==root and key[1]==tab_id and key!=signature:_MAP_CHRONOLOGY_CACHE.pop(key,None)
        _MAP_CHRONOLOGY_CACHE[signature]=result
        while len(_MAP_CHRONOLOGY_CACHE)>_MAP_CHRONOLOGY_CACHE_MAX:_MAP_CHRONOLOGY_CACHE.pop(next(iter(_MAP_CHRONOLOGY_CACHE)))
    return result

def _map_anchor_position(anchor_type:Optional[str],anchor_id:Optional[str],block_id:Optional[str],offset:int,chronology:dict,*,edge:str="start")->Optional[float]:
    if not anchor_type: return None
    if anchor_type=='chapter':
        try: rng=chronology['chapter_ranges'].get(int(anchor_id))
        except Exception: rng=None
        return rng.get('end' if edge=='end' else 'start') if rng else None
    if anchor_type=='scene':
        try: rng=chronology['scene_ranges'].get(int(anchor_id))
        except Exception: rng=None
        return rng.get('end' if edge=='end' else 'start') if rng else None
    if anchor_type=='manuscript_range':
        info=chronology['block_positions'].get(block_id)
        if not info: return None
        length=max(1,int(info.get('text_length') or 0)); fraction=max(0.0,min(1.0,float(offset or 0)/length))
        return min(1.0,info['start']+(info['end']-info['start'])*fraction)
    return None

def _map_anchor_label(con,anchor_type:Optional[str],anchor_id:Optional[str],excerpt:str='',chronology:dict|None=None)->str:
    if not anchor_type:return ''
    if anchor_type=='manuscript_range':return (excerpt or 'Manuscript selection')[:180]
    if chronology is not None:
        try:ident=int(anchor_id or 0)
        except Exception:ident=0
        if anchor_type=='chapter' and ident:return chronology.get('chapter_names',{}).get(ident,'Chapter')
        if anchor_type=='scene' and ident:return chronology.get('scene_names',{}).get(ident,'Scene')
    return _describe_ref(con,anchor_type,str(anchor_id or ''))

def _validate_map_anchor(con,pid:int,anchor_type:Optional[str],anchor_id:Optional[str],block_id:Optional[str],*,field:str):
    if anchor_type is None: return
    if anchor_type not in domain.MAP_ANCHOR_TYPES: raise HTTPException(400,detail={"code":"MAP_014","message":f"Unsupported {field} anchor type.","params":{}})
    if anchor_type=='manuscript_range':
        if not block_id or not one(con,"SELECT id FROM manuscript_blocks WHERE id=? AND project_id=?",(block_id,pid)):
            raise HTTPException(409,detail={"code":"MAP_015","message":f"{field.capitalize()} manuscript anchor no longer exists.","params":{}})
    elif not anchor_id or not _entity_exists(con,pid,anchor_type,str(anchor_id)):
        raise HTTPException(404,detail={"code":"MAP_016","message":f"{field.capitalize()} anchor no longer exists.","params":{}})

def _project_map_item(con,item:dict,chronology:dict,*,prefer_planned:bool=False)->dict:
    out=dict(item); is_span=item['item_type']=='span'
    anchored_start=_map_anchor_position(item.get('start_anchor_type'),item.get('start_anchor_id'),item.get('start_block_id'),item.get('start_offset') or 0,chronology,edge='start')
    anchored_end=_map_anchor_position(item.get('end_anchor_type'),item.get('end_anchor_id'),item.get('end_block_id'),item.get('end_offset') or 0,chronology,edge='end') if is_span else None
    planned_start=domain._clamp01(item.get('planned_start')); planned_end=domain._clamp01(item.get('planned_end')) if is_span else None
    start=planned_start if prefer_planned and planned_start is not None else anchored_start
    end=planned_end if is_span and prefer_planned and planned_end is not None else anchored_end
    if start is None: start=planned_start
    if is_span and end is None: end=planned_end
    if is_span and start is not None and end is None: end=min(1.0,start+.08)
    if is_span and start is not None and end is not None and end<start: start,end=end,start
    out['scaled_start']=start; out['scaled_end']=end
    out['start_source']='manuscript' if item.get('start_anchor_type') and anchored_start is not None else ('planned' if start is not None else 'unplaced')
    out['end_source']='manuscript' if is_span and item.get('end_anchor_type') and anchored_end is not None else ('planned' if is_span and end is not None else 'unplaced')
    out['start_anchor_label']=_map_anchor_label(con,item.get('start_anchor_type'),item.get('start_anchor_id'),item.get('excerpt') or '',chronology)
    out['end_anchor_label']=_map_anchor_label(con,item.get('end_anchor_type'),item.get('end_anchor_id'),item.get('excerpt') or '',chronology)
    out['start_chapter_id']=None; out['start_scene_id']=None
    if item.get('start_anchor_type')=='chapter':
        try: out['start_chapter_id']=int(item.get('start_anchor_id'))
        except Exception: pass
    elif item.get('start_anchor_type')=='scene':
        try:
            out['start_scene_id']=int(item.get('start_anchor_id')); out['start_chapter_id']=(chronology['scene_ranges'].get(out['start_scene_id']) or {}).get('chapter_id')
        except Exception: pass
    elif item.get('start_anchor_type')=='manuscript_range':
        info=chronology['block_positions'].get(item.get('start_block_id')) or {}; out['start_chapter_id']=info.get('chapter_id'); out['start_scene_id']=info.get('scene_id')
    out['linked']=out['start_source']=='manuscript' and (not is_span or out['end_source']=='manuscript')
    return out

def _detach_map_anchors_for_removed_manuscript(con,pid:int,*,chapter_ids=(),scene_ids=(),block_ids=()):
    """Detach Map anchors whose manuscript targets are about to disappear.

    Maps are planning artifacts and survive manuscript replacement/deletion. Before
    removing the anchored prose, capture its current scaled position as the planned
    fallback so the route does not collapse or retain a fake "linked" state.
    """
    chapter_ids={str(x) for x in chapter_ids}; scene_ids={str(x) for x in scene_ids}; block_ids=set(block_ids)
    if not (chapter_ids or scene_ids or block_ids): return
    chronology=_manuscript_chronology(con,pid)
    rows=allrows(con,"""SELECT i.* FROM map_items i
        JOIN map_lines l ON l.id=i.line_id JOIN story_maps m ON m.id=l.map_id
        WHERE m.project_id=?""",(pid,))
    for item in rows:
        projected=_project_map_item(con,item,chronology); updates={}
        for edge in ('start','end'):
            typ=item.get(f'{edge}_anchor_type'); ident=item.get(f'{edge}_anchor_id'); block=item.get(f'{edge}_block_id')
            affected=(typ=='chapter' and str(ident) in chapter_ids) or (typ=='scene' and str(ident) in scene_ids) or (typ=='manuscript_range' and block in block_ids)
            if not affected: continue
            position=projected.get('scaled_start' if edge=='start' else 'scaled_end')
            planned=f'planned_{edge}'
            if position is not None: updates[planned]=domain._clamp01(position)
            updates.update({f'{edge}_anchor_type':None,f'{edge}_anchor_id':None,f'{edge}_block_id':None,f'{edge}_offset':0})
        if updates:
            if item.get('item_type')=='stop':
                updates.update({'planned_end':None,'end_anchor_type':None,'end_anchor_id':None,'end_block_id':None,'end_offset':0})
            updates['updated_at']=storage_values.now(); sets=', '.join(f"{key}=?" for key in updates)
            con.execute(f"UPDATE map_items SET {sets} WHERE id=?",(*updates.values(),item['id']))

def _map_detail(con,pid:int,mid:int)->dict:
    m=domain._map_row(con,pid,mid)
    if not m: raise HTTPException(404,detail="MAP_002: Map not found.")
    chronology=_manuscript_chronology(con,pid)
    lines=allrows(con,"SELECT * FROM map_lines WHERE map_id=? ORDER BY position,id",(mid,)); lids=[x['id'] for x in lines]
    items=[]
    if lids:
        q=','.join('?' for _ in lids); items=allrows(con,f"SELECT * FROM map_items WHERE line_id IN ({q}) ORDER BY line_id,position,id",lids)
    by_line={lid:[] for lid in lids}
    line_by_id={line['id']:line for line in lines}
    for item in items:
        owner=line_by_id[item['line_id']]
        by_line[item['line_id']].append(_project_map_item(con,item,chronology,prefer_planned=not bool(owner.get('layout_locked'))))
    for line in lines:
        line['items']=by_line[line['id']]
        line['ruled']=any(i.get('start_source')=='manuscript' or i.get('end_source')=='manuscript' for i in line['items'])
    junctions=allrows(con,"SELECT * FROM map_junctions WHERE map_id=? ORDER BY id",(mid,))
    return {"map":m,"lines":lines,"junctions":junctions,"axis":{"chapters":chronology['chapters'],"scenes":chronology['scenes'],"total_words":chronology['total_words']},"palette":domain.MAP_LINE_COLORS}
