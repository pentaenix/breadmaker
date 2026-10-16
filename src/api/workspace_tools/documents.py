from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException
from backend.dependencies import feature_call
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import values as storage_values
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from backend.history_runtime import commit
from api.workspace_tools.contracts import DocumentCreate, DocumentUpdate

_detach_map_anchors_for_removed_manuscript = feature_call('maps.anchors', '_detach_map_anchors_for_removed_manuscript')
repair_orphaned_bookmark = feature_call('manuscript.anchors', 'repair_orphaned_bookmark')
sync_developmental_canvases = feature_call('canvas.developmental', 'sync_developmental_canvases')

router=APIRouter()

@router.post("/api/documents")
def create_document(body:DocumentCreate):
    title=(body.title or 'Notes').strip()[:140] or 'Notes'
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; t=storage_values.now()
        if body.is_manuscript and con.execute("SELECT 1 FROM document_tabs WHERE project_id=? AND is_manuscript=1",(pid,)).fetchone():
            raise HTTPException(409,detail="DOCUMENT_001: This project already has a primary manuscript.")
        pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM document_tabs WHERE project_id=?",(pid,)).fetchone()[0]
        did=con.execute("INSERT INTO document_tabs(project_id,position,title,description,is_manuscript,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(pid,pos,title,body.description[:12000],int(body.is_manuscript),t,t)).lastrowid
        cid=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(pid,did,0,title if not body.is_manuscript else 'Chapter 1','',0 if body.is_manuscript else 1,'','',t,t)).lastrowid
        bid=storage_values.uid('b_'); con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,did,cid,0,'paragraph','body','','','',t,t))
    commit(f"Created document: {title}","structure"); return {"id":did,"chapter_id":cid,"title":title}

@router.patch("/api/documents/{did}")
def rename_document(did:int,body:DocumentUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'title' in vals:
        vals['title']=(vals['title'] or '').strip()[:140]
        if not vals['title']: raise HTTPException(400,detail="DOCUMENT_002: Tab name cannot be empty.")
    if 'description' in vals: vals['description']=(vals['description'] or '')[:12000]
    with storage_mutations.tx() as con:
        p=project(con)
        if not one(con,"SELECT id FROM document_tabs WHERE id=? AND project_id=?",(did,p['id'])): raise HTTPException(404,detail="DOCUMENT_003: Tab not found.")
        if vals:
            sets=', '.join(f"{k}=?" for k in vals)+",updated_at=?"; con.execute(f"UPDATE document_tabs SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),did))
    return {"ok":True}

@router.post("/api/documents/{did}/make-manuscript")
def make_document_manuscript(did:int):
    """Make one Tab the project's Main Manuscript without changing its contents."""
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; t=storage_values.now()
        doc=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(did,pid))
        if not doc: raise HTTPException(404,detail="DOCUMENT_003: Tab not found.")
        if doc.get('is_manuscript'): return {"ok":True,"id":did}
        con.execute("UPDATE document_tabs SET is_manuscript=0,updated_at=? WHERE project_id=? AND is_manuscript=1",(t,pid))
        con.execute("UPDATE document_tabs SET is_manuscript=1,updated_at=? WHERE id=? AND project_id=?",(t,did,pid))
        con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid)); storage_invariants.validate_project_invariants(con,pid)
    commit(f"Main manuscript set: {doc['title']}","structure")
    return {"ok":True,"id":did}

