from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.dependencies import feature_call
from security import content_security
import json
from storage import database as storage_database
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import values as storage_values
from api.canvas.contracts import CanvasCreate, CanvasNodeBulkDelete, CanvasNodePositionBatch, CanvasPopulateRequest, CanvasUpdate, CanvasVersionCreate, CanvasVersionUpdate, DevelopmentalApply, DevelopmentalLayout, DevelopmentalPromote, EdgeCreate, EdgeUpdate, NodeCreate, NodeUpdate
from api.shared_contracts import Reorder
from backend.content import (
    CONTENT_CARD_SHAPES,
    DEVELOPMENTAL_FREE_Y,
    DEVELOPMENTAL_LANE_START_X,
    FIGURE_NODE_SHAPES,
    VALID_CANVAS_TYPES,
    VALID_NODE_SHAPES,
    VALID_NODE_TYPES,
    normalize_color,
)
from backend.data_access import (
    _delete_polymorphic_references,
    allrows,
    one,
    project,
)
from backend.environment import STORY_TEMPLATES_CONFIG
from backend.history_runtime import commit

router = APIRouter()

@router.patch("/api/canvases/{cid}")
def update_canvas(cid:int,body:CanvasUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'name' in vals:
        vals['name']=(vals['name'] or '').strip()
        if not vals['name']: raise HTTPException(400,"Canvas name cannot be empty")
    if 'description' in vals: vals['description']=(vals['description'] or '')[:12000]
    if vals:
        with storage_mutations.tx() as con:
            p=project(con)
            if not one(con,"SELECT id FROM canvases WHERE id=? AND project_id=?",(cid,p['id'])): raise HTTPException(404,"Canvas not found")
            if 'parent_id' in vals and vals['parent_id'] is not None:
                if vals['parent_id']==cid: raise HTTPException(400,"A canvas cannot contain itself")
                if not one(con,"SELECT id FROM canvases WHERE id=? AND project_id=?",(vals['parent_id'],p['id'])): raise HTTPException(400,"Parent canvas not found")
                cur=vals['parent_id']; seen=set()
                while cur is not None:
                    if cur==cid: raise HTTPException(400,"That would create a canvas-parent cycle")
                    if cur in seen: raise HTTPException(400,"Existing canvas hierarchy contains a cycle")
                    seen.add(cur); row=con.execute("SELECT parent_id FROM canvases WHERE id=? AND project_id=?",(cur,p['id'])).fetchone(); cur=row[0] if row else None
            sets=', '.join(f"{k}=?" for k in vals)+", updated_at=?"; con.execute(f"UPDATE canvases SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),cid))
    return {"ok":True}

@router.post("/api/canvases/reorder")
def reorder_canvases(body:Reorder):
    if len(body.ids)!=len(set(body.ids)) or not body.ids: raise HTTPException(400,"Invalid canvas order")
    with storage_mutations.tx() as con:
        p=project(con); q=','.join('?' for _ in body.ids)
        rows=allrows(con,f"SELECT id,parent_id FROM canvases WHERE project_id=? AND id IN ({q})",(p['id'],*body.ids))
        if len(rows)!=len(body.ids): raise HTTPException(400,"One or more canvases no longer exist")
        parents={row['parent_id'] for row in rows}
        if len(parents)!=1: raise HTTPException(400,"Only sibling canvases can be reordered together")
        parent_id=next(iter(parents)); siblings=[row['id'] for row in allrows(con,"SELECT id FROM canvases WHERE project_id=? AND parent_id IS ? ORDER BY position,id",(p['id'],parent_id))]
        if set(siblings)!=set(body.ids): raise HTTPException(400,"Reorder must include every canvas at this level")
        for index,cid in enumerate(body.ids): con.execute("UPDATE canvases SET position=?,updated_at=? WHERE id=?",(index,storage_values.now(),cid))
    return {"ok":True,"ids":body.ids}

@router.delete("/api/canvases/{cid}")
def delete_canvas(cid:int):
    with storage_mutations.tx() as con:
        p=project(con)
        cv=one(con,"SELECT * FROM canvases WHERE id=? AND project_id=?",(cid,p['id']))
        if not cv: raise HTTPException(404,"Canvas not found")
    commit("Before deleting canvas","safety")
    with storage_mutations.tx() as con:
        p=project(con)
        cv=one(con,"SELECT * FROM canvases WHERE id=? AND project_id=?",(cid,p['id']))
        if not cv: raise HTTPException(404,"Canvas not found")
        # Deleting a folder-like canvas must not cascade-delete every canvas nested
        # below it. Preserve children by promoting them to the deleted canvas' parent.
        node_ids=[row['id'] for row in allrows(con,"SELECT id FROM canvas_nodes WHERE canvas_id=?",(cid,))]
        _delete_polymorphic_references(con,cv['project_id'],{'canvas':(cid,),'node':node_ids})
        con.execute("UPDATE canvases SET parent_id=?,updated_at=? WHERE parent_id=?",(cv['parent_id'],storage_values.now(),cid))
        con.execute("DELETE FROM canvases WHERE id=?",(cid,))
    commit("Deleted canvas"); return {"ok":True}

