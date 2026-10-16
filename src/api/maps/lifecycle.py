from __future__ import annotations

"""Story Map lifecycle and detail routes."""

from fastapi import APIRouter, HTTPException
from api.maps.contracts import MapCreate, MapUpdate
from api.shared_contracts import Reorder
from backend.content import normalize_color
from backend.data_access import allrows, one, project
from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import anchors, domain

router=APIRouter()

@router.get("/api/scenes/{scene_id}/map-memberships")
def scene_map_memberships(scene_id:int):
    with storage_mutations.tx() as con:
        p=project(con)
        if not one(con,"SELECT id FROM scenes WHERE id=? AND project_id=?",(scene_id,p['id'])): raise HTTPException(404,"Scene not found")
        return allrows(con,"""SELECT i.id item_id,i.title item_title,i.item_type,l.id line_id,l.name line_name,l.color,m.id map_id,m.name map_name
            FROM map_items i JOIN map_lines l ON l.id=i.line_id JOIN story_maps m ON m.id=l.map_id
            WHERE m.project_id=? AND ((i.start_anchor_type='scene' AND i.start_anchor_id=?) OR (i.end_anchor_type='scene' AND i.end_anchor_id=?))
            ORDER BY m.position,l.position,i.position,i.id""",(p['id'],str(scene_id),str(scene_id)))

@router.get("/api/maps")
def list_maps():
    with storage_database.read_tx() as con:
        p=project(con); return allrows(con,"SELECT * FROM story_maps WHERE project_id=? ORDER BY position,id",(p['id'],))

