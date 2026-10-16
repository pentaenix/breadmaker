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

from . import edge_styles

router = APIRouter()

def _story_template_catalog_raw():
    try:
        data=json.loads(STORY_TEMPLATES_CONFIG.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:
        raise HTTPException(500,f"Canvas Populate configuration is invalid: {exc}")
    if data.get("version")!=1 or not isinstance(data.get("categories"),list):
        raise HTTPException(500,"Canvas Populate configuration must use version 1 with a categories array")
    seen=set()
    for category in data["categories"]:
        if not isinstance(category,dict) or not str(category.get("id","")).strip() or not str(category.get("label","")).strip():
            raise HTTPException(500,"Canvas Populate categories require id and label")
        templates=category.get("templates")
        if not isinstance(templates,list): raise HTTPException(500,"Canvas Populate category templates must be an array")
        for template in templates:
            if not isinstance(template,dict): raise HTTPException(500,"Canvas Populate templates must be objects")
            tid=str(template.get("id","")).strip()
            if not tid or tid in seen: raise HTTPException(500,"Canvas Populate template ids must be unique and non-empty")
            seen.add(tid)
            if not str(template.get("label","")).strip(): raise HTTPException(500,f"Canvas Populate template {tid} requires a label")
            source=template.get("source")
            if source is not None and source not in {"project.chapters","project.scenes"}:
                raise HTTPException(500,f"Canvas Populate template {tid} has an unsupported source")
            if source is None and not isinstance(template.get("items"),list):
                raise HTTPException(500,f"Canvas Populate template {tid} requires items or a project source")
            node=template.get("node") or {}; shape=node.get("shape","rect")
            if shape not in CONTENT_CARD_SHAPES: raise HTTPException(500,f"Canvas Populate template {tid} has an invalid Card shape")
            w=float(node.get("w",220)); h=float(node.get("h",125))
            if not 40<=w<=5000 or not 30<=h<=5000: raise HTTPException(500,f"Canvas Populate template {tid} has invalid node dimensions")
            layout=template.get("layout") or {}
            if layout.get("type","grid")!="grid": raise HTTPException(500,f"Canvas Populate template {tid} has an unsupported layout")
            columns=int(layout.get("columns",4))
            if not 1<=columns<=20: raise HTTPException(500,f"Canvas Populate template {tid} has invalid columns")
            if layout.get("direction","snake") not in {"snake","rows"}: raise HTTPException(500,f"Canvas Populate template {tid} has an invalid direction")
            connect=template.get("connect") or {}
            if connect.get("mode","none") not in {"none","sequence"}: raise HTTPException(500,f"Canvas Populate template {tid} has an invalid connector mode")
            if connect.get("route_style","straight") not in edge_styles.VALID_EDGE_ROUTE_STYLES: raise HTTPException(500,f"Canvas Populate template {tid} has an invalid connector route style")
            if connect.get("end_style","arrow") not in edge_styles.VALID_EDGE_END_STYLES: raise HTTPException(500,f"Canvas Populate template {tid} has an invalid connector end style")
            if template.get("map_compatible"):
                for item in template.get("items") or []:
                    map_type=item.get("map_type","stop")
                    if map_type not in {"stop","span"}: raise HTTPException(500,f"Story template {tid} has an invalid Map item type")
                    start=item.get("map_start"); end=item.get("map_end")
                    if start is not None and not 0<=float(start)<=1: raise HTTPException(500,f"Story template {tid} has an invalid Map start")
                    if end is not None and not 0<=float(end)<=1: raise HTTPException(500,f"Story template {tid} has an invalid Map end")
                    if map_type=="span" and start is not None and end is not None and float(end)<float(start): raise HTTPException(500,f"Story template {tid} has a backwards Map span")
    return data

def _story_template(template_id:str):
    config=_story_template_catalog_raw()
    for category in config["categories"]:
        for template in category["templates"]:
            if template["id"]==template_id: return category,template
    raise HTTPException(404,"Canvas Populate template not found")

def _canvas_populate_items(con,pid:int,template:dict):
    source=template.get("source")
    if source=="project.chapters":
        rows=allrows(con,"SELECT c.* FROM chapters c JOIN document_tabs t ON t.id=c.tab_id WHERE c.project_id=? AND t.is_manuscript=1 AND c.is_implicit=0 ORDER BY t.position,c.position,c.id",(pid,))
        return [{"title":r["title"],"body":"","node":{"node_type":"chapter","chapter_id":r["id"]}} for r in rows]
    if source=="project.scenes":
        main=one(con,"SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(pid,))
        if not main: return []
        main_id=int(main['id'])
        rows=allrows(con,"""SELECT s.*,b.chapter_id first_chapter_id
            FROM scenes s
            LEFT JOIN manuscript_blocks b ON b.id=(
                SELECT bx.id FROM manuscript_blocks bx
                JOIN chapters cx ON cx.id=bx.chapter_id
                WHERE bx.scene_id=s.id AND bx.tab_id=? AND cx.tab_id=?
                ORDER BY cx.position,bx.position,bx.id LIMIT 1)
            WHERE s.project_id=? AND s.tab_id=?
              AND EXISTS(SELECT 1 FROM manuscript_blocks bx WHERE bx.scene_id=s.id AND bx.tab_id=?)
            ORDER BY COALESCE((SELECT cx.position FROM chapters cx WHERE cx.id=b.chapter_id),999999),
                     COALESCE(b.position,999999),s.id""",(main_id,main_id,pid,main_id,main_id))
        return [{"title":r["title"],"body":r.get("description") or "","node":{"node_type":"scene","scene_id":r["id"],"chapter_id":r.get("first_chapter_id")}} for r in rows]
    return list(template.get("items") or [])

def _canvas_populate_card_size(item:dict,node:dict,default_w:float,default_h:float):
    """Choose a comfortable card size before laying out Populate results.

    Populate used to stamp every card into the template's fixed height. That made
    longer framework descriptions clip and also made later rows collide once a
    browser expanded one card. Keep the template dimensions as minimums, then
    estimate enough height for the supplied title/body.
    """
    w=max(180.0,float(node.get("w",default_w)))
    minimum=max(70.0,float(node.get("h",default_h)))
    title=str(item.get("title","") or "").strip(); body=str(item.get("body","") or "").strip()
    chars_per_line=max(18,int((w-28)/7.4))
    def lines(text):
        if not text: return 0
        return sum(max(1,(len(part)+chars_per_line-1)//chars_per_line) for part in text.splitlines() or [text])
    title_lines=max(1,lines(title)); body_lines=lines(body)
    estimated=26+title_lines*20+(10 if body_lines else 0)+body_lines*20+18
    return w,max(minimum,float(estimated))

def _canvas_populate_layout(count:int,layout:dict):
    return {
        "columns":max(1,min(int(layout.get("columns",4)),count or 1)),
        "gap_x":max(20.0,float(layout.get("gap_x",44))),
        "gap_y":max(20.0,float(layout.get("gap_y",44))),
    }


def _canvas_populate_positions(cards:list[dict],x:float,y:float,layout:dict):
    """Lay Populate cards out as an aligned row-major grid.

    Python can only estimate text height, so the browser performs one final measured
    settle after rendering. Keeping columns aligned here makes the first paint stable
    and also gives API consumers a sensible deterministic layout.
    """
    count=len(cards); effective=_canvas_populate_layout(count,layout); columns=effective["columns"]
    gap_x=effective["gap_x"]; gap_y=effective["gap_y"]
    start_x=max(0.0,float(x)); cursor_y=max(0.0,float(y)); out=[]
    column_widths=[0.0]*columns
    for index,card in enumerate(cards):
        column=index%columns; column_widths[column]=max(column_widths[column],float(card["w"]))
    column_x=[start_x]
    for column in range(1,columns):
        column_x.append(column_x[-1]+column_widths[column-1]+gap_x)
    for row_start in range(0,count,columns):
        row=cards[row_start:row_start+columns]; row_h=max((float(card["h"]) for card in row),default=0.0)
        for offset,_card in enumerate(row): out.append((column_x[offset],cursor_y))
        cursor_y+=row_h+gap_y
    return out

def _canvas_populate_ports(a:dict,b:dict):
    """Populate sequences always read as output-right to input-left.

    Row wrapping is layout only. It must not invert semantic connector direction,
    so even the last Card of one row connects from its right output to the left
    input of the first Card on the next row.
    """
    return ("right","left")

@router.get("/api/canvas-populate")
def canvas_populate_catalog():
    config=_story_template_catalog_raw()
    with storage_database.read_tx() as con:
        p=project(con); pid=p["id"]
        counts={
            "project.chapters":con.execute("SELECT COUNT(*) FROM chapters c JOIN document_tabs t ON t.id=c.tab_id WHERE c.project_id=? AND t.is_manuscript=1 AND c.is_implicit=0",(pid,)).fetchone()[0],
            "project.scenes":con.execute("SELECT COUNT(DISTINCT s.id) FROM scenes s JOIN document_tabs t ON t.id=s.tab_id WHERE s.project_id=? AND t.is_manuscript=1 AND EXISTS(SELECT 1 FROM manuscript_blocks b WHERE b.scene_id=s.id AND b.tab_id=t.id)",(pid,)).fetchone()[0],
        }
    categories=[]
    for category in config["categories"]:
        templates=[]
        for template in category["templates"]:
            source=template.get("source")
            templates.append({
                "id":template["id"],"label":template["label"],"description":template.get("description","")[:1000],
                "source":source,"item_count":counts.get(source,len(template.get("items") or []))
            })
        categories.append({"id":category["id"],"label":category["label"],"templates":templates})
    return {"version":config["version"],"categories":categories}

@router.post("/api/canvases/{cid}/populate")
def populate_canvas(cid:int,body:CanvasPopulateRequest):
    _,template=_story_template(body.template_id)
    with storage_mutations.tx(operation="canvas_populate") as con:
        p=project(con); pid=p["id"]
        if not one(con,"SELECT id FROM canvases WHERE id=? AND project_id=?",(cid,pid)): raise HTTPException(404,"Canvas not found")
        items=_canvas_populate_items(con,pid,template)
        if not items: raise HTTPException(400,f"Nothing is available for {template['label']} yet")
        if len(items)>500: raise HTTPException(400,"Canvas Populate is limited to 500 nodes per operation")
        defaults=template.get("node") or {}; layout=template.get("layout") or {}; created=[]; t=storage_values.now()
        default_w=float(defaults.get("w",220)); default_h=float(defaults.get("h",125))
        prepared=[]
        for item in items:
            overrides=item.get("node") or {}; node={**defaults,**overrides}
            shape=node.get("shape","rect"); node_type=node.get("node_type","free")
            if shape not in CONTENT_CARD_SHAPES or node_type not in {"free","chapter","scene"}: raise HTTPException(500,f"Canvas Populate template {template['id']} contains an invalid node")
            w,h=_canvas_populate_card_size(item,node,default_w,default_h)
            prepared.append({"item":item,"node":node,"shape":shape,"node_type":node_type,"w":w,"h":h})
        positions=_canvas_populate_positions(prepared,body.x,body.y,layout)
        for prepared_item,(x,y) in zip(prepared,positions):
            item=prepared_item["item"]; node=prepared_item["node"]; shape=prepared_item["shape"]; node_type=prepared_item["node_type"]; w=prepared_item["w"]; h=prepared_item["h"]
            color=normalize_color(str(node.get("color","") or ""),True); text_color=normalize_color(str(node.get("text_color","") or ""),True)
            icon=str(node.get("icon","") or "").strip()[:16]; title=str(item.get("title","") or "")[:1000]; content=str(item.get("body","") or "")[:12000]
            chapter_id=node.get("chapter_id"); scene_id=node.get("scene_id")
            nid=con.execute("INSERT INTO canvas_nodes(canvas_id,node_type,shape,icon,x,y,w,h,title,body,color,text_color,chapter_id,scene_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(cid,node_type,shape,icon,x,y,w,h,title,content,color,text_color,chapter_id,scene_id,t,t)).lastrowid
            created.append({"id":nid,"x":x,"y":y,"w":w,"h":h})
        edges=[]; connect=template.get("connect") or {}
        if connect.get("mode","none")=="sequence" and len(created)>1:
            route=connect.get("route_style","straight"); end=connect.get("end_style","arrow"); line=connect.get("line_style","solid"); color=normalize_color(str(connect.get("color","") or ""),True); width=float(connect.get("line_width",1.5))
            for a,b in zip(created,created[1:]):
                from_port,to_port=_canvas_populate_ports(a,b)
                eid=con.execute("INSERT INTO canvas_edges(canvas_id,from_node_id,to_node_id,from_port,to_port,label,color,line_style,end_style,line_width,route_style,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(cid,a["id"],b["id"],from_port,to_port,"",color,line,end,width,route,t)).lastrowid
                edges.append(eid)
        nodes=allrows(con,f"SELECT n.*,l.name label_name,l.color label_color FROM canvas_nodes n LEFT JOIN markup_labels l ON l.id=n.label_id WHERE n.id IN ({','.join('?' for _ in created)}) ORDER BY n.id",[n["id"] for n in created])
        edge_rows=allrows(con,f"SELECT * FROM canvas_edges WHERE id IN ({','.join('?' for _ in edges)}) ORDER BY id",edges) if edges else []
    return {"template_id":template["id"],"label":template["label"],"nodes":nodes,"edges":edge_rows,"layout":_canvas_populate_layout(len(nodes),layout),"origin":{"x":max(0.0,float(body.x)),"y":max(0.0,float(body.y))}}

