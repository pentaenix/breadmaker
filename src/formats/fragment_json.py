from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from formats.project_json import validation as project_json_validation
from formats.project_json import markup as project_json_markup
from formats.project_json import layout as project_json_layout
from storage import invariants as storage_invariants
from storage import values as storage_values
FORMAT = "breadmaker-fragment"
SCHEMA_VERSION = "1.0"
TARGET_TYPES = {"tab", "chapter", "canvas", "map", "map_line", "task"}


class FragmentJSONError(project_json_validation.ProjectJSONError):
    pass


def _err(path: str, message: str, code: str = "FRAGMENT_JSON_001"):
    raise FragmentJSONError(message, path, code)


def _target(value: Any, path: str, warnings: list[str]) -> dict:
    raw = project_json_validation._dict(value, path)
    project_json_validation._unknown(raw, {"type", "current", "name", "path", "tab", "map"}, path, warnings)
    typ = project_json_validation._enum(raw.get("type"), f"{path}.type", TARGET_TYPES, "canvas")
    current = project_json_validation._bool(raw.get("current"), f"{path}.current", False)
    name = project_json_validation._text(raw.get("name"), f"{path}.name", limit=240)
    tab = project_json_validation._text(raw.get("tab"), f"{path}.tab", limit=240)
    map_name = project_json_validation._text(raw.get("map"), f"{path}.map", limit=240)
    path_value = raw.get("path")
    parts: list[str] = []
    if path_value is not None:
        for i, part in enumerate(project_json_validation._list(path_value, f"{path}.path")):
            parts.append(project_json_validation._text(part, f"{path}.path[{i}]", required=True, limit=240))
        if typ != "canvas":
            _err(f"{path}.path", "Only Canvas targets use path.")
    if current and (name or parts or tab or map_name):
        warnings.append(f"{path}: current=true takes precedence over the other target hints")
    return {"type": typ, "current": current, "name": name, "path": parts, "tab": tab, "map": map_name}


def _cut(value: Any, path: str, warnings: list[str]) -> dict:
    raw = project_json_validation._dict(value, path)
    project_json_validation._unknown(raw, {"id", "title", "content", "note", "source_label", "source_project", "source_chapter", "source_scene"}, path, warnings)
    return {
        "id": project_json_validation._ref(raw.get("id"), f"{path}.id"),
        "title": project_json_validation._text(raw.get("title"), f"{path}.title", required=True, limit=240),
        "content": project_json_validation._text(raw.get("content"), f"{path}.content", limit=2_000_000),
        "note": project_json_validation._text(raw.get("note"), f"{path}.note", limit=10_000),
        "source_label": project_json_validation._text(raw.get("source_label"), f"{path}.source_label", limit=240),
        "source_project": project_json_validation._text(raw.get("source_project"), f"{path}.source_project", limit=240),
        "source_chapter": project_json_validation._text(raw.get("source_chapter"), f"{path}.source_chapter", limit=240),
        "source_scene": project_json_validation._text(raw.get("source_scene"), f"{path}.source_scene", limit=240),
    }


