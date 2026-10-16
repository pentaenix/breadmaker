from __future__ import annotations
from typing import Optional
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException
from storage import mutations as storage_mutations
from storage import values as storage_values
from backend.data_access import allrows, one, project
from api.workspace_tools.contracts import ReferenceCreate

router=APIRouter()

_REFERENCE_SOURCE_TYPES={"task","milestone","node","canvas","map","map_line","map_item","chapter","scene","annotation","document","manuscript_range"}
_REFERENCE_TARGET_TYPES=_REFERENCE_SOURCE_TYPES|{"url","review_highlight","review_comment","review_suggestion","review_answer","review_general_feedback"}

def _entity_exists(con, pid:int, typ:str, ident:str)->bool:
    if typ=="manuscript_range": return True
    if typ=="url":
        try:
            parsed=urlparse(str(ident).strip())
            return parsed.scheme in {"http","https"} and bool(parsed.netloc)
        except Exception:
            return False
    if typ in {"review_highlight","review_comment","review_suggestion","review_answer","review_general_feedback"}:
        table={"review_highlight":"review_highlights","review_comment":"review_comments","review_suggestion":"review_suggestions","review_answer":"review_question_answers","review_general_feedback":"review_general_feedback"}[typ]
        return con.execute(f"SELECT 1 FROM {table} i JOIN reviews r ON r.id=i.review_id WHERE i.id=? AND r.project_id=?",(str(ident),pid)).fetchone() is not None
    try: iid=int(ident)
    except (TypeError,ValueError): return False
    mapping={
        "task":("tasks","id","project_id"),"milestone":("task_milestones","id","project_id"),"canvas":("canvases","id","project_id"),
        "map":("story_maps","id","project_id"),"scene":("scenes","id","project_id"),
        "chapter":("chapters","id","project_id"),"annotation":("annotations","id","project_id"),
        "document":("document_tabs","id","project_id"),
    }
    if typ=="node":
        return con.execute("SELECT 1 FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE n.id=? AND c.project_id=?",(iid,pid)).fetchone() is not None
    if typ=="map_line":
        return con.execute("SELECT 1 FROM map_lines l JOIN story_maps m ON m.id=l.map_id WHERE l.id=? AND m.project_id=?",(iid,pid)).fetchone() is not None
    if typ=="map_item":
        return con.execute("SELECT 1 FROM map_items i JOIN map_lines l ON l.id=i.line_id JOIN story_maps m ON m.id=l.map_id WHERE i.id=? AND m.project_id=?",(iid,pid)).fetchone() is not None
    if typ not in mapping: return False
    table,col,pcol=mapping[typ]
    return con.execute(f"SELECT 1 FROM {table} WHERE {col}=? AND {pcol}=?",(iid,pid)).fetchone() is not None

def _describe_ref(con, typ:str, ident:str)->str:
    if typ=="url": return str(ident)[:180]
    if typ in {"review_highlight","review_comment","review_suggestion"}:
        table={"review_highlight":"review_highlights","review_comment":"review_comments","review_suggestion":"review_suggestions"}[typ]
        row=con.execute(f"SELECT rp.display_name,a.selected_text FROM {table} i JOIN review_participants rp ON rp.id=i.participant_id AND rp.review_id=i.review_id JOIN review_anchors a ON a.id=i.anchor_id AND a.review_id=i.review_id WHERE i.id=?",(str(ident),)).fetchone()
        if not row: return f"Missing {typ.replace('_',' ')}"
        excerpt=str(row[1] or '').replace('\n',' ')[:90]
        return f"{row[0]}: {excerpt}" if excerpt else str(row[0])
    if typ=="review_answer":
        row=con.execute("SELECT rp.display_name,a.answer_text FROM review_question_answers a JOIN review_participants rp ON rp.id=a.participant_id AND rp.review_id=a.review_id WHERE a.id=?",(str(ident),)).fetchone()
        if not row:return "Missing review answer"
        excerpt=str(row[1] or '').replace('\n',' ')[:90];return f"{row[0]}: {excerpt}" if excerpt else str(row[0])
    if typ=="review_general_feedback":
        row=con.execute("SELECT rp.display_name,g.body FROM review_general_feedback g JOIN review_participants rp ON rp.id=g.participant_id AND rp.review_id=g.review_id WHERE g.id=?",(str(ident),)).fetchone()
        if not row:return "Missing review feedback"
        excerpt=str(row[1] or '').replace('\n',' ')[:90];return f"{row[0]}: {excerpt}" if excerpt else str(row[0])
    try: iid=int(ident)
    except Exception: return "Manuscript selection" if typ=="manuscript_range" else ident
    sql={
      "task":("tasks","title"),"milestone":("task_milestones","title"),"canvas":("canvases","name"),"map":("story_maps","name"),
      "scene":("scenes","title"),"chapter":("chapters","title"),"annotation":("annotations","body"),
      "document":("document_tabs","title"),"node":("canvas_nodes","COALESCE(NULLIF(title,''),NULLIF(body,''),'Untitled node')"),
      "map_line":("map_lines","name"),"map_item":("map_items","COALESCE(NULLIF(title,''),'Map item')")
    }.get(typ)
    if not sql: return typ
    row=con.execute(f"SELECT {sql[1]} FROM {sql[0]} WHERE id=?",(iid,)).fetchone()
    return str(row[0])[:180] if row else f"Missing {typ}"

