from __future__ import annotations

"""Checkpoint lifecycle HTTP routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException

from api.checkpoints.contracts import CheckpointCreate, CheckpointUpdate
from backend.data_access import allrows, project
from storage import assets as storage_assets
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import capture, lineage

router = APIRouter()

CHECKPOINT_META_COLUMNS='''id,project_id,scope,name,description,source_tab_id,source_tab_title,source_tab_created_at,
    source_chapter_id,source_chapter_title,source_chapter_created_at,source_chapter_position,
    current_source_chapter_id,current_source_chapter_created_at,snapshot_hash,word_count,include_scenes,include_markup,include_notes,created_at,updated_at'''

@router.get("/api/checkpoints")
def list_checkpoints(q:str='',scope:Optional[str]=None,chapter_id:Optional[int]=None):
    with storage_mutations.tx() as con:
        p=project(con); rows=allrows(con,f"SELECT {CHECKPOINT_META_COLUMNS} FROM checkpoints WHERE project_id=? ORDER BY created_at DESC,id DESC",(p['id'],)); result=[]; needle=(q or '').strip().casefold()
        for row in rows:
            meta=lineage._checkpoint_meta(con,row)
            if scope in {'chapter','book'} and meta['scope']!=scope: continue
            if chapter_id is not None and (meta['scope']!='chapter' or int(meta.get('current_chapter_id') or 0)!=int(chapter_id)): continue
            hay=' '.join([str(meta.get('display_name') or ''),str(meta.get('description') or ''),str(meta.get('source_chapter_title') or ''),str(meta.get('source_tab_title') or '')]).casefold()
            if needle and needle not in hay: continue
            result.append(meta)
        return {'checkpoints':result}

@router.post("/api/checkpoints")
def create_checkpoint(body:CheckpointCreate):
    scope=(body.scope or '').strip().lower()
    if scope not in {'chapter','book'}: raise HTTPException(400,"Checkpoint scope must be chapter or book")
    with storage_mutations.tx() as con:
        p=project(con); opts=dict(include_scenes=body.include_scenes,include_markup=body.include_markup,include_notes=body.include_notes); snap=capture._capture_chapter_checkpoint(con,p['id'],int(body.chapter_id),**opts) if scope=='chapter' and body.chapter_id else (capture._capture_book_checkpoint(con,p['id'],**opts) if scope=='book' else None)
        if snap is None: raise HTTPException(400,"Choose a chapter for this checkpoint")
        cid=capture._insert_checkpoint(con,p['id'],snap,body.name,body.description); row=lineage._checkpoint_row(con,p['id'],cid); return lineage._checkpoint_meta(con,row)

@router.get("/api/checkpoints/{cid}")
def get_checkpoint(cid:int):
    with storage_mutations.tx() as con:
        p=project(con); row=lineage._checkpoint_row(con,p['id'],cid); return {'checkpoint':lineage._checkpoint_meta(con,row),'snapshot':capture._decode_checkpoint_snapshot(con,row)}

@router.patch("/api/checkpoints/{cid}")
def update_checkpoint(cid:int,body:CheckpointUpdate):
    with storage_mutations.tx() as con:
        p=project(con); row=lineage._checkpoint_row(con,p['id'],cid); vals={}
        if body.name is not None: vals['name']=' '.join(body.name.strip().split())[:180]
        if body.description is not None: vals['description']=body.description.strip()[:12000]
        if vals:
            vals['updated_at']=storage_values.now(); con.execute("UPDATE checkpoints SET "+','.join(f"{k}=?" for k in vals)+" WHERE id=?",(*vals.values(),cid))
        return lineage._checkpoint_meta(con,lineage._checkpoint_row(con,p['id'],cid))

@router.delete("/api/checkpoints/{cid}")
def delete_checkpoint(cid:int):
    with storage_mutations.tx() as con:
        p=project(con);lineage._checkpoint_row(con,p['id'],cid);con.execute('DELETE FROM checkpoints WHERE id=?',(cid,));storage_assets.cleanup_checkpoint_assets(con)
    return {'ok':True}