def validate_fragment(source: dict) -> dict:
    """Validate Fragment JSON by mounting it into a synthetic Project JSON document.

    This deliberately reuses Project JSON's object definitions so Card/Map/Task/etc.
    semantics cannot drift between whole-project and additive imports.
    """
    warnings: list[str] = []
    raw = project_json_validation._dict(source, "$")
    project_json_validation._unknown(raw, {"format", "schema_version", "name", "markers", "manuscript", "canvases", "maps", "tasks", "cuts", "mounts"}, "$", warnings)
    if raw.get("format") != FORMAT:
        _err("$.format", f"Expected {FORMAT!r}.", "FRAGMENT_JSON_002")
    if str(raw.get("schema_version") or "") != SCHEMA_VERSION:
        _err("$.schema_version", f"Unsupported schema version. This Breadmaker expects {SCHEMA_VERSION}.", "FRAGMENT_JSON_003")
    name = project_json_validation._text(raw.get("name"), "$.name", required=True, limit=120)
    manuscript = project_json_validation._dict(raw.get("manuscript"), "$.manuscript")
    project_json_validation._unknown(manuscript, {"tabs"}, "$.manuscript", warnings)
    root_tabs = []
    for i, item in enumerate(project_json_validation._list(manuscript.get("tabs"), "$.manuscript.tabs")):
        tab = dict(project_json_validation._dict(item, f"$.manuscript.tabs[{i}]"))
        if tab.get("main") is True:
            _err(f"$.manuscript.tabs[{i}].main", "Fragment imports add Tabs but never replace the project's Main Manuscript.")
        tab["main"] = False
        root_tabs.append(tab)

    root_markers = project_json_validation._list(raw.get("markers"), "$.markers")
    root_canvases = [dict(project_json_validation._dict(x, f"$.canvases[{i}]")) for i,x in enumerate(project_json_validation._list(raw.get("canvases"), "$.canvases"))]
    root_maps = project_json_validation._list(raw.get("maps"), "$.maps")
    root_tasks = project_json_validation._list(raw.get("tasks"), "$.tasks")
    root_cuts = project_json_validation._list(raw.get("cuts"), "$.cuts")
    for i, canvas in enumerate(root_canvases):
        if isinstance(canvas, dict) and project_json_validation._list(canvas.get("structure"), f"$.canvases[{i}].structure"):
            _err(f"$.canvases[{i}].structure", "Fragment JSON 1.0 can create a Developmental Canvas, but Structure Order remains human-controlled after import.")

    mounts_raw = project_json_validation._list(raw.get("mounts"), "$.mounts")
    mounts_meta: list[dict] = []
    synthetic_tabs: list[dict] = []
    synthetic_canvases: list[dict] = []
    synthetic_maps: list[dict] = []
    synthetic_tasks: list[dict] = []

    if not any((root_markers, root_tabs, root_canvases, root_maps, root_tasks, root_cuts, mounts_raw)):
        _err("$", "Fragment does not contain anything to import.", "FRAGMENT_JSON_004")

    # Reserve one Main Manuscript only for the validator. It is never imported.
    used_ids = set()
    def collect_ids(value):
        if isinstance(value, dict):
            ident = value.get("id")
            if isinstance(ident, str):
                used_ids.add(ident.lower())
            for child in value.values(): collect_ids(child)
        elif isinstance(value, list):
            for child in value: collect_ids(child)
    collect_ids(raw)
    def internal_id(stem: str) -> str:
        candidate = f"fragment-internal:{stem}"
        n = 2
        while candidate.lower() in used_ids:
            candidate = f"fragment-internal:{stem}-{n}"; n += 1
        used_ids.add(candidate.lower())
        return candidate

    fake_main = internal_id("main")
    synthetic_tabs.append({"id": fake_main, "name": "Fragment validation", "main": True, "chapters": []})
    synthetic_tabs.extend(root_tabs)

    for mi, item in enumerate(mounts_raw):
        p = f"$.mounts[{mi}]"; mount = project_json_validation._dict(item, p)
        project_json_validation._unknown(mount, {"target", "chapters", "scenes", "canvases", "nodes", "connectors", "layout", "layouts", "lines", "junctions", "items", "subtasks"}, p, warnings)
        target = _target(mount.get("target"), f"{p}.target", warnings)
        typ = target["type"]
        allowed_by_type = {
            "tab": {"chapters"},
            "chapter": {"scenes"},
            "canvas": {"canvases", "nodes", "connectors", "layout", "layouts"},
            "map": {"lines", "junctions"},
            "map_line": {"items"},
            "task": {"subtasks"},
        }[typ]
        supplied = {k for k in allowed_by_type if mount.get(k) not in (None, [], {})}
        if not supplied:
            _err(p, f"A {typ} mount does not contain anything to add.")
        illegal = {k for k in {"chapters","scenes","canvases","nodes","connectors","layout","layouts","lines","junctions","items","subtasks"} if mount.get(k) not in (None, [], {}) and k not in allowed_by_type}
        if illegal:
            _err(p, f"A {typ} target cannot receive: {', '.join(sorted(illegal))}.")

        meta = {"target": target, "kind": typ, "source_index": mi}
        if typ == "tab":
            fid = internal_id(f"mount-tab-{mi}")
            synthetic_tabs.append({"id": fid, "name": f"Mount {mi}", "main": False, "chapters": mount.get("chapters") or []})
            meta["container_id"] = fid
        elif typ == "chapter":
            tid = internal_id(f"mount-chapter-tab-{mi}"); cid = internal_id(f"mount-chapter-{mi}")
            synthetic_tabs.append({"id": tid, "name": f"Mount {mi}", "main": False, "chapters": [{"id": cid, "title": "Mount", "scenes": mount.get("scenes") or []}]})
            meta["container_id"] = cid
        elif typ == "canvas":
            fid = internal_id(f"mount-canvas-{mi}")
            fake = {"id": fid, "name": f"Mount {mi}", "type": "normal", "nodes": mount.get("nodes") or [], "connectors": mount.get("connectors") or []}
            if mount.get("layout") is not None: fake["layout"] = mount.get("layout")
            if mount.get("layouts") is not None: fake["layouts"] = mount.get("layouts")
            synthetic_canvases.append(fake)
            child_ids = []
            for ci, child in enumerate(project_json_validation._list(mount.get("canvases"), f"{p}.canvases")):
                if not isinstance(child, dict): _err(f"{p}.canvases[{ci}]", "Expected an object.")
                child = dict(child)
                if project_json_validation._list(child.get("structure"), f"{p}.canvases[{ci}].structure"):
                    _err(f"{p}.canvases[{ci}].structure", "Fragment JSON 1.0 leaves Developmental Structure Order human-controlled.")
                if not child.get("parent"): child["parent"] = fid
                child_ids.append(child.get("id")); synthetic_canvases.append(child)
            meta["container_id"] = fid; meta["child_ids"] = child_ids
        elif typ == "map":
            fid = internal_id(f"mount-map-{mi}")
            synthetic_maps.append({"id": fid, "name": f"Mount {mi}", "lines": mount.get("lines") or [], "junctions": mount.get("junctions") or []})
            meta["container_id"] = fid
        elif typ == "map_line":
            mid = internal_id(f"mount-line-map-{mi}"); lid = internal_id(f"mount-line-{mi}")
            synthetic_maps.append({"id": mid, "name": f"Mount {mi}", "lines": [{"id": lid, "name": "Mount", "items": mount.get("items") or []}]})
            meta["container_id"] = lid
        else:
            tid = internal_id(f"mount-task-{mi}")
            synthetic_tasks.append({"id": tid, "title": f"Mount {mi}", "subtasks": mount.get("subtasks") or []})
            meta["container_id"] = tid
        mounts_meta.append(meta)

    fake_doc = {
        "format": project_json_validation.FORMAT,
        "schema_version": project_json_validation.SCHEMA_VERSION,
        "name": name,
        "markers": root_markers,
        "manuscript": {"tabs": synthetic_tabs},
        "canvases": [*root_canvases, *synthetic_canvases],
        "maps": [*root_maps, *synthetic_maps],
        "tasks": [*root_tasks, *synthetic_tasks],
    }
    try:
        validated = project_json_validation.validate_document(fake_doc)
    except project_json_validation.ProjectJSONError as exc:
        raise FragmentJSONError(exc.message, exc.path, exc.code.replace("PROJECT_JSON", "FRAGMENT_JSON")) from exc
    warnings.extend(validated["warnings"])
    vdoc = validated["document"]
    tab_by_id = {x["id"]: x for x in vdoc["manuscript"]["tabs"]}
    canvas_by_id = {x["id"]: x for x in vdoc["canvases"]}
    map_by_id = {x["id"]: x for x in vdoc["maps"]}
    task_by_id = {x["id"]: x for x in vdoc["tasks"]}

    root_tab_ids = {str(x.get("id")) for x in root_tabs}
    root_canvas_ids = {str(x.get("id")) for x in root_canvases}
    root_map_ids = {str(x.get("id")) for x in root_maps if isinstance(x, dict)}
    root_task_ids = {str(x.get("id")) for x in root_tasks if isinstance(x, dict)}
    fragment = {
        "format": FORMAT, "schema_version": SCHEMA_VERSION, "name": name,
        "markers": vdoc["markers"],
        "manuscript": {"tabs": []},
        "canvases": [canvas_by_id[i] for i in root_canvas_ids if i in canvas_by_id],
        "maps": [map_by_id[i] for i in root_map_ids if i in map_by_id],
        "tasks": [task_by_id[i] for i in root_task_ids if i in task_by_id],
        "cuts": [], "mounts": [],
    }
    # Preserve source order.
    fragment["manuscript"]["tabs"] = [tab_by_id[str(x.get("id"))] for x in root_tabs]
    fragment["canvases"] = [canvas_by_id[str(x.get("id"))] for x in root_canvases]
    fragment["maps"] = [map_by_id[str(x.get("id"))] for x in root_maps]
    fragment["tasks"] = [task_by_id[str(x.get("id"))] for x in root_tasks]
    for tab in fragment["manuscript"]["tabs"]: tab["main"] = False

    cut_ids = set()
    all_user_ids = {k.lower() for k in validated.get("id_kinds", {}) if not k.lower().startswith("fragment-internal:")}
    for i, item in enumerate(root_cuts):
        cut = _cut(item, f"$.cuts[{i}]", warnings)
        low = cut["id"].lower()
        if low in cut_ids or low in all_user_ids: _err(f"$.cuts[{i}].id", f"Reference ID {cut['id']!r} is duplicated.")
        cut_ids.add(low); fragment["cuts"].append(cut)

    for meta in mounts_meta:
        typ = meta["kind"]; entry = {"target": meta["target"], "kind": typ}
        if typ == "tab":
            entry["chapters"] = tab_by_id[meta["container_id"]]["chapters"]
        elif typ == "chapter":
            holder = next(ch for tab in vdoc["manuscript"]["tabs"] for ch in tab["chapters"] if ch["id"] == meta["container_id"])
            entry["scenes"] = holder["scenes"]
        elif typ == "canvas":
            holder = canvas_by_id[meta["container_id"]]
            entry.update({"nodes": holder["nodes"], "connectors": holder["connectors"], "layouts": holder["layouts"], "canvases": []})
            for child_id in meta.get("child_ids", []):
                if child_id not in canvas_by_id: continue
                child = dict(canvas_by_id[child_id])
                if child.get("parent") == meta["container_id"]: child["parent"] = None
                entry["canvases"].append(child)
        elif typ == "map":
            holder = map_by_id[meta["container_id"]]; entry.update({"lines": holder["lines"], "junctions": holder["junctions"]})
        elif typ == "map_line":
            holder = next(line for m in vdoc["maps"] for line in m["lines"] if line["id"] == meta["container_id"]); entry["items"] = holder["items"]
        else:
            entry["subtasks"] = task_by_id[meta["container_id"]]["subtasks"]
        fragment["mounts"].append(entry)

    summary = {
        "markers": len(fragment["markers"]),
        "tabs": len(fragment["manuscript"]["tabs"]),
        "chapters": sum(len(t["chapters"]) for t in fragment["manuscript"]["tabs"]) + sum(len(m.get("chapters", [])) for m in fragment["mounts"]),
        "scenes": sum(len(ch["scenes"]) for t in fragment["manuscript"]["tabs"] for ch in t["chapters"]) + sum(len(m.get("scenes", [])) for m in fragment["mounts"]),
        "canvases": len(fragment["canvases"]) + sum(len(m.get("canvases", [])) for m in fragment["mounts"]),
        "cards": sum(sum(1 for n in c["nodes"] if n["type"] == "card") for c in fragment["canvases"]) + sum(sum(1 for n in m.get("nodes", []) if n["type"] == "card") for m in fragment["mounts"]),
        "canvas_objects": sum(len(c["nodes"]) for c in fragment["canvases"]) + sum(len(m.get("nodes", [])) for m in fragment["mounts"]),
        "connectors": sum(len(c["connectors"]) for c in fragment["canvases"]) + sum(len(m.get("connectors", [])) for m in fragment["mounts"]),
        "maps": len(fragment["maps"]),
        "map_lines": sum(len(m["lines"]) for m in fragment["maps"]) + sum(len(m.get("lines", [])) for m in fragment["mounts"]),
        "map_items": sum(len(l["items"]) for m in fragment["maps"] for l in m["lines"]) + sum(len(m.get("items", [])) for m in fragment["mounts"]),
        "tasks": len(fragment["tasks"]),
        "subtasks": sum(len(t["subtasks"]) for t in fragment["tasks"]) + sum(len(m.get("subtasks", [])) for m in fragment["mounts"]),
        "cuts": len(fragment["cuts"]),
        "mounts": len(fragment["mounts"]),
    }
    return {"fragment": fragment, "warnings": warnings, "summary": summary}


