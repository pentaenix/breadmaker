from __future__ import annotations

"""Story Map Stop/Span item routes."""

from fastapi import APIRouter, HTTPException
from api.maps.contracts import MapItemCreate, MapItemUpdate
from backend.data_access import allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import anchors, domain

router=APIRouter()

def _normalize_map_item_payload(vals:dict):
    if 'item_type' in vals and vals['item_type'] not in domain.MAP_ITEM_TYPES: raise HTTPException(400,detail="MAP_012: Invalid Map item type.")
    if 'title' in vals:
        vals['title']=(vals['title'] or '').strip()[:180] or 'Untitled Stop'
    if 'short_label' in vals: vals['short_label']=(vals['short_label'] or '')[:80]
    if 'description' in vals: vals['description']=(vals['description'] or '')[:12000]
    if 'excerpt' in vals: vals['excerpt']=(vals['excerpt'] or '')[:2000]
    for key in ('planned_start','planned_end','schematic_start','schematic_end'):
        if key in vals and vals[key] is not None: vals[key]=domain._clamp01(vals[key])
    for key in ('start_offset','end_offset'):
        if key in vals and vals[key] is not None: vals[key]=max(0,int(vals[key]))
    if 'label_offset' in vals and vals['label_offset'] is not None: vals['label_offset']=max(-600.0,min(600.0,float(vals['label_offset'])))
    for key in ('label_pinned','break_after'):
        if key in vals and vals[key] is not None: vals[key]=1 if vals[key] else 0
    return vals

@router.post("/api/map-lines/{lid}/items")
def create_map_item(lid:int,body:MapItemCreate):
    vals=_normalize_map_item_payload(body.model_dump())
    with storage_mutations.tx() as con:
        p=project(con); line=domain._map_line_row(con,p['id'],lid)
        if not line: raise HTTPException(404,detail="MAP_009: Map line not found.")
        anchors._validate_map_anchor(con,p['id'],vals.get('start_anchor_type'),vals.get('start_anchor_id'),vals.get('start_block_id'),field='start')
        anchors._validate_map_anchor(con,p['id'],vals.get('end_anchor_type'),vals.get('end_anchor_id'),vals.get('end_block_id'),field='end')
        if vals['item_type']=='stop': vals['planned_end']=None; vals['schematic_end']=None; vals['end_anchor_type']=None; vals['end_anchor_id']=None; vals['end_block_id']=None; vals['end_offset']=0
        if vals['item_type']=='span' and vals.get('planned_start') is not None and vals.get('planned_end') is not None and vals['planned_end']<vals['planned_start']: vals['planned_start'],vals['planned_end']=vals['planned_end'],vals['planned_start']
        if vals['item_type']=='span' and vals.get('schematic_start') is not None and vals.get('schematic_end') is not None and vals['schematic_end']<vals['schematic_start']: vals['schematic_start'],vals['schematic_end']=vals['schematic_end'],vals['schematic_start']
        pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM map_items WHERE line_id=?",(lid,)).fetchone()[0]; t=storage_values.now()
        cols=['item_type','title','short_label','description','planned_start','planned_end','schematic_start','schematic_end','start_anchor_type','start_anchor_id','start_block_id','start_offset','end_anchor_type','end_anchor_id','end_block_id','end_offset','excerpt','label_offset','label_pinned','break_after']
        iid=con.execute(f"INSERT INTO map_items(line_id,position,{','.join(cols)},created_at,updated_at) VALUES({','.join('?' for _ in range(len(cols)+4))})",(lid,pos,*[vals[c] for c in cols],t,t)).lastrowid
        return one(con,"SELECT * FROM map_items WHERE id=?",(iid,))

@router.patch("/api/map-items/{iid}")
def update_map_item(iid:int,body:MapItemUpdate):
    vals=_normalize_map_item_payload(body.model_dump(exclude_unset=True)); vals.pop('clear_start_anchor',None); vals.pop('clear_end_anchor',None)
    with storage_mutations.tx() as con:
        p=project(con); current=domain._map_item_row(con,p['id'],iid)
        if not current: raise HTTPException(404,detail="MAP_013: Map item not found.")
        owner=domain._map_line_row(con,p['id'],current['line_id'])
        layout_fields={'planned_start','planned_end','schematic_start','schematic_end'}
        if owner and owner['layout_locked'] and layout_fields.intersection(vals): raise HTTPException(409,detail="MAP_028: Unlock this Line before moving its Stops or Spans.")
        if body.clear_start_anchor: vals.update({'start_anchor_type':None,'start_anchor_id':None,'start_block_id':None,'start_offset':0})
        if body.clear_end_anchor: vals.update({'end_anchor_type':None,'end_anchor_id':None,'end_block_id':None,'end_offset':0})
        merged={**current,**vals}; item_type=merged.get('item_type','stop')
        anchors._validate_map_anchor(con,p['id'],merged.get('start_anchor_type'),merged.get('start_anchor_id'),merged.get('start_block_id'),field='start')
        if item_type=='span': anchors._validate_map_anchor(con,p['id'],merged.get('end_anchor_type'),merged.get('end_anchor_id'),merged.get('end_block_id'),field='end')
        else: vals.update({'planned_end':None,'schematic_end':None,'end_anchor_type':None,'end_anchor_id':None,'end_block_id':None,'end_offset':0})
        merged={**current,**vals}
        for a,b in (('planned_start','planned_end'),('schematic_start','schematic_end')):
            if item_type=='span' and merged.get(a) is not None and merged.get(b) is not None and merged[b]<merged[a]: raise HTTPException(400,detail="MAP_017: Span end must not precede its start.")
        if vals:
            sets=', '.join(f"{k}=?" for k in vals)+",updated_at=?"; con.execute(f"UPDATE map_items SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),iid))
    return {"ok":True}

@router.delete("/api/map-items/{iid}")
def delete_map_item(iid:int):
    with storage_mutations.tx() as con:
        p=project(con); item=domain._map_item_row(con,p['id'],iid)
        if not item: raise HTTPException(404,detail="MAP_013: Map item not found.")
        domain._delete_map_references(con,p['id'],item_ids=(iid,))
        con.execute("DELETE FROM map_items WHERE id=?",(iid,)); rows=allrows(con,"SELECT id FROM map_items WHERE line_id=? ORDER BY position,id",(item['line_id'],))
        for pos,row in enumerate(rows): con.execute("UPDATE map_items SET position=? WHERE id=?",(pos,row['id']))
    return {"ok":True}
