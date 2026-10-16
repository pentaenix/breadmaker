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

def _valid_canvas_label(value:str)->bool:
    value=(value or '').strip()
    return bool(value) and (value.isdigit() or len(value)==1)

@router.post("/api/canvases/{cid}/nodes")
def create_node(cid:int,body:NodeCreate):
    if body.shape not in VALID_NODE_SHAPES: raise HTTPException(400,'Unknown canvas shape')
    if body.node_type not in VALID_NODE_TYPES: raise HTTPException(400,'Unknown canvas node type')
    if body.node_type=='routing' and body.shape!='routing': raise HTTPException(400,'Routing points require the routing shape')
    if body.node_type=='label' and body.shape!='label': raise HTTPException(400,'Label stickers require the label shape')
    if body.node_type=='figure' and body.shape not in FIGURE_NODE_SHAPES: raise HTTPException(400,'Unknown Figure shape')
    if body.node_type=='figure' and body.shape=='circle' and abs(float(body.w)-float(body.h))>0.001: raise HTTPException(400,'Circle figures must keep equal width and height')
    if body.node_type=='free' and body.shape not in CONTENT_CARD_SHAPES: raise HTTPException(400,'Cards must be Card or Text only')
    if body.node_type=='image' and body.shape!='rect': raise HTTPException(400,'Canvas images use rectangular geometry')
    if body.node_type in {'chapter','scene'} and body.shape not in {'rect','rounded'}: raise HTTPException(400,'Structural nodes use rectangular geometry')
    if body.node_type=='label' and not _valid_canvas_label(body.title): raise HTTPException(400,'Canvas labels must contain digits or one character')
    if body.node_type in {'routing','label','image','figure'} and (body.label_id is not None or body.icon_marker_id is not None): raise HTTPException(400,'Only content nodes can use markers')
    if body.node_type=='figure' and any([body.title,body.body,body.icon,body.color,body.text_color,body.chapter_id,body.scene_id,body.image_path]): raise HTTPException(400,'Figures are visual objects and cannot contain content or links')
    if body.node_type=='free' and body.text_align not in {'left','center','right'}: raise HTTPException(400,'Unknown Card text alignment')
    if body.node_type!='free' and body.text_align!='left': raise HTTPException(400,'Only Cards support text alignment')
    if body.node_type=='figure' and body.figure_layer not in {'back','front'}: raise HTTPException(400,'Unknown Figure layer')
    if body.node_type!='figure' and (body.figure_fill_color or body.figure_stroke_color or body.figure_layer!='back' or abs(float(body.figure_border_width)-1.0)>0.000001): raise HTTPException(400,'Only Figures support Figure appearance')
    if not .5<=float(body.figure_border_width)<=12: raise HTTPException(400,'Figure border width must be between 0.5 and 12')
    for value in (body.figure_line_x1,body.figure_line_y1,body.figure_line_x2,body.figure_line_y2):
        if not 0<=float(value)<=1: raise HTTPException(400,'Figure line endpoints must stay inside the Figure bounds')
    color=normalize_color(body.color,True)
    min_w,min_h=(12,12) if body.node_type=='routing' else ((30,30) if body.node_type=='label' else (40,30))
    if body.w<min_w or body.h<min_h or body.w>5000 or body.h>5000: raise HTTPException(400,'Canvas node size is out of bounds')
    with storage_mutations.tx() as con:
        p=project(con)
        if not one(con,"SELECT id FROM canvases WHERE id=? AND project_id=?",(cid,p['id'])): raise HTTPException(404,"Canvas not found")
        refs=((body.label_id,'markup_labels','Marker'),(body.chapter_id,'chapters','Chapter'),(body.scene_id,'scenes','Scene'))
        for value,table,label in refs:
            if value is not None and not one(con,f"SELECT id FROM {table} WHERE id=? AND project_id=?",(value,p['id'])): raise HTTPException(400,f"{label} link no longer exists")
        if body.label_id is not None:
            marker=one(con,"SELECT * FROM markup_labels WHERE id=? AND project_id=?",(body.label_id,p['id']))
            color=marker['color']
        icon=(body.icon or '').strip()[:16]
        if body.icon_marker_id is not None:
            marker=one(con,"SELECT * FROM icon_markers WHERE id=? AND project_id=?",(body.icon_marker_id,p['id']))
            if not marker: raise HTTPException(400,"Icon marker no longer exists")
            icon=marker['icon'][:16]
        image_path=body.image_path if content_security.is_safe_project_asset_path(body.image_path or '') else ''
        text_color=normalize_color(body.text_color,True)
        figure_fill=normalize_color(body.figure_fill_color,True) if body.node_type=='figure' else ''
        figure_stroke=normalize_color(body.figure_stroke_color,True) if body.node_type=='figure' else ''
        figure_width=float(body.figure_border_width) if body.node_type=='figure' else 1.0
        figure_layer=body.figure_layer if body.node_type=='figure' else 'back'
        text_align=body.text_align if body.node_type=='free' else 'left'
        t=storage_values.now(); nid=con.execute("INSERT INTO canvas_nodes(canvas_id,node_type,shape,icon,x,y,w,h,title,body,display_mode,text_align,color,text_color,figure_fill_color,figure_stroke_color,figure_border_width,figure_layer,figure_line_x1,figure_line_y1,figure_line_x2,figure_line_y2,label_id,chapter_id,scene_id,image_path,icon_marker_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(cid,body.node_type,body.shape,icon,body.x,body.y,body.w,body.h,body.title,body.body,body.display_mode if body.node_type=='free' and body.display_mode in {'full','header','body'} else 'full',text_align,color,text_color,figure_fill,figure_stroke,figure_width,figure_layer,float(body.figure_line_x1),float(body.figure_line_y1),float(body.figure_line_x2),float(body.figure_line_y2),body.label_id,body.chapter_id,body.scene_id,image_path,body.icon_marker_id,t,t)).lastrowid; return one(con,"SELECT * FROM canvas_nodes WHERE id=?",(nid,))

@router.patch("/api/canvases/{cid}/nodes/positions")
def update_canvas_node_positions(cid:int,body:CanvasNodePositionBatch):
    if len(body.items)>500:raise HTTPException(400,'Canvas position batch is limited to 500 nodes')
    if not body.items:return {"updated":0}
    ids=[int(item.id) for item in body.items]
    if len(ids)!=len(set(ids)):raise HTTPException(400,'Canvas position batch contains duplicate nodes')
    with storage_mutations.tx(operation='canvas_position_batch') as con:
        p=project(con)
        if not one(con,"SELECT id FROM canvases WHERE id=? AND project_id=?",(cid,p['id'])):raise HTTPException(404,'Canvas not found')
        marks=','.join('?' for _ in ids)
        owned={int(row[0]) for row in con.execute(f"SELECT id FROM canvas_nodes WHERE canvas_id=? AND id IN ({marks})",(cid,*ids))}
        if owned!=set(ids):raise HTTPException(404,'One or more Canvas nodes no longer exist')
        t=storage_values.now()
        con.executemany("UPDATE canvas_nodes SET x=?,y=?,updated_at=? WHERE id=?",[(max(0.0,float(item.x)),max(0.0,float(item.y)),t,int(item.id)) for item in body.items])
    return {"updated":len(ids)}

@router.patch("/api/nodes/{nid}")
def update_node(nid:int,body:NodeUpdate):
    vals=body.model_dump(exclude_unset=True); allowed={'x','y','w','h','title','body','display_mode','text_align','color','text_color','figure_fill_color','figure_stroke_color','figure_border_width','figure_layer','figure_line_x1','figure_line_y1','figure_line_x2','figure_line_y2','shape','icon','label_id','icon_marker_id','image_path'}; vals={k:v for k,v in vals.items() if k in allowed}
    if 'color' in vals: vals['color']=normalize_color(vals['color'],True)
    if 'text_color' in vals: vals['text_color']=normalize_color(vals['text_color'],True)
    if 'figure_fill_color' in vals: vals['figure_fill_color']=normalize_color(vals['figure_fill_color'],True)
    if 'figure_stroke_color' in vals: vals['figure_stroke_color']=normalize_color(vals['figure_stroke_color'],True)
    if 'figure_border_width' in vals:
        vals['figure_border_width']=float(vals['figure_border_width'])
        if not .5<=vals['figure_border_width']<=12: raise HTTPException(400,'Figure border width must be between 0.5 and 12')
    if 'figure_layer' in vals and vals['figure_layer'] not in {'back','front'}: raise HTTPException(400,'Unknown Figure layer')
    for key in ('figure_line_x1','figure_line_y1','figure_line_x2','figure_line_y2'):
        if key in vals:
            vals[key]=float(vals[key])
            if not 0<=vals[key]<=1: raise HTTPException(400,'Figure line endpoints must stay inside the Figure bounds')
    if 'text_align' in vals and vals['text_align'] not in {'left','center','right'}: raise HTTPException(400,'Unknown Card text alignment')
    if 'icon' in vals: vals['icon']=(vals['icon'] or '').strip()[:16]
    if 'shape' in vals and vals['shape'] not in VALID_NODE_SHAPES: raise HTTPException(400,'Unknown canvas shape')
    if 'display_mode' in vals and vals['display_mode'] not in {'full','header','body'}: raise HTTPException(400,'Unknown Card display mode')
    for dim in ('w','h'):
        if dim in vals and vals[dim] is not None and not 30<=vals[dim]<=5000: raise HTTPException(400,'Canvas node size is out of bounds')
    if vals:
        with storage_mutations.tx() as con:
            p=project(con); node=one(con,"SELECT n.* FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE n.id=? AND c.project_id=?",(nid,p['id']))
            if not node: raise HTTPException(404,"Canvas node not found")
            if node['node_type']=='label' and 'title' in vals and not _valid_canvas_label(vals['title']): raise HTTPException(400,'Canvas labels must contain digits or one character')
            if node['node_type']=='routing' and 'shape' in vals and vals['shape']!='routing': raise HTTPException(400,'Routing points require the routing shape')
            if node['node_type']=='label' and 'shape' in vals and vals['shape']!='label': raise HTTPException(400,'Label stickers require the label shape')
            if node['node_type']=='free' and 'shape' in vals and vals['shape'] not in CONTENT_CARD_SHAPES: raise HTTPException(400,'Cards must be Card or Text only')
            if 'display_mode' in vals and node['node_type']!='free': raise HTTPException(400,'Only Cards support display modes')
            if 'text_align' in vals and node['node_type']!='free': raise HTTPException(400,'Only Cards support text alignment')
            if node['node_type']=='image' and 'shape' in vals and vals['shape']!='rect': raise HTTPException(400,'Canvas images use rectangular geometry')
            if 'image_path' in vals:
                if node['node_type']!='image': raise HTTPException(400,'Only Canvas images can replace image media')
                if not content_security.is_safe_project_asset_path(str(vals['image_path'] or '')): raise HTTPException(400,'Canvas images must use project assets')
            if node['node_type'] in {'chapter','scene'} and 'shape' in vals and vals['shape'] not in {'rect','rounded'}: raise HTTPException(400,'Structural nodes use rectangular geometry')
            if node['node_type']=='figure':
                if 'shape' in vals and vals['shape'] not in FIGURE_NODE_SHAPES: raise HTTPException(400,'Unknown Figure shape')
                forbidden=set(vals)-{'x','y','w','h','shape','figure_fill_color','figure_stroke_color','figure_border_width','figure_layer','figure_line_x1','figure_line_y1','figure_line_x2','figure_line_y2'}
                if forbidden: raise HTTPException(400,'Figures only support geometry and appearance changes')
                next_shape=vals.get('shape',node['shape']);next_w=float(vals.get('w',node['w']));next_h=float(vals.get('h',node['h']))
                if next_shape=='circle' and abs(next_w-next_h)>0.001: raise HTTPException(400,'Circle figures must keep equal width and height')
                if next_shape!='line' and any(k in vals for k in {'figure_line_x1','figure_line_y1','figure_line_x2','figure_line_y2'}): raise HTTPException(400,'Only Line figures support endpoint geometry')
            if node['node_type']!='figure' and any(k in vals for k in {'figure_fill_color','figure_stroke_color','figure_border_width','figure_layer','figure_line_x1','figure_line_y1','figure_line_x2','figure_line_y2'}): raise HTTPException(400,'Only Figures support Figure appearance')
            if node['node_type'] in {'routing','label','image','figure'} and (vals.get('label_id') is not None or vals.get('icon_marker_id') is not None): raise HTTPException(400,'Only content nodes can use markers')
            if 'label_id' in vals and vals['label_id'] is not None:
                marker=one(con,"SELECT * FROM markup_labels WHERE id=? AND project_id=?",(vals['label_id'],p['id']))
                if not marker: raise HTTPException(400,"Marker no longer exists")
                vals['color']=marker['color']
            elif 'color' in vals and 'label_id' not in vals:
                # A hand-picked color is, by definition, not a named Marker.
                vals['label_id']=None
            if 'icon_marker_id' in vals and vals['icon_marker_id'] is not None:
                marker=one(con,"SELECT * FROM icon_markers WHERE id=? AND project_id=?",(vals['icon_marker_id'],p['id']))
                if not marker: raise HTTPException(400,"Icon marker no longer exists")
                vals['icon']=marker['icon'][:16]
            elif 'icon' in vals and 'icon_marker_id' not in vals:
                # Typing a raw icon detaches reusable icon meaning automatically.
                vals['icon_marker_id']=None
            sets=', '.join(f"{k}=?" for k in vals)+", updated_at=?"; con.execute(f"UPDATE canvas_nodes SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),nid))
    return {"ok":True}

def _delete_canvas_nodes_tx(con,p:dict,cid:int,ids:list[int]):
    requested=[]
    for value in ids:
        try:nid=int(value)
        except Exception:continue
        if nid not in requested: requested.append(nid)
    if not requested:return []
    if len(requested)>500: raise HTTPException(400,"Delete at most 500 Canvas objects at once")
    q=','.join('?' for _ in requested)
    rows=allrows(con,f"SELECT n.id,n.canvas_id,n.node_type,c.canvas_type FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE n.id IN ({q}) AND n.canvas_id=? AND c.project_id=?",(*requested,cid,p['id']))
    by_id={int(row['id']):row for row in rows}
    missing=[nid for nid in requested if nid not in by_id]
    if missing: raise HTTPException(404,"One or more Canvas objects no longer exist")
    for nid in requested:
        node=by_id[nid]
        edge_ids=[int(r['id']) for r in allrows(con,"SELECT id FROM canvas_edges WHERE canvas_id=? AND (from_node_id=? OR to_node_id=?)",(cid,nid,nid))]
        if node['canvas_type']=='developmental' and node['node_type'] not in {'chapter','scene'}:
            # Developmental free-workspace objects are version-owned. Removing one
            # from the current proposal must not destroy another Structure Version.
            con.execute("UPDATE canvas_nodes SET developmental_hidden=1,lane_position=NULL,updated_at=? WHERE id=?",(storage_values.now(),nid))
            if edge_ids:
                qe=','.join('?' for _ in edge_ids);con.execute(f"UPDATE canvas_edges SET developmental_hidden=1 WHERE id IN ({qe})",edge_ids)
            continue
        developmental._scrub_developmental_workspace_refs(con,cid,node_ids=(nid,),edge_ids=edge_ids)
        _delete_polymorphic_references(con,p['id'],{'node':(nid,)})
        con.execute("DELETE FROM canvas_nodes WHERE id=?",(nid,))
    return requested

@router.delete("/api/nodes/{nid}")
def delete_node(nid:int):
    commit("Before deleting canvas node","safety")
    with storage_mutations.tx() as con:
        p=project(con)
        row=one(con,"SELECT n.canvas_id FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE n.id=? AND c.project_id=?",(nid,p['id']))
        if not row: raise HTTPException(404,"Canvas node not found")
        _delete_canvas_nodes_tx(con,p,int(row['canvas_id']),[nid])
    return {"ok":True}

@router.post("/api/canvases/{cid}/nodes/delete-batch")
def delete_canvas_nodes_batch(cid:int,body:CanvasNodeBulkDelete):
    commit("Before deleting canvas nodes","safety")
    with storage_mutations.tx() as con:
        p=project(con)
        if not one(con,"SELECT id FROM canvases WHERE id=? AND project_id=?",(cid,p['id'])): raise HTTPException(404,"Canvas not found")
        deleted=_delete_canvas_nodes_tx(con,p,cid,body.ids)
    return {"ok":True,"deleted":deleted}

