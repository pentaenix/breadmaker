from __future__ import annotations

"""Manuscript image-block layout and image mutation routes."""

from fastapi import APIRouter, HTTPException

from backend.dependencies import feature_call
from api.manuscript.contracts import ManuscriptImageInsert, ManuscriptImageMove
from backend.content import sanitize_inline_html
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from backend.environment import runtime
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import values as storage_values

_detach_map_anchors_for_removed_manuscript = feature_call('maps.anchors', '_detach_map_anchors_for_removed_manuscript')
_maybe_external_backup = feature_call('durability.backups', '_maybe_external_backup')
_move_split_anchors = feature_call('durability.cuts', '_move_split_anchors')
_split_inline_html = feature_call('durability.cuts', '_split_inline_html')

router = APIRouter()

def _require_manuscript_asset(api_path:str) -> None:
    try:
        if not runtime.assets.exists(api_path):raise FileNotFoundError(api_path)
    except ValueError:raise HTTPException(400,'Invalid project image path')
    except FileNotFoundError:raise HTTPException(404,'Image asset not found')

def _rehash_image_block(con, block_id:str) -> None:
    row=one(con,"SELECT content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id FROM manuscript_blocks WHERE id=?",(block_id,))
    if not row:return
    digest=storage_values.block_content_hash(str(row.get('content_html') or ''),str(row.get('text_content') or ''),str(row.get('image_path') or ''),int(row.get('image_width') or 100),str(row.get('image_footer') or ''),str(row.get('image_description') or ''),str(row.get('image_row_id') or ''))
    con.execute("UPDATE manuscript_blocks SET content_hash=? WHERE id=?",(digest,block_id))


def _image_row_members(con,row_id:str) -> list[dict]:
    row_id=str(row_id or '').strip()
    if not row_id:return []
    return allrows(con,"SELECT * FROM manuscript_blocks WHERE image_row_id=? ORDER BY chapter_id,position,id",(row_id,))


def _set_image_row(con,block_ids:list[str],row_id:str,width:int,t:str) -> None:
    width=max(5,min(100,int(width or 100))); row_id=str(row_id or '').strip()[:80]
    for bid in block_ids:
        con.execute("UPDATE manuscript_blocks SET image_row_id=?,image_width=?,updated_at=? WHERE id=?",(row_id,width,t,bid));_rehash_image_block(con,bid)


def _detach_image_from_row(con,block:dict,t:str) -> None:
    row_id=str(block.get('image_row_id') or '').strip()
    if not row_id:return
    bid=str(block['id']);con.execute("UPDATE manuscript_blocks SET image_row_id='',updated_at=? WHERE id=?",(t,bid));_rehash_image_block(con,bid)
    remaining=[row for row in _image_row_members(con,row_id) if str(row['id'])!=bid]
    if len(remaining)==1:
        lone=str(remaining[0]['id']);con.execute("UPDATE manuscript_blocks SET image_row_id='',updated_at=? WHERE id=?",(t,lone));_rehash_image_block(con,lone)


def _normalize_image_rows(con,chapter_id:int,t:str) -> None:
    rows=allrows(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(chapter_id,));by={}
    for index,row in enumerate(rows):
        rid=str(row.get('image_row_id') or '').strip()
        if rid:by.setdefault(rid,[]).append((index,row))
    for rid,members in by.items():
        indexes=[item[0] for item in members];blocks=[item[1] for item in members]
        valid=(2<=len(blocks)<=4 and all(row.get('block_type')=='image' for row in blocks) and len({row.get('scene_id') for row in blocks})==1 and indexes==list(range(indexes[0],indexes[0]+len(indexes))))
        if not valid:
            for row in blocks:
                con.execute("UPDATE manuscript_blocks SET image_row_id='',updated_at=? WHERE id=?",(t,row['id']));_rehash_image_block(con,str(row['id']))
            continue
        width=max(5,min(100,int(blocks[0].get('image_width') or 100)))
        for row in blocks:
            if int(row.get('image_width') or 100)!=width:
                con.execute("UPDATE manuscript_blocks SET image_width=?,updated_at=? WHERE id=?",(width,t,row['id']));_rehash_image_block(con,str(row['id']))