def _canvas_paths(rows: list[dict]) -> tuple[dict[int, str], dict[int, list[str]]]:
    by_id = {int(r["id"]): r for r in rows}; labels: dict[int, str] = {}; parts_memo: dict[int, list[str]] = {}
    def parts(cid: int, seen=None):
        if cid in parts_memo: return parts_memo[cid]
        seen = set(seen or ())
        if cid in seen: return [str(by_id[cid].get("name") or "Canvas")]
        seen.add(cid); row = by_id[cid]; parent = row.get("parent_id")
        value = [str(row.get("name") or "Canvas")]
        if parent and int(parent) in by_id: value = parts(int(parent), seen) + value
        parts_memo[cid] = value; labels[cid] = " / ".join(value); return value
    for cid in by_id: parts(cid)
    return labels, parts_memo


def target_candidates(con, pid: int, target: dict, context: dict | None = None) -> list[dict]:
    context = context or {}; typ = target["type"]
    rows: list[dict] = []
    current_key = {"tab":"document_id","chapter":"chapter_id","canvas":"canvas_id","map":"map_id","map_line":"map_line_id","task":"task_id"}[typ]
    current_requested = bool(target.get("current"))
    if current_requested:
        raw_ident = context.get(current_key)
        if raw_ident is not None:
            try: ident = int(raw_ident)
            except (TypeError, ValueError): _err(f"$.context.{current_key}", "Current import destination is invalid.", "FRAGMENT_JSON_009")
            where = {
                "tab": ("SELECT id,title name FROM document_tabs WHERE project_id=? AND id=?", (pid, ident)),
                "chapter": ("SELECT c.id,c.title name,dt.title parent FROM chapters c JOIN document_tabs dt ON dt.id=c.tab_id WHERE c.project_id=? AND c.id=? AND c.is_implicit=0", (pid, ident)),
                "canvas": ("SELECT id,name,parent_id FROM canvases WHERE project_id=? AND id=?", (pid, ident)),
                "map": ("SELECT id,name FROM story_maps WHERE project_id=? AND id=?", (pid, ident)),
                "map_line": ("SELECT l.id,l.name,m.name parent FROM map_lines l JOIN story_maps m ON m.id=l.map_id WHERE m.project_id=? AND l.id=?", (pid, ident)),
                "task": ("SELECT id,title name FROM tasks WHERE project_id=? AND id=?", (pid, ident)),
            }[typ]
            rows = [dict(r) for r in con.execute(*where).fetchall()]
            if rows:
                if typ == "canvas":
                    allc = [dict(r) for r in con.execute("SELECT id,name,parent_id FROM canvases WHERE project_id=?", (pid,)).fetchall()]; labels,_ = _canvas_paths(allc); rows[0]["label"] = labels[int(rows[0]["id"])]
                else: rows[0]["label"] = f"{rows[0].get('parent')} / {rows[0]['name']}" if rows[0].get("parent") else rows[0]["name"]
                return rows
    if typ == "tab": rows = [dict(r) for r in con.execute("SELECT id,title name FROM document_tabs WHERE project_id=? ORDER BY position,id", (pid,)).fetchall()]
    elif typ == "chapter": rows = [dict(r) for r in con.execute("SELECT c.id,c.title name,dt.title parent FROM chapters c JOIN document_tabs dt ON dt.id=c.tab_id WHERE c.project_id=? AND c.is_implicit=0 ORDER BY dt.position,c.position,c.id", (pid,)).fetchall()]
    elif typ == "canvas": rows = [dict(r) for r in con.execute("SELECT id,name,parent_id FROM canvases WHERE project_id=? ORDER BY position,id", (pid,)).fetchall()]
    elif typ == "map": rows = [dict(r) for r in con.execute("SELECT id,name FROM story_maps WHERE project_id=? ORDER BY position,id", (pid,)).fetchall()]
    elif typ == "map_line": rows = [dict(r) for r in con.execute("SELECT l.id,l.name,m.name parent FROM map_lines l JOIN story_maps m ON m.id=l.map_id WHERE m.project_id=? ORDER BY m.position,l.position,l.id", (pid,)).fetchall()]
    else: rows = [dict(r) for r in con.execute("SELECT id,title name FROM tasks WHERE project_id=? ORDER BY id", (pid,)).fetchall()]
    if typ == "canvas":
        labels,path_parts = _canvas_paths(rows)
        for row in rows: row["label"] = labels[int(row["id"])]; row["_path_parts"] = path_parts[int(row["id"])]
    else:
        for row in rows: row["label"] = f"{row.get('parent')} / {row['name']}" if row.get("parent") else row["name"]
    name = (target.get("name") or "").strip().lower(); tab = (target.get("tab") or "").strip().lower(); map_name = (target.get("map") or "").strip().lower(); wanted_parts = [x.strip().lower() for x in target.get("path") or []]
    filtered = rows
    selector_used = bool(wanted_parts or name or tab or map_name)
    if not current_requested:
        if wanted_parts and typ == "canvas": filtered = [r for r in rows if [p.strip().lower() for p in r.get("_path_parts", [])] == wanted_parts]
        elif name: filtered = [r for r in rows if str(r["name"]).strip().lower() == name]
        if tab and typ == "chapter": filtered = [r for r in filtered if str(r.get("parent") or "").strip().lower() == tab]
        if map_name and typ == "map_line": filtered = [r for r in filtered if str(r.get("parent") or "").strip().lower() == map_name]
    matched = bool(filtered) if selector_used and not current_requested else not current_requested
    chosen = filtered if filtered else rows
    for row in chosen: row["_target_match"] = matched
    return chosen


