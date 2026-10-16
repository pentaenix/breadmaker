from __future__ import annotations

import html
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from bs4 import BeautifulSoup, NavigableString

from storage import invariants as storage_invariants
from storage import values as storage_values
FORMAT = "breadmaker-project"
SCHEMA_VERSION = "1.0"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ENTITIES = 25_000
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
REF_RE = re.compile(r"^[A-Za-z0-9_.-]+:[A-Za-z0-9_.:-]+$")
EDGE_PORTS = {"top","right","bottom","left","top_left","top_right","bottom_left","bottom_right","center"}
EDGE_STROKES = {"solid","dashed","dotted"}
EDGE_ENDS = {"arrow","none","circle","diamond"}
EDGE_ROUTES = {"curved","straight"}
LAYOUTS = {"compact","horizontal","vertical","circular","manual"}
TASK_STATUSES = {"open","later","done","archived"}
MAP_ORIENTATIONS = {"horizontal","vertical"}
MAP_LAYOUTS = {"schematic","scaled"}
MAP_LABELS = {"minimal","full","hidden"}
MAP_RULERS = {"none","chapters","scenes","percent","pages"}
MAP_ITEM_TYPES = {"stop","span"}
MAP_JUNCTION_TYPES = {"branch","merge"}
DEFAULT_MAP_COLORS = ["#6b7f73", "#7b6f8c", "#8b7561", "#637b8a", "#8a6868", "#667a58"]
_KIND_TO_REFERENCE_TYPE={"document":"document","chapter":"chapter","scene":"scene","canvas":"canvas","node":"node","map":"map","map_line":"map_line","map_item":"map_item","task":"task","milestone":"milestone"}


class ProjectJSONError(ValueError):
    def __init__(self, message: str, path: str = "$", code: str = "PROJECT_JSON_001"):
        super().__init__(message)
        self.message = message
        self.path = path
        self.code = code

    def detail(self) -> str:
        return f"{self.code}: {self.path}: {self.message}"


def _err(path: str, message: str, code: str = "PROJECT_JSON_001"):
    raise ProjectJSONError(message, path, code)


