from __future__ import annotations

"""Story Map junction routes and endpoint validation."""

from typing import Optional
from fastapi import APIRouter, HTTPException
from api.maps.contracts import MapJunctionCreate, MapJunctionUpdate
from backend.data_access import one, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain

router=APIRouter()

def _normalize_map_junction_endpoint(con,pid:int,mid:int,prefix:str,values:dict)->dict:
    iid=values.get(f'{prefix}_item_id'); lid=values.get(f'{prefix}_line_id'); edge=values.get(f'{prefix}_edge') or 'point'
    if edge not in {'point','start','end'}: raise HTTPException(400,detail="MAP_029: Invalid junction endpoint.")
    planned=domain._clamp01(values.get(f'{prefix}_planned_position')); schematic=domain._clamp01(values.get(f'{prefix}_schematic_position'))
    if iid is not None:
        item=domain._map_item_row(con,pid,int(iid))
        if not item: raise HTTPException(404,detail="MAP_019: Junction item no longer exists.")
        line=domain._map_line_row(con,pid,item['line_id']); lid=item['line_id']; planned=None; schematic=None
        if item['item_type']=='span':
            if edge not in {'point','start','end'}: raise HTTPException(400,detail="MAP_029: Invalid Span junction endpoint.")
        else:
            edge='point'
    else:
        if lid is None: raise HTTPException(400,detail="MAP_030: Junction endpoint needs a Line or Station.")
        line=domain._map_line_row(con,pid,int(lid))
        if not line: raise HTTPException(404,detail="MAP_009: Map line not found.")
        if edge=='point':
            if planned is None and schematic is None: raise HTTPException(400,detail="MAP_031: Route junction needs a position.")
            if planned is None: planned=schematic
            if schematic is None: schematic=planned
        else:
            planned=None; schematic=None
    if line['map_id']!=mid: raise HTTPException(400,detail="MAP_020: Junction endpoints must belong to this Map.")
    return {'line':line,'line_id':int(lid),'item_id':int(iid) if iid is not None else None,'edge':edge,'planned':planned,'schematic':schematic}

def _map_junction_signature(mid:int,a:dict,b:dict,kind:str)->tuple:
    return (mid,a['line_id'],a['item_id'],a['edge'],a['planned'],a['schematic'],b['line_id'],b['item_id'],b['edge'],b['planned'],b['schematic'],kind)

def _assert_unique_map_junction(con,signature:tuple,exclude_id:Optional[int]=None):
    sql="""SELECT id FROM map_junctions WHERE map_id=? AND from_line_id=? AND from_item_id IS ? AND from_edge=? AND from_planned_position IS ? AND from_schematic_position IS ?
             AND to_line_id=? AND to_item_id IS ? AND to_edge=? AND to_planned_position IS ? AND to_schematic_position IS ? AND kind=?"""
    duplicate=con.execute(sql,signature).fetchone()
    if duplicate and (exclude_id is None or int(duplicate[0])!=int(exclude_id)): raise HTTPException(409,detail="MAP_024: This junction already exists.")

@router.post("/api/maps/{mid}/junctions")
def create_map_junction(mid:int,body:MapJunctionCreate):
    if body.kind not in domain.MAP_JUNCTION_TYPES: raise HTTPException(400,detail="MAP_018: Invalid junction type.")
    with storage_mutations.tx() as con:
        p=project(con); m=domain._map_row(con,p['id'],mid)
        if not m: raise HTTPException(404,detail="MAP_002: Map not found.")
        values=body.model_dump(); a=_normalize_map_junction_endpoint(con,p['id'],mid,'from',values); b=_normalize_map_junction_endpoint(con,p['id'],mid,'to',values)
        if a['line_id']==b['line_id']: raise HTTPException(400,detail="MAP_021: A junction must connect two different Lines.")
        signature=_map_junction_signature(mid,a,b,body.kind); _assert_unique_map_junction(con,signature)
        t=storage_values.now(); jid=con.execute("""INSERT INTO map_junctions(map_id,from_line_id,from_item_id,from_edge,from_planned_position,from_schematic_position,to_line_id,to_item_id,to_edge,to_planned_position,to_schematic_position,kind,created_at)
                                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(*signature,t)).lastrowid
        return one(con,"SELECT * FROM map_junctions WHERE id=?",(jid,))

@router.patch("/api/map-junctions/{jid}")
def update_map_junction(jid:int,body:MapJunctionUpdate):
    vals=body.model_dump(exclude_unset=True)
    with storage_mutations.tx() as con:
        p=project(con); current=one(con,"SELECT j.* FROM map_junctions j JOIN story_maps m ON m.id=j.map_id WHERE j.id=? AND m.project_id=?",(jid,p['id']))
        if not current: raise HTTPException(404,detail="MAP_022: Junction not found.")
        merged={**current,**vals}; kind=merged.get('kind')
        if kind not in domain.MAP_JUNCTION_TYPES: raise HTTPException(400,detail="MAP_018: Invalid junction type.")
        a=_normalize_map_junction_endpoint(con,p['id'],current['map_id'],'from',merged); b=_normalize_map_junction_endpoint(con,p['id'],current['map_id'],'to',merged)
        if a['line_id']==b['line_id']: raise HTTPException(400,detail="MAP_021: A junction must connect two different Lines.")
        signature=_map_junction_signature(current['map_id'],a,b,kind); _assert_unique_map_junction(con,signature,exclude_id=jid)
        con.execute("""UPDATE map_junctions SET from_line_id=?,from_item_id=?,from_edge=?,from_planned_position=?,from_schematic_position=?,
                     to_line_id=?,to_item_id=?,to_edge=?,to_planned_position=?,to_schematic_position=?,kind=? WHERE id=?""",
                    (a['line_id'],a['item_id'],a['edge'],a['planned'],a['schematic'],b['line_id'],b['item_id'],b['edge'],b['planned'],b['schematic'],kind,jid))
        return one(con,"SELECT * FROM map_junctions WHERE id=?",(jid,))

@router.delete("/api/map-junctions/{jid}")
def delete_map_junction(jid:int):
    with storage_mutations.tx() as con:
        p=project(con); cur=con.execute("DELETE FROM map_junctions WHERE id=? AND map_id IN (SELECT id FROM story_maps WHERE project_id=?)",(jid,p['id']))
        if not cur.rowcount: raise HTTPException(404,detail="MAP_022: Junction not found.")
    return {"ok":True}
