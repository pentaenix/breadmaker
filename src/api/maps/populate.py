from __future__ import annotations

"""Story Map Populate/source synchronization routes."""

from fastapi import APIRouter, HTTPException
from api.maps.contracts import MapPopulateRequest
from backend.dependencies import feature_call
from backend.data_access import allrows, project
from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import anchors, domain

_story_template = feature_call('canvas.populate', '_story_template')
_story_template_catalog_raw = feature_call('canvas.populate', '_story_template_catalog_raw')

router=APIRouter()

def _map_population_items(con,pid:int,template:dict):
    source=template.get('source')
    if source=='project.chapters':
        rows=allrows(con,"SELECT c.id,c.title FROM chapters c JOIN document_tabs t ON t.id=c.tab_id WHERE c.project_id=? AND t.is_manuscript=1 AND c.is_implicit=0 ORDER BY t.position,c.position,c.id",(pid,))
        n=len(rows); return [{"source_key":f"chapter:{r['id']}","title":r['title'],"description":"","item_type":"stop","start_anchor_type":"chapter","start_anchor_id":str(r['id']),"planned_start":None,"planned_end":None,"schematic_start":.5 if n==1 else (i/(n-1)),"schematic_end":None} for i,r in enumerate(rows)]
    if source=='project.scenes':
        chronology=anchors._manuscript_chronology(con,pid); order={s['id']:i for i,s in enumerate(chronology['scenes'])}; rows=allrows(con,"SELECT id,title,description FROM scenes WHERE project_id=?",(pid,)); rows.sort(key=lambda r:(order.get(r['id'],999999),r['id'])); n=len(rows)
        return [{"source_key":f"scene:{r['id']}","title":r['title'],"description":r.get('description') or '',"item_type":"stop","start_anchor_type":"scene","start_anchor_id":str(r['id']),"planned_start":None,"planned_end":None,"schematic_start":.5 if n==1 else (i/(n-1)),"schematic_end":None} for i,r in enumerate(rows)]
    raw=list(template.get('items') or []); n=len(raw); result=[]
    for i,item in enumerate(raw):
        typ=item.get('map_type','stop'); start=item.get('map_start'); end=item.get('map_end')
        if start is None: start=.5 if n==1 else .05+.90*i/(n-1)
        if typ=='span' and end is None: end=min(.98,float(start)+.12)
        result.append({"source_key":str(item.get('map_key') or f"{template.get('id','template')}:{i}"),"title":str(item.get('title') or 'Untitled'),"description":str(item.get('body') or ''),"item_type":typ,"planned_start":domain._clamp01(start),"planned_end":domain._clamp01(end) if typ=='span' else None,"schematic_start":domain._clamp01(start),"schematic_end":domain._clamp01(end) if typ=='span' else None})
    return result

def _source_item_values(item:dict)->dict:
    return {
        'item_type':item.get('item_type','stop'),'title':str(item.get('title') or 'Untitled')[:180],
        'description':str(item.get('description') or '')[:12000],
        'planned_start':item.get('planned_start'),'planned_end':item.get('planned_end'),
        'schematic_start':item.get('schematic_start'),'schematic_end':item.get('schematic_end'),
        'start_anchor_type':item.get('start_anchor_type'),'start_anchor_id':item.get('start_anchor_id'),
        'start_block_id':item.get('start_block_id'),'start_offset':int(item.get('start_offset') or 0),
        'end_anchor_type':item.get('end_anchor_type'),'end_anchor_id':item.get('end_anchor_id'),
        'end_block_id':item.get('end_block_id'),'end_offset':int(item.get('end_offset') or 0),
        'source_key':str(item.get('source_key') or '')[:240] or None,
    }

def _insert_source_map_item(con,line_id:int,position:int,item:dict,*,preserve_schematic:dict|None=None)->int:
    vals=_source_item_values(item); t=storage_values.now()
    if preserve_schematic:
        vals['schematic_start']=preserve_schematic.get('schematic_start')
        vals['schematic_end']=preserve_schematic.get('schematic_end')
    cols=['item_type','title','short_label','description','planned_start','planned_end','schematic_start','schematic_end','start_anchor_type','start_anchor_id','start_block_id','start_offset','end_anchor_type','end_anchor_id','end_block_id','end_offset','excerpt','label_offset','label_pinned','break_after','source_key']
    row={**vals,'short_label':'','excerpt':'','label_offset':0,'label_pinned':0,'break_after':0}
    return con.execute(f"INSERT INTO map_items(line_id,position,{','.join(cols)},created_at,updated_at) VALUES({','.join('?' for _ in range(len(cols)+4))})",(line_id,position,*[row.get(c) for c in cols],t,t)).lastrowid

