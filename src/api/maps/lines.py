from __future__ import annotations

"""Story Map Line routes."""

from fastapi import APIRouter, HTTPException
from api.maps.contracts import MapLineCreate, MapLineUpdate
from api.shared_contracts import Reorder
from backend.content import normalize_color
from backend.data_access import allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain

router=APIRouter()

@router.post("/api/maps/{mid}/lines")
def create_map_line(mid:int,body:MapLineCreate):
    with storage_mutations.tx() as con:
        p=project(con)
        if not domain._map_row(con,p['id'],mid): raise HTTPException(404,detail="MAP_002: Map not found.")
        name=domain._unique_line_name(con,mid,body.name); color=normalize_color(body.color,True) or domain._next_map_color(con,mid); pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM map_lines WHERE map_id=?",(mid,)).fetchone()[0]; t=storage_values.now()
        start=domain._clamp01(body.planned_start); end=domain._clamp01(body.planned_end); sstart=domain._clamp01(body.schematic_start); send=domain._clamp01(body.schematic_end)
        if end is None or start is None or end<start or send is None or sstart is None or send<sstart: raise HTTPException(400,detail="MAP_026: Line end must not precede its start.")
        lid=con.execute("INSERT INTO map_lines(map_id,position,name,description,color,visible,provenance,source_template_id,layout_locked,lane_offset,planned_start,planned_end,schematic_start,schematic_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(mid,pos,name,(body.description or '')[:12000],color,1 if body.visible else 0,'manual',None,0,0,start,end,sstart,send,t,t)).lastrowid
        return one(con,"SELECT * FROM map_lines WHERE id=?",(lid,))

@router.patch("/api/map-lines/{lid}")
def update_map_line(lid:int,body:MapLineUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'name' in vals:
        vals['name']=(vals['name'] or '').strip()[:120]
        if not vals['name']: raise HTTPException(400,detail="MAP_008: Line name cannot be empty.")
    if 'description' in vals: vals['description']=(vals['description'] or '')[:12000]
    if 'color' in vals: vals['color']=normalize_color(vals['color'],False)
    if 'visible' in vals: vals['visible']=1 if vals['visible'] else 0
    if 'layout_locked' in vals: vals['layout_locked']=1 if vals['layout_locked'] else 0
    if 'position' in vals: vals['position']=max(0,int(vals['position']))
    if 'lane_offset' in vals: vals['lane_offset']=max(-1200.0,min(1200.0,float(vals['lane_offset'])))
    for key in ('planned_start','planned_end','schematic_start','schematic_end'):
        if key in vals: vals[key]=domain._clamp01(vals[key])
    with storage_mutations.tx() as con:
        p=project(con); line=domain._map_line_row(con,p['id'],lid)
        if not line: raise HTTPException(404,detail="MAP_009: Map line not found.")
        if 'name' in vals and con.execute("SELECT 1 FROM map_lines WHERE map_id=? AND name=? AND id<>?",(line['map_id'],vals['name'],lid)).fetchone(): raise HTTPException(409,detail="MAP_010: This Map already has a Line with that name.")
        geometry_keys={'lane_offset','planned_start','planned_end','schematic_start','schematic_end'}
        if line['layout_locked'] and geometry_keys.intersection(vals) and vals.get('layout_locked',1): raise HTTPException(409,detail="MAP_027: Unlock this Line before changing its layout.")
        merged={**line,**vals}
        for a,b in (('planned_start','planned_end'),('schematic_start','schematic_end')):
            if merged.get(a) is None or merged.get(b) is None or merged[b]<merged[a]: raise HTTPException(400,detail="MAP_026: Line end must not precede its start.")
        if vals:
            sets=', '.join(f"{k}=?" for k in vals)+",updated_at=?"; con.execute(f"UPDATE map_lines SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),lid))
    return {"ok":True}

@router.delete("/api/map-lines/{lid}")
def delete_map_line(lid:int):
    with storage_mutations.tx() as con:
        p=project(con); line=domain._map_line_row(con,p['id'],lid)
        if not line: raise HTTPException(404,detail="MAP_009: Map line not found.")
        item_ids=[r['id'] for r in allrows(con,"SELECT id FROM map_items WHERE line_id=?",(lid,))]
        domain._delete_map_references(con,p['id'],line_ids=(lid,),item_ids=item_ids)
        con.execute("DELETE FROM map_lines WHERE id=?",(lid,))
        rows=allrows(con,"SELECT id FROM map_lines WHERE map_id=? ORDER BY position,id",(line['map_id'],))
        for pos,row in enumerate(rows): con.execute("UPDATE map_lines SET position=? WHERE id=?",(pos,row['id']))
    return {"ok":True}

@router.post("/api/maps/{mid}/lines/reorder")
def reorder_map_lines(mid:int,body:Reorder):
    with storage_mutations.tx() as con:
        p=project(con)
        if not domain._map_row(con,p['id'],mid): raise HTTPException(404,detail="MAP_002: Map not found.")
        existing=[r[0] for r in con.execute("SELECT id FROM map_lines WHERE map_id=? ORDER BY position,id",(mid,))]
        if sorted(existing)!=sorted(body.ids) or len(existing)!=len(body.ids): raise HTTPException(400,detail="MAP_011: Line reorder must contain every Line exactly once.")
        for pos,lid in enumerate(body.ids): con.execute("UPDATE map_lines SET position=?,updated_at=? WHERE id=?",(pos,storage_values.now(),lid))
    return {"ok":True}
