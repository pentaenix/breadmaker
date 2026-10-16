from __future__ import annotations
import re
from collections import defaultdict
from .validation import FORMAT, SCHEMA_VERSION, ProjectJSONError, validate_document
from . import markup

def _slug(value:str)->str:
    s=re.sub(r"[^a-z0-9]+","-",(value or "").lower()).strip("-")
    return s[:56] or "item"


def export_active_database(con) -> tuple[dict,list[str]]:
    p=con.execute("SELECT * FROM projects ORDER BY id LIMIT 1").fetchone()
    if not p:raise ProjectJSONError("No active project is available to export.")
    pid=int(p["id"]);warnings=[];doc={"format":FORMAT,"schema_version":SCHEMA_VERSION,"name":p["title"],"markers":[],"manuscript":{"tabs":[]},"canvases":[],"maps":[],"tasks":[]}
    public:dict[tuple[str,str],str]={};used:set[str]=set()
    def make(kind:str,label:str,internal:Any)->str:
        base=f"{kind}:{_slug(label)}";candidate=base;n=2
        while candidate.lower() in used:candidate=f"{base}-{n}";n+=1
        used.add(candidate.lower());public[(kind,str(internal))]=candidate;return candidate
    markers=[dict(r) for r in con.execute("SELECT * FROM markup_labels WHERE project_id=? ORDER BY created_at,id",(pid,))]
    tabs=[dict(r) for r in con.execute("SELECT * FROM document_tabs WHERE project_id=? ORDER BY position,id",(pid,))]
    chapters=[dict(r) for r in con.execute("SELECT * FROM chapters WHERE project_id=? ORDER BY tab_id,position,id",(pid,))]
    scenes=[dict(r) for r in con.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY tab_id,position,id",(pid,))]
    canvases=[dict(r) for r in con.execute("SELECT * FROM canvases WHERE project_id=? ORDER BY position,id",(pid,))]
    maps=[dict(r) for r in con.execute("SELECT * FROM story_maps WHERE project_id=? ORDER BY position,id",(pid,))]
    tasks=[dict(r) for r in con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY id",(pid,))]
    for r in markers:make("marker",r["name"],r["id"])
    for r in tabs:make("tab",r["title"],r["id"])
    for r in chapters:make("chapter",r["title"],r["id"])
    for r in scenes:make("scene",r["title"],r["id"])
    for r in canvases:make("canvas",r["name"],r["id"])
    for r in maps:make("map",r["name"],r["id"])
    for r in tasks:make("task",r["title"],r["id"])
    lines=[dict(r) for r in con.execute("SELECT l.* FROM map_lines l JOIN story_maps m ON m.id=l.map_id WHERE m.project_id=? ORDER BY l.map_id,l.position,l.id",(pid,))]
    items=[dict(r) for r in con.execute("SELECT i.* FROM map_items i JOIN map_lines l ON l.id=i.line_id JOIN story_maps m ON m.id=l.map_id WHERE m.project_id=? ORDER BY i.line_id,i.position,i.id",(pid,))]
    milestones=[dict(r) for r in con.execute("SELECT * FROM task_milestones WHERE project_id=? ORDER BY task_id,position,id",(pid,))]
    for r in lines:make("map-line",r["name"],r["id"])
    for r in items:make("map-item",r["title"],r["id"])
    for r in milestones:make("subtask",r["title"],r["id"])
    nodes=[dict(r) for r in con.execute("SELECT n.* FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE c.project_id=? AND n.node_type NOT IN ('chapter','scene','image') AND COALESCE(n.developmental_hidden,0)=0 ORDER BY n.canvas_id,n.id",(pid,))]
    for r in nodes:
        label=r.get("title") or ("routing" if r["node_type"]=="routing" else r["node_type"]);make("node",label,r["id"])
    if con.execute("SELECT 1 FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE c.project_id=? AND n.node_type='image' LIMIT 1",(pid,)).fetchone():warnings.append("Canvas images are omitted because Project JSON 1.0 intentionally contains no media.")
    if con.execute("SELECT 1 FROM manuscript_blocks WHERE project_id=? AND block_type='image' LIMIT 1",(pid,)).fetchone():warnings.append("Manuscript images are omitted because Project JSON 1.0 intentionally contains no media.")

    refs_rows=[dict(r) for r in con.execute("SELECT * FROM references_map WHERE project_id=? ORDER BY id",(pid,))]
    type_to_kind={"document":"tab","chapter":"chapter","scene":"scene","canvas":"canvas","node":"node","map":"map","map_line":"map-line","map_item":"map-item","task":"task","milestone":"subtask"}
    links_by_source=defaultdict(list)
    for r in refs_rows:
        sk=type_to_kind.get(r["source_type"]);tk=type_to_kind.get(r["target_type"])
        source=public.get((sk,str(r["source_id"]))) if sk else None
        if not source:continue
        if r["target_type"]=="url":link={"url":r["target_id"]}
        else:
            target=public.get((tk,str(r["target_id"]))) if tk else None
            if not target:continue
            link={"target":target}
        if r.get("relationship"):link["relationship"]=r["relationship"]
        links_by_source[source].append(link)
    def add_links(obj:dict):
        if links_by_source.get(obj["id"]):obj["links"]=links_by_source[obj["id"]]

    for m in markers:doc["markers"].append({"id":public[("marker",str(m["id"]))],"name":m["name"],"description":m.get("description") or "","color":m["color"]})
    blocks=[dict(r) for r in con.execute("SELECT * FROM manuscript_blocks WHERE project_id=? AND block_type='paragraph' ORDER BY tab_id,chapter_id,position,id",(pid,))]
    blocks_by_ch=defaultdict(list)
    for b in blocks:blocks_by_ch[int(b["chapter_id"])].append(b)
    scene_by_id={int(s["id"]):s for s in scenes}
    chapter_by_id={int(c["id"]):c for c in chapters}

    # Project JSON 1.0's simple public manuscript model is intentionally readable:
    # chapter.content followed by chapter.scenes[]. Refuse to export live manuscript
    # arrangements that cannot be represented in that model without reordering prose.
    # This is safer than silently producing a lossy round trip.
    scene_chapters=defaultdict(set)
    for b in blocks:
        if b.get("scene_id") is not None:scene_chapters[int(b["scene_id"])].add(int(b["chapter_id"]))
    for sc in scenes:
        sid=int(sc["id"]); owners=scene_chapters.get(sid,set())
        if not owners:
            raise ProjectJSONError(f"Scene {sc['title']!r} has no manuscript prose and cannot be represented by Project JSON 1.0.","$.manuscript","PROJECT_JSON_013")
        if len(owners)>1:
            raise ProjectJSONError(f"Scene {sc['title']!r} crosses Chapter boundaries. Project JSON 1.0 only represents a Scene inside one Chapter.","$.manuscript","PROJECT_JSON_014")
        owner=chapter_by_id.get(next(iter(owners)))
        if owner and int(owner.get("is_implicit") or 0):
            raise ProjectJSONError(f"Scene {sc['title']!r} lives in chapterless Tab prose. Project JSON 1.0 cannot represent that Scene without losing its assignment.","$.manuscript","PROJECT_JSON_015")
    for ch in chapters:
        if int(ch.get("is_implicit") or 0):continue
        seen_scene=False;finished:set[int]=set();current_scene=None
        for b in blocks_by_ch.get(int(ch["id"]),[]):
            sid=int(b["scene_id"]) if b.get("scene_id") is not None else None
            if sid is None:
                if seen_scene:
                    raise ProjectJSONError(f"Chapter {ch['title']!r} contains unassigned prose after Scene prose. Project JSON 1.0 would reorder that text.","$.manuscript","PROJECT_JSON_016")
                continue
            seen_scene=True
            if current_scene!=sid:
                if sid in finished:
                    raise ProjectJSONError(f"Scene {scene_by_id.get(sid,{}).get('title','Scene')!r} is not contiguous inside its Chapter.","$.manuscript","PROJECT_JSON_017")
                if current_scene is not None:finished.add(current_scene)
                current_scene=sid
    for tab in tabs:
        tout={"id":public[("tab",str(tab["id"]))],"name":tab["title"],"description":tab.get("description") or "","main":bool(tab.get("is_manuscript")),"chapters":[]};add_links(tout)
        implicit=[x for x in chapters if int(x.get("tab_id") or 0)==int(tab["id"]) and int(x.get("is_implicit") or 0)]
        implicit_blocks=[]
        for ch in implicit: implicit_blocks.extend(blocks_by_ch.get(int(ch["id"]),[]))
        if implicit_blocks:
            implicit_blocks.sort(key=lambda b:(float(b.get("position") or 0),str(b.get("id") or "")))
            tout["content"]="\n".join(markup.inline_html_to_markup(b.get("content_html") or "",b.get("text_content") or "") for b in implicit_blocks)
        for ch in [x for x in chapters if int(x.get("tab_id") or 0)==int(tab["id"]) and not int(x.get("is_implicit") or 0)]:
            cout={"id":public[("chapter",str(ch["id"]))],"title":ch["title"],"description":ch.get("description") or "","content":"","scenes":[]};add_links(cout)
            unscened=[];scene_blocks=defaultdict(list);scene_order=[]
            for b in blocks_by_ch[int(ch["id"])]:
                if b.get("scene_id") is None:unscened.append(b)
                else:
                    sid=int(b["scene_id"])
                    if sid not in scene_blocks:scene_order.append(sid)
                    scene_blocks[sid].append(b)
            cout["content"]="\n".join(markup.inline_html_to_markup(b.get("content_html") or "",b.get("text_content") or "") for b in unscened)
            for sid in scene_order:
                sc=scene_by_id.get(sid)
                if not sc:continue
                sout={"id":public[("scene",str(sid))],"title":sc["title"],"description":sc.get("description") or "","content":"\n".join(markup.inline_html_to_markup(b.get("content_html") or "",b.get("text_content") or "") for b in scene_blocks[sid])};add_links(sout);cout["scenes"].append(sout)
            tout["chapters"].append(cout)
        doc["manuscript"]["tabs"].append(tout)

    marker_public={int(m["id"]):public[("marker",str(m["id"]))] for m in markers}
    for cv in canvases:
        cid=int(cv["id"]);cout={"id":public[("canvas",str(cid))],"name":cv["name"],"description":cv.get("description") or "","type":"developmental" if cv["canvas_type"]=="developmental" else "normal","nodes":[],"connectors":[]};add_links(cout)
        if cv.get("parent_id") is not None:cout["parent"]=public.get(("canvas",str(cv["parent_id"])))
        if cv["canvas_type"]=="developmental":
            cout["developmental_scope"]=cv.get("developmental_scope") or "chapters";struct=[dict(r) for r in con.execute("SELECT * FROM canvas_nodes WHERE canvas_id=? AND node_type IN ('chapter','scene') AND lane_position IS NOT NULL ORDER BY lane_position,id",(cid,))]
            cout["structure"]=[public.get(("scene" if r["node_type"]=="scene" else "chapter",str(r["scene_id"] if r["node_type"]=="scene" else r["chapter_id"]))) for r in struct];cout["structure"]=[x for x in cout["structure"] if x]
        local_nodes=[n for n in nodes if int(n["canvas_id"])==cid]
        for n in local_nodes:
            nid=public[("node",str(n["id"]))];position={"x":n["x"],"y":n["y"],"width":n["w"],"height":n["h"]}
            if n["node_type"]=="free":
                no={"id":nid,"type":"card","title":n.get("title") or "","body":n.get("body") or "","display":n.get("display_mode") or "full","text_align":n.get("text_align") if n.get("text_align") in {"left","center","right"} else "left","shape":"text" if n.get("shape")=="text" else "card","position":position}
                if n.get("label_id") in marker_public:no["marker"]=marker_public[int(n["label_id"])]
                elif n.get("color"):no["color"]=n["color"]
                if n.get("text_color"):no["text_color"]=n["text_color"]
                if n.get("chapter_id") is not None:no["linked_to"]=public.get(("chapter",str(n["chapter_id"])))
                elif n.get("scene_id") is not None:no["linked_to"]=public.get(("scene",str(n["scene_id"])))
            elif n["node_type"]=="figure":
                no={"id":nid,"type":"figure","shape":n.get("shape") or "rect","position":position,"border_width":float(n.get("figure_border_width") or 1.0),"layer":n.get("figure_layer") if n.get("figure_layer") in {"back","front"} else "back"}
                if n.get("figure_fill_color"):no["fill_color"]=n["figure_fill_color"]
                if n.get("figure_stroke_color"):no["stroke_color"]=n["figure_stroke_color"]
                if n.get("shape")=="line":no.update({"line_x1":float(n.get("figure_line_x1") if n.get("figure_line_x1") is not None else .02),"line_y1":float(n.get("figure_line_y1") if n.get("figure_line_y1") is not None else .5),"line_x2":float(n.get("figure_line_x2") if n.get("figure_line_x2") is not None else .98),"line_y2":float(n.get("figure_line_y2") if n.get("figure_line_y2") is not None else .5)})
            elif n["node_type"]=="label":no={"id":nid,"type":"index","value":n.get("title") or "1","position":position}
            else:no={"id":nid,"type":"routing","position":position}
            add_links(no);cout["nodes"].append(no)
        edges=[dict(r) for r in con.execute("SELECT * FROM canvas_edges WHERE canvas_id=? AND COALESCE(developmental_hidden,0)=0 ORDER BY id",(cid,))]
        local_node_ids={int(n["id"]) for n in local_nodes}
        for e in edges:
            if int(e["from_node_id"]) not in local_node_ids or int(e["to_node_id"]) not in local_node_ids:continue
            eo={"from":public[("node",str(e["from_node_id"]))],"to":public[("node",str(e["to_node_id"]))],"from_port":e["from_port"],"to_port":e["to_port"],"label":e.get("label") or "","stroke":e.get("line_style") or "solid","end":e.get("end_style") or "arrow","width":float(e.get("line_width") or 1.5),"route":e.get("route_style") or "straight"}
            if e.get("marker_id") in marker_public:eo["marker"]=marker_public[int(e["marker_id"])]
            elif e.get("color"):eo["color"]=e["color"]
            cout["connectors"].append(eo)
        doc["canvases"].append(cout)

    items_by_line=defaultdict(list)
    for it in items:items_by_line[int(it["line_id"])].append(it)
    junctions=[dict(r) for r in con.execute("SELECT j.* FROM map_junctions j JOIN story_maps m ON m.id=j.map_id WHERE m.project_id=? ORDER BY j.map_id,j.id",(pid,))]
    for mp in maps:
        mo={"id":public[("map",str(mp["id"]))],"name":mp["name"],"description":mp.get("description") or "","orientation":mp["orientation"],"layout_mode":mp["layout_mode"],"label_mode":mp["label_mode"],"ruler":mp["ruler"],"background":mp["background"],"lines":[],"junctions":[]};add_links(mo)
        for l in [x for x in lines if int(x["map_id"])==int(mp["id"])]:
            lo={"id":public[("map-line",str(l["id"]))],"name":l["name"],"description":l.get("description") or "","color":l["color"],"visible":bool(l["visible"]),"items":[]};add_links(lo)
            for it in items_by_line[int(l["id"])]:
                io={"id":public[("map-item",str(it["id"]))],"type":it["item_type"],"title":it["title"],"short_label":it.get("short_label") or "","description":it.get("description") or "","excerpt":it.get("excerpt") or "","break_after":bool(it.get("break_after"))}
                if it.get("planned_start") is not None:io["start"]=float(it["planned_start"])
                if it.get("planned_end") is not None and it["item_type"]=="span":io["end"]=float(it["planned_end"])
                if it.get("start_anchor_type") in {"chapter","scene"}:io["anchor"]=public.get((it["start_anchor_type"],str(it["start_anchor_id"])))
                if it.get("end_anchor_type") in {"chapter","scene"}:io["end_anchor"]=public.get((it["end_anchor_type"],str(it["end_anchor_id"])))
                add_links(io);lo["items"].append(io)
            mo["lines"].append(lo)
        for j in [x for x in junctions if int(x["map_id"])==int(mp["id"])]:
            jo={"kind":j["kind"],"from":{"line":public.get(("map-line",str(j["from_line_id"]))),"edge":j["from_edge"]},"to":{"line":public.get(("map-line",str(j["to_line_id"]))),"edge":j["to_edge"]}}
            if j.get("from_item_id") is not None:jo["from"]["item"]=public.get(("map-item",str(j["from_item_id"])))
            if j.get("to_item_id") is not None:jo["to"]["item"]=public.get(("map-item",str(j["to_item_id"])))
            mo["junctions"].append(jo)
        doc["maps"].append(mo)

    milestones_by_task=defaultdict(list)
    for m in milestones:milestones_by_task[int(m["task_id"])].append(m)
    for task in tasks:
        to={"id":public[("task",str(task["id"]))],"title":task["title"],"description":task.get("description") or "","status":task["status"],"subtasks":[]};add_links(to)
        if task.get("marker_id") in marker_public:to["marker"]=marker_public[int(task["marker_id"])]
        for s in milestones_by_task[int(task["id"])]:
            so={"id":public[("subtask",str(s["id"]))],"title":s["title"],"description":s.get("description") or "","completed":bool(s["completed"])};add_links(so);to["subtasks"].append(so)
        doc["tasks"].append(to)

    # The exporter is part of the public contract too. Never hand the user a file
    # Breadmaker's own importer would reject; surface the exact contract violation
    # here instead of letting it appear later during a round trip.
    try:
        validate_document(doc)
    except ProjectJSONError as exc:
        raise ProjectJSONError(f"The live project cannot be represented as valid Project JSON 1.0: {exc.detail()}", "$", "PROJECT_JSON_018") from exc
    return doc,warnings
