from __future__ import annotations

"""Non-content project metadata/statistics HTTP surface."""

from fastapi import APIRouter, HTTPException

from backend.data_access import project
from backend.environment import runtime
from storage import database as storage_database
from storage import undo as storage_undo

router=APIRouter()

@router.get("/api/project/metadata")
def project_metadata():
    """Non-content project statistics for the Project Center Metadata view."""
    con=storage_database.connect()
    try:
        p=project(con)
        if not p: raise HTTPException(404,detail="No active project.")
        pid=p["id"]
        def scalar(sql:str,params=()):
            return int(con.execute(sql,params).fetchone()[0] or 0)
        rows=con.execute("SELECT mb.text_content FROM manuscript_blocks mb JOIN document_tabs dt ON dt.id=mb.tab_id WHERE mb.project_id=? AND dt.is_manuscript=1 AND mb.block_type!='image' ORDER BY mb.position",(pid,)).fetchall()
        manuscript_text=' '.join((row[0] or '') for row in rows).strip()
        words=len(manuscript_text.split()) if manuscript_text else 0
        characters=len(manuscript_text)
        characters_no_spaces=sum(1 for ch in manuscript_text if not ch.isspace())
        visible_history=len(runtime.history.list_revisions())
        undo=storage_undo.session_undo_count(pid)
        return {
            "project":{"name":p["title"],"created_at":p["created_at"],"updated_at":p["updated_at"]},
            "manuscript":{
                "words":words,"characters":characters,"characters_no_spaces":characters_no_spaces,
                "chapters":scalar("SELECT COUNT(*) FROM chapters c JOIN document_tabs dt ON dt.id=c.tab_id WHERE c.project_id=? AND dt.is_manuscript=1 AND c.is_implicit=0",(pid,)),
                "blocks":scalar("SELECT COUNT(*) FROM manuscript_blocks mb JOIN document_tabs dt ON dt.id=mb.tab_id WHERE mb.project_id=? AND dt.is_manuscript=1",(pid,)),
                "images":scalar("SELECT COUNT(*) FROM manuscript_blocks mb JOIN document_tabs dt ON dt.id=mb.tab_id WHERE mb.project_id=? AND dt.is_manuscript=1 AND mb.block_type='image'",(pid,)),
                "scenes":scalar("SELECT COUNT(*) FROM scenes WHERE project_id=?",(pid,)),
            },
            "structure":{
                "tabs":scalar("SELECT COUNT(*) FROM document_tabs WHERE project_id=?",(pid,)),
                "annotations":scalar("SELECT COUNT(*) FROM annotations WHERE project_id=?",(pid,)),
                "markers":scalar("SELECT COUNT(*) FROM markup_labels WHERE project_id=?",(pid,)),
                "tasks":scalar("SELECT COUNT(*) FROM tasks WHERE project_id=?",(pid,)),
                "milestones":scalar("SELECT COUNT(*) FROM task_milestones WHERE project_id=?",(pid,)),
            },
            "canvas":{
                "canvases":scalar("SELECT COUNT(*) FROM canvases WHERE project_id=?",(pid,)),
                "nodes":scalar("SELECT COUNT(*) FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE c.project_id=?",(pid,)),
                "connections":scalar("SELECT COUNT(*) FROM canvas_edges e JOIN canvases c ON c.id=e.canvas_id WHERE c.project_id=?",(pid,)),
            },
            "maps":{
                "maps":scalar("SELECT COUNT(*) FROM story_maps WHERE project_id=?",(pid,)),
                "lines":scalar("SELECT COUNT(*) FROM map_lines l JOIN story_maps m ON m.id=l.map_id WHERE m.project_id=?",(pid,)),
                "items":scalar("SELECT COUNT(*) FROM map_items i JOIN map_lines l ON l.id=i.line_id JOIN story_maps m ON m.id=l.map_id WHERE m.project_id=?",(pid,)),
                "connections":scalar("SELECT COUNT(*) FROM map_junctions j JOIN story_maps m ON m.id=j.map_id WHERE m.project_id=?",(pid,)),
            },
            "safety":{
                "checkpoints":scalar("SELECT COUNT(*) FROM checkpoints WHERE project_id=?",(pid,)),
                "cuts":scalar("SELECT COUNT(*) FROM cuts WHERE project_id=?",(pid,)),
                "history":visible_history,"undo":undo,
            }
        }
    finally:
        con.close()