def _reorder_image_block(con,block_id:str,source_cid:int,target:dict,placement:str,t:str) -> tuple[int,int]:
    target_cid=int(target['chapter_id'])
    source_ids=[str(r['id']) for r in allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(source_cid,)) if str(r['id'])!=block_id]
    source=one(con,"SELECT * FROM manuscript_blocks WHERE id=?",(block_id,))
    if source_cid!=target_cid and not source_ids:
        blank_id=storage_values.uid('b_');con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(blank_id,source['project_id'],source['tab_id'],source_cid,None,0,'paragraph','body','','','',storage_values.block_content_hash('','',''),t,t));source_ids=[blank_id]
    target_ids=source_ids if source_cid==target_cid else [str(r['id']) for r in allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(target_cid,))]
    target_id=str(target['id']);idx=target_ids.index(target_id) if target_id in target_ids else len(target_ids)
    if placement in {'after','split','row_after'}:idx+=1
    target_ids.insert(idx,block_id)
    con.execute("UPDATE manuscript_blocks SET chapter_id=?,tab_id=?,scene_id=?,updated_at=? WHERE id=?",(target_cid,int(target['tab_id']),target.get('scene_id'),t,block_id))
    if source_cid!=target_cid:
        for i,bid in enumerate(source_ids):con.execute("UPDATE manuscript_blocks SET position=? WHERE id=?",(i,bid))
    for i,bid in enumerate(target_ids):con.execute("UPDATE manuscript_blocks SET position=? WHERE id=?",(i,bid))
    return source_cid,target_cid


@router.post('/api/manuscript/images/insert')
def insert_manuscript_image(body:ManuscriptImageInsert):
    _require_manuscript_asset(body.image_path)
    placement=str(body.placement or 'at_offset')
    if placement not in {'at_offset','before','after','row_before','row_after'}: raise HTTPException(400,'Invalid image placement')
    with storage_mutations.tx() as con:
        p=project(con);target=one(con,"""SELECT b.*,c.tab_id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.id=? AND b.project_id=?""",(body.target_block_id,p['id']))
        if not target:raise HTTPException(404,'Place the cursor in the manuscript before adding an image')
        if placement.startswith('row_') and target.get('block_type')!='image':placement='before' if placement=='row_before' else 'after'
        cid=int(target['chapter_id']);tab_id=int(target['tab_id']);t=storage_values.now();pos=float(target['position']);scene_id=target.get('scene_id');first_pos=pos
        row_id='';row_width=max(5,min(100,int(body.image_width or 100)))
        if placement.startswith('row_'):
            members=_image_row_members(con,str(target.get('image_row_id') or '')) if target.get('image_row_id') else [target]
            if len(members)>=4:raise HTTPException(409,'This image row already has four images')
            row_id=str(target.get('image_row_id') or storage_values.uid('image_row_'));row_width=max(5,min(100,int(target.get('image_width') or 100)))
            if not target.get('image_row_id'):_set_image_row(con,[str(target['id'])],row_id,row_width,t)
            con.execute("UPDATE manuscript_blocks SET position=position+1 WHERE chapter_id=? AND position>=?",(cid,pos if placement=='row_before' else pos+1));first_pos=pos if placement=='row_before' else pos+1
        elif placement=='at_offset':
            if target['block_type']!='paragraph':placement='after'
            else:
                text=str(target.get('text_content') or '');offset=max(0,min(int(body.offset or 0),len(text)))
                if offset<=0:placement='before'
                elif offset>=len(text):placement='after'
                else:
                    left_html,right_html=_split_inline_html(str(target.get('content_html') or ''),offset);left_text=text[:offset];right_text=text[offset:]
                    left_html=sanitize_inline_html(left_html,left_text);right_html=sanitize_inline_html(right_html,right_text)
                    con.execute("UPDATE manuscript_blocks SET content_html=?,text_content=?,content_hash=?,updated_at=? WHERE id=?",(left_html,left_text,storage_values.block_content_hash(left_html,left_text,''),t,target['id']))
                    con.execute("UPDATE manuscript_blocks SET position=position+2 WHERE chapter_id=? AND position>?",(cid,pos));right_id=storage_values.uid('b_')
                    con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(right_id,p['id'],tab_id,cid,scene_id,pos+2,'paragraph',target.get('semantic_style') or 'body',right_html,right_text,'',storage_values.block_content_hash(right_html,right_text,''),t,t));_move_split_anchors(con,target['id'],right_id,offset);first_pos=pos+1;placement='split'
        if placement=='before':con.execute("UPDATE manuscript_blocks SET position=position+1 WHERE chapter_id=? AND position>=?",(cid,pos));first_pos=pos
        elif placement=='after':con.execute("UPDATE manuscript_blocks SET position=position+1 WHERE chapter_id=? AND position>?",(cid,pos));first_pos=pos+1
        image_width=row_width if row_id else max(5,min(100,int(body.image_width or 100)));image_footer=(body.image_footer or '').strip()[:4000];image_description=(body.image_description or '').strip()[:12000]
        bid=storage_values.uid('b_');digest=storage_values.block_content_hash('','',body.image_path,image_width,image_footer,image_description,row_id)
        con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,p['id'],tab_id,cid,scene_id,first_pos,'image','image','','',body.image_path,image_width,image_footer,image_description,row_id,digest,t,t))
        _normalize_image_rows(con,cid,t);con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,cid));con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,p['id']));storage_invariants.validate_project_invariants(con,p['id']);revision=storage_values.chapter_revision(con,cid)
    _maybe_external_backup('manuscript_image_insert');return {'ok':True,'block_id':bid,'chapter_id':cid,'revision':revision}