@router.delete("/api/documents/{did}")
def delete_document(did:int):
    """Delete one complete manuscript document while keeping the project valid.

    A project must always retain at least one Document. If the deleted Document is
    the primary manuscript, the next remaining Document is promoted so book-level
    services always retain one deterministic manuscript root.
    """
    # Validate before taking the safety snapshot. A refused last-document delete is
    # not an operation and should not leave noise in History.
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']
        doc=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(did,pid))
        if not doc: raise HTTPException(404,detail="DOCUMENT_003: Tab not found.")
        remaining=allrows(con,"SELECT * FROM document_tabs WHERE project_id=? AND id<>? ORDER BY position,id",(pid,did))
        if not remaining: raise HTTPException(409,detail="DOCUMENT_004: A project must keep at least one document.")
    commit("Before deleting document","safety")
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']
        doc=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(did,pid))
        if not doc: raise HTTPException(404,detail="DOCUMENT_003: Tab not found.")
        remaining=allrows(con,"SELECT * FROM document_tabs WHERE project_id=? AND id<>? ORDER BY position,id",(pid,did))
        # Keep navigation local: prefer the next Document in rail order, otherwise
        # fall back to the previous one. Primary promotion remains deterministic.
        next_choice=next((row for row in remaining if int(row['position'])>int(doc['position'])),remaining[-1])
        next_document_id=int(next_choice['id'])

        chapters=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,did)); chapter_ids=[r['id'] for r in chapters]
        scenes=allrows(con,"SELECT id FROM scenes WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,did)); scene_ids=[r['id'] for r in scenes]
        blocks=allrows(con,"SELECT id FROM manuscript_blocks WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,did)); block_ids=[r['id'] for r in blocks]
        annotation_ids=[]; thread_ids=[]; annotations=[]
        if chapter_ids:
            cm=','.join('?' for _ in chapter_ids)
            annotations.extend(allrows(con,f"SELECT id,thread_id FROM annotations WHERE project_id=? AND chapter_id IN ({cm})",[pid,*chapter_ids]))
        if block_ids:
            marks=','.join('?' for _ in block_ids)
            annotations.extend(allrows(con,f"SELECT id,thread_id FROM annotations WHERE project_id=? AND block_id IN ({marks})",[pid,*block_ids]))
        if annotations:
            annotation_ids=sorted({int(r['id']) for r in annotations})
            candidate_threads=sorted({int(r['thread_id']) for r in annotations if r.get('thread_id') is not None})
            am=','.join('?' for _ in annotation_ids)
            for tid in candidate_threads:
                outside=one(con,f"SELECT id FROM annotations WHERE project_id=? AND thread_id=? AND id NOT IN ({am}) LIMIT 1",[pid,tid,*annotation_ids])
                if not outside: thread_ids.append(tid)

        _detach_map_anchors_for_removed_manuscript(con,pid,chapter_ids=chapter_ids,scene_ids=scene_ids,block_ids=block_ids)
        _delete_polymorphic_references(con,pid,{'document':(did,),'chapter':chapter_ids,'scene':scene_ids,'annotation':annotation_ids})
        if block_ids:
            marks=','.join('?' for _ in block_ids)
            con.execute(f"DELETE FROM references_map WHERE project_id=? AND (start_block_id IN ({marks}) OR end_block_id IN ({marks}))",[pid,*block_ids,*block_ids])
        if thread_ids:
            marks=','.join('?' for _ in thread_ids)
            con.execute(f"DELETE FROM comment_messages WHERE project_id=? AND thread_id IN ({marks})",[pid,*thread_ids])
            con.execute(f"DELETE FROM comment_threads WHERE project_id=? AND id IN ({marks})",[pid,*thread_ids])

        if chapter_ids:
            marks=','.join('?' for _ in chapter_ids); con.execute(f"DELETE FROM chapters WHERE project_id=? AND id IN ({marks})",[pid,*chapter_ids])
        # Scenes are explicitly removed even though deleting the tab would cascade;
        # doing it here keeps the operation's affected rows obvious to the journal.
        if scene_ids:
            marks=','.join('?' for _ in scene_ids); con.execute(f"DELETE FROM scenes WHERE project_id=? AND id IN ({marks})",[pid,*scene_ids])
        con.execute("DELETE FROM document_tabs WHERE project_id=? AND id=?",(pid,did))

        remaining=allrows(con,"SELECT id,is_manuscript FROM document_tabs WHERE project_id=? ORDER BY position,id",(pid,))
        if doc.get('is_manuscript'):
            con.execute("UPDATE document_tabs SET is_manuscript=0 WHERE project_id=?",(pid,))
            con.execute("UPDATE document_tabs SET is_manuscript=1,updated_at=? WHERE id=?",(storage_values.now(),remaining[0]['id']))
        for i,row in enumerate(remaining): con.execute("UPDATE document_tabs SET position=?,updated_at=? WHERE id=?",(i,storage_values.now(),row['id']))
        repair_orphaned_bookmark(con,pid,next_document_id)
        next_chapter=one(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id LIMIT 1",(pid,next_document_id))
        con.execute("UPDATE projects SET updated_at=? WHERE id=?",(storage_values.now(),pid)); storage_invariants.validate_project_invariants(con,pid)
    commit(f"Deleted document: {doc['title']}","structure"); sync_developmental_canvases(pid)
    return {"ok":True,"next_document_id":next_document_id,"next_chapter_id":next_chapter['id'] if next_chapter else None}