def _sync_source_line(con,pid:int,line:dict)->dict:
    """Explicitly reconcile one populated/source Line with its configured source.

    This helper is intentionally never called while merely reading/rendering a Map.
    Populated Lines are writer-owned snapshots until the writer chooses Sync.
    """
    template_id=(line.get('source_template_id') or '').strip()
    if line.get('provenance')!='source' or not template_id:
        raise HTTPException(409,detail="MAP_035: Only populated source Lines can be synced.")
    _,template=_story_template(template_id=template_id)
    if not template.get('map_compatible'):
        raise HTTPException(409,detail="MAP_036: This source can no longer populate a Map Line.")
    desired=_map_population_items(con,pid,template)
    existing=allrows(con,"SELECT * FROM map_items WHERE line_id=? ORDER BY position,id",(line['id'],))
    by_key={row['source_key']:row for row in existing if row.get('source_key')}
    desired_keys=set(); now=storage_values.now(); added=0; updated=0
    for position,item in enumerate(desired):
        key=str(item.get('source_key') or f"{template_id}:{position}")[:240]; desired_keys.add(key)
        vals=_source_item_values({**item,'source_key':key}); row=by_key.get(key)
        if row:
            updates={k:v for k,v in vals.items() if k not in {'source_key','title','description'}}
            updates['position']=position
            # Writer-authored identity is always authoritative. When unlocked, visual
            # geometry is writer-authored too; Sync only reconciles source membership
            # and structural anchors instead of clobbering those edits.
            if not line.get('layout_locked'):
                for key in ('planned_start','planned_end','schematic_start','schematic_end'): updates.pop(key,None)
            if vals['item_type']=='stop':
                updates.update({'planned_end':None,'end_anchor_type':None,'end_anchor_id':None,'end_block_id':None,'end_offset':0})
                if line.get('layout_locked'): updates['schematic_end']=None
            sets=', '.join(f"{k}=?" for k in updates)
            con.execute(f"UPDATE map_items SET {sets},updated_at=? WHERE id=?",(*updates.values(),now,row['id']))
            updated+=1
        else:
            _insert_source_map_item(con,line['id'],position,{**item,'source_key':key}); added+=1
    stale=[row['id'] for row in existing if row.get('source_key') and row['source_key'] not in desired_keys]
    if stale:
        domain._delete_map_references(con,pid,item_ids=stale)
        q=','.join('?' for _ in stale); con.execute(f"DELETE FROM map_items WHERE id IN ({q})",stale)
    # Keep writer-added items (source_key NULL), then restore deterministic row order.
    rows=allrows(con,"SELECT id,source_key,position FROM map_items WHERE line_id=? ORDER BY CASE WHEN source_key IS NULL THEN 1 ELSE 0 END,position,id",(line['id'],))
    for pos,row in enumerate(rows): con.execute("UPDATE map_items SET position=? WHERE id=?",(pos,row['id']))
    # Locked source Lines use source-owned geometry. Keep the Line envelope in sync
    # with the generated items as well, so percentage rulers reach the true 0–100%
    # endpoints for sources such as Chapters and Scenes. Unlocked Lines retain the
    # writer-authored geometry contract above.
    if line.get('layout_locked') and desired:
        starts=[x.get('schematic_start') for x in desired if x.get('schematic_start') is not None]
        ends=[x.get('schematic_end') if x.get('schematic_end') is not None else x.get('schematic_start') for x in desired if x.get('schematic_start') is not None]
        s0=min(starts) if starts else 0.0; s1=max(ends) if ends else 1.0
        con.execute("UPDATE map_lines SET planned_start=?,planned_end=?,schematic_start=?,schematic_end=?,updated_at=? WHERE id=?",(s0,s1,s0,s1,now,line['id']))
    return {'ok':True,'line_id':line['id'],'template_id':template_id,'added':added,'updated':updated,'removed':len(stale),'item_count':len(rows)}

