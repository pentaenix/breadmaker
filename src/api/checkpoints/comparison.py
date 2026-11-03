from __future__ import annotations

"""Checkpoint read-only comparison and current-version projection."""

from fastapi import APIRouter, HTTPException

from backend.data_access import project
from storage import mutations as storage_mutations
from . import capture, lineage

router = APIRouter()

# Preserve the canonical helper imports without loading native routes in hosted mode.
from formats.checkpoint_comparison import (
    _checkpoint_image_width, _checkpoint_image_unit, _checkpoint_units,
    _checkpoint_unit_render, _word_diff_pair, _checkpoint_compare_rows,
)

def _checkpoint_version_snapshot(con,pid:int,ref:str,other:dict|None=None,other_row:dict|None=None) -> tuple[dict,str,bool]:
    if ref=='current':
        if not other: raise HTTPException(400,"Current comparison needs a checkpoint context")
        if other.get('scope')=='chapter':
            current_id=int((lineage._checkpoint_meta(con,other_row).get('current_chapter_id') if other_row else 0) or 0) or lineage._checkpoint_live_chapter_id_for_snapshot(con,pid,other)
            if current_id is None: raise HTTPException(409,"The source chapter no longer exists in the current manuscript")
            return capture._capture_chapter_checkpoint(con,pid,current_id),'Current',True
        return capture._capture_book_checkpoint(con,pid),'Current',True
    try: cid=int(ref)
    except ValueError: raise HTTPException(400,"Invalid comparison version")
    row=lineage._checkpoint_row(con,pid,cid); snap=capture._decode_checkpoint_snapshot(con,row); meta=lineage._checkpoint_meta(con,row)
    label=meta['display_name']
    return snap,label,meta['source_exists']

@router.get("/api/checkpoints/compare/versions")
def compare_checkpoints(left:str,right:str):
    if left=='current' and right=='current': raise HTTPException(400,"Choose at least one checkpoint")
    with storage_mutations.tx() as con:
        p=project(con); left_seed=None; right_seed=None
        try:
            left_row=lineage._checkpoint_row(con,p['id'],int(left)) if left!='current' else None; right_row=lineage._checkpoint_row(con,p['id'],int(right)) if right!='current' else None
        except ValueError:
            raise HTTPException(400,"Invalid comparison version")
        if left_row: left_seed=capture._decode_checkpoint_snapshot(con,left_row)
        if right_row: right_seed=capture._decode_checkpoint_snapshot(con,right_row)
        if left_seed and right_seed:
            if left_seed.get('scope')!=right_seed.get('scope'): raise HTTPException(400,"Compare checkpoints with the same scope")
            if left_seed.get('scope')=='chapter' and not lineage._checkpoint_rows_same_chapter_lineage(left_row,right_row): raise HTTPException(400,"Compare checkpoints from the same chapter lineage")
        l,lname,_=_checkpoint_version_snapshot(con,p['id'],left,right_seed,right_row); r,rname,_=_checkpoint_version_snapshot(con,p['id'],right,left_seed or l,left_row)
        if l.get('scope')!=r.get('scope'): raise HTTPException(400,"Compare checkpoints with the same scope")
        result=_checkpoint_compare_rows(l,r); result.update({'left_name':lname,'right_name':rname,'scope':l.get('scope')}); return result