def _dict(value: Any, path: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        _err(path, "Expected an object.")
    return value


def _list(value: Any, path: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        _err(path, "Expected an array.")
    return value


def _text(value: Any, path: str, *, required: bool = False, limit: int = 12000, default: str = "") -> str:
    if value is None:
        value = default
    if not isinstance(value, str):
        _err(path, "Expected text.")
    value = value.strip() if required else value
    if required and not value:
        _err(path, "Value cannot be empty.")
    if len(value) > limit:
        _err(path, f"Text is longer than {limit:,} characters.")
    return value


def _bool(value: Any, path: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        _err(path, "Expected true or false.")
    return value


def _number(value: Any, path: str, default: float | None = None, *, low: float | None = None, high: float | None = None) -> float | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _err(path, "Expected a finite number.")
    out = float(value)
    if low is not None and out < low:
        _err(path, f"Value must be at least {low}.")
    if high is not None and out > high:
        _err(path, f"Value must be at most {high}.")
    return out


def _enum(value: Any, path: str, choices: set[str], default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        _err(path, "Expected text.")
    clean = value.strip().lower()
    if clean not in choices:
        _err(path, f"Expected one of: {', '.join(sorted(choices))}.")
    return clean


def _color(value: Any, path: str, *, allow_empty: bool = True) -> str:
    if value in (None, "") and allow_empty:
        return ""
    if not isinstance(value, str) or not HEX_RE.fullmatch(value.strip()):
        _err(path, "Color must be a six-digit hex value such as #6b7f73.")
    return value.strip().lower()


def _ref(value: Any, path: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    value = _text(value, path, required=required, limit=180)
    if not value and not required:
        return None
    if not REF_RE.fullmatch(value):
        _err(path, "Reference IDs must use a readable namespace such as chapter:opening or card:goal.")
    return value


def _unknown(raw: dict, allowed: set[str], path: str, warnings: list[str]) -> None:
    for key in raw:
        if key not in allowed:
            warnings.append(f"{path}.{key}: unknown field ignored")


def load_bytes(raw: bytes) -> dict:
    if len(raw) > MAX_JSON_BYTES:
        raise ProjectJSONError(f"Project JSON is larger than {MAX_JSON_BYTES // (1024*1024)} MB.", "$", "PROJECT_JSON_002")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ProjectJSONError("Project JSON must be UTF-8.", "$", "PROJECT_JSON_003") from exc
    except json.JSONDecodeError as exc:
        raise ProjectJSONError(f"Invalid JSON near line {exc.lineno}, column {exc.colno}.", "$", "PROJECT_JSON_004") from exc
    if not isinstance(data, dict):
        raise ProjectJSONError("The root JSON value must be an object.")
    return data


def _normalize_links(value: Any, path: str, warnings: list[str]) -> list[dict]:
    out = []
    for i, item in enumerate(_list(value, path)):
        p = f"{path}[{i}]"
        if isinstance(item, str):
            out.append({"target": _ref(item, p), "relationship": ""})
            continue
        raw = _dict(item, p)
        _unknown(raw, {"target", "url", "relationship"}, p, warnings)
        relationship = _text(raw.get("relationship"), f"{p}.relationship", limit=120)
        has_url = raw.get("url") is not None
        has_target = raw.get("target") is not None
        if has_url == has_target:
            _err(p, "A link must contain exactly one of target or url.")
        if has_url:
            url = _text(raw.get("url"), f"{p}.url", required=True, limit=2000)
            if not re.match(r"^https?://", url, re.I):
                _err(f"{p}.url", "Only http:// and https:// links are supported.")
            out.append({"url": url, "relationship": relationship})
        else:
            out.append({"target": _ref(raw.get("target"), f"{p}.target"), "relationship": relationship})
    return out


def _normalize_position(value: Any, path: str, warnings: list[str], *, default_w: float, default_h: float, fixed: bool = False) -> dict:
    raw = _dict(value, path)
    _unknown(raw, {"x", "y", "width", "height"}, path, warnings)
    x = _number(raw.get("x"), f"{path}.x")
    y = _number(raw.get("y"), f"{path}.y")
    w = _number(raw.get("width"), f"{path}.width", default_w, low=12 if fixed else 30, high=5000)
    h = _number(raw.get("height"), f"{path}.height", default_h, low=12 if fixed else 30, high=5000)
    return {"x": x, "y": y, "width": w, "height": h}


def _estimate_card_height(node: dict) -> float:
    if node.get("display") == "header":
        return 52.0
    width = max(120.0, float(node["position"].get("width") or 220))
    chars = max(18, int((width - 28) / 7.4))
    title_lines = max(1, math.ceil(len(node.get("title") or "") / chars)) if node.get("display") != "body" else 0
    body_lines = 0
    if node.get("display") != "header":
        for line in (node.get("body") or "").splitlines() or [""]:
            body_lines += max(1, math.ceil(len(line) / chars))
    return max(72.0, 24 + title_lines * 22 + body_lines * 19 + (10 if title_lines and body_lines else 0))


def validate_document(source: dict) -> dict:
    warnings: list[str] = []
    raw = _dict(source, "$")
    _unknown(raw, {"format","schema_version","name","markers","manuscript","canvases","maps","tasks"}, "$", warnings)
    if raw.get("format") != FORMAT:
        _err("$.format", f"Expected {FORMAT!r}.", "PROJECT_JSON_005")
    if str(raw.get("schema_version") or "") != SCHEMA_VERSION:
        _err("$.schema_version", f"Unsupported schema version. This Breadmaker expects {SCHEMA_VERSION}.", "PROJECT_JSON_006")
    doc: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "name": _text(raw.get("name"), "$.name", required=True, limit=120),
        "markers": [], "manuscript": {"tabs": []}, "canvases": [], "maps": [], "tasks": []
    }
    ids: dict[str, str] = {}
    ids_lower: dict[str, str] = {}
    entity_count = 0

    def register(ident: str, kind: str, path: str):
        nonlocal entity_count
        key = ident.lower()
        if key in ids_lower:
            _err(path, f"Reference ID {ident!r} duplicates {ids_lower[key]!r}.", "PROJECT_JSON_007")
        ids[ident] = kind; ids_lower[key] = ident; entity_count += 1
        if entity_count > MAX_ENTITIES:
            _err(path, f"Project contains more than {MAX_ENTITIES:,} entities.", "PROJECT_JSON_008")

    # Markers are public, reusable named colors. Their names and colors follow the
    # same uniqueness rules as Breadmaker's Marker UI.
    marker_names: set[str] = set(); marker_colors: set[str] = set()
    for i, item in enumerate(_list(raw.get("markers"), "$.markers")):
        p = f"$.markers[{i}]"; m = _dict(item, p)
        _unknown(m, {"id","name","description","color"}, p, warnings)
        ident = _ref(m.get("id"), f"{p}.id"); register(ident, "marker", f"{p}.id")
        name = _text(m.get("name"), f"{p}.name", required=True, limit=140)
        color = _color(m.get("color"), f"{p}.color", allow_empty=False)
        if name.lower() in marker_names: _err(f"{p}.name", "Marker names must be unique.")
        if color.lower() in marker_colors: _err(f"{p}.color", "Marker colors must be unique.")
        marker_names.add(name.lower()); marker_colors.add(color.lower())
        doc["markers"].append({"id": ident, "name": name, "description": _text(m.get("description"), f"{p}.description"), "color": color})

    manuscript = _dict(raw.get("manuscript"), "$.manuscript")
    _unknown(manuscript, {"tabs"}, "$.manuscript", warnings)
    tabs_raw = _list(manuscript.get("tabs"), "$.manuscript.tabs")
    main_count = sum(1 for t in tabs_raw if isinstance(t, dict) and t.get("main") is True)
    if main_count > 1:
        _err("$.manuscript.tabs", "Only one tab can be the Main Manuscript.")
    if not tabs_raw:
        tabs_raw = [{"id":"tab:manuscript","name":"Manuscript","main":True,"chapters":[]}]
    elif main_count == 0:
        existing_ids = {str(t.get("id") or "").lower() for t in tabs_raw if isinstance(t, dict)}
        default_id = "tab:manuscript" if "tab:manuscript" not in existing_ids else "tab:main-manuscript"
        tabs_raw = [{"id":default_id,"name":"Manuscript","main":True,"chapters":[]}, *tabs_raw]
        warnings.append("$.manuscript.tabs: no Main Manuscript was supplied; Breadmaker will create an empty Manuscript tab")

    for ti, item in enumerate(tabs_raw):
        p=f"$.manuscript.tabs[{ti}]"; t=_dict(item,p)
        _unknown(t,{"id","name","description","main","content","chapters","links"},p,warnings)
        tid=_ref(t.get("id"),f"{p}.id");register(tid,"document",f"{p}.id")
        tab={"id":tid,"name":_text(t.get("name"),f"{p}.name",required=True,limit=140),"description":_text(t.get("description"),f"{p}.description"),"main":_bool(t.get("main"),f"{p}.main"),"content":_text(t.get("content"),f"{p}.content",limit=2_000_000),"chapters":[],"links":_normalize_links(t.get("links"),f"{p}.links",warnings)}
        for ci, citem in enumerate(_list(t.get("chapters"),f"{p}.chapters")):
            cp=f"{p}.chapters[{ci}]";c=_dict(citem,cp)
            _unknown(c,{"id","title","description","content","scenes","links"},cp,warnings)
            cid=_ref(c.get("id"),f"{cp}.id");register(cid,"chapter",f"{cp}.id")
            chapter={"id":cid,"title":_text(c.get("title"),f"{cp}.title",required=True,limit=240),"description":_text(c.get("description"),f"{cp}.description"),"content":_text(c.get("content"),f"{cp}.content",limit=2_000_000),"scenes":[],"links":_normalize_links(c.get("links"),f"{cp}.links",warnings)}
            for si, sitem in enumerate(_list(c.get("scenes"),f"{cp}.scenes")):
                sp=f"{cp}.scenes[{si}]";s=_dict(sitem,sp)
                _unknown(s,{"id","title","description","content","links"},sp,warnings)
                sid=_ref(s.get("id"),f"{sp}.id");register(sid,"scene",f"{sp}.id")
                chapter["scenes"].append({"id":sid,"title":_text(s.get("title"),f"{sp}.title",required=True,limit=240),"description":_text(s.get("description"),f"{sp}.description"),"content":_text(s.get("content"),f"{sp}.content",limit=2_000_000),"links":_normalize_links(s.get("links"),f"{sp}.links",warnings)})
            tab["chapters"].append(chapter)
        doc["manuscript"]["tabs"].append(tab)

    # Canvases / nodes / connectors.
    for ci, item in enumerate(_list(raw.get("canvases"),"$.canvases")):
        p=f"$.canvases[{ci}]";c=_dict(item,p)
        _unknown(c,{"id","name","description","type","parent","developmental_scope","structure","nodes","connectors","layout","layouts","links"},p,warnings)
        cid=_ref(c.get("id"),f"{p}.id");register(cid,"canvas",f"{p}.id")
        ctype=_enum(c.get("type"),f"{p}.type",{"normal","developmental"},"normal")
        canvas={"id":cid,"name":_text(c.get("name"),f"{p}.name",required=True,limit=140),"description":_text(c.get("description"),f"{p}.description"),"type":ctype,"parent":_ref(c.get("parent"),f"{p}.parent",required=False),"developmental_scope":_enum(c.get("developmental_scope"),f"{p}.developmental_scope",{"chapters","scenes"},"chapters"),"structure":[],"nodes":[],"connectors":[],"layouts":[],"links":_normalize_links(c.get("links"),f"{p}.links",warnings)}
        for si,sref in enumerate(_list(c.get("structure"),f"{p}.structure")):
            canvas["structure"].append(_ref(sref,f"{p}.structure[{si}]"))
        for ni,nitem in enumerate(_list(c.get("nodes"),f"{p}.nodes")):
            np=f"{p}.nodes[{ni}]";n=_dict(nitem,np)
            _unknown(n,{"id","type","title","body","value","display","text_align","shape","marker","color","text_color","fill_color","stroke_color","border_width","layer","line_x1","line_y1","line_x2","line_y2","linked_to","position","links"},np,warnings)
            nid=_ref(n.get("id"),f"{np}.id");
            ntype=_enum(n.get("type"),f"{np}.type",{"card","figure","index","routing"},"card")
            register(nid,"node",f"{np}.id")
            marker=_ref(n.get("marker"),f"{np}.marker",required=False); color=_color(n.get("color"),f"{np}.color")
            if marker and color: _err(np,"Use either marker or color on a Canvas node, not both.")
            if ntype=="card":
                shape=_enum(n.get("shape"),f"{np}.shape",{"card","text"},"card")
                position=_normalize_position(n.get("position"),f"{np}.position",warnings,default_w=220,default_h=130)
                node={"id":nid,"type":"card","title":_text(n.get("title"),f"{np}.title",limit=12000),"body":_text(n.get("body"),f"{np}.body",limit=50000),"display":_enum(n.get("display"),f"{np}.display",{"full","header","body"},"full"),"text_align":_enum(n.get("text_align"),f"{np}.text_align",{"left","center","right"},"left"),"shape":shape,"marker":marker,"color":color,"text_color":_color(n.get("text_color"),f"{np}.text_color"),"linked_to":_ref(n.get("linked_to"),f"{np}.linked_to",required=False),"position":position,"links":_normalize_links(n.get("links"),f"{np}.links",warnings)}
                if n.get("position") is None or "height" not in _dict(n.get("position"),f"{np}.position"):
                    node["position"]["height"]=_estimate_card_height(node)
            elif ntype=="figure":
                shape=_enum(n.get("shape"),f"{np}.shape",{"rect","circle","triangle","oval","line"},"rect")
                position=_normalize_position(n.get("position"),f"{np}.position",warnings,default_w=220,default_h=130)
                if shape=="circle": position["height"]=position["width"]
                node={"id":nid,"type":"figure","shape":shape,"fill_color":_color(n.get("fill_color"),f"{np}.fill_color"),"stroke_color":_color(n.get("stroke_color"),f"{np}.stroke_color"),"border_width":_number(n.get("border_width"),f"{np}.border_width",1.0,low=.5,high=12),"layer":_enum(n.get("layer"),f"{np}.layer",{"back","front"},"back"),"line_x1":_number(n.get("line_x1"),f"{np}.line_x1",.02,low=0,high=1),"line_y1":_number(n.get("line_y1"),f"{np}.line_y1",.5,low=0,high=1),"line_x2":_number(n.get("line_x2"),f"{np}.line_x2",.98,low=0,high=1),"line_y2":_number(n.get("line_y2"),f"{np}.line_y2",.5,low=0,high=1),"position":position,"links":_normalize_links(n.get("links"),f"{np}.links",warnings)}
                if marker or color: warnings.append(f"{np}: figures use fill_color/stroke_color instead of Canvas markers or Card colors")
            elif ntype=="index":
                value=_text(n.get("value",n.get("title")),f"{np}.value",required=True,limit=16)
                if not (value.isdigit() or len(value)==1): _err(f"{np}.value","Indexes must contain digits or one character.")
                node={"id":nid,"type":"index","value":value,"position":_normalize_position(n.get("position"),f"{np}.position",warnings,default_w=44,default_h=44),"links":_normalize_links(n.get("links"),f"{np}.links",warnings)}
            else:
                node={"id":nid,"type":"routing","position":_normalize_position(n.get("position"),f"{np}.position",warnings,default_w=12,default_h=12,fixed=True),"links":_normalize_links(n.get("links"),f"{np}.links",warnings)}
                node["position"]["width"]=12;node["position"]["height"]=12
            canvas["nodes"].append(node)
        for ei,eitem in enumerate(_list(c.get("connectors"),f"{p}.connectors")):
            ep=f"{p}.connectors[{ei}]";e=_dict(eitem,ep)
            _unknown(e,{"from","to","from_port","to_port","label","marker","color","stroke","end","width","route"},ep,warnings)
            marker=_ref(e.get("marker"),f"{ep}.marker",required=False); color=_color(e.get("color"),f"{ep}.color")
            if marker and color:_err(ep,"Use either marker or color on a connector, not both.")
            canvas["connectors"].append({"from":_ref(e.get("from"),f"{ep}.from"),"to":_ref(e.get("to"),f"{ep}.to"),"from_port":_enum(e.get("from_port"),f"{ep}.from_port",EDGE_PORTS,"right"),"to_port":_enum(e.get("to_port"),f"{ep}.to_port",EDGE_PORTS,"left"),"label":_text(e.get("label"),f"{ep}.label",limit=180),"marker":marker,"color":color,"stroke":_enum(e.get("stroke"),f"{ep}.stroke",EDGE_STROKES,"solid"),"end":_enum(e.get("end"),f"{ep}.end",EDGE_ENDS,"arrow"),"width":_number(e.get("width"),f"{ep}.width",1.5,low=.5,high=8),"route":_enum(e.get("route"),f"{ep}.route",EDGE_ROUTES,"straight")})
        if c.get("layout") is not None:
            canvas["layouts"].append({"mode":_enum(c.get("layout"),f"{p}.layout",LAYOUTS,"compact"),"nodes":[],"origin":{"x":100.0,"y":100.0},"gap":32.0})
        for li,litem in enumerate(_list(c.get("layouts"),f"{p}.layouts")):
            lp=f"{p}.layouts[{li}]";l=_dict(litem,lp)
            _unknown(l,{"mode","nodes","origin","gap"},lp,warnings)
            origin=_dict(l.get("origin"),f"{lp}.origin");_unknown(origin,{"x","y"},f"{lp}.origin",warnings)
            canvas["layouts"].append({"mode":_enum(l.get("mode"),f"{lp}.mode",LAYOUTS,"compact"),"nodes":[_ref(x,f"{lp}.nodes[{j}]") for j,x in enumerate(_list(l.get("nodes"),f"{lp}.nodes"))],"origin":{"x":_number(origin.get("x"),f"{lp}.origin.x",100.0),"y":_number(origin.get("y"),f"{lp}.origin.y",100.0)},"gap":_number(l.get("gap"),f"{lp}.gap",32.0,low=0,high=500)})
        doc["canvases"].append(canvas)

    # Maps. Names are kept unique case-insensitively because Fragment JSON uses
    # human-readable Map/Line selectors and ambiguous generated names are avoidable.
    map_names:set[str]=set()
    for mi,item in enumerate(_list(raw.get("maps"),"$.maps")):
        p=f"$.maps[{mi}]";m=_dict(item,p)
        _unknown(m,{"id","name","description","orientation","layout_mode","label_mode","ruler","background","lines","junctions","links"},p,warnings)
        mid=_ref(m.get("id"),f"{p}.id");register(mid,"map",f"{p}.id")
        map_name=_text(m.get("name"),f"{p}.name",required=True,limit=140)
        if map_name.lower() in map_names:_err(f"{p}.name",f"Map name {map_name!r} is duplicated.")
        map_names.add(map_name.lower())
        mp={"id":mid,"name":map_name,"description":_text(m.get("description"),f"{p}.description"),"orientation":_enum(m.get("orientation"),f"{p}.orientation",MAP_ORIENTATIONS,"horizontal"),"layout_mode":_enum(m.get("layout_mode"),f"{p}.layout_mode",MAP_LAYOUTS,"scaled"),"label_mode":_enum(m.get("label_mode"),f"{p}.label_mode",MAP_LABELS,"minimal"),"ruler":_enum(m.get("ruler"),f"{p}.ruler",MAP_RULERS,"chapters"),"background":_color(m.get("background"),f"{p}.background") or "#f3f3f1","lines":[],"junctions":[],"links":_normalize_links(m.get("links"),f"{p}.links",warnings)}
        line_names:set[str]=set()
        for li,litem in enumerate(_list(m.get("lines"),f"{p}.lines")):
            lp=f"{p}.lines[{li}]";l=_dict(litem,lp)
            _unknown(l,{"id","name","description","color","visible","items","links"},lp,warnings)
            lid=_ref(l.get("id"),f"{lp}.id");register(lid,"map_line",f"{lp}.id")
            line_name=_text(l.get("name"),f"{lp}.name",required=True,limit=140)
            if line_name.lower() in line_names:_err(f"{lp}.name",f"Map Line name {line_name!r} is duplicated in this Map.")
            line_names.add(line_name.lower())
            line={"id":lid,"name":line_name,"description":_text(l.get("description"),f"{lp}.description"),"color":_color(l.get("color"),f"{lp}.color") or DEFAULT_MAP_COLORS[li%len(DEFAULT_MAP_COLORS)],"visible":_bool(l.get("visible"),f"{lp}.visible",True),"items":[],"links":_normalize_links(l.get("links"),f"{lp}.links",warnings)}
            for ii,iitem in enumerate(_list(l.get("items"),f"{lp}.items")):
                ip=f"{lp}.items[{ii}]";it=_dict(iitem,ip)
                _unknown(it,{"id","type","title","short_label","description","start","end","anchor","end_anchor","excerpt","break_after","links"},ip,warnings)
                iid=_ref(it.get("id"),f"{ip}.id");register(iid,"map_item",f"{ip}.id")
                typ=_enum(it.get("type"),f"{ip}.type",MAP_ITEM_TYPES,"stop")
                start=_number(it.get("start"),f"{ip}.start",None,low=0,high=1)
                end=_number(it.get("end"),f"{ip}.end",None,low=0,high=1)
                end_anchor=_ref(it.get("end_anchor"),f"{ip}.end_anchor",required=False)
                if typ=="stop":
                    if end is not None or end_anchor:
                        warnings.append(f"{ip}: end/end_anchor ignored for a stop")
                    end=None; end_anchor=None
                elif start is not None and end is not None and end < start:
                    # Match the normal Map creation path: a backwards span is treated
                    # as the same range with its endpoints ordered correctly.
                    start,end=end,start
                    warnings.append(f"{ip}: span start/end were reversed to keep the range ordered")
                line["items"].append({"id":iid,"type":typ,"title":_text(it.get("title"),f"{ip}.title",required=True,limit=240),"short_label":_text(it.get("short_label"),f"{ip}.short_label",limit=80),"description":_text(it.get("description"),f"{ip}.description"),"start":start,"end":end,"anchor":_ref(it.get("anchor"),f"{ip}.anchor",required=False),"end_anchor":end_anchor,"excerpt":_text(it.get("excerpt"),f"{ip}.excerpt",limit=2000),"break_after":_bool(it.get("break_after"),f"{ip}.break_after",False),"links":_normalize_links(it.get("links"),f"{ip}.links",warnings)})
            mp["lines"].append(line)
        for ji,jitem in enumerate(_list(m.get("junctions"),f"{p}.junctions")):
            jp=f"{p}.junctions[{ji}]";j=_dict(jitem,jp)
            _unknown(j,{"kind","from","to"},jp,warnings)
            def side(which: str):
                sp=f"{jp}.{which}";s=_dict(j.get(which),sp);_unknown(s,{"line","item","edge"},sp,warnings)
                return {"line":_ref(s.get("line"),f"{sp}.line"),"item":_ref(s.get("item"),f"{sp}.item",required=False),"edge":_enum(s.get("edge"),f"{sp}.edge",{"point","start","end"},"point")}
            mp["junctions"].append({"kind":_enum(j.get("kind"),f"{jp}.kind",MAP_JUNCTION_TYPES,"branch"),"from":side("from"),"to":side("to")})
        doc["maps"].append(mp)

    # Tasks. Breadmaker's existing subtask model is task milestones, so Project JSON
    # calls them subtasks while preserving exactly the editor's current capabilities.
    for ti,item in enumerate(_list(raw.get("tasks"),"$.tasks")):
        p=f"$.tasks[{ti}]";t=_dict(item,p)
        _unknown(t,{"id","title","description","status","marker","subtasks","links"},p,warnings)
        tid=_ref(t.get("id"),f"{p}.id");register(tid,"task",f"{p}.id")
        task={"id":tid,"title":_text(t.get("title"),f"{p}.title",required=True,limit=500),"description":_text(t.get("description"),f"{p}.description"),"status":_enum(t.get("status"),f"{p}.status",TASK_STATUSES,"open"),"marker":_ref(t.get("marker"),f"{p}.marker",required=False),"subtasks":[],"links":_normalize_links(t.get("links"),f"{p}.links",warnings)}
        for si,sitem in enumerate(_list(t.get("subtasks"),f"{p}.subtasks")):
            sp=f"{p}.subtasks[{si}]";s=_dict(sitem,sp)
            _unknown(s,{"id","title","description","completed","links"},sp,warnings)
            sid=_ref(s.get("id"),f"{sp}.id");register(sid,"milestone",f"{sp}.id")
            task["subtasks"].append({"id":sid,"title":_text(s.get("title"),f"{sp}.title",required=True,limit=500),"description":_text(s.get("description"),f"{sp}.description"),"completed":_bool(s.get("completed"),f"{sp}.completed",False),"links":_normalize_links(s.get("links"),f"{sp}.links",warnings)})
        doc["tasks"].append(task)

    # Resolve references only after every entity has been registered. This permits
    # forward references while still failing the entire import before touching disk.
    def require_target(ref: str | None, path: str, kinds: set[str] | None = None):
        if not ref: return
        actual = ids.get(ref)
        if not actual:
            # Accept matching IDs case-insensitively so a hand-edited file is not
            # punished for harmless casing differences; normalize to the canonical ID.
            canonical = ids_lower.get(ref.lower()); actual = ids.get(canonical) if canonical else None
        if not actual: _err(path,f"Reference {ref!r} does not exist.","PROJECT_JSON_009")
        if kinds and actual not in kinds:_err(path,f"Reference {ref!r} points to {actual}, expected {', '.join(sorted(kinds))}.")

    linkable_kinds=set(_KIND_TO_REFERENCE_TYPE)
    def check_links(links:list[dict],path:str):
        for i,link in enumerate(links):
            if link.get("target"): require_target(link["target"],f"{path}[{i}].target",linkable_kinds)

    marker_ids={m["id"] for m in doc["markers"]}
    for ti,tab in enumerate(doc["manuscript"]["tabs"]):
        check_links(tab["links"],f"$.manuscript.tabs[{ti}].links")
        for ci,ch in enumerate(tab["chapters"]):
            check_links(ch["links"],f"$.manuscript.tabs[{ti}].chapters[{ci}].links")
            for si,sc in enumerate(ch["scenes"]):check_links(sc["links"],f"$.manuscript.tabs[{ti}].chapters[{ci}].scenes[{si}].links")
    main_tab=next((tab for tab in doc["manuscript"]["tabs"] if tab.get("main")),None)
    main_chapters={ch["id"] for ch in (main_tab.get("chapters",[]) if main_tab else [])}
    main_scenes={sc["id"] for ch in (main_tab.get("chapters",[]) if main_tab else []) for sc in ch.get("scenes",[])}
    canvas_parent={c["id"]:c.get("parent") for c in doc["canvases"]}
    for ci,c in enumerate(doc["canvases"]):
        p=f"$.canvases[{ci}]";require_target(c.get("parent"),f"{p}.parent",{"canvas"});check_links(c["links"],f"{p}.links")
        # Detect hierarchy cycles.
        cur=c.get("parent");seen={c["id"].lower()}
        while cur:
            low=cur.lower()
            if low in seen:_err(f"{p}.parent","Canvas nesting contains a cycle.")
            seen.add(low);canonical=ids_lower.get(low,cur);cur=canvas_parent.get(canonical)
        if c["type"]=="developmental":
            wanted={"chapter"} if c["developmental_scope"]=="chapters" else {"scene"}
            available=main_chapters if c["developmental_scope"]=="chapters" else main_scenes
            structure_seen:set[str]=set()
            for si,ref in enumerate(c["structure"]):
                require_target(ref,f"{p}.structure[{si}]",wanted)
                canonical=ids_lower.get(ref.lower(),ref)
                if canonical.lower() in structure_seen:
                    _err(f"{p}.structure[{si}]",f"Structure Order contains {canonical!r} more than once.")
                structure_seen.add(canonical.lower())
                if canonical not in available:
                    _err(f"{p}.structure[{si}]","Developmental Structure Order can only reference Chapters/Scenes from the Main Manuscript tab.")
        elif c["structure"]: warnings.append(f"{p}.structure: ignored because this is not a Developmental Canvas")
        local_nodes={n["id"] for n in c["nodes"]}
        for ni,n in enumerate(c["nodes"]):
            np=f"{p}.nodes[{ni}]";check_links(n["links"],f"{np}.links")
            if n.get("marker"):
                require_target(n["marker"],f"{np}.marker",{"marker"})
            if n.get("linked_to"):
                require_target(n["linked_to"],f"{np}.linked_to",{"chapter","scene"})
        layout_seen:set[str]=set()
        for li,l in enumerate(c["layouts"]):
            for j,ref in enumerate(l["nodes"]):
                require_target(ref,f"{p}.layouts[{li}].nodes[{j}]",{"node"})
                canonical=ids_lower.get(ref.lower(),ref)
                if canonical not in local_nodes:_err(f"{p}.layouts[{li}].nodes[{j}]","Layout nodes must belong to this Canvas.")
                if canonical in layout_seen:_err(f"{p}.layouts[{li}].nodes[{j}]","A Canvas node can appear in only one explicit layout group.")
                layout_seen.add(canonical)
        for ei,e in enumerate(c["connectors"]):
            ep=f"{p}.connectors[{ei}]";require_target(e["from"],f"{ep}.from",{"node"});require_target(e["to"],f"{ep}.to",{"node"})
            fr=ids_lower.get(e["from"].lower(),e["from"]);to=ids_lower.get(e["to"].lower(),e["to"])
            if fr not in local_nodes or to not in local_nodes:_err(ep,"Connector endpoints must belong to this Canvas.")
            if fr==to:_err(ep,"A connector cannot connect a node to itself.")
            if e.get("marker"):require_target(e["marker"],f"{ep}.marker",{"marker"})
    for mi,m in enumerate(doc["maps"]):
        p=f"$.maps[{mi}]";check_links(m["links"],f"{p}.links");line_ids={l["id"] for l in m["lines"]};item_ids={i["id"] for l in m["lines"] for i in l["items"]};item_line={i["id"]:l["id"] for l in m["lines"] for i in l["items"]}
        for li,l in enumerate(m["lines"]):
            check_links(l["links"],f"{p}.lines[{li}].links")
            for ii,it in enumerate(l["items"]):
                ip=f"{p}.lines[{li}].items[{ii}]";check_links(it["links"],f"{ip}.links")
                require_target(it.get("anchor"),f"{ip}.anchor",{"chapter","scene"});require_target(it.get("end_anchor"),f"{ip}.end_anchor",{"chapter","scene"})
                if it["type"]=="stop" and (it.get("end") is not None or it.get("end_anchor")):
                    warnings.append(f"{ip}: end/end_anchor ignored for a stop")
        for ji,j in enumerate(m["junctions"]):
            for which in ("from","to"):
                sp=f"{p}.junctions[{ji}].{which}";require_target(j[which]["line"],f"{sp}.line",{"map_line"})
                line_ref=ids_lower.get(j[which]["line"].lower(),j[which]["line"])
                if line_ref not in line_ids:_err(f"{sp}.line","Junction line must belong to this Map.")
                if j[which].get("item"):
                    require_target(j[which]["item"],f"{sp}.item",{"map_item"});item_ref=ids_lower.get(j[which]["item"].lower(),j[which]["item"])
                    if item_ref not in item_ids:_err(f"{sp}.item","Junction item must belong to this Map.")
                    if item_line.get(item_ref) != line_ref:_err(f"{sp}.item","Junction item must belong to the selected Junction line.")
    for ti,t in enumerate(doc["tasks"]):
        p=f"$.tasks[{ti}]";check_links(t["links"],f"{p}.links")
        if t.get("marker"):require_target(t["marker"],f"{p}.marker",{"marker"})
        for si,s in enumerate(t["subtasks"]):check_links(s["links"],f"{p}.subtasks[{si}].links")

    summary={
        "tabs":len(doc["manuscript"]["tabs"]),
        "chapters":sum(len(t["chapters"]) for t in doc["manuscript"]["tabs"]),
        "scenes":sum(len(c["scenes"]) for t in doc["manuscript"]["tabs"] for c in t["chapters"]),
        "markers":len(doc["markers"]),"canvases":len(doc["canvases"]),
        "cards":sum(sum(1 for n in c["nodes"] if n["type"]=="card") for c in doc["canvases"]),
        "canvas_objects":sum(len(c["nodes"]) for c in doc["canvases"]),
        "connectors":sum(len(c["connectors"]) for c in doc["canvases"]),
        "maps":len(doc["maps"]),"map_lines":sum(len(m["lines"]) for m in doc["maps"]),
        "map_items":sum(len(l["items"]) for m in doc["maps"] for l in m["lines"]),
        "tasks":len(doc["tasks"]),"subtasks":sum(len(t["subtasks"]) for t in doc["tasks"]),
    }
    return {"document":doc,"warnings":warnings,"summary":summary,"id_kinds":ids}


