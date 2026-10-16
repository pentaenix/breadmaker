from __future__ import annotations

"""Shared Story Map primitives and row helpers."""

from backend.data_access import _delete_polymorphic_references, one

MAP_LINE_COLORS=["#f3680e","#0e8ef3","#36a269","#d84c8b","#7557d9","#11a7a2","#c89000","#df4a3f","#6f8f2f","#6978c9"]
MAP_ORIENTATIONS={"horizontal","vertical"}
MAP_LAYOUT_MODES={"schematic","scaled"}
MAP_RULERS={"none","chapters","scenes","percent","pages"}
MAP_LABEL_MODES={"minimal","full","hidden"}
MAP_ITEM_TYPES={"stop","span"}
MAP_ANCHOR_TYPES={"scene","chapter","manuscript_range"}
MAP_JUNCTION_TYPES={"branch","merge"}

def _clamp01(value:Optional[float])->Optional[float]:
    if value is None: return None
    return max(0.0,min(1.0,float(value)))

def _map_row(con,pid:int,mid:int):
    return one(con,"SELECT * FROM story_maps WHERE id=? AND project_id=?",(mid,pid))

def _map_line_row(con,pid:int,lid:int):
    return one(con,"SELECT l.* FROM map_lines l JOIN story_maps m ON m.id=l.map_id WHERE l.id=? AND m.project_id=?",(lid,pid))

def _map_item_row(con,pid:int,iid:int):
    return one(con,"SELECT i.* FROM map_items i JOIN map_lines l ON l.id=i.line_id JOIN story_maps m ON m.id=l.map_id WHERE i.id=? AND m.project_id=?",(iid,pid))

def _delete_map_references(con,pid:int,*,map_ids=(),line_ids=(),item_ids=()):
    _delete_polymorphic_references(con,pid,{'map':map_ids,'map_line':line_ids,'map_item':item_ids})
    if item_ids:
        ids=tuple(int(x) for x in item_ids); q=','.join('?' for _ in ids)
        con.execute(f"DELETE FROM map_junctions WHERE from_item_id IN ({q}) OR to_item_id IN ({q})",(*ids,*ids))

def _next_map_color(con,map_id:int)->str:
    count=int(con.execute("SELECT COUNT(*) FROM map_lines WHERE map_id=?",(map_id,)).fetchone()[0])
    return MAP_LINE_COLORS[count%len(MAP_LINE_COLORS)]

def _unique_map_name(con,pid:int,base:str)->str:
    base=(base or "Story Map").strip()[:120] or "Story Map"; name=base; n=2
    while con.execute("SELECT 1 FROM story_maps WHERE project_id=? AND name=?",(pid,name)).fetchone():
        suffix=f" {n}"; name=(base[:120-len(suffix)]+suffix); n+=1
    return name

def _unique_line_name(con,map_id:int,base:str)->str:
    base=(base or "Story Line").strip()[:120] or "Story Line"; name=base; n=2
    while con.execute("SELECT 1 FROM map_lines WHERE map_id=? AND name=?",(map_id,name)).fetchone():
        suffix=f" {n}"; name=(base[:120-len(suffix)]+suffix); n+=1
    return name
