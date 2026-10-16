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

from . import developmental

router = APIRouter()

@router.post("/api/canvases")
def create_canvas(body:CanvasCreate):
    name=body.name.strip()
    if not name: raise HTTPException(400,"Canvas name cannot be empty")
    if body.canvas_type not in VALID_CANVAS_TYPES: raise HTTPException(400,"Unknown canvas template")
    scope=(body.developmental_scope or 'chapters').lower()
    if scope not in {'chapters','scenes'}: raise HTTPException(400,"Developmental Canvas must use Chapters or Scenes")
    if body.canvas_type!='developmental': scope='chapters'
    with storage_mutations.tx(operation="canvas_create") as con:
        p=project(con)
        if body.parent_id is not None and not one(con,"SELECT id FROM canvases WHERE id=? AND project_id=?",(body.parent_id,p['id'])): raise HTTPException(400,"Parent canvas not found")
        if body.canvas_type=='developmental' and scope=='scenes':
            written=con.execute("SELECT COUNT(DISTINCT s.id) FROM scenes s JOIN document_tabs t ON t.id=s.tab_id JOIN manuscript_blocks b ON b.scene_id=s.id AND b.tab_id=t.id WHERE s.project_id=? AND t.is_manuscript=1",(p['id'],)).fetchone()[0]
            if not written: raise HTTPException(400,"Create at least one Scene in the Main Manuscript before making a Scene Developmental Canvas")
        pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM canvases WHERE project_id=? AND parent_id IS ?",(p['id'],body.parent_id)).fetchone()[0]; t=storage_values.now(); cid=con.execute("INSERT INTO canvases(project_id,parent_id,position,name,description,canvas_type,developmental_scope,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(p['id'],body.parent_id,pos,name,body.description[:12000],body.canvas_type,scope,t,t)).lastrowid; out=one(con,"SELECT * FROM canvases WHERE id=?",(cid,))
    if body.canvas_type=='developmental': developmental.sync_developmental_canvases(p['id'])
    return out

@router.get("/api/canvases/{cid}")
def get_canvas(cid:int):
    with storage_database.read_tx() as con:
        p=project(con)
        cv=one(con,"SELECT * FROM canvases WHERE id=? AND project_id=?",(cid,p['id']))
        if not cv: raise HTTPException(404,"Canvas not found")
        return {"canvas":cv,"nodes":allrows(con,"SELECT n.*,l.name label_name,l.color label_color FROM canvas_nodes n LEFT JOIN markup_labels l ON l.id=n.label_id WHERE n.canvas_id=? ORDER BY n.id",(cid,)),"edges":allrows(con,"SELECT * FROM canvas_edges WHERE canvas_id=? ORDER BY id",(cid,))}