def resolve_mount_targets(con, validated: dict, context: dict | None = None, choices: dict | None = None) -> list[dict]:
    pid = int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()[0]); choices = choices or {}; out = []
    for i, mount in enumerate(validated["fragment"]["mounts"]):
        candidates = target_candidates(con, pid, mount["target"], context)
        chosen = None; requested = choices.get(str(i), choices.get(i))
        if requested is not None:
            try: requested_id = int(requested)
            except (TypeError, ValueError): _err(f"$.mounts[{i}].target", "The selected destination is invalid.", "FRAGMENT_JSON_010")
            chosen = next((c for c in candidates if int(c["id"]) == requested_id), None)
            if chosen is None: _err(f"$.mounts[{i}].target", "The selected destination is no longer available.", "FRAGMENT_JSON_010")
        elif len(candidates) == 1 and candidates[0].get("_target_match", True): chosen = candidates[0]
        out.append({"index": i, "type": mount["kind"], "resolved_id": int(chosen["id"]) if chosen else None, "resolved_label": chosen.get("label") if chosen else None, "candidates": [{"id": int(c["id"]), "label": c.get("label") or c["name"]} for c in candidates], "needs_choice": chosen is None})
    return out


def check_marker_conflicts(con, validated: dict) -> None:
    pid = int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()[0])
    existing = [dict(r) for r in con.execute("SELECT id,name,color FROM markup_labels WHERE project_id=?", (pid,)).fetchall()]
    by_name = {r["name"].strip().lower(): r for r in existing}; by_color = {r["color"].strip().lower(): r for r in existing}
    for i, marker in enumerate(validated["fragment"]["markers"]):
        n = marker["name"].strip().lower(); c = marker["color"].strip().lower(); same_name = by_name.get(n); same_color = by_color.get(c)
        if same_name and same_name["color"].strip().lower() != c:
            _err(f"$.markers[{i}]", f"Marker {marker['name']!r} already exists with a different color.", "FRAGMENT_JSON_011")
        if same_color and same_color["name"].strip().lower() != n:
            _err(f"$.markers[{i}]", f"Color {marker['color']} is already used by Marker {same_color['name']!r}.", "FRAGMENT_JSON_012")