@router.post("/api/maps")
def create_map(body:MapCreate):
    if body.orientation not in domain.MAP_ORIENTATIONS or body.layout_mode not in domain.MAP_LAYOUT_MODES or body.label_mode not in domain.MAP_LABEL_MODES or body.ruler not in domain.MAP_RULERS: raise HTTPException(400,detail="MAP_001: Invalid Map settings.")
    with storage_mutations.tx(operation="map_create") as con:
        p=project(con); name=domain._unique_map_name(con,p['id'],body.name); pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM story_maps WHERE project_id=?",(p['id'],)).fetchone()[0]; t=storage_values.now()
        mid=con.execute("INSERT INTO story_maps(project_id,position,name,description,orientation,layout_mode,label_mode,ruler,background,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(p['id'],pos,name,(body.description or '')[:12000],body.orientation,body.layout_mode,body.label_mode,body.ruler,'#f3f3f1',t,t)).lastrowid
        return one(con,"SELECT * FROM story_maps WHERE id=?",(mid,))

@router.get("/api/maps/{mid}")
def get_map(mid:int):
    with storage_database.read_tx() as con:
        p=project(con); return anchors._map_detail(con,p['id'],mid)

@router.patch("/api/maps/{mid}")
def update_map(mid:int,body:MapUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'name' in vals:
        vals['name']=(vals['name'] or '').strip()[:120]
        if not vals['name']: raise HTTPException(400,detail="MAP_003: Map name cannot be empty.")
    if 'description' in vals: vals['description']=(vals['description'] or '')[:12000]
    if 'orientation' in vals and vals['orientation'] not in domain.MAP_ORIENTATIONS: raise HTTPException(400,detail="MAP_004: Invalid orientation.")
    if 'layout_mode' in vals and vals['layout_mode'] not in domain.MAP_LAYOUT_MODES: raise HTTPException(400,detail="MAP_005: Invalid layout mode.")
    if 'label_mode' in vals and vals['label_mode'] not in domain.MAP_LABEL_MODES: raise HTTPException(400,detail="MAP_025: Invalid label mode.")
    if 'ruler' in vals and vals['ruler'] not in domain.MAP_RULERS: raise HTTPException(400,detail="MAP_006: Invalid ruler.")
    if 'background' in vals: vals['background']=normalize_color(vals['background'],False)
    with storage_mutations.tx() as con:
        p=project(con); current=domain._map_row(con,p['id'],mid)
        if not current: raise HTTPException(404,detail="MAP_002: Map not found.")
        if 'name' in vals and con.execute("SELECT 1 FROM story_maps WHERE project_id=? AND name=? AND id<>?",(p['id'],vals['name'],mid)).fetchone(): raise HTTPException(409,detail="MAP_007: A Map with that name already exists.")
        if vals:
            sets=', '.join(f"{k}=?" for k in vals)+",updated_at=?"; con.execute(f"UPDATE story_maps SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),mid))
    return {"ok":True}

@router.delete("/api/maps/{mid}")
def delete_map(mid:int):
    with storage_mutations.tx() as con:
        p=project(con)
        if not domain._map_row(con,p['id'],mid): raise HTTPException(404,detail="MAP_002: Map not found.")
        line_ids=[r['id'] for r in allrows(con,"SELECT id FROM map_lines WHERE map_id=?",(mid,))]
        item_ids=[]
        if line_ids:
            q=','.join('?' for _ in line_ids); item_ids=[r['id'] for r in allrows(con,f"SELECT id FROM map_items WHERE line_id IN ({q})",line_ids)]
        domain._delete_map_references(con,p['id'],map_ids=(mid,),line_ids=line_ids,item_ids=item_ids)
        con.execute("DELETE FROM story_maps WHERE id=?",(mid,))
    return {"ok":True}

@router.post("/api/maps/{mid}/duplicate")
def duplicate_map(mid:int):
    with storage_mutations.tx() as con:
        p=project(con); source=domain._map_row(con,p['id'],mid)
        if not source: raise HTTPException(404,detail="MAP_002: Map not found.")
        name=domain._unique_map_name(con,p['id'],f"{source['name']} Copy"); pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM story_maps WHERE project_id=?",(p['id'],)).fetchone()[0]; t=storage_values.now()
        new_mid=con.execute("INSERT INTO story_maps(project_id,position,name,description,orientation,layout_mode,label_mode,ruler,background,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(p['id'],pos,name,source['description'],source['orientation'],source['layout_mode'],source['label_mode'],source['ruler'],source['background'],t,t)).lastrowid
        line_map={}; item_map={}
        for line in allrows(con,"SELECT * FROM map_lines WHERE map_id=? ORDER BY position,id",(mid,)):
            nl=con.execute("INSERT INTO map_lines(map_id,position,name,description,color,visible,provenance,source_template_id,layout_locked,lane_offset,planned_start,planned_end,schematic_start,schematic_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(new_mid,line['position'],line['name'],line['description'],line['color'],line['visible'],line['provenance'],line['source_template_id'],line['layout_locked'],line['lane_offset'],line['planned_start'],line['planned_end'],line['schematic_start'],line['schematic_end'],t,t)).lastrowid; line_map[line['id']]=nl
            for item in allrows(con,"SELECT * FROM map_items WHERE line_id=? ORDER BY position,id",(line['id'],)):
                cols=['position','item_type','title','short_label','description','planned_start','planned_end','schematic_start','schematic_end','start_anchor_type','start_anchor_id','start_block_id','start_offset','end_anchor_type','end_anchor_id','end_block_id','end_offset','excerpt','label_offset','label_pinned','break_after','source_key']
                vals=[item[c] for c in cols]; placeholders=','.join('?' for _ in range(len(vals)+3))
                ni=con.execute(f"INSERT INTO map_items(line_id,{','.join(cols)},created_at,updated_at) VALUES({placeholders})",(nl,*vals,t,t)).lastrowid; item_map[item['id']]=ni
        for j in allrows(con,"SELECT * FROM map_junctions WHERE map_id=? ORDER BY id",(mid,)):
            from_line=line_map.get(j['from_line_id']); to_line=line_map.get(j['to_line_id'])
            if not from_line or not to_line: continue
            from_item=item_map.get(j['from_item_id']) if j.get('from_item_id') is not None else None
            to_item=item_map.get(j['to_item_id']) if j.get('to_item_id') is not None else None
            if j.get('from_item_id') is not None and not from_item: continue
            if j.get('to_item_id') is not None and not to_item: continue
            con.execute("""INSERT INTO map_junctions(map_id,from_line_id,from_item_id,from_edge,from_planned_position,from_schematic_position,to_line_id,to_item_id,to_edge,to_planned_position,to_schematic_position,kind,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(new_mid,from_line,from_item,j['from_edge'],j['from_planned_position'],j['from_schematic_position'],to_line,to_item,j['to_edge'],j['to_planned_position'],j['to_schematic_position'],j['kind'],t))
        return one(con,"SELECT * FROM story_maps WHERE id=?",(new_mid,))

@router.post("/api/maps/reorder")
def reorder_maps(body:Reorder):
    if len(body.ids)!=len(set(body.ids)) or not body.ids: raise HTTPException(400,detail="MAP_027: Invalid Map order.")
    with storage_mutations.tx() as con:
        p=project(con); existing=[r[0] for r in con.execute("SELECT id FROM story_maps WHERE project_id=? ORDER BY position,id",(p['id'],))]
        if len(existing)!=len(body.ids) or set(existing)!=set(body.ids): raise HTTPException(400,detail="MAP_028: Map reorder must contain every Map exactly once.")
        for pos,mid in enumerate(body.ids): con.execute("UPDATE story_maps SET position=?,updated_at=? WHERE id=? AND project_id=?",(pos,storage_values.now(),mid,p['id']))
    return {"ok":True,"ids":body.ids}