@router.post("/api/map-lines/{lid}/sync")
def sync_source_map_line(lid:int):
    with storage_mutations.tx() as con:
        p=project(con); line=domain._map_line_row(con,p['id'],lid)
        if not line: raise HTTPException(404,detail="MAP_009: Map line not found.")
        return _sync_source_line(con,p['id'],line)

def _replace_line_from_template(con,pid:int,line:dict,template:dict)->dict:
    items=_map_population_items(con,pid,template)
    if not items: raise HTTPException(400,detail=f"Nothing is available for {template['label']} yet.")
    old_ids=[r['id'] for r in allrows(con,"SELECT id FROM map_items WHERE line_id=?",(line['id'],))]
    if old_ids: domain._delete_map_references(con,pid,item_ids=old_ids)
    con.execute("DELETE FROM map_items WHERE line_id=?",(line['id'],))
    t=storage_values.now(); starts=[x.get('schematic_start') for x in items if x.get('schematic_start') is not None]; ends=[x.get('schematic_end') if x.get('schematic_end') is not None else x.get('schematic_start') for x in items if x.get('schematic_start') is not None]; s0=min(starts) if starts else 0.0; s1=max(ends) if ends else 1.0
    con.execute("UPDATE map_lines SET provenance='source',source_template_id=?,layout_locked=1,description=?,planned_start=?,planned_end=?,schematic_start=?,schematic_end=?,updated_at=? WHERE id=?",(template['id'],str(template.get('description') or '')[:12000],s0,s1,s0,s1,t,line['id']))
    created=[]
    for index,item in enumerate(items): created.append(_insert_source_map_item(con,line['id'],index,item))
    return {'line_id':line['id'],'line_name':line['name'],'item_ids':created,'item_count':len(created),'label':template['label']}

@router.get("/api/map-populate")
def map_populate_catalog():
    config=_story_template_catalog_raw()
    with storage_database.read_tx() as con:
        p=project(con); counts={"project.chapters":con.execute("SELECT COUNT(*) FROM chapters c JOIN document_tabs t ON t.id=c.tab_id WHERE c.project_id=? AND t.is_manuscript=1 AND c.is_implicit=0",(p['id'],)).fetchone()[0],"project.scenes":con.execute("SELECT COUNT(*) FROM scenes WHERE project_id=?",(p['id'],)).fetchone()[0]}
    categories=[]
    for category in config['categories']:
        templates=[]
        for template in category['templates']:
            if not template.get('map_compatible'): continue
            source=template.get('source'); templates.append({"id":template['id'],"label":template['label'],"description":template.get('description','')[:1000],"source":source,"item_count":counts.get(source,len(template.get('items') or []))})
        if templates: categories.append({"id":category['id'],"label":category['label'],"templates":templates})
    return {"version":config['version'],"categories":categories}

