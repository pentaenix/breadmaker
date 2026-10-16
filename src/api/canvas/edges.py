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


from . import developmental, edge_styles

router = APIRouter()

def _validate_edge_style(values:dict):
    if values.get('from_port') is not None and values['from_port'] not in edge_styles.VALID_EDGE_PORTS: raise HTTPException(400,"Invalid connector port")
    if values.get('to_port') is not None and values['to_port'] not in edge_styles.VALID_EDGE_PORTS: raise HTTPException(400,"Invalid connector port")
    if values.get('line_style') is not None and values['line_style'] not in edge_styles.VALID_EDGE_LINE_STYLES: raise HTTPException(400,"Invalid connector line style")
    if values.get('end_style') is not None and values['end_style'] not in edge_styles.VALID_EDGE_END_STYLES: raise HTTPException(400,"Invalid connector end style")
    if values.get('route_style') is not None and values['route_style'] not in edge_styles.VALID_EDGE_ROUTE_STYLES: raise HTTPException(400,"Invalid connector route style")
    if values.get('color') is not None: values['color']=normalize_color(values['color'],True)
    if values.get('line_width') is not None:
        values['line_width']=float(values['line_width'])
        if not 0.5 <= values['line_width'] <= 8: raise HTTPException(400,"Connector width must be between 0.5 and 8")
    if values.get('label') is not None: values['label']=(values['label'] or '')[:180]

@router.post("/api/canvases/{cid}/edges")
def create_edge(cid:int,body:EdgeCreate):
    if body.from_node_id==body.to_node_id: raise HTTPException(400,"Cannot connect a node to itself")
    vals=body.model_dump(); _validate_edge_style(vals)
    with storage_mutations.tx() as con:
        p=project(con)
        if not one(con,"SELECT id FROM canvases WHERE id=? AND project_id=?",(cid,p['id'])): raise HTTPException(404,"Canvas not found")
        if vals.get('marker_id') is not None:
            marker=one(con,"SELECT id,color FROM markup_labels WHERE id=? AND project_id=?",(vals['marker_id'],p['id']))
            if not marker: raise HTTPException(400,"Marker no longer exists")
            vals['color']=marker['color']
        nodes=allrows(con,"SELECT id,canvas_id,node_type FROM canvas_nodes WHERE id IN (?,?)",(body.from_node_id,body.to_node_id))
        if len(nodes)!=2 or any(n['canvas_id']!=cid for n in nodes): raise HTTPException(400,"Both connector nodes must belong to this canvas")
        if any(n['node_type'] in {'label','figure'} for n in nodes): raise HTTPException(400,"Labels and figures cannot be connected")
        eid=con.execute("INSERT INTO canvas_edges(canvas_id,from_node_id,to_node_id,from_port,to_port,label,color,marker_id,line_style,end_style,line_width,route_style,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(cid,vals['from_node_id'],vals['to_node_id'],vals['from_port'],vals['to_port'],vals['label'],vals['color'],vals.get('marker_id'),vals['line_style'],vals['end_style'],vals['line_width'],vals['route_style'],storage_values.now())).lastrowid
        return one(con,"SELECT * FROM canvas_edges WHERE id=?",(eid,))

@router.patch("/api/edges/{eid}")
def update_edge(eid:int,body:EdgeUpdate):
    vals=body.model_dump(exclude_unset=True); _validate_edge_style(vals)
    with storage_mutations.tx() as con:
        p=project(con); edge=one(con,"SELECT e.* FROM canvas_edges e JOIN canvases c ON c.id=e.canvas_id WHERE e.id=? AND c.project_id=?",(eid,p['id']))
        if not edge: raise HTTPException(404,"Canvas connector not found")
        if 'marker_id' in vals:
            if vals['marker_id'] is None:
                pass
            else:
                marker=one(con,"SELECT id,color FROM markup_labels WHERE id=? AND project_id=?",(vals['marker_id'],p['id']))
                if not marker: raise HTTPException(400,"Marker no longer exists")
                vals['color']=marker['color']
        elif 'color' in vals:
            # A hand-picked connector color detaches reusable marker meaning.
            vals['marker_id']=None
        from_id=vals.get('from_node_id',edge['from_node_id']); to_id=vals.get('to_node_id',edge['to_node_id'])
        if from_id==to_id: raise HTTPException(400,"Cannot connect a node to itself")
        if 'from_node_id' in vals or 'to_node_id' in vals:
            nodes=allrows(con,"SELECT id,canvas_id,node_type FROM canvas_nodes WHERE id IN (?,?)",(from_id,to_id))
            if len(nodes)!=2 or any(n['canvas_id']!=edge['canvas_id'] for n in nodes): raise HTTPException(400,"Both connector nodes must belong to this canvas")
            if any(n['node_type'] in {'label','figure'} for n in nodes): raise HTTPException(400,"Labels and figures cannot be connected")
        if vals:
            sets=', '.join(f"{k}=?" for k in vals); con.execute(f"UPDATE canvas_edges SET {sets} WHERE id=?",(*vals.values(),eid))
    return {"ok":True}

@router.delete("/api/edges/{eid}")
def delete_edge(eid:int):
    with storage_mutations.tx() as con:
        p=project(con)
        edge=one(con,"SELECT e.id,e.canvas_id,c.canvas_type FROM canvas_edges e JOIN canvases c ON c.id=e.canvas_id WHERE e.id=? AND c.project_id=?",(eid,p['id']))
        if not edge: raise HTTPException(404,"Canvas connector not found")
        if edge['canvas_type']=='developmental':
            con.execute("UPDATE canvas_edges SET developmental_hidden=1 WHERE id=?",(eid,))
            return {"ok":True,"versioned":True}
        developmental._scrub_developmental_workspace_refs(con,int(edge['canvas_id']),edge_ids=(eid,))
        con.execute("DELETE FROM canvas_edges WHERE id=?",(eid,))
    return {"ok":True}

