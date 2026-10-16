from __future__ import annotations
"""Review Marker labels and their attachment to active Review rounds."""
from fastapi import APIRouter, HTTPException
import re
from api.reviews.contracts import ReviewMarkerCreate, ReviewMarkerPopulateIn, ReviewMarkerUpdate
from backend.data_access import allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain as review_domain
from . import questionnaires as review_questionnaires
from . import snapshots as review_snapshots
router=APIRouter()

def _normalize_review_marker_ids(con,project_id:int,marker_ids:list[int]|None) -> list[int]:
    ids=[]
    for raw in marker_ids or []:
        try:value=int(raw)
        except (TypeError,ValueError):raise HTTPException(400,detail='REVIEW_MARKER_001: Review markers contain an unavailable Review Marker.')
        if value>0 and value not in ids:ids.append(value)
    if not ids:return []
    marks=','.join('?' for _ in ids);rows=allrows(con,f"SELECT id FROM review_marker_labels WHERE project_id=? AND id IN ({marks})",(project_id,*ids));found={int(row['id']) for row in rows}
    if found!=set(ids):raise HTTPException(400,detail='REVIEW_MARKER_001: Review markers contain an unavailable Review Marker.')
    return ids

def _replace_review_markers(con,rid:str,marker_ids:list[int],created_at:str|None=None) -> None:
    con.execute('DELETE FROM review_markers WHERE review_id=?',(rid,));t=created_at or storage_values.now()
    con.executemany('INSERT INTO review_markers(review_id,marker_id,position,created_at) VALUES(?,?,?,?)',[(rid,mid,pos,t) for pos,mid in enumerate(marker_ids)])

def _review_marker_name(value:str) -> str:
    name=str(value or '').strip()[:120]
    if not name: raise HTTPException(400,detail='REVIEW_MARKER_002: Review Marker needs a name.')
    return name

def _review_marker_description(value:str) -> str:
    return str(value or '').strip()[:4000]

def _review_marker_color(value:str) -> str:
    color=str(value or '').strip().lower()
    if not re.fullmatch(r'#[0-9a-f]{6}',color): raise HTTPException(400,detail='REVIEW_MARKER_003: Review Marker needs a six-digit hex color.')
    return color

@router.get('/api/review-markers')
def list_review_markers():
    with storage_mutations.tx() as con:
        p=project(con);return allrows(con,"SELECT * FROM review_marker_labels WHERE project_id=? ORDER BY created_at DESC,id DESC",(p['id'],))

@router.post('/api/review-markers')
def create_review_marker(body:ReviewMarkerCreate):
    name=_review_marker_name(body.name);description=_review_marker_description(body.description);color=_review_marker_color(body.color)
    with storage_mutations.tx(operation='review-marker-create') as con:
        p=project(con)
        if one(con,"SELECT id FROM review_marker_labels WHERE project_id=? AND name=? COLLATE NOCASE",(p['id'],name)):raise HTTPException(409,detail='REVIEW_MARKER_004: A Review Marker with that name already exists.')
        t=storage_values.now();mid=con.execute("INSERT INTO review_marker_labels(project_id,name,description,color,created_at,updated_at) VALUES(?,?,?,?,?,?)",(p['id'],name,description,color,t,t)).lastrowid
        return one(con,"SELECT * FROM review_marker_labels WHERE id=?",(mid,))