@router.post("/api/maps/{mid}/populate")
def populate_map(mid:int,body:MapPopulateRequest):
    _,template=_story_template(template_id=body.template_id)
    if not template.get('map_compatible'): raise HTTPException(400,detail="MAP_023: This template is not available for Maps.")
    with storage_mutations.tx(operation="map_populate") as con:
        p=project(con)
        if not domain._map_row(con,p['id'],mid): raise HTTPException(404,detail="MAP_002: Map not found.")
        items=_map_population_items(con,p['id'],template)
        if not items: raise HTTPException(400,detail=f"Nothing is available for {template['label']} yet.")
        name=domain._unique_line_name(con,mid,template.get('map_line_name') or template['label']); color=domain._next_map_color(con,mid); t=storage_values.now()
        # A newly generated Line only joins the ruled group when its generated
        # items actually carry manuscript anchors. Framework/template Lines without
        # manuscript links are unruled and keep normal append order. This is a
        # creation default only; explicit writer reorder remains authoritative.
        new_ruled=any(item.get('start_anchor_type') or item.get('end_anchor_type') for item in items)
        first_unruled=None
        if new_ruled:
            for existing in allrows(con,"SELECT l.id,l.position FROM map_lines l WHERE l.map_id=? ORDER BY l.position,l.id",(mid,)):
                linked=con.execute("SELECT 1 FROM map_items WHERE line_id=? AND (start_anchor_type IS NOT NULL OR end_anchor_type IS NOT NULL) LIMIT 1",(existing['id'],)).fetchone()
                if not linked:
                    first_unruled=existing['position']; break
        if first_unruled is None: pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM map_lines WHERE map_id=?",(mid,)).fetchone()[0]
        else:
            pos=int(first_unruled); con.execute("UPDATE map_lines SET position=position+1 WHERE map_id=? AND position>=?",(mid,pos))
        starts=[x.get('schematic_start') for x in items if x.get('schematic_start') is not None]; ends=[x.get('schematic_end') if x.get('schematic_end') is not None else x.get('schematic_start') for x in items if x.get('schematic_start') is not None]
        s0=min(starts) if starts else .05; s1=max(ends) if ends else .95
        lid=con.execute("INSERT INTO map_lines(map_id,position,name,description,color,visible,provenance,source_template_id,layout_locked,lane_offset,planned_start,planned_end,schematic_start,schematic_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(mid,pos,name,str(template.get('description') or '')[:12000],color,1,'source',template['id'],1,0,s0,s1,s0,s1,t,t)).lastrowid
        created=[]
        for index,item in enumerate(items): created.append(_insert_source_map_item(con,lid,index,item))
        return {"map_id":mid,"line_id":lid,"line_name":name,"item_ids":created,"item_count":len(created),"label":template['label']}

@router.post("/api/map-lines/{lid}/populate")
def populate_existing_map_line(lid:int,body:MapPopulateRequest):
    _,template=_story_template(template_id=body.template_id)
    if not template.get('map_compatible'): raise HTTPException(400,detail="MAP_023: This template is not available for Maps.")
    with storage_mutations.tx(operation="map_line_populate") as con:
        p=project(con); line=domain._map_line_row(con,p['id'],lid)
        if not line: raise HTTPException(404,detail="MAP_009: Map line not found.")
        result=_replace_line_from_template(con,p['id'],line,template); result['map_id']=line['map_id']; return result

@router.post("/api/map-lines/{lid}/duplicate-manual")
def duplicate_map_line_as_manual(lid:int,target_map_id:int|None=None):
    with storage_mutations.tx() as con:
        p=project(con); source=domain._map_line_row(con,p['id'],lid)
        if not source: raise HTTPException(404,detail="MAP_009: Map line not found.")
        mid=source['map_id'] if target_map_id is None else int(target_map_id)
        target=domain._map_row(con,p['id'],mid)
        if not target: raise HTTPException(404,detail="MAP_001: Map not found.")
        name=domain._unique_line_name(con,mid,f"{source['name']} Copy"); pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM map_lines WHERE map_id=?",(mid,)).fetchone()[0]; t=storage_values.now()
        nl=con.execute("INSERT INTO map_lines(map_id,position,name,description,color,visible,provenance,source_template_id,layout_locked,lane_offset,planned_start,planned_end,schematic_start,schematic_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(mid,pos,name,source['description'],source['color'],source['visible'],'manual',None,0,source['lane_offset'],source['planned_start'],source['planned_end'],source['schematic_start'],source['schematic_end'],t,t)).lastrowid
        created=[]
        for item in allrows(con,"SELECT * FROM map_items WHERE line_id=? ORDER BY position,id",(lid,)):
            cols=['item_type','title','short_label','description','planned_start','planned_end','schematic_start','schematic_end','start_anchor_type','start_anchor_id','start_block_id','start_offset','end_anchor_type','end_anchor_id','end_block_id','end_offset','excerpt','label_offset','label_pinned','break_after']
            vals=[item[c] for c in cols]
            ni=con.execute(f"INSERT INTO map_items(line_id,position,{','.join(cols)},source_key,created_at,updated_at) VALUES({','.join('?' for _ in range(len(cols)+5))})",(nl,item['position'],*vals,None,t,t)).lastrowid; created.append(ni)
        return {'id':nl,'map_id':mid,'name':name,'item_ids':created,'item_count':len(created)}
