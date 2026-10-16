from __future__ import annotations

"""Manuscript bootstrap, document reads, and basic Chapter metadata routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException

from api.manuscript import anchors as manuscript_anchors
from api.manuscript import navigation as manuscript_navigation
from backend.dependencies import feature_call
from api.manuscript.contracts import ChapterCreate, ChapterTitle, ChapterUpdate, ProjectUpdate
from backend.data_access import allrows, one, project
from backend.environment import runtime
from backend.configuration import _canvas_config_raw, _feature_flags_raw, _metro_view_config_raw
from backend.request_context import _bound_project
from backend.history_runtime import commit
from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import values as storage_values
from workspace.filesystem import safe_name

_review_rows = feature_call('reviews.lifecycle', '_review_rows')
sync_developmental_canvases = feature_call('canvas.developmental', 'sync_developmental_canvases')

router = APIRouter()

@router.get("/api/bootstrap")
def bootstrap():
    # Bootstrap is navigation metadata, not an eager manuscript hydration endpoint.
    # Revision hashes require every prose/image field and scale with book length; the
    # active Chapter/Full Text/Scene endpoint already computes the exact revisions it
    # needs when that editing context is hydrated.  Keeping them out of bootstrap cuts
    # a full manuscript payload scan from every launch.
    with storage_database.read_tx() as con:
        p=project(con);pid=p["id"]
        chapters=allrows(con,"SELECT * FROM chapters WHERE project_id=? ORDER BY tab_id,position,id",(pid,))
        scenes,scene_gaps=manuscript_navigation._manuscript_navigation_projection(con,pid)
        return {
            "project":p,
            "tabs":allrows(con,"SELECT * FROM document_tabs WHERE project_id=? ORDER BY position,id",(pid,)),
            "chapters":chapters,
            "chapter_outlines":allrows(con,"SELECT id,chapter_id,position,semantic_style,text_content FROM manuscript_blocks WHERE project_id=? AND block_type='paragraph' AND semantic_style IN ('heading2','heading3','heading4') ORDER BY chapter_id,position,id",(pid,)),
            "canvases":allrows(con,"SELECT * FROM canvases WHERE project_id=? ORDER BY parent_id IS NOT NULL,parent_id,position,id",(pid,)),
            "scenes":scenes,
            "scene_gaps":scene_gaps,
            "bookmark":manuscript_navigation._bookmark_payload(con,pid),
            "labels":allrows(con,"SELECT * FROM markup_labels WHERE project_id=? ORDER BY name",(pid,)),
            "icon_markers":allrows(con,"SELECT * FROM icon_markers WHERE project_id=? ORDER BY name,id",(pid,)),
            "maps":allrows(con,"SELECT * FROM story_maps WHERE project_id=? ORDER BY position,id",(pid,)),
            "reviews":_review_rows(con,pid),
            "features":_feature_flags_raw()["features"],
            "metro_view_config":_metro_view_config_raw(),
            "canvas_config":_canvas_config_raw(),
        }

@router.patch("/api/project/title")
def update_project(body:ProjectUpdate):
    clean=safe_name(body.title)
    rec=_bound_project()
    if not rec: raise HTTPException(409,detail="PROJECT_004: No request-bound project.")
    runtime.projects.rename(rec["id"],clean)
    with storage_mutations.tx() as con:
        p=project(con); con.execute("UPDATE projects SET title=?,updated_at=? WHERE id=?",(clean,storage_values.now(),p["id"]))
    commit("Renamed project"); return {"ok":True,"title":clean}

@router.get("/api/chapters/{cid}")
def get_chapter(cid:int):
    with storage_database.read_tx() as con:
        ch=one(con,"SELECT * FROM chapters WHERE id=?",(cid,))
        if not ch:raise HTTPException(404,"Chapter not found")
        blocks=allrows(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(cid,))
        anns=allrows(con,"""SELECT a.*,l.name label_name,l.color label_color FROM annotations a
            LEFT JOIN markup_labels l ON l.id=a.label_id
            WHERE a.chapter_id=? OR a.block_id IN (SELECT id FROM manuscript_blocks WHERE chapter_id=?) ORDER BY a.id""",(cid,cid))
        return {"chapter":ch,"blocks":blocks,"annotations":anns,"revision":storage_values.chapter_revision_from_rows(ch,blocks)}

@router.get("/api/book")
def get_book(tab_id:Optional[int]=None):
    """Return one source tab as a continuous editable document.

    Full Text is a read model: never take the mutation coordinator/write lock. The
    chapter revisions are computed from the blocks we already fetched instead of
    re-reading every chapter and its blocks a second time.
    """
    with storage_database.read_tx() as con:
        p=project(con)
        if tab_id is None:
            row=con.execute("SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(p['id'],)).fetchone()
            tab_id=row[0] if row else None
        tab=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(tab_id,p['id'])) if tab_id is not None else None
        if not tab: raise HTTPException(404,"Source tab not found")
        chapters=allrows(con,"SELECT * FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(p['id'],tab_id))
        blocks=allrows(con,"SELECT * FROM manuscript_blocks WHERE project_id=? AND tab_id=? ORDER BY chapter_id,position,id",(p['id'],tab_id))
        by_chapter={}
        for block in blocks:by_chapter.setdefault(int(block['chapter_id']),[]).append(block)
        for ch in chapters:ch['revision']=storage_values.chapter_revision_from_rows(ch,by_chapter.get(int(ch['id']),[]))
        anns=allrows(con,"""SELECT a.*,l.name label_name,l.color label_color
            FROM annotations a LEFT JOIN markup_labels l ON l.id=a.label_id
            WHERE a.project_id=? AND (
              a.chapter_id IN (SELECT id FROM chapters WHERE project_id=? AND tab_id=?) OR
              a.block_id IN (SELECT id FROM manuscript_blocks WHERE project_id=? AND tab_id=?)
            ) ORDER BY a.id""",(p['id'],p['id'],tab_id,p['id'],tab_id))
        return {"tab":tab,"chapters":chapters,"blocks":blocks,"annotations":anns}

@router.post("/api/chapters")
def create_chapter(body:ChapterCreate):
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; anchor=None
        if body.after_chapter_id is not None:
            anchor=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(int(body.after_chapter_id),pid))
            if not anchor: raise HTTPException(404,"Chapter to insert after was not found")
            if body.tab_id is not None and int(body.tab_id)!=int(anchor['tab_id']): raise HTTPException(409,"A Chapter can only be inserted beside a Chapter in the same Tab")
            tab=int(anchor['tab_id'])
        else:
            tab=body.tab_id or con.execute("SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY id LIMIT 1",(pid,)).fetchone()[0]
        t=storage_values.now()
        if anchor:
            pos=float(anchor['position'])+1
            con.execute("UPDATE chapters SET position=position+1,updated_at=? WHERE project_id=? AND tab_id=? AND position>=?",(t,pid,tab,pos))
        else:
            pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM chapters WHERE project_id=? AND tab_id=?",(pid,tab)).fetchone()[0]
        cid=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(pid,tab,pos,body.title,'','','',t,t)).lastrowid
        bid=storage_values.uid('b_'); con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,tab,cid,0,'paragraph','body','','','',t,t))
        out=one(con,"SELECT * FROM chapters WHERE id=?",(cid,))
    commit(f"Created {body.title}"); sync_developmental_canvases(pid); return out

@router.patch("/api/chapters/{cid}/title")
def update_chapter_title(cid:int,body:ChapterTitle):
    with storage_mutations.tx() as con:
        ch=one(con,"SELECT * FROM chapters WHERE id=?",(cid,));
        if not ch: raise HTTPException(404,"Chapter not found")
        current=storage_values.chapter_revision(con,cid)
        if body.expected_revision and body.expected_revision!=current:
            raise HTTPException(409,detail={"code":"MANUSCRIPT_CONFLICT_001","message":"This Chapter changed after this view was loaded. Your text was not overwritten.","params":{"chapter_id":cid,"current_revision":current}})
        manuscript_anchors.reanchor_chapter_title_annotations(con,cid,body.title)
        con.execute("UPDATE chapters SET title=?,updated_at=? WHERE id=?",(body.title,storage_values.now(),cid)); con.execute("UPDATE canvas_nodes SET title=?,updated_at=? WHERE chapter_id=?",(body.title,storage_values.now(),cid))
        revision=storage_values.chapter_revision(con,cid)
    return {"ok":True,"revision":revision}

@router.patch("/api/chapters/{cid}")
def update_chapter_metadata(cid:int,body:ChapterUpdate):
    vals=body.model_dump(exclude_unset=True)
    with storage_mutations.tx() as con:
        p=project(con); ch=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(cid,p['id']))
        if not ch: raise HTTPException(404,"Chapter not found")
        current=storage_values.chapter_revision(con,cid)
        if body.expected_revision and body.expected_revision!=current:
            raise HTTPException(409,detail={"code":"MANUSCRIPT_CONFLICT_001","message":"This Chapter changed after this view was loaded. Your text was not overwritten.","params":{"chapter_id":cid,"current_revision":current}})
        if 'description' in vals:
            con.execute("UPDATE chapters SET description=?,updated_at=? WHERE id=?",((vals.get('description') or '').strip()[:12000],storage_values.now(),cid))
        revision=storage_values.chapter_revision(con,cid)
    return {"ok":True,"revision":revision}