@router.patch('/api/review-markers/{marker_id}')
def update_review_marker(marker_id:int,body:ReviewMarkerUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'name' in vals:vals['name']=_review_marker_name(vals.get('name') or '')
    if 'description' in vals:vals['description']=_review_marker_description(vals.get('description') or '')
    if 'color' in vals:vals['color']=_review_marker_color(vals.get('color') or '')
    with storage_mutations.tx(operation='review-marker-update') as con:
        p=project(con);current=one(con,"SELECT * FROM review_marker_labels WHERE id=? AND project_id=?",(marker_id,p['id']))
        if not current:raise HTTPException(404,detail='REVIEW_MARKER_005: Review Marker not found.')
        if 'name' in vals and one(con,"SELECT id FROM review_marker_labels WHERE project_id=? AND id<>? AND name=? COLLATE NOCASE",(p['id'],marker_id,vals['name'])):raise HTTPException(409,detail='REVIEW_MARKER_004: A Review Marker with that name already exists.')
        if vals:
            sets=', '.join(f'{key}=?' for key in vals)+',updated_at=?';con.execute(f"UPDATE review_marker_labels SET {sets} WHERE id=? AND project_id=?",(*vals.values(),storage_values.now(),marker_id,p['id']))
        return one(con,"SELECT * FROM review_marker_labels WHERE id=?",(marker_id,))

@router.delete('/api/review-markers/{marker_id}')
def delete_review_marker(marker_id:int):
    with storage_mutations.tx(operation='review-marker-delete') as con:
        p=project(con);current=one(con,"SELECT * FROM review_marker_labels WHERE id=? AND project_id=?",(marker_id,p['id']))
        if not current:raise HTTPException(404,detail='REVIEW_MARKER_005: Review Marker not found.')
        con.execute("DELETE FROM review_marker_labels WHERE id=?",(marker_id,))
        return {'ok':True}

@router.post('/api/review-markers/populate')
def populate_review_markers(body:ReviewMarkerPopulateIn):
    result=[];created=0
    with storage_mutations.tx(operation='review-marker-populate') as con:
        p=project(con);t=storage_values.now()
        for raw in body.markers[:80]:
            name=_review_marker_name(raw.name);description=_review_marker_description(raw.description);color=_review_marker_color(raw.color)
            marker=one(con,"SELECT * FROM review_marker_labels WHERE project_id=? AND name=? COLLATE NOCASE",(p['id'],name))
            if not marker:
                mid=con.execute("INSERT INTO review_marker_labels(project_id,name,description,color,created_at,updated_at) VALUES(?,?,?,?,?,?)",(p['id'],name,description,color,t,t)).lastrowid;marker=one(con,"SELECT * FROM review_marker_labels WHERE id=?",(mid,));created+=1
            result.append(marker)
    return {'markers':result,'count':len(result),'created_count':created}

def _sync_review_marker_into_active_snapshot(con,rid:str,marker_id:int) -> int:
    snapshot=review_snapshots._review_snapshot_summary(con,rid)
    if not snapshot or str(snapshot.get('status') or '') not in {'open','paused'} or not bool(snapshot.get('permission_comment')):return 0
    sid=str(snapshot['id'])
    if one(con,"SELECT id FROM review_snapshot_markers WHERE snapshot_id=? AND source_marker_id=?",(sid,marker_id)):return 0
    marker=one(con,"SELECT id,name,description,color FROM review_marker_labels WHERE id=?",(marker_id,))
    if not marker:return 0
    position=int((one(con,"SELECT COALESCE(MAX(position),-1)+1 position FROM review_snapshot_markers WHERE snapshot_id=?",(sid,)) or {}).get('position') or 0);t=storage_values.now()
    con.execute("INSERT INTO review_snapshot_markers(snapshot_id,source_marker_id,position,name,description,color,created_at) VALUES(?,?,?,?,?,?,?)",(sid,marker_id,position,str(marker.get('name') or ''),str(marker.get('description') or ''),str(marker.get('color') or ''),t))
    review_questionnaires._recompute_review_snapshot_content_hash(con,rid,sid);return 1

def _remove_review_marker_from_active_snapshot(con,rid:str,marker_id:int) -> int:
    snapshot=review_snapshots._review_snapshot_summary(con,rid)
    if not snapshot or str(snapshot.get('status') or '') not in {'open','paused'}:return 0
    sid=str(snapshot['id']);frozen=one(con,"SELECT id,name FROM review_snapshot_markers WHERE snapshot_id=? AND source_marker_id=?",(sid,marker_id))
    if not frozen:return 0
    used=int((one(con,"""SELECT COUNT(*) n FROM review_highlights h JOIN review_anchors a ON a.id=h.anchor_id AND a.review_id=h.review_id WHERE h.review_id=? AND a.snapshot_id=? AND h.tag=? COLLATE NOCASE""",(rid,sid,str(frozen.get('name') or ''))) or {}).get('n') or 0)
    if used:raise HTTPException(409,detail='REVIEW_MARKER_006: This Review Marker is already used by feedback in the active round.')
    con.execute("DELETE FROM review_snapshot_markers WHERE id=?",(frozen['id'],))
    remaining=allrows(con,"SELECT id FROM review_snapshot_markers WHERE snapshot_id=? ORDER BY position,id",(sid,))
    for position,row in enumerate(remaining):con.execute("UPDATE review_snapshot_markers SET position=? WHERE id=?",(position,row['id']))
    review_questionnaires._recompute_review_snapshot_content_hash(con,rid,sid);return 1

@router.post('/api/reviews/{rid}/markers/{marker_id}')
def attach_review_marker(rid:str,marker_id:int):
    with storage_mutations.tx(operation='review-marker-attach') as con:
        p=project(con);review_domain._review_for_current_project(con,p['id'],rid)
        if not one(con,"SELECT id FROM review_marker_labels WHERE id=? AND project_id=?",(marker_id,p['id'])):raise HTTPException(404,detail='REVIEW_MARKER_005: Review Marker not found.')
        if not one(con,"SELECT id FROM review_markers WHERE review_id=? AND marker_id=?",(rid,marker_id)):
            position=int((one(con,"SELECT COALESCE(MAX(position),-1)+1 position FROM review_markers WHERE review_id=?",(rid,)) or {}).get('position') or 0)
            con.execute("INSERT INTO review_markers(review_id,marker_id,position,created_at) VALUES(?,?,?,?)",(rid,marker_id,position,storage_values.now()))
        _sync_review_marker_into_active_snapshot(con,rid,marker_id)
        return {'ok':True,'marker_id':marker_id}

@router.delete('/api/reviews/{rid}/markers/{marker_id}')
def detach_review_marker(rid:str,marker_id:int):
    with storage_mutations.tx(operation='review-marker-detach') as con:
        p=project(con);review_domain._review_for_current_project(con,p['id'],rid);_remove_review_marker_from_active_snapshot(con,rid,marker_id)
        con.execute("DELETE FROM review_markers WHERE review_id=? AND marker_id=?",(rid,marker_id))
        rows=allrows(con,"SELECT id FROM review_markers WHERE review_id=? ORDER BY position,id",(rid,))
        for position,row in enumerate(rows):con.execute("UPDATE review_markers SET position=? WHERE id=?",(position,row['id']))
        return {'ok':True,'marker_id':marker_id}