def check_destination_conflicts(con, validated: dict, resolved_targets: list[dict] | None = None) -> None:
    """Keep additive JSON aligned with the normal Map naming rules.

    Maps and Lines are name-addressable in Fragment targets. Letting an import create
    case-insensitive duplicates would immediately make later selectors ambiguous, even
    though the regular Breadmaker UI avoids those collisions.
    """
    pid = int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()[0])
    existing_maps={str(r["name"] or "").strip().lower() for r in con.execute("SELECT name FROM story_maps WHERE project_id=?",(pid,)).fetchall()}
    for i,m in enumerate(validated["fragment"]["maps"]):
        if m["name"].strip().lower() in existing_maps:
            _err(f"$.maps[{i}].name",f"A Map named {m['name']!r} already exists. Rename the new Map or mount Lines into the existing one.","FRAGMENT_JSON_014")

    target_by_index={int(x["index"]):x for x in (resolved_targets or [])}
    for i,mount in enumerate(validated["fragment"]["mounts"]):
        if mount["kind"]!="map" or not mount.get("lines"):continue
        target=target_by_index.get(i)
        if not target or target.get("resolved_id") is None:continue
        map_id=int(target["resolved_id"])
        existing={str(r["name"] or "").strip().lower() for r in con.execute("SELECT name FROM map_lines WHERE map_id=?",(map_id,)).fetchall()}
        for li,line in enumerate(mount["lines"]):
            key=line["name"].strip().lower()
            if key in existing:
                _err(f"$.mounts[{i}].lines[{li}].name",f"A Map Line named {line['name']!r} already exists in the selected Map.","FRAGMENT_JSON_015")
            existing.add(key)


def _insert_blocks(con, pid: int, tab_id: int, chapter_id: int, content: str, start: int, scene_id: int | None = None) -> int:
    t = storage_values.now(); pos = start
    for frag, text in project_json_markup._content_lines(content):
        bid = storage_values.uid("b_"); con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,'paragraph','body',?,?,?,?,?)", (bid,pid,tab_id,chapter_id,scene_id,pos,frag,text,storage_values.sha(frag+'\0'+text+'\0'),t,t)); pos += 1
    return pos


