from __future__ import annotations

"""Structural Chapter split route."""

from fastapi import APIRouter, HTTPException

from api.manuscript import anchors as manuscript_anchors
from backend.dependencies import feature_call
from api.manuscript.contracts import ChapterSplit
from backend.data_access import _delete_polymorphic_references, allrows, one
from backend.history_runtime import commit
from storage import mutations as storage_mutations
from storage import values as storage_values
from workspace.filesystem import safe_name

_detach_map_anchors_for_removed_manuscript = feature_call('maps.anchors', '_detach_map_anchors_for_removed_manuscript')
sync_developmental_canvases = feature_call('canvas.developmental', 'sync_developmental_canvases')

router = APIRouter()

@router.post("/api/chapters/{cid}/split")
def split_chapter_at_block(cid:int,body:ChapterSplit):
    """Turn one manuscript paragraph into a real Chapter boundary.

    The selected paragraph becomes the new Chapter title and is removed from prose.
    Every block after it moves to the new Chapter with stable block IDs so scenes,
    annotations, links, and Map manuscript anchors keep following the same content.
    """
    with storage_mutations.tx() as con:
        ch=one(con,"SELECT * FROM chapters WHERE id=?",(cid,))
        if not ch: raise HTTPException(404,"Chapter not found")
        pivot=one(con,"SELECT * FROM manuscript_blocks WHERE id=? AND chapter_id=?",(body.block_id,cid))
        if not pivot or pivot['block_type']!='paragraph': raise HTTPException(400,"Choose a text paragraph to create a Chapter")
        title=safe_name((body.title or pivot['text_content'] or '').strip() or 'Untitled Chapter')
        t=storage_values.now(); new_pos=int(ch['position'])+1
        con.execute("UPDATE chapters SET position=position+1,updated_at=? WHERE project_id=? AND tab_id=? AND position>=?",(t,ch['project_id'],ch['tab_id'],new_pos))
        new_id=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(ch['project_id'],ch['tab_id'],new_pos,title,'','','',t,t)).lastrowid
        following=allrows(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? AND position>? ORDER BY position,id",(cid,pivot['position']))
        for i,row in enumerate(following):
            con.execute("UPDATE manuscript_blocks SET chapter_id=?,tab_id=?,position=?,updated_at=? WHERE id=?",(new_id,ch['tab_id'],i,t,row['id']))
        if following:
            target=dict(following[0]); target['chapter_id']=new_id; target['position']=0
            candidates=[dict(row) for row in following]
            for i,row in enumerate(candidates): row['chapter_id']=new_id; row['position']=i
        else:
            bid=storage_values.uid('b_')
            con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,ch['project_id'],ch['tab_id'],new_id,pivot['scene_id'],0,'paragraph','body','','','',t,t))
            target={'id':bid,'text_content':'','position':0,'scene_id':pivot['scene_id'],'chapter_id':new_id}
            candidates=[target]
        # The title paragraph itself leaves prose. Keep any editorial anchors safe by
        # re-homing them to the first block in the new Chapter rather than cascading.
        con.execute("UPDATE annotations SET block_id=?,start_offset=0,end_offset=0,stale=1,updated_at=? WHERE block_id=?",(target['id'],t,pivot['id']))
        bm=one(con,"SELECT * FROM bookmarks WHERE project_id=? AND target_type='range' AND (start_block_id=? OR end_block_id=?)",(ch['project_id'],pivot['id'],pivot['id']))
        if bm:
            updates={}
            if bm.get('start_block_id')==pivot['id']: updates.update(start_block_id=target['id'],start_offset=0)
            if bm.get('end_block_id')==pivot['id']: updates.update(end_block_id=target['id'],end_offset=0)
            if updates:
                updates['stale']=1; updates['updated_at']=t; sets=','.join(f"{k}=?" for k in updates)
                con.execute(f"UPDATE bookmarks SET {sets} WHERE project_id=?",(*updates.values(),ch['project_id']))
        con.execute("UPDATE references_map SET start_block_id=? WHERE start_block_id=?",(target['id'],pivot['id']))
        con.execute("UPDATE references_map SET end_block_id=? WHERE end_block_id=?",(target['id'],pivot['id']))
        manuscript_anchors._rehome_map_range_anchors_for_block(con,pivot['id'],candidates,target)
        removed_scene_id=None
        if pivot.get('scene_id') is not None and not one(con,"SELECT id FROM manuscript_blocks WHERE scene_id=? AND id<>? LIMIT 1",(pivot['scene_id'],pivot['id'])):
            # If the split created a blank continuation carrying this same Scene, the
            # Scene still has a real manuscript block and remains valid.
            if int(target.get('scene_id') or 0)!=int(pivot['scene_id']):
                removed_scene_id=int(pivot['scene_id']); _detach_map_anchors_for_removed_manuscript(con,ch['project_id'],scene_ids=(removed_scene_id,),block_ids=(pivot['id'],)); _delete_polymorphic_references(con,ch['project_id'],{'scene':(removed_scene_id,)})
        con.execute("DELETE FROM manuscript_blocks WHERE id=?",(pivot['id'],))
        if removed_scene_id is not None: con.execute("DELETE FROM scenes WHERE id=?",(removed_scene_id,))
        # Re-normalize the source Chapter positions after removing the title line.
        for i,row in enumerate(allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(cid,))):
            con.execute("UPDATE manuscript_blocks SET position=?,updated_at=? WHERE id=?",(i,t,row['id']))
        con.execute("UPDATE chapters SET updated_at=? WHERE id IN (?,?)",(t,cid,new_id));con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,ch['project_id']))
        out=one(con,"SELECT * FROM chapters WHERE id=?",(new_id,))
    commit(f"Created {title}");sync_developmental_canvases(ch['project_id']);return out
