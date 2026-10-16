from __future__ import annotations
from collections import defaultdict
from typing import Any, Callable
from storage import invariants as storage_invariants
from storage import values as storage_values
from .validation import ProjectJSONError, _KIND_TO_REFERENCE_TYPE
from . import markup, layout

def import_into_active_database(con, validated:dict) -> dict:
    doc=validated["document"];old=con.execute("SELECT id,project_uid FROM projects ORDER BY id LIMIT 1").fetchone()
    pid=int(old[0]) if old else 1;project_uid=str(old[1] or '') if old else '';t=storage_values.now()
    if old:
        con.execute("DELETE FROM projects WHERE id=?",(pid,))
    if not project_uid:raise ProjectJSONError("Active Breadmaker project identity is missing.")
    con.execute("INSERT INTO projects(id,project_uid,title,created_at,updated_at) VALUES(?,?,?,?,?)",(pid,project_uid,doc["name"],t,t))
    refs:dict[str,tuple[str,Any]]={}
    link_queue:list[tuple[str,dict]]=[]

    def bind(public_id:str,kind:str,internal:Any):refs[public_id]=(kind,internal)
    def marker_id(ref:str|None):
        if not ref:return None
        canonical=next((k for k in refs if k.lower()==ref.lower()),ref); row=refs.get(canonical)
        return int(row[1]) if row and row[0]=="marker" else None
    def resolve(ref:str):
        canonical=next((k for k in refs if k.lower()==ref.lower()),ref)
        if canonical not in refs:raise ProjectJSONError(f"Internal import could not resolve {ref!r}.")
        return refs[canonical]
    def queue_links(public_id:str,links:list[dict]):
        for link in links:link_queue.append((public_id,link))

    # Markers first so Cards, Tasks and Connectors can reference them.
    for m in doc["markers"]:
        mid=con.execute("INSERT INTO markup_labels(project_id,name,description,color,created_at,updated_at) VALUES(?,?,?,?,?,?)",(pid,m["name"],m["description"],m["color"],t,t)).lastrowid;bind(m["id"],"marker",mid)

    scene_counter=defaultdict(int)
    for ti,tab in enumerate(doc["manuscript"]["tabs"]):
        did=con.execute("INSERT INTO document_tabs(project_id,position,title,description,is_manuscript,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(pid,ti,tab["name"],tab["description"],1 if tab["main"] else 0,t,t)).lastrowid;bind(tab["id"],"document",did);queue_links(tab["id"],tab["links"])
        # A Tab may contain prose outside explicit Chapters. Breadmaker already models
        # this as a persistence-only implicit Chapter; keeping it in the public schema
        # as tab.content avoids exposing that internal implementation detail. An
        # otherwise-empty secondary Tab still needs Breadmaker's normal implicit
        # blank writing container so it behaves like a Tab created through the UI.
        if tab.get("content") or (not tab["main"] and not tab["chapters"]):
            implicit=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,1,'','',?,?)",(pid,did,-1,tab["name"],"",t,t)).lastrowid
            for block_pos,(frag,text) in enumerate(markup._content_lines(tab.get("content") or "")):
                bid=storage_values.uid("b_");con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,'paragraph','body',?,?,?,?,?)",(bid,pid,did,implicit,block_pos,frag,text,storage_values.sha(frag+'\0'+text+'\0'),t,t))
        for ci,ch in enumerate(tab["chapters"]):
            cid=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,0,'','',?,?)",(pid,did,ci,ch["title"],ch["description"],t,t)).lastrowid;bind(ch["id"],"chapter",cid);queue_links(ch["id"],ch["links"])
            block_pos=0
            if ch["content"]:
                for frag,text in markup._content_lines(ch["content"]):
                    bid=storage_values.uid("b_");con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,'paragraph','body',?,?,?,?,?)",(bid,pid,did,cid,block_pos,frag,text,storage_values.sha(frag+'\0'+text+'\0'),t,t));block_pos+=1
            for sc in ch["scenes"]:
                spos=scene_counter[did];scene_counter[did]+=1
                sid=con.execute("INSERT INTO scenes(project_id,tab_id,position,title,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(pid,did,spos,sc["title"],sc["description"],t,t)).lastrowid;bind(sc["id"],"scene",sid);queue_links(sc["id"],sc["links"])
                for frag,text in markup._content_lines(sc["content"]):
                    bid=storage_values.uid("b_");con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,'paragraph','body',?,?,?,?,?)",(bid,pid,did,cid,sid,block_pos,frag,text,storage_values.sha(frag+'\0'+text+'\0'),t,t));block_pos+=1
            if block_pos==0:
                bid=storage_values.uid("b_");con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,0,'paragraph','body','','','',?,?)",(bid,pid,did,cid,t,t))

    # Canvas hierarchy can use forward parent references, so insert parents first.
    canvas_defs={c["id"]:c for c in doc["canvases"]};pending=list(doc["canvases"]);sibling_pos=defaultdict(int)
    while pending:
        progressed=False
        for c in list(pending):
            parent=c.get("parent")
            parent_row=None
            if parent:
                canonical=next((k for k in refs if k.lower()==parent.lower()),None)
                if canonical is None:continue
                parent_row=refs[canonical]
            parent_id=int(parent_row[1]) if parent_row else None;key=parent_id
            pos=sibling_pos[key];sibling_pos[key]+=1
            cid=con.execute("INSERT INTO canvases(project_id,parent_id,position,name,description,canvas_type,developmental_scope,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(pid,parent_id,pos,c["name"],c["description"],"developmental" if c["type"]=="developmental" else "blank",c["developmental_scope"] if c["type"]=="developmental" else "chapters",t,t)).lastrowid;bind(c["id"],"canvas",cid);queue_links(c["id"],c["links"]);pending.remove(c);progressed=True
        if not progressed:raise ProjectJSONError("Canvas hierarchy could not be resolved.")

    # Nodes then edges. Structural Developmental cards are created by the normal
    # sync pass after this transaction; Project JSON nodes are the free workspace.
    for c in doc["canvases"]:
        cid=int(resolve(c["id"])[1]);positions=layout._layout_positions(c)
        for n in c["nodes"]:
            pos=positions[n["id"]];typ=n["type"];label_id=marker_id(n.get("marker"));color=n.get("color") or ""
            if label_id:
                color=con.execute("SELECT color FROM markup_labels WHERE id=?",(label_id,)).fetchone()[0]
            chapter_id=scene_id=None
            if n.get("linked_to"):
                kind,internal=resolve(n["linked_to"])
                if kind=="chapter":chapter_id=int(internal)
                elif kind=="scene":scene_id=int(internal)
            if typ=="card":node_type="free";shape="text" if n["shape"]=="text" else "rect";title=n["title"];body=n["body"];display=n["display"];text_align=n.get("text_align") or "left";text_color=n.get("text_color") or "";figure_fill=figure_stroke="";figure_width=1.0;figure_layer="back";line_x1=.02;line_y1=.5;line_x2=.98;line_y2=.5
            elif typ=="figure":node_type="figure";shape=n["shape"];title=body="";display="full";text_align="left";color=text_color="";label_id=None;figure_fill=n.get("fill_color") or "";figure_stroke=n.get("stroke_color") or "";figure_width=float(n.get("border_width") or 1.0);figure_layer=n.get("layer") or "back";line_x1=float(n.get("line_x1",.02));line_y1=float(n.get("line_y1",.5));line_x2=float(n.get("line_x2",.98));line_y2=float(n.get("line_y2",.5))
            elif typ=="index":node_type="label";shape="label";title=n["value"];body="";display="full";text_align="left";color=text_color=figure_fill=figure_stroke="";figure_width=1.0;figure_layer="back";line_x1=.02;line_y1=.5;line_x2=.98;line_y2=.5;label_id=None
            else:node_type="routing";shape="routing";title=body="";display="full";text_align="left";color=text_color=figure_fill=figure_stroke="";figure_width=1.0;figure_layer="back";line_x1=.02;line_y1=.5;line_x2=.98;line_y2=.5;label_id=None
            nid=con.execute("""INSERT INTO canvas_nodes(canvas_id,node_type,shape,icon,x,y,w,h,title,body,display_mode,text_align,color,text_color,figure_fill_color,figure_stroke_color,figure_border_width,figure_layer,figure_line_x1,figure_line_y1,figure_line_x2,figure_line_y2,label_id,chapter_id,scene_id,image_path,icon_marker_id,created_at,updated_at)
                VALUES(?,?,?,'',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, '',NULL,?,?)""",(cid,node_type,shape,pos["x"],pos["y"],pos["w"],pos["h"],title,body,display,text_align,color,text_color,figure_fill,figure_stroke,figure_width,figure_layer,line_x1,line_y1,line_x2,line_y2,label_id,chapter_id,scene_id,t,t)).lastrowid;bind(n["id"],"node",nid);queue_links(n["id"],n["links"])
        for e in c["connectors"]:
            fk,from_id=resolve(e["from"]);tk,to_id=resolve(e["to"]);mid=marker_id(e.get("marker"));color=e.get("color") or ""
            if mid:color=con.execute("SELECT color FROM markup_labels WHERE id=?",(mid,)).fetchone()[0]
            con.execute("INSERT INTO canvas_edges(canvas_id,from_node_id,to_node_id,from_port,to_port,label,color,marker_id,line_style,end_style,line_width,route_style,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(cid,int(from_id),int(to_id),e["from_port"],e["to_port"],e["label"],color,mid,e["stroke"],e["end"],e["width"],e["route"],t))

    # Maps.
    for mi,m in enumerate(doc["maps"]):
        mid=con.execute("INSERT INTO story_maps(project_id,position,name,description,orientation,layout_mode,label_mode,ruler,background,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(pid,mi,m["name"],m["description"],m["orientation"],m["layout_mode"],m["label_mode"],m["ruler"],m["background"],t,t)).lastrowid;bind(m["id"],"map",mid);queue_links(m["id"],m["links"])
        for li,l in enumerate(m["lines"]):
            lid=con.execute("INSERT INTO map_lines(map_id,position,name,description,color,visible,provenance,layout_locked,lane_offset,planned_start,planned_end,schematic_start,schematic_end,created_at,updated_at) VALUES(?,?,?,?,?,?,'manual',0,0,0.0,1.0,0.0,1.0,?,?)",(mid,li,l["name"],l["description"],l["color"],1 if l["visible"] else 0,t,t)).lastrowid;bind(l["id"],"map_line",lid);queue_links(l["id"],l["links"])
            nitems=len(l["items"])
            for ii,it in enumerate(l["items"]):
                auto=.5 if nitems<=1 else .05+.90*ii/(nitems-1)
                anchor_type=anchor_id=None
                if it.get("anchor"):
                    kind,ident=resolve(it["anchor"]);anchor_type=kind;anchor_id=str(ident)
                end_type=end_id=None
                if it.get("end_anchor"):
                    kind,ident=resolve(it["end_anchor"]);end_type=kind;end_id=str(ident)
                start=it.get("start");end=it.get("end") if it["type"]=="span" else None
                schematic_start=float(start if start is not None else auto);schematic_end=float(end) if end is not None else (min(.98,schematic_start+.08) if it["type"]=="span" else None)
                planned_start=float(start) if start is not None else (None if anchor_type else schematic_start);planned_end=float(end) if end is not None else (None if end_type else schematic_end)
                iid=con.execute("""INSERT INTO map_items(line_id,position,item_type,title,short_label,description,planned_start,planned_end,schematic_start,schematic_end,start_anchor_type,start_anchor_id,start_offset,end_anchor_type,end_anchor_id,end_offset,excerpt,label_offset,label_pinned,break_after,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,0,?,0,0,?,?,?)""",(lid,ii,it["type"],it["title"],it["short_label"],it["description"],planned_start,planned_end,schematic_start,schematic_end,anchor_type,anchor_id,end_type,end_id,it["excerpt"],1 if it["break_after"] else 0,t,t)).lastrowid;bind(it["id"],"map_item",iid);queue_links(it["id"],it["links"])
        for j in m["junctions"]:
            fl=int(resolve(j["from"]["line"])[1]);tl=int(resolve(j["to"]["line"])[1]);fi=int(resolve(j["from"]["item"])[1]) if j["from"].get("item") else None;ti=int(resolve(j["to"]["item"])[1]) if j["to"].get("item") else None
            con.execute("INSERT INTO map_junctions(map_id,from_line_id,from_item_id,from_edge,to_line_id,to_item_id,to_edge,kind,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(mid,fl,fi,j["from"]["edge"],tl,ti,j["to"]["edge"],j["kind"],t))

    # Tasks and existing Breadmaker subtask/milestone model.
    for task in doc["tasks"]:
        mid=marker_id(task.get("marker"));archived_from="open" if task["status"]=="archived" else None
        tid=con.execute("INSERT INTO tasks(project_id,title,description,status,archived_from_status,marker_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(pid,task["title"],task["description"],task["status"],archived_from,mid,t,t)).lastrowid;bind(task["id"],"task",tid);queue_links(task["id"],task["links"])
        for si,sub in enumerate(task["subtasks"]):
            sid=con.execute("INSERT INTO task_milestones(project_id,task_id,position,title,description,completed,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(pid,tid,si,sub["title"],sub["description"],1 if sub["completed"] else 0,t,t)).lastrowid;bind(sub["id"],"milestone",sid);queue_links(sub["id"],sub["links"])

    # Resolve generic links last, after every target exists.
    for source_public,link in link_queue:
        skind,sid=resolve(source_public);source_type=_KIND_TO_REFERENCE_TYPE.get(skind)
        if not source_type:continue
        if link.get("url"):
            target_type="url";target_id=link["url"]
        else:
            tkind,tid=resolve(link["target"]);target_type=_KIND_TO_REFERENCE_TYPE.get(tkind)
            if not target_type:continue
            target_id=str(tid)
        con.execute("INSERT INTO references_map(project_id,source_type,source_id,target_type,target_id,start_offset,end_offset,excerpt,relationship,created_at) VALUES(?,?,?,?,?,0,0,'',?,?)",(pid,source_type,str(sid),target_type,str(target_id),link.get("relationship") or "",t))

    con.execute("UPDATE projects SET title=?,updated_at=? WHERE id=?",(doc["name"],t,pid))
    storage_invariants.validate_project_invariants(con,pid)
    return {"project_id":pid,"refs":refs}


def configure_developmental_versions(con, validated:dict, refs:dict[str,tuple[str,Any]], snapshot:Callable[[Any,dict,int],None]) -> None:
    """Apply imported Structure Order after app.sync_developmental_canvases created mirrors."""
    doc=validated["document"];t=storage_values.now()
    def resolve(ref:str):
        canonical=next((k for k in refs if k.lower()==ref.lower()),ref);return refs[canonical]
    for c in doc["canvases"]:
        if c["type"]!="developmental":continue
        cid=int(resolve(c["id"])[1]);cv=dict(con.execute("SELECT * FROM canvases WHERE id=?",(cid,)).fetchone())
        scope=c["developmental_scope"];entity_type="scene" if scope=="scenes" else "chapter";column="scene_id" if scope=="scenes" else "chapter_id"
        wanted=[int(resolve(r)[1]) for r in c["structure"]]
        rows=[dict(r) for r in con.execute(f"SELECT * FROM canvas_nodes WHERE canvas_id=? AND node_type=?",(cid,entity_type)).fetchall()]
        by_entity={int(r[column]):r for r in rows if r.get(column) is not None}
        # When structure is omitted, retain the natural manuscript order created by sync.
        if wanted:
            for r in rows:con.execute("UPDATE canvas_nodes SET lane_position=NULL,x=?,y=?,updated_at=? WHERE id=?",(float(r.get('x') or 120),390.0,t,r["id"]))
            for pos,eid in enumerate(wanted):
                row=by_entity.get(eid)
                if row:con.execute("UPDATE canvas_nodes SET lane_position=?,x=120,y=118,updated_at=? WHERE id=?",(pos,t,row["id"]))
        version=con.execute("SELECT * FROM canvas_versions WHERE canvas_id=? ORDER BY position,id LIMIT 1",(cid,)).fetchone()
        if version:snapshot(con,cv,int(version["id"]))


# ---------- Export ----------