def import_into_active_database(con, validated: dict, resolved_targets: list[dict]) -> dict:
    frag = validated["fragment"]; project = con.execute("SELECT * FROM projects ORDER BY id LIMIT 1").fetchone()
    if not project: _err("$", "No active project is available.", "FRAGMENT_JSON_020")
    pid = int(project["id"]); t = storage_values.now(); refs: dict[str, tuple[str, Any]] = {}; link_queue: list[tuple[str,dict]] = []
    target_map = {int(x["index"]): int(x["resolved_id"]) for x in resolved_targets if x.get("resolved_id") is not None}
    if len(target_map) != len(frag["mounts"]): _err("$.mounts", "Choose a destination for every fragment mount before importing.", "FRAGMENT_JSON_021")
    def bind(public_id, kind, internal): refs[public_id] = (kind, internal)
    def resolve(ref):
        canonical = next((k for k in refs if k.lower() == ref.lower()), ref)
        if canonical not in refs: _err("$", f"Internal fragment import could not resolve {ref!r}.", "FRAGMENT_JSON_022")
        return refs[canonical]
    def marker_id(ref):
        if not ref: return None
        return int(resolve(ref)[1])
    def queue_links(public_id, links):
        for link in links: link_queue.append((public_id, link))

    # Markers are idempotent when both name and exact color match.
    existing_markers = [dict(r) for r in con.execute("SELECT * FROM markup_labels WHERE project_id=?", (pid,)).fetchall()]
    for m in frag["markers"]:
        found = next((r for r in existing_markers if r["name"].strip().lower()==m["name"].strip().lower() and r["color"].strip().lower()==m["color"].strip().lower()), None)
        if found: mid = int(found["id"])
        else:
            mid = con.execute("INSERT INTO markup_labels(project_id,name,description,color,created_at,updated_at) VALUES(?,?,?,?,?,?)", (pid,m["name"],m["description"],m["color"],t,t)).lastrowid
            existing_markers.append({"id":mid,"name":m["name"],"color":m["color"]})
        bind(m["id"], "marker", mid)

    scene_pos = defaultdict(int)
    for row in con.execute("SELECT tab_id,COALESCE(MAX(position),-1)+1 next_pos FROM scenes WHERE project_id=? GROUP BY tab_id", (pid,)).fetchall(): scene_pos[int(row["tab_id"])] = int(row["next_pos"])

    def add_scene(tab_id: int, chapter_id: int, sc: dict, block_pos: int) -> int:
        spos = scene_pos[tab_id]; scene_pos[tab_id] += 1
        sid = con.execute("INSERT INTO scenes(project_id,tab_id,position,title,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (pid,tab_id,spos,sc["title"],sc["description"],t,t)).lastrowid
        bind(sc["id"], "scene", sid); queue_links(sc["id"], sc["links"])
        return _insert_blocks(con,pid,tab_id,chapter_id,sc["content"],block_pos,sid)

    def add_chapter(tab_id: int, ch: dict, position: int) -> int:
        cid = con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,0,'','',?,?)", (pid,tab_id,position,ch["title"],ch["description"],t,t)).lastrowid
        bind(ch["id"], "chapter", cid); queue_links(ch["id"], ch["links"]); block_pos = 0
        if ch["content"]: block_pos = _insert_blocks(con,pid,tab_id,cid,ch["content"],block_pos)
        for sc in ch["scenes"]: block_pos = add_scene(tab_id,cid,sc,block_pos)
        if block_pos == 0:
            bid=storage_values.uid("b_"); con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,0,'paragraph','body','','','',?,?)", (bid,pid,tab_id,cid,t,t))
        return cid

    # New Tabs.
    next_tab = int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM document_tabs WHERE project_id=?", (pid,)).fetchone()[0])
    for tab in frag["manuscript"]["tabs"]:
        did = con.execute("INSERT INTO document_tabs(project_id,position,title,description,is_manuscript,created_at,updated_at) VALUES(?,?,?,?,0,?,?)", (pid,next_tab,tab["name"],tab["description"],t,t)).lastrowid; next_tab += 1
        bind(tab["id"],"document",did); queue_links(tab["id"],tab["links"])
        if tab.get("content") or not tab["chapters"]:
            # Match Tabs created through the normal UI: an empty secondary Tab is
            # immediately writable even though its implicit Chapter stays private.
            implicit=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,1,'','',?,?)",(pid,did,-1,tab["name"],"",t,t)).lastrowid
            _insert_blocks(con,pid,did,implicit,tab.get("content") or "",0)
        for pos,ch in enumerate(tab["chapters"]): add_chapter(did,ch,pos)

    def add_canvas_tree(canvases: list[dict], default_parent: int | None = None):
        pending = list(canvases); local_ids = {c["id"] for c in canvases}
        while pending:
            progressed=False
            for c in list(pending):
                parent_ref=c.get("parent"); parent_id=default_parent
                if parent_ref:
                    canonical=next((k for k in refs if k.lower()==parent_ref.lower()),None)
                    if canonical is None:
                        if parent_ref in local_ids: continue
                        _err("$.canvases", f"Canvas parent {parent_ref!r} was not created by this fragment.")
                    parent_id=int(refs[canonical][1])
                pos=int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM canvases WHERE project_id=? AND parent_id IS ?",(pid,parent_id)).fetchone()[0])
                cid=con.execute("INSERT INTO canvases(project_id,parent_id,position,name,description,canvas_type,developmental_scope,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(pid,parent_id,pos,c["name"],c["description"],"developmental" if c["type"]=="developmental" else "blank",c["developmental_scope"] if c["type"]=="developmental" else "chapters",t,t)).lastrowid
                bind(c["id"],"canvas",cid);queue_links(c["id"],c["links"]);pending.remove(c);progressed=True
            if not progressed:_err("$.canvases","Canvas hierarchy could not be resolved.")
        for c in canvases: add_canvas_content(int(resolve(c["id"])[1]),c["nodes"],c["connectors"],c.get("layouts") or [],shift_auto=False)

    def add_canvas_content(canvas_id:int,nodes:list[dict],connectors:list[dict],layouts:list[dict],shift_auto:bool=True):
        if not nodes and not connectors:return
        pseudo={"nodes":nodes,"connectors":connectors,"layouts":layouts}
        positions=project_json_layout._layout_positions(pseudo)
        manual=bool(nodes) and all(n["position"].get("x") is not None and n["position"].get("y") is not None for n in nodes) and (not layouts or all(l.get("mode")=="manual" for l in layouts))
        shift_y=0.0
        if shift_auto and not manual and positions:
            row=con.execute("SELECT MAX(y+h) bottom FROM canvas_nodes WHERE canvas_id=?",(canvas_id,)).fetchone(); bottom=float(row["bottom"] or 60); min_y=min(p["y"] for p in positions.values());shift_y=max(0.0,bottom+40-min_y)
        for n in nodes:
            pos=positions[n["id"]];typ=n["type"];label_id=marker_id(n.get("marker"));color=n.get("color") or ""
            if label_id: color=con.execute("SELECT color FROM markup_labels WHERE id=?",(label_id,)).fetchone()[0]
            chapter_id=scene_id=None
            if n.get("linked_to"):
                kind,internal=resolve(n["linked_to"]);chapter_id=int(internal) if kind=="chapter" else None;scene_id=int(internal) if kind=="scene" else None
            if typ=="card": node_type="free";shape="text" if n["shape"]=="text" else "rect";title=n["title"];body=n["body"];display=n["display"];text_align=n.get("text_align") or "left";text_color=n.get("text_color") or "";figure_fill=figure_stroke="";figure_width=1.0;figure_layer="back";line_x1=.02;line_y1=.5;line_x2=.98;line_y2=.5
            elif typ=="figure": node_type="figure";shape=n["shape"];title=body="";display="full";text_align="left";color=text_color="";label_id=None;figure_fill=n.get("fill_color") or "";figure_stroke=n.get("stroke_color") or "";figure_width=float(n.get("border_width") or 1.0);figure_layer=n.get("layer") or "back";line_x1=float(n.get("line_x1",.02));line_y1=float(n.get("line_y1",.5));line_x2=float(n.get("line_x2",.98));line_y2=float(n.get("line_y2",.5))
            elif typ=="index": node_type="label";shape="label";title=n["value"];body="";display="full";text_align="left";color=text_color=figure_fill=figure_stroke="";figure_width=1.0;figure_layer="back";line_x1=.02;line_y1=.5;line_x2=.98;line_y2=.5;label_id=None
            else: node_type="routing";shape="routing";title=body="";display="full";text_align="left";color=text_color=figure_fill=figure_stroke="";figure_width=1.0;figure_layer="back";line_x1=.02;line_y1=.5;line_x2=.98;line_y2=.5;label_id=None
            nid=con.execute("""INSERT INTO canvas_nodes(canvas_id,node_type,shape,icon,x,y,w,h,title,body,display_mode,text_align,color,text_color,figure_fill_color,figure_stroke_color,figure_border_width,figure_layer,figure_line_x1,figure_line_y1,figure_line_x2,figure_line_y2,label_id,chapter_id,scene_id,image_path,icon_marker_id,created_at,updated_at)
                VALUES(?,?,?,'',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, '',NULL,?,?)""",(canvas_id,node_type,shape,pos["x"],pos["y"]+shift_y,pos["w"],pos["h"],title,body,display,text_align,color,text_color,figure_fill,figure_stroke,figure_width,figure_layer,line_x1,line_y1,line_x2,line_y2,label_id,chapter_id,scene_id,t,t)).lastrowid
            bind(n["id"],"node",nid);queue_links(n["id"],n["links"])
        for e in connectors:
            _,from_id=resolve(e["from"]);_,to_id=resolve(e["to"]);mid=marker_id(e.get("marker"));color=e.get("color") or ""
            if mid:color=con.execute("SELECT color FROM markup_labels WHERE id=?",(mid,)).fetchone()[0]
            con.execute("INSERT INTO canvas_edges(canvas_id,from_node_id,to_node_id,from_port,to_port,label,color,marker_id,line_style,end_style,line_width,route_style,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(canvas_id,int(from_id),int(to_id),e["from_port"],e["to_port"],e["label"],color,mid,e["stroke"],e["end"],e["width"],e["route"],t))

    add_canvas_tree(frag["canvases"])

    def add_map_lines(map_id:int,lines:list[dict],junctions:list[dict]):
        next_line=int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM map_lines WHERE map_id=?",(map_id,)).fetchone()[0])
        for l in lines:
            lid=con.execute("INSERT INTO map_lines(map_id,position,name,description,color,visible,provenance,layout_locked,lane_offset,planned_start,planned_end,schematic_start,schematic_end,created_at,updated_at) VALUES(?,?,?,?,?,?,'manual',0,0,0.0,1.0,0.0,1.0,?,?)",(map_id,next_line,l["name"],l["description"],l["color"],1 if l["visible"] else 0,t,t)).lastrowid;next_line+=1
            bind(l["id"],"map_line",lid);queue_links(l["id"],l["links"]);add_map_items(lid,l["items"])
        for j in junctions:
            fl=int(resolve(j["from"]["line"])[1]);tl=int(resolve(j["to"]["line"])[1]);fi=int(resolve(j["from"]["item"])[1]) if j["from"].get("item") else None;ti=int(resolve(j["to"]["item"])[1]) if j["to"].get("item") else None
            con.execute("INSERT INTO map_junctions(map_id,from_line_id,from_item_id,from_edge,to_line_id,to_item_id,to_edge,kind,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(map_id,fl,fi,j["from"]["edge"],tl,ti,j["to"]["edge"],j["kind"],t))

    def add_map_items(line_id:int,items:list[dict]):
        next_pos=int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM map_items WHERE line_id=?",(line_id,)).fetchone()[0]); existing_max=float(con.execute("SELECT COALESCE(MAX(schematic_start),0) FROM map_items WHERE line_id=?",(line_id,)).fetchone()[0] or 0)
        count=max(1,len(items))
        for idx,it in enumerate(items):
            auto=min(.96, existing_max + (idx+1)*max(.04,(1-existing_max)/(count+1))) if next_pos else (.5 if count<=1 else .05+.90*idx/(count-1))
            anchor_type=anchor_id=None
            if it.get("anchor"): kind,ident=resolve(it["anchor"]);anchor_type=kind;anchor_id=str(ident)
            end_type=end_id=None
            if it.get("end_anchor"):kind,ident=resolve(it["end_anchor"]);end_type=kind;end_id=str(ident)
            start=it.get("start");end=it.get("end") if it["type"]=="span" else None;schematic_start=float(start if start is not None else auto);schematic_end=float(end) if end is not None else (min(.98,schematic_start+.08) if it["type"]=="span" else None);planned_start=float(start) if start is not None else (None if anchor_type else schematic_start);planned_end=float(end) if end is not None else (None if end_type else schematic_end)
            iid=con.execute("""INSERT INTO map_items(line_id,position,item_type,title,short_label,description,planned_start,planned_end,schematic_start,schematic_end,start_anchor_type,start_anchor_id,start_offset,end_anchor_type,end_anchor_id,end_offset,excerpt,label_offset,label_pinned,break_after,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,0,?,0,0,?,?,?)""",(line_id,next_pos,it["type"],it["title"],it["short_label"],it["description"],planned_start,planned_end,schematic_start,schematic_end,anchor_type,anchor_id,end_type,end_id,it["excerpt"],1 if it["break_after"] else 0,t,t)).lastrowid;next_pos+=1
            bind(it["id"],"map_item",iid);queue_links(it["id"],it["links"])

    next_map=int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM story_maps WHERE project_id=?",(pid,)).fetchone()[0])
    for m in frag["maps"]:
        mid=con.execute("INSERT INTO story_maps(project_id,position,name,description,orientation,layout_mode,label_mode,ruler,background,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(pid,next_map,m["name"],m["description"],m["orientation"],m["layout_mode"],m["label_mode"],m["ruler"],m["background"],t,t)).lastrowid;next_map+=1
        bind(m["id"],"map",mid);queue_links(m["id"],m["links"]);add_map_lines(mid,m["lines"],m["junctions"])

    for task in frag["tasks"]:
        mid=marker_id(task.get("marker"));archived_from="open" if task["status"]=="archived" else None
        tid=con.execute("INSERT INTO tasks(project_id,title,description,status,archived_from_status,marker_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(pid,task["title"],task["description"],task["status"],archived_from,mid,t,t)).lastrowid;bind(task["id"],"task",tid);queue_links(task["id"],task["links"])
        for si,sub in enumerate(task["subtasks"]):
            sid=con.execute("INSERT INTO task_milestones(project_id,task_id,position,title,description,completed,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(pid,tid,si,sub["title"],sub["description"],1 if sub["completed"] else 0,t,t)).lastrowid;bind(sub["id"],"milestone",sid);queue_links(sub["id"],sub["links"])

    for cut in frag["cuts"]:
        parts=[]
        for frag_html,_ in project_json_markup._content_lines(cut["content"]): parts.append(f"<p>{frag_html}</p>")
        html="".join(parts);source_project=cut["source_project"] or str(project["title"] or "Project")[:240]
        con.execute("INSERT INTO cuts(project_id,title,content_html,source_chapter_id,created_at,note,source_label,source_project,source_chapter,source_scene) VALUES(?,?,?,?,?,?,?,?,?,?)",(pid,cut["title"],html,None,t,cut["note"],cut["source_label"],source_project,cut["source_chapter"],cut["source_scene"]))

    # Mount into existing containers after all top-level fragment entities exist.
    for i,mount in enumerate(frag["mounts"]):
        target_id=target_map[i]
        if mount["kind"]=="tab":
            pos=int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM chapters WHERE project_id=? AND tab_id=? AND is_implicit=0",(pid,target_id)).fetchone()[0])
            for ch in mount.get("chapters",[]): add_chapter(target_id,ch,pos);pos+=1
        elif mount["kind"]=="chapter":
            row=con.execute("SELECT tab_id FROM chapters WHERE id=? AND project_id=? AND is_implicit=0",(target_id,pid)).fetchone();block_pos=int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM manuscript_blocks WHERE chapter_id=?",(target_id,)).fetchone()[0])
            for sc in mount.get("scenes",[]):block_pos=add_scene(int(row["tab_id"]),target_id,sc,block_pos)
        elif mount["kind"]=="canvas":
            add_canvas_tree(mount.get("canvases",[]),default_parent=target_id);add_canvas_content(target_id,mount.get("nodes",[]),mount.get("connectors",[]),mount.get("layouts",[]),shift_auto=True)
        elif mount["kind"]=="map": add_map_lines(target_id,mount.get("lines",[]),mount.get("junctions",[]))
        elif mount["kind"]=="map_line": add_map_items(target_id,mount.get("items",[]))
        else:
            start=int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM task_milestones WHERE task_id=?",(target_id,)).fetchone()[0])
            for sub in mount.get("subtasks",[]):
                sid=con.execute("INSERT INTO task_milestones(project_id,task_id,position,title,description,completed,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(pid,target_id,start,sub["title"],sub["description"],1 if sub["completed"] else 0,t,t)).lastrowid;start+=1;bind(sub["id"],"milestone",sid);queue_links(sub["id"],sub["links"])

    for source_public,link in link_queue:
        skind,sid=resolve(source_public);source_type=project_json_validation._KIND_TO_REFERENCE_TYPE.get(skind)
        if not source_type:continue
        if link.get("url"):target_type="url";target_id=link["url"]
        else:
            tkind,tid=resolve(link["target"]);target_type=project_json_validation._KIND_TO_REFERENCE_TYPE.get(tkind)
            if not target_type:continue
            target_id=str(tid)
        con.execute("INSERT INTO references_map(project_id,source_type,source_id,target_type,target_id,start_offset,end_offset,excerpt,relationship,created_at) VALUES(?,?,?,?,?,0,0,'',?,?)",(pid,source_type,str(sid),target_type,str(target_id),link.get("relationship") or "",t))
    con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid));storage_invariants.validate_project_invariants(con,pid)
    return {"project_id":pid,"refs":refs}