@router.post("/api/references")
def create_reference(body:ReferenceCreate):
    if body.source_type not in _REFERENCE_SOURCE_TYPES or body.target_type not in _REFERENCE_TARGET_TYPES:
        raise HTTPException(400,detail="LINK_001: Unsupported link type.")
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']
        if not _entity_exists(con,pid,body.source_type,body.source_id): raise HTTPException(404,detail="LINK_002: Link source no longer exists.")
        if body.source_type=='task':
            source_task=one(con,"SELECT status FROM tasks WHERE id=? AND project_id=?",(int(body.source_id),pid))
            if source_task and source_task['status']=='archived': raise HTTPException(409,detail="TASK_007: Archived tasks are read-only until unarchived")
        if body.source_type=='milestone':
            source_task=one(con,"SELECT t.status FROM task_milestones tm JOIN tasks t ON t.id=tm.task_id WHERE tm.id=? AND tm.project_id=?",(int(body.source_id),pid))
            if source_task and source_task['status']=='archived': raise HTTPException(409,detail="TASK_007: Archived tasks are read-only until unarchived")
        if body.target_type=="manuscript_range":
            if not body.start_block_id: raise HTTPException(400,detail="LINK_003: A manuscript link needs a text range.")
            a=one(con,"SELECT * FROM manuscript_blocks WHERE id=? AND project_id=?",(body.start_block_id,pid)); b=one(con,"SELECT * FROM manuscript_blocks WHERE id=? AND project_id=?",(body.end_block_id or body.start_block_id,pid))
            if not a or not b: raise HTTPException(409,detail="LINK_004: Selected manuscript text changed before the link was saved.")
            target_id="range"
        elif body.target_type=="url":
            target_id=str(body.target_id or '').strip()
            if len(target_id)>2000 or not _entity_exists(con,pid,'url',target_id): raise HTTPException(400,detail="LINK_007: Web links must use a valid http:// or https:// address.")
        else:
            if not _entity_exists(con,pid,body.target_type,body.target_id): raise HTTPException(404,detail="LINK_005: Link target no longer exists.")
            target_id=body.target_id
        rid=con.execute("INSERT INTO references_map(project_id,source_type,source_id,target_type,target_id,start_block_id,end_block_id,start_offset,end_offset,excerpt,relationship,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(pid,body.source_type,str(body.source_id),body.target_type,str(target_id),body.start_block_id,body.end_block_id,body.start_offset,body.end_offset,body.excerpt[:2000],body.relationship[:120],storage_values.now())).lastrowid
        return {"id":rid}

@router.get("/api/references")
def list_references(source_type:Optional[str]=None,source_id:Optional[str]=None,target_type:Optional[str]=None,target_id:Optional[str]=None):
    with storage_mutations.tx() as con:
        p=project(con); q="SELECT * FROM references_map WHERE project_id=?"; args=[p['id']]
        for col,val in (("source_type",source_type),("source_id",source_id),("target_type",target_type),("target_id",target_id)):
            if val is not None: q+=f" AND {col}=?"; args.append(str(val))
        q+=" ORDER BY id DESC"
        rows=allrows(con,q,args)
        for r in rows:
            r['source_label']=_describe_ref(con,r['source_type'],r['source_id'])
            r['target_label']=r['excerpt'][:120] if r['target_type']=='manuscript_range' and r['excerpt'] else (r['relationship'][:120] if r['target_type']=='url' and r['relationship'] else _describe_ref(con,r['target_type'],r['target_id']))
            if r['target_type']=='manuscript_range':
                start=one(con,"SELECT chapter_id FROM manuscript_blocks WHERE id=? AND project_id=?",(r['start_block_id'],p['id'])) if r.get('start_block_id') else None
                end=one(con,"SELECT chapter_id FROM manuscript_blocks WHERE id=? AND project_id=?",(r['end_block_id'] or r['start_block_id'],p['id'])) if (r.get('end_block_id') or r.get('start_block_id')) else None
                r['target_start_chapter_id']=start['chapter_id'] if start else None
                r['target_end_chapter_id']=end['chapter_id'] if end else None
                r['target_missing']=not bool(start and end)
        return rows

@router.delete("/api/references/{rid}")
def delete_reference(rid:int):
    with storage_mutations.tx() as con:
        p=project(con); ref=one(con,"SELECT * FROM references_map WHERE id=? AND project_id=?",(rid,p['id']))
        if not ref: raise HTTPException(404,detail="LINK_006: Link not found.")
        if ref['source_type']=='task':
            source_task=one(con,"SELECT status FROM tasks WHERE id=? AND project_id=?",(int(ref['source_id']),p['id']))
            if source_task and source_task['status']=='archived': raise HTTPException(409,detail="TASK_007: Archived tasks are read-only until unarchived")
        if ref['source_type']=='milestone':
            source_task=one(con,"SELECT t.status FROM task_milestones tm JOIN tasks t ON t.id=tm.task_id WHERE tm.id=? AND tm.project_id=?",(int(ref['source_id']),p['id']))
            if source_task and source_task['status']=='archived': raise HTTPException(409,detail="TASK_007: Archived tasks are read-only until unarchived")
        cur=con.execute("DELETE FROM references_map WHERE id=? AND project_id=?",(rid,p['id']))
        if not cur.rowcount: raise HTTPException(404,detail="LINK_006: Link not found.")
    return {"ok":True}