@router.post('/api/manuscript/images/{block_id}/move')
def move_manuscript_image(block_id:str,body:ManuscriptImageMove):
    placement=str(body.placement or 'before')
    if placement not in {'at_offset','before','after','row_before','row_after'}:raise HTTPException(400,'Invalid image placement')
    with storage_mutations.tx() as con:
        p=project(con);source=one(con,"""SELECT b.*,c.tab_id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.id=? AND b.project_id=?""",(block_id,p['id']));target=one(con,"""SELECT b.*,c.tab_id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.id=? AND b.project_id=?""",(body.target_block_id,p['id']))
        if not source or source.get('block_type')!='image':raise HTTPException(404,'Image block not found')
        if not target:raise HTTPException(404,'Drop target not found')
        if source['id']==target['id']:
            result={'ok':True,'block_id':block_id,'chapter_id':int(source['chapter_id'])}
        else:
            if int(source['tab_id'])!=int(target['tab_id']):raise HTTPException(409,'Move images within the same manuscript document')
            source_cid=int(source['chapter_id']);target_cid=int(target['chapter_id']);old_scene=source.get('scene_id');t=storage_values.now();old_row=str(source.get('image_row_id') or '')
            row_move=False
            if placement.startswith('row_'):
                if target.get('block_type')!='image':
                    placement='before' if placement=='row_before' else 'after'
                else:
                    target_row=str(target.get('image_row_id') or '')
                    target_members=_image_row_members(con,target_row) if target_row else [target]
                    same_row=bool(old_row and target_row and old_row==target_row)
                    if not same_row and len(target_members)>=4:raise HTTPException(409,'This image row already has four images')
                    if not same_row:_detach_image_from_row(con,source,t)
                    source=one(con,"SELECT * FROM manuscript_blocks WHERE id=?",(block_id,))
                    row_id=target_row or storage_values.uid('image_row_');row_width=max(5,min(100,int(target.get('image_width') or 100)))
                    if not target_row:_set_image_row(con,[str(target['id'])],row_id,row_width,t)
                    _reorder_image_block(con,block_id,source_cid,target,placement,t);_set_image_row(con,[block_id],row_id,row_width,t);row_move=True
            if not row_move:
                _detach_image_from_row(con,source,t);source=one(con,"SELECT * FROM manuscript_blocks WHERE id=?",(block_id,))
                if placement=='at_offset':
                    if target.get('block_type')!='paragraph':placement='after'
                    else:
                        text=str(target.get('text_content') or '');offset=max(0,min(int(body.offset or 0),len(text)))
                        if offset<=0:placement='before'
                        elif offset>=len(text):placement='after'
                        else:
                            left_html,right_html=_split_inline_html(str(target.get('content_html') or ''),offset);left_text=text[:offset];right_text=text[offset:];left_html=sanitize_inline_html(left_html,left_text);right_html=sanitize_inline_html(right_html,right_text)
                            con.execute("UPDATE manuscript_blocks SET content_html=?,text_content=?,content_hash=?,updated_at=? WHERE id=?",(left_html,left_text,storage_values.block_content_hash(left_html,left_text,''),t,target['id']));right_id=storage_values.uid('b_')
                            con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(right_id,p['id'],int(target['tab_id']),target_cid,target.get('scene_id'),float(target.get('position') or 0)+0.5,'paragraph',target.get('semantic_style') or 'body',right_html,right_text,'',storage_values.block_content_hash(right_html,right_text,''),t,t));_move_split_anchors(con,target['id'],right_id,offset);placement='split'
                _reorder_image_block(con,block_id,source_cid,target,placement,t)
            _normalize_image_rows(con,source_cid,t);_normalize_image_rows(con,target_cid,t)
            if old_scene is not None and old_scene!=target.get('scene_id') and not one(con,"SELECT id FROM manuscript_blocks WHERE scene_id=? LIMIT 1",(old_scene,)):_detach_map_anchors_for_removed_manuscript(con,p['id'],scene_ids=[int(old_scene)],block_ids=[]);_delete_polymorphic_references(con,p['id'],{'scene':[int(old_scene)]});con.execute("DELETE FROM scenes WHERE id=?",(old_scene,))
            con.execute("UPDATE chapters SET updated_at=? WHERE id IN (?,?)",(t,source_cid,target_cid));con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,p['id']));storage_invariants.validate_project_invariants(con,p['id']);revisions={str(cid):storage_values.chapter_revision(con,cid) for cid in {source_cid,target_cid}}
            result={'ok':True,'block_id':block_id,'chapter_id':target_cid,'chapter_revisions':revisions}
    _maybe_external_backup('manuscript_image_move');return result
