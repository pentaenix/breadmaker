from __future__ import annotations
from fastapi import APIRouter, HTTPException
from backend.dependencies import feature_call
from storage import mutations as storage_mutations
from storage import values as storage_values
from backend.data_access import one, project
from api.workspace_tools.contracts import BookmarkSet

_bookmark_context_values = feature_call('manuscript.anchors', '_bookmark_context_values')
_bookmark_payload = feature_call('manuscript.navigation', '_bookmark_payload')

router=APIRouter()

@router.get("/api/bookmark")
def get_bookmark():
    with storage_mutations.tx() as con:
        p=project(con); return {"bookmark":_bookmark_payload(con,p['id'])}

@router.put("/api/bookmark")
def set_bookmark(body:BookmarkSet):
    kind=str(body.target_type or '').strip().lower()
    if kind not in {'chapter','scene','range'}: raise HTTPException(400,detail="BOOKMARK_001: Unsupported bookmark target.")
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; t=storage_values.now()
        chapter_id=scene_id=None; start_block_id=end_block_id=None; start_offset=end_offset=0
        if kind=='chapter':
            ch=one(con,"SELECT id FROM chapters WHERE id=? AND project_id=? AND is_implicit=0",(body.chapter_id,pid))
            if not ch: raise HTTPException(404,detail="BOOKMARK_002: Chapter no longer exists.")
            chapter_id=ch['id']
        elif kind=='scene':
            sc=one(con,"SELECT id FROM scenes WHERE id=? AND project_id=?",(body.scene_id,pid))
            if not sc: raise HTTPException(404,detail="BOOKMARK_003: Scene no longer exists.")
            scene_id=sc['id']
        else:
            if not body.start_block_id or not body.end_block_id: raise HTTPException(400,detail="BOOKMARK_004: Text bookmark needs a manuscript range.")
            a=one(con,"SELECT id,tab_id,text_content FROM manuscript_blocks WHERE id=? AND project_id=?",(body.start_block_id,pid))
            b=one(con,"SELECT id,tab_id,text_content FROM manuscript_blocks WHERE id=? AND project_id=?",(body.end_block_id,pid))
            if not a or not b: raise HTTPException(404,detail="BOOKMARK_005: Bookmarked text no longer exists.")
            if int(a.get('tab_id') or 0)!=int(b.get('tab_id') or 0): raise HTTPException(400,detail="BOOKMARK_006: A bookmark cannot cross Tabs.")
            start_block_id=a['id']; end_block_id=b['id']
            start_offset=max(0,min(int(body.start_offset or 0),len(a.get('text_content') or '')))
            end_offset=max(0,min(int(body.end_offset or 0),len(b.get('text_content') or '')))
            if start_block_id==end_block_id and end_offset<=start_offset: raise HTTPException(400,detail="BOOKMARK_007: Select text before bookmarking it.")
        existing=one(con,"SELECT created_at FROM bookmarks WHERE project_id=?",(pid,))
        created=existing['created_at'] if existing else t
        context=('', '', '', '', '', '')
        if kind=='range':
            context=_bookmark_context_values(str(a.get('text_content') or ''),str(b.get('text_content') or ''),start_offset,end_offset,start_block_id==end_block_id)
        con.execute("""INSERT INTO bookmarks(project_id,target_type,chapter_id,scene_id,start_block_id,end_block_id,start_offset,end_offset,start_selected_text,start_prefix_context,start_suffix_context,end_selected_text,end_prefix_context,end_suffix_context,stale,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(project_id) DO UPDATE SET target_type=excluded.target_type,chapter_id=excluded.chapter_id,scene_id=excluded.scene_id,start_block_id=excluded.start_block_id,end_block_id=excluded.end_block_id,start_offset=excluded.start_offset,end_offset=excluded.end_offset,start_selected_text=excluded.start_selected_text,start_prefix_context=excluded.start_prefix_context,start_suffix_context=excluded.start_suffix_context,end_selected_text=excluded.end_selected_text,end_prefix_context=excluded.end_prefix_context,end_suffix_context=excluded.end_suffix_context,stale=excluded.stale,updated_at=excluded.updated_at""",
                    (pid,kind,chapter_id,scene_id,start_block_id,end_block_id,start_offset,end_offset,*context,0,created,t))
        return {"bookmark":_bookmark_payload(con,pid)}

@router.delete("/api/bookmark")
def delete_bookmark():
    with storage_mutations.tx() as con:
        p=project(con); cur=con.execute("DELETE FROM bookmarks WHERE project_id=?",(p['id'],))
        return {"ok":True,"changed":bool(cur.rowcount)}

