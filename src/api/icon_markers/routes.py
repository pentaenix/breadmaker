from __future__ import annotations

"""Icon Markers API/domain routes.

Extracted mechanically from the pre-10.1 composition module. Route behavior and
function names are intentionally unchanged; dependencies are declared explicitly below; cross-feature calls resolve to their
canonical owner lazily to avoid import cycles.
"""

from fastapi import APIRouter

from backend.dependencies import feature_call
from fastapi import HTTPException
import sqlite3
from storage import mutations as storage_mutations
from storage import values as storage_values
from .contracts import IconMarkerCreate, IconMarkerUpdate
from backend.data_access import (
    allrows,
    one,
    project,
)

# Explicit composition-root dependencies.

router = APIRouter()

# ---------------- Icon markers ----------------
def _icon_marker_values(body):
    vals=body.model_dump(exclude_unset=True)
    if 'name' in vals:
        vals['name']=(vals['name'] or '').strip()[:180]
        if not vals['name']: raise HTTPException(400,"Icon marker name cannot be empty")
    if 'description' in vals: vals['description']=(vals['description'] or '')[:12000]
    if 'icon' in vals:
        vals['icon']=(vals['icon'] or '').strip()[:16]
        if not vals['icon']: raise HTTPException(400,"Icon marker needs an icon")
    return vals

@router.get("/api/icon-markers")
def list_icon_markers():
    with storage_mutations.tx() as con:
        p=project(con)
        return allrows(con,"SELECT * FROM icon_markers WHERE project_id=? ORDER BY name,id",(p['id'],))

@router.post("/api/icon-markers")
def create_icon_marker(body:IconMarkerCreate):
    vals=_icon_marker_values(body)
    with storage_mutations.tx() as con:
        p=project(con); t=storage_values.now()
        try:
            mid=con.execute("INSERT INTO icon_markers(project_id,name,description,icon,created_at,updated_at) VALUES(?,?,?,?,?,?)",(p['id'],vals['name'],vals.get('description',''),vals['icon'],t,t)).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409,"An icon marker with that name already exists")
        return one(con,"SELECT * FROM icon_markers WHERE id=?",(mid,))

@router.patch("/api/icon-markers/{mid}")
def update_icon_marker(mid:int,body:IconMarkerUpdate):
    vals=_icon_marker_values(body)
    with storage_mutations.tx() as con:
        p=project(con); current=one(con,"SELECT * FROM icon_markers WHERE id=? AND project_id=?",(mid,p['id']))
        if not current: raise HTTPException(404,"Icon marker not found")
        if vals:
            if 'icon' in vals:
                con.execute("UPDATE canvas_nodes SET icon=?,updated_at=? WHERE icon_marker_id=?",(vals['icon'],storage_values.now(),mid))
            sets=', '.join(f"{k}=?" for k in vals)+",updated_at=?"
            try: con.execute(f"UPDATE icon_markers SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),mid))
            except sqlite3.IntegrityError: raise HTTPException(409,"An icon marker with that name already exists")
    return {"ok":True}

@router.delete("/api/icon-markers/{mid}")
def delete_icon_marker(mid:int):
    with storage_mutations.tx() as con:
        p=project(con); marker=one(con,"SELECT * FROM icon_markers WHERE id=? AND project_id=?",(mid,p['id']))
        if not marker: raise HTTPException(404,"Icon marker not found")
        usage=con.execute("SELECT COUNT(*) FROM canvas_nodes WHERE icon_marker_id=?",(mid,)).fetchone()[0]
        # FK SET NULL removes only reusable meaning; the displayed icon stays.
        con.execute("DELETE FROM icon_markers WHERE id=?",(mid,))
    return {"ok":True,"usage":usage}

