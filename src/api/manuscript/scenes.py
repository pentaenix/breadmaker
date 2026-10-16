from __future__ import annotations

"""Scene ownership, range mutation, manuscript projection, and Scene movement routes."""

from fastapi import APIRouter, HTTPException

from api.manuscript import anchors as manuscript_anchors
from api.manuscript import images as manuscript_images
from backend.dependencies import feature_call
from api.manuscript.contracts import SceneBlocksUpdate, SceneCreate, SceneMove, SceneRangeUpdate, SceneUpdate
from backend.content import VALID_BLOCK_TYPES, VALID_SEMANTIC_STYLES, sanitize_inline_html
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from backend.history_runtime import commit
from security import content_security
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import values as storage_values

_cut_chapter_label = feature_call('durability.cuts', '_cut_chapter_label')
_detach_map_anchors_for_removed_manuscript = feature_call('maps.anchors', '_detach_map_anchors_for_removed_manuscript')
_maybe_external_backup = feature_call('durability.backups', '_maybe_external_backup')
sync_developmental_canvases = feature_call('canvas.developmental', 'sync_developmental_canvases')

router = APIRouter()

def _next_scene_title(con,pid:int,prefix:str="Scene")->str:
    next_id=int(con.execute("SELECT COALESCE(MAX(id),0)+1 FROM scenes").fetchone()[0])
    safe_prefix=(prefix or "Scene").strip()[:80] or "Scene"
    return f"{safe_prefix} {next_id}"

def _validate_scene_blocks(con,pid:int,block_ids:list[str], *, exclude_scene_id:int|None=None)->list[dict]:
    ids=list(dict.fromkeys(str(x) for x in block_ids if x))
    if not ids: return []
    q=','.join('?' for _ in ids)
    rows=allrows(con,f"SELECT b.*,c.position chapter_position,t.position tab_position FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id JOIN document_tabs t ON t.id=b.tab_id WHERE b.id IN ({q})",ids)
    if len(rows)!=len(ids): raise HTTPException(400,"One or more manuscript blocks no longer exist")
    if any(r['project_id']!=pid for r in rows): raise HTTPException(400,"Scene prose must belong to this project")
    tabs={r['tab_id'] for r in rows}
    if len(tabs)>1: raise HTTPException(400,"A Scene can cross Chapters, but not separate source-document tabs")
    tab_id=next(iter(tabs))
    ordered=[r['id'] for r in allrows(con,"SELECT b.id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.project_id=? AND b.tab_id=? ORDER BY c.position,b.position,b.id",(pid,tab_id))]
    chosen=set(ids); indices=[i for i,bid in enumerate(ordered) if bid in chosen]
    if not indices or set(ordered[min(indices):max(indices)+1])!=chosen:
        raise HTTPException(400,"A Scene must cover one contiguous range of manuscript blocks")
    occupied={int(r['scene_id']) for r in rows if r.get('scene_id') is not None and int(r['scene_id'])!=int(exclude_scene_id or 0)}
    if occupied: raise HTTPException(409,"That prose already belongs to another Scene")
    rows.sort(key=lambda r:(r['tab_position'],r['chapter_position'],r['position'],r['id']))
    return rows

def _next_scene_position(con,pid:int,tab_id:int)->float:
    return float(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM scenes WHERE project_id=? AND tab_id=?",(pid,tab_id)).fetchone()[0])

def _sync_scene_outline_to_manuscript(con,pid:int,tab_id:int)->None:
    """Keep Scene outline placement aligned with manuscript placement.

    Scene titles/IDs preserve creation identity, but the navigator is spatial: a
    Scene created later inside an earlier unassigned gap must appear where its
    prose actually lives, not at the end of the Scene list.
    """
    ordered=[]; seen=set()
    for row in allrows(con,"""SELECT b.scene_id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id
        WHERE b.project_id=? AND b.tab_id=? AND b.scene_id IS NOT NULL
        ORDER BY c.position,b.position,b.id""",(pid,tab_id)):
        sid=int(row['scene_id'])
        if sid not in seen:
            seen.add(sid); ordered.append(sid)
    # Detached Scenes are not part of the current Manuscript model, but retain a
    # deterministic tail position if defensive/imported data ever contains one.
    for row in allrows(con,"SELECT id FROM scenes WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab_id)):
        sid=int(row['id'])
        if sid not in seen:
            seen.add(sid); ordered.append(sid)
    for position,sid in enumerate(ordered):
        con.execute("UPDATE scenes SET position=? WHERE id=?",(position,sid))

def _ensure_implicit_tab_chapter(con,pid:int,tab_id:int,*,after_chapter_id:int|None=None)->dict:
    """Keep a Tab writable without forcing a visible Chapter on the writer.

    When this container replaces a deleted Chapter, allocate above that retired ID.
    SQLite INTEGER PRIMARY KEY may otherwise reuse the just-deleted maximum ID, which
    would make retained Checkpoints/Cuts mistake the hidden container for their old source.
    """
    existing=one(con,"SELECT * FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id LIMIT 1",(pid,tab_id))
    if existing: return existing
    tab=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(tab_id,pid))
    if not tab: raise HTTPException(404,"Source tab not found")
    t=storage_values.now(); created=tab.get('created_at') or t; title=(tab.get('title') or 'Manuscript')[:300]
    if after_chapter_id is not None:
        floor=max(int(after_chapter_id),int(con.execute("SELECT COALESCE(MAX(id),0) FROM chapters").fetchone()[0] or 0))
        cid=floor+1
        con.execute("INSERT INTO chapters(id,project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(cid,pid,tab_id,0,title,'',1,'','',created,t))
    else:
        cid=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(pid,tab_id,0,title,'',1,'','',created,t)).lastrowid
    bid=storage_values.uid('b_')
    con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,tab_id,cid,None,0,'paragraph','body','','','','',t,t))
    return one(con,"SELECT * FROM chapters WHERE id=?",(cid,))

@router.post("/api/scenes")
def create_scene(body:SceneCreate):
    """Create a real manuscript Scene.

    Selecting prose assigns that contiguous range immediately. Creating from the
    Scenes navigator (no block_ids) creates one blank paragraph at the end of the
    requested tab and assigns it to the new Scene, so Scenes never exist as detached
    planning placeholders inside Manuscript.
    """
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; rows=_validate_scene_blocks(con,pid,body.block_ids)
        created_blank=False
        anchor_scene=None
        if rows:
            tab_id=int(rows[0]['tab_id'])
        else:
            if body.after_scene_id is not None:
                anchor_scene=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(int(body.after_scene_id),pid))
                if not anchor_scene: raise HTTPException(404,"Scene to insert after was not found")
                if body.tab_id is not None and int(body.tab_id)!=int(anchor_scene['tab_id']): raise HTTPException(409,"A Scene can only be inserted beside a Scene in the same Tab")
                tab=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(int(anchor_scene['tab_id']),pid))
            elif body.tab_id is not None:
                tab=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(int(body.tab_id),pid))
            else:
                tab=one(con,"SELECT * FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(pid,)) or one(con,"SELECT * FROM document_tabs WHERE project_id=? ORDER BY position,id LIMIT 1",(pid,))
            if not tab: raise HTTPException(409,"Create a tab before creating a Scene")
            tab_id=int(tab['id'])
            if anchor_scene:
                last=one(con,"""SELECT b.* FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id
                    WHERE b.project_id=? AND b.scene_id=? ORDER BY c.position DESC,b.position DESC,b.id DESC LIMIT 1""",(pid,anchor_scene['id']))
                if not last: raise HTTPException(409,"The selected Scene no longer has manuscript prose")
                chapter=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(last['chapter_id'],pid))
                t=storage_values.now(); pos=float(last['position'])+1
                con.execute("UPDATE manuscript_blocks SET position=position+1,updated_at=? WHERE chapter_id=? AND position>=?",(t,chapter['id'],pos))
                bid=storage_values.uid('b_')
                con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,tab_id,chapter['id'],None,pos,'paragraph','body','','','','',t,t))
                con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,chapter['id']))
                rows=[one(con,"SELECT b.*,c.position chapter_position,t.position tab_position FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id JOIN document_tabs t ON t.id=b.tab_id WHERE b.id=?",(bid,))]
                created_blank=True
            else:
                chapter=one(con,"SELECT * FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position DESC,id DESC LIMIT 1",(pid,tab_id))
                if not chapter:
                    chapter=_ensure_implicit_tab_chapter(con,pid,tab_id)
                last=one(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position DESC,id DESC LIMIT 1",(chapter['id'],))
                if last and last.get('block_type')=='paragraph' and not (last.get('text_content') or '').strip() and last.get('scene_id') is None:
                    rows=[last]
                else:
                    t=storage_values.now(); bid=storage_values.uid('b_'); pos=float(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM manuscript_blocks WHERE chapter_id=?",(chapter['id'],)).fetchone()[0])
                    con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,tab_id,chapter['id'],None,pos,'paragraph','body','','','','',t,t))
                    rows=[one(con,"SELECT b.*,c.position chapter_position,t.position tab_position FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id JOIN document_tabs t ON t.id=b.tab_id WHERE b.id=?",(bid,))]
                    created_blank=True
        title=(body.title or '').strip()[:300] or _next_scene_title(con,pid,body.default_title_prefix)
        description=(body.description or '').strip()[:12000]; t=storage_values.now(); pos=_next_scene_position(con,pid,tab_id)
        sid=con.execute("INSERT INTO scenes(project_id,tab_id,position,title,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(pid,tab_id,pos,title,description,t,t)).lastrowid
        if anchor_scene: _renumber_scene_outline(con,pid,tab_id,sid,after_scene_id=int(anchor_scene['id']))
        ids=[r['id'] for r in rows]; q=','.join('?' for _ in ids)
        con.execute(f"UPDATE manuscript_blocks SET scene_id=?,updated_at=? WHERE id IN ({q})",(sid,t,*ids))
        _sync_scene_outline_to_manuscript(con,pid,tab_id)
        out=one(con,"SELECT * FROM scenes WHERE id=?",(sid,)); out['block_ids']=ids; out['created_blank']=created_blank
    commit(f"Created scene: {title}","structure"); sync_developmental_canvases(pid); return out

@router.post("/api/scenes/{sid}/extend")
def extend_scene(sid:int, body:SceneRangeUpdate):
    """Extend one Scene through a selected contiguous range.

    The selection may contain prose already in this Scene plus adjacent unassigned
    paragraphs. It may never absorb prose belonging to a different Scene, and the
    resulting Scene must remain one contiguous range inside its Tab.
    """
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; scene=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(sid,pid))
        if not scene: raise HTTPException(404,"Scene not found")
        rows=_validate_scene_blocks(con,pid,body.block_ids,exclude_scene_id=sid)
        if not rows: raise HTTPException(400,"Select manuscript prose to add to the Scene")
        tab_id=int(rows[0]['tab_id'])
        if tab_id!=int(scene.get('tab_id') or 0): raise HTTPException(409,"A Scene cannot cross separate source-document tabs")
        ordered=[r['id'] for r in allrows(con,"SELECT b.id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.project_id=? AND b.tab_id=? ORDER BY c.position,b.position,b.id",(pid,tab_id))]
        current={r['id'] for r in allrows(con,"SELECT id FROM manuscript_blocks WHERE project_id=? AND scene_id=?",(pid,sid))}
        chosen=current|{r['id'] for r in rows}; indices=[i for i,bid in enumerate(ordered) if bid in chosen]
        if not indices or set(ordered[min(indices):max(indices)+1])!=chosen:
            raise HTTPException(409,"Add prose beside the beginning or end of this Scene so it stays contiguous")
        add_ids=[r['id'] for r in rows if r.get('scene_id') is None]
        if add_ids:
            q=','.join('?' for _ in add_ids); t=storage_values.now()
            con.execute(f"UPDATE manuscript_blocks SET scene_id=?,updated_at=? WHERE id IN ({q})",(sid,t,*add_ids))
            con.execute("UPDATE scenes SET updated_at=? WHERE id=?",(t,sid)); con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid))
    if add_ids:
        commit(f"Extended scene: {scene['title']}","structure"); sync_developmental_canvases(pid)
    return {"ok":True,"scene_id":sid,"added":len(add_ids)}

@router.post("/api/scenes/{sid}/remove-blocks")
def remove_scene_blocks(sid:int, body:SceneRangeUpdate):
    """Remove selected paragraphs from one end of a Scene without splitting it."""
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; scene=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(sid,pid))
        if not scene: raise HTTPException(404,"Scene not found")
        ids=list(dict.fromkeys(str(x) for x in body.block_ids if x))
        if not ids: raise HTTPException(400,"Select Scene prose to remove")
        q=','.join('?' for _ in ids)
        selected=allrows(con,f"SELECT id,scene_id FROM manuscript_blocks WHERE project_id=? AND id IN ({q})",[pid,*ids])
        if len(selected)!=len(ids) or any(int(r.get('scene_id') or 0)!=sid for r in selected):
            raise HTTPException(409,"Remove from Scene only works on prose that belongs to this Scene")
        current=[r['id'] for r in allrows(con,"""SELECT b.id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id
            WHERE b.project_id=? AND b.scene_id=? ORDER BY c.position,b.position,b.id""",(pid,sid))]
        chosen=set(ids); indices=[i for i,bid in enumerate(current) if bid in chosen]
        if not indices or set(current[min(indices):max(indices)+1])!=chosen:
            raise HTTPException(409,"Remove one contiguous range from the Scene")
        if min(indices)!=0 and max(indices)!=len(current)-1:
            raise HTTPException(409,"Remove prose from the beginning or end of a Scene so the Scene stays contiguous")
        t=storage_values.now(); con.execute(f"UPDATE manuscript_blocks SET scene_id=NULL,updated_at=? WHERE id IN ({q})",[t,*ids])
        remaining=[bid for bid in current if bid not in chosen]; deleted=False
        if remaining:
            con.execute("UPDATE scenes SET updated_at=? WHERE id=?",(t,sid))
        else:
            _delete_polymorphic_references(con,pid,{'scene':(sid,)})
            con.execute("UPDATE map_items SET start_anchor_type=NULL,start_anchor_id=NULL,start_block_id=NULL,start_offset=0 WHERE start_anchor_type='scene' AND start_anchor_id=?",(str(sid),))
            con.execute("UPDATE map_items SET end_anchor_type=NULL,end_anchor_id=NULL,end_block_id=NULL,end_offset=0 WHERE end_anchor_type='scene' AND end_anchor_id=?",(str(sid),))
            con.execute("DELETE FROM scenes WHERE id=?",(sid,)); deleted=True
        con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid))
    commit((f"Removed prose from scene: {scene['title']}" if not deleted else f"Removed scene: {scene['title']}"),"structure"); sync_developmental_canvases(pid)
    return {"ok":True,"scene_id":sid,"removed":len(ids),"scene_deleted":deleted}

@router.patch("/api/scenes/{sid}")
def update_scene(sid:int,body:SceneUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'title' in vals:
        vals['title']=(vals['title'] or '').strip()[:300]
        if not vals['title']: raise HTTPException(400,"Scene name cannot be empty")
    if 'description' in vals: vals['description']=(vals['description'] or '').strip()[:12000]
    with storage_mutations.tx() as con:
        p=project(con); current=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(sid,p['id']))
        if not current: raise HTTPException(404,"Scene not found")
        if vals:
            title=vals.get('title',current['title']); description=vals.get('description',current.get('description',''))
            con.execute("UPDATE scenes SET title=?,description=?,updated_at=? WHERE id=?",(title,description,storage_values.now(),sid))
    label=vals.get('title',current['title']); commit(f"Updated scene: {label}"); sync_developmental_canvases(p['id']); return {"ok":True}

@router.delete("/api/scenes/{sid}")
def delete_scene(sid:int):
    with storage_mutations.tx() as con:
        p=project(con); scene=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(sid,p['id']))
        if not scene: raise HTTPException(404,"Scene not found")
        _delete_polymorphic_references(con,p['id'],{'scene':(sid,)})
        # Map anchors may point to a Scene even when it has no manuscript prose.
        con.execute("UPDATE map_items SET start_anchor_type=NULL,start_anchor_id=NULL,start_block_id=NULL,start_offset=0 WHERE start_anchor_type='scene' AND start_anchor_id=?",(str(sid),))
        con.execute("UPDATE map_items SET end_anchor_type=NULL,end_anchor_id=NULL,end_block_id=NULL,end_offset=0 WHERE end_anchor_type='scene' AND end_anchor_id=?",(str(sid),))
        con.execute("DELETE FROM scenes WHERE id=?",(sid,))
    commit(f"Deleted scene: {scene['title']}","structure"); sync_developmental_canvases(p['id']); return {"ok":True}

@router.post("/api/scenes/{sid}/cut")
def cut_scene(sid:int):
    """Move every manuscript block in a Scene to Cuts and remove the Scene.

    Unlike deleting a Scene, Cut removes its prose. Chapters remain in place; an
    emptied Chapter receives one blank unassigned paragraph so it stays writable.
    """
    commit("Before cutting scene","safety")
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; scene=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(sid,pid))
        if not scene: raise HTTPException(404,"Scene not found")
        blocks=allrows(con,"""SELECT b.*,c.position chapter_position,CASE WHEN c.is_implicit=1 THEN '' ELSE c.title END chapter_title FROM manuscript_blocks b
            JOIN chapters c ON c.id=b.chapter_id WHERE b.project_id=? AND b.scene_id=? ORDER BY c.position,b.position,b.id""",(pid,sid))
        if not blocks: raise HTTPException(409,"Scene has no manuscript contents")
        block_ids=[b['id'] for b in blocks]; chapter_ids=sorted({int(b['chapter_id']) for b in blocks})
        first_chapter_id=int(blocks[0]['chapter_id']); cut_html=''.join(f"<p>{b['content_html']}</p>" if b['block_type']=='paragraph' else _cut_image_figure_html(b) for b in blocks)
        marks=','.join('?' for _ in block_ids); annotation_ids=[r['id'] for r in allrows(con,f"SELECT id FROM annotations WHERE block_id IN ({marks})",block_ids)]
        _detach_map_anchors_for_removed_manuscript(con,pid,scene_ids=(sid,),block_ids=block_ids)
        _delete_polymorphic_references(con,pid,{'scene':(sid,),'annotation':annotation_ids})
        con.execute(f"DELETE FROM references_map WHERE project_id=? AND (start_block_id IN ({marks}) OR end_block_id IN ({marks}))",[pid,*block_ids,*block_ids])
        source_project=str(p.get('title') or 'Project').strip()[:240]
        ch=one(con,"SELECT id,project_id,tab_id,position,title FROM chapters WHERE id=? AND project_id=?",(first_chapter_id,pid)); source_chapter=_cut_chapter_label(con,ch)[:240] if ch else ''
        source_scene=str(scene.get('title') or '').strip()[:240]
        title=(f"Cut: {scene['title']}" if scene.get('title') else 'Cut Scene')[:240]
        con.execute("INSERT INTO cuts(project_id,title,content_html,source_chapter_id,created_at,note,source_label,source_project,source_chapter,source_scene) VALUES(?,?,?,?,?,?,?,?,?,?)",(pid,title,cut_html,first_chapter_id,storage_values.now(),'','Scene: '+source_scene,source_project,source_chapter,source_scene))
        con.execute(f"DELETE FROM manuscript_blocks WHERE project_id=? AND id IN ({marks})",[pid,*block_ids])
        con.execute("DELETE FROM scenes WHERE id=? AND project_id=?",(sid,pid))
        t=storage_values.now()
        for chapter_id in chapter_ids:
            rows=allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(chapter_id,))
            if not rows:
                chapter=one(con,"SELECT tab_id FROM chapters WHERE id=? AND project_id=?",(chapter_id,pid))
                if not chapter: continue
                bid=storage_values.uid('b_'); con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,chapter['tab_id'],chapter_id,None,0,'paragraph','body','','','','',t,t))
            else:
                for i,row in enumerate(rows): con.execute("UPDATE manuscript_blocks SET position=?,updated_at=? WHERE id=?",(i,t,row['id']))
            con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,chapter_id))
        manuscript_anchors.repair_orphaned_bookmark(con,pid,int(scene['tab_id']))
        con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid)); storage_invariants.validate_project_invariants(con,pid)
    commit(f"Cut scene: {scene['title']}","structure"); sync_developmental_canvases(pid); return {"ok":True}


@router.get("/api/scenes/{sid}/manuscript")
def get_scene_manuscript(sid:int):
    with storage_mutations.tx() as con:
        p=project(con); scene=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(sid,p['id']))
        if not scene: raise HTTPException(404,"Scene not found")
        blocks=allrows(con,"""SELECT b.* FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id JOIN document_tabs t ON t.id=b.tab_id
            WHERE b.project_id=? AND b.scene_id=? ORDER BY t.position,c.position,b.position,b.id""",(p['id'],sid))
        ids=[b['id'] for b in blocks]; anns=[]
        if ids:
            q=','.join('?' for _ in ids)
            anns=allrows(con,f"SELECT a.*,l.name label_name,l.color label_color FROM annotations a LEFT JOIN markup_labels l ON l.id=a.label_id WHERE a.block_id IN ({q}) ORDER BY a.id",ids)
        revisions={str(cid):storage_values.chapter_revision(con,int(cid)) for cid in sorted({int(b['chapter_id']) for b in blocks})}
        return {"scene":scene,"blocks":blocks,"annotations":anns,"chapter_revisions":revisions}

@router.put("/api/scenes/{sid}/blocks")
def save_scene_blocks(sid:int, body:SceneBlocksUpdate):
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; scene=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(sid,pid))
        if not scene: raise HTTPException(404,"Scene not found")
        existing_rows=allrows(con,"SELECT * FROM manuscript_blocks WHERE project_id=? AND scene_id=?",(pid,sid)); existing={r['id']:r for r in existing_rows}
        incoming=body.blocks or []
        if not incoming: raise HTTPException(400,"A Scene must contain at least one manuscript block")
        incoming_ids=[b.id for b in incoming if b.id]
        if any(bid not in existing for bid in incoming_ids): raise HTTPException(400,"Scene View can only edit blocks that belong to this Scene")
        chapter_ids={int(b.chapter_id) for b in incoming}|{int(r['chapter_id']) for r in existing_rows}
        for chapter_id in chapter_ids:
            expected=body.expected_revisions.get(str(chapter_id)) if body.expected_revisions else None
            current=storage_values.chapter_revision(con,chapter_id)
            if expected and expected!=current:
                raise HTTPException(409,detail={"code":"MANUSCRIPT_CONFLICT_001","message":"This manuscript changed after this view was loaded. Your text was not overwritten.","params":{"chapter_id":chapter_id,"current_revision":current}})
        chapters={}
        if chapter_ids:
            q=','.join('?' for _ in chapter_ids); rows=allrows(con,f"SELECT * FROM chapters WHERE project_id=? AND id IN ({q})",[pid,*sorted(chapter_ids)])
            chapters={r['id']:r for r in rows}
            if set(chapter_ids)!=set(chapters): raise HTTPException(400,"One or more Scene chapters no longer exist")
        tabs={r['tab_id'] for r in chapters.values()}
        if len(tabs)>1: raise HTTPException(400,"A Scene can cross Chapters, but not source-document tabs")
        if tabs and int(next(iter(tabs)))!=int(scene.get('tab_id') or 0): raise HTTPException(409,"Scene View cannot move a Scene into another tab")
        existing_scene_chapters={int(r['chapter_id']) for r in existing_rows}
        t=storage_values.now(); incoming_by_chapter={cid:[] for cid in chapter_ids}; result_ids=[]; prepared={}
        for b in incoming:
            cid=int(b.chapter_id)
            if b.id and b.id in existing and int(existing[b.id]['chapter_id'])!=cid:
                raise HTTPException(409,"Scene View edits prose in place; move the Scene itself to change Chapter placement")
            if (not b.id or b.id not in existing) and existing_scene_chapters and cid not in existing_scene_chapters:
                raise HTTPException(409,"New Scene paragraphs must stay inside the Scene's existing Chapter span")
            block_type=b.block_type if b.block_type in VALID_BLOCK_TYPES else 'paragraph'; style=b.semantic_style if b.semantic_style in VALID_SEMANTIC_STYLES else 'body'
            if block_type=='image': style='image'
            txt=b.text_content if block_type=='paragraph' else ''; safe=sanitize_inline_html(b.content_html,txt) if block_type=='paragraph' else ''; image=b.image_path if content_security.is_safe_project_asset_path(b.image_path or '') else ''
            image_width=max(5,min(100,int(b.image_width or 100))) if block_type=='image' else 100; image_footer=(b.image_footer or '').strip()[:4000] if block_type=='image' else ''; image_description=(b.image_description or '').strip()[:12000] if block_type=='image' else ''; image_row_id=(b.image_row_id or '').strip()[:80] if block_type=='image' else ''
            bid=b.id if b.id and b.id in existing else storage_values.uid('b_'); result_ids.append(bid); incoming_by_chapter.setdefault(cid,[]).append(bid); prepared[bid]=(b,block_type,style,txt,safe,image,image_width,image_footer,image_description,image_row_id)
        keep=set(result_ids)
        for cid in chapter_ids:
            chapter=chapters[cid]; all_blocks=allrows(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(cid,)); old_scene=[r for r in all_blocks if int(r.get('scene_id') or 0)==sid]; repl=incoming_by_chapter.get(cid,[])
            insert_at=next((i for i,r in enumerate(all_blocks) if int(r.get('scene_id') or 0)==sid),len(all_blocks))
            base=[r['id'] for r in all_blocks if int(r.get('scene_id') or 0)!=sid]; insert_at=sum(1 for r in all_blocks[:insert_at] if int(r.get('scene_id') or 0)!=sid); final_ids=base[:insert_at]+repl+base[insert_at:]
            for bid in repl:
                b,bt,style,txt,safe,image,image_width,image_footer,image_description,image_row_id=prepared[bid]; h=storage_values.block_content_hash(safe,txt,image,image_width,image_footer,image_description,image_row_id)
                if bid in existing:
                    manuscript_anchors.reanchor_block_annotations(con,bid,txt); manuscript_anchors.reanchor_block_bookmark(con,bid,txt); con.execute("UPDATE manuscript_blocks SET chapter_id=?,tab_id=?,scene_id=?,block_type=?,semantic_style=?,content_html=?,text_content=?,image_path=?,image_width=?,image_footer=?,image_description=?,image_row_id=?,content_hash=?,updated_at=? WHERE id=?",(cid,chapter['tab_id'],sid,bt,style,safe,txt,image,image_width,image_footer,image_description,image_row_id,h,t,bid))
                else:
                    con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,chapter['tab_id'],cid,sid,0,bt,style,safe,txt,image,image_width,image_footer,image_description,image_row_id,h,t,t))
            for row in old_scene:
                if row['id'] not in keep:
                    manuscript_anchors.rehome_deleted_block_refs(con,row,cid); con.execute("DELETE FROM manuscript_blocks WHERE id=?",(row['id'],))
            for pos,bid in enumerate(final_ids): con.execute("UPDATE manuscript_blocks SET position=? WHERE id=?",(pos,bid))
            manuscript_anchors.repair_stale_annotations(con,cid); manuscript_anchors.repair_stale_bookmark(con,cid); manuscript_images._normalize_image_rows(con,cid,t); con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,cid))
        con.execute("UPDATE scenes SET updated_at=? WHERE id=?",(t,sid)); con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid)); storage_invariants.validate_project_invariants(con,pid)
        revisions={str(cid):storage_values.chapter_revision(con,int(cid)) for cid in chapter_ids}
    _maybe_external_backup('scene_save')
    return {"ok":True,"ids":result_ids,"chapter_revisions":revisions}

def _renumber_scene_outline(con,pid:int,tab_id:int,sid:int,before_scene_id:int|None=None,after_scene_id:int|None=None):
    ids=[r['id'] for r in allrows(con,"SELECT id FROM scenes WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab_id))]
    if sid not in ids: return
    ids.remove(sid)
    if before_scene_id in ids: ids.insert(ids.index(before_scene_id),sid)
    elif after_scene_id in ids: ids.insert(ids.index(after_scene_id)+1,sid)
    else: ids.append(sid)
    for i,x in enumerate(ids): con.execute("UPDATE scenes SET position=? WHERE id=?",(i,x))

@router.post("/api/scenes/{sid}/move")
def move_scene(sid:int, body:SceneMove):
    commit("Before moving scene","safety")
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; scene=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(sid,pid))
        if not scene: raise HTTPException(404,"Scene not found")
        if body.before_scene_id==sid or body.after_scene_id==sid: return {"ok":True}
        for neighbor_id in (body.before_scene_id,body.after_scene_id):
            if neighbor_id:
                neighbor=one(con,"SELECT tab_id FROM scenes WHERE id=? AND project_id=?",(neighbor_id,pid))
                if not neighbor or int(neighbor.get('tab_id') or 0)!=int(scene.get('tab_id') or 0): raise HTTPException(409,"Scenes can only be reordered inside the same tab")
        source=allrows(con,"""SELECT b.* FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id JOIN document_tabs t ON t.id=b.tab_id
            WHERE b.project_id=? AND b.scene_id=? ORDER BY t.position,c.position,b.position,b.id""",(pid,sid))
        target=None
        if body.target_block_id:
            target=one(con,"SELECT * FROM manuscript_blocks WHERE id=? AND project_id=?",(body.target_block_id,pid))
            if not target: raise HTTPException(404,"Drop target no longer exists")
            if int(target.get('scene_id') or 0)==sid: return {"ok":True}
        elif body.before_scene_id:
            target=one(con,"""SELECT b.* FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id JOIN document_tabs t ON t.id=b.tab_id
                WHERE b.project_id=? AND b.scene_id=? ORDER BY t.position,c.position,b.position,b.id LIMIT 1""",(pid,body.before_scene_id))
        if source and not target:
            raise HTTPException(409,"Move a written Scene beside written prose or an unassigned gap")
        if source and target:
            source_tab=source[0]['tab_id']
            if any(r['tab_id']!=source_tab for r in source) or target['tab_id']!=source_tab:
                raise HTTPException(409,"Written Scenes can only be reordered inside the same source tab")
            source_ids=[r['id'] for r in source]; source_chapters={int(r['chapter_id']) for r in source}; target_cid=int(target['chapter_id']); target_ch=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(target_cid,pid)); t=storage_values.now()
            # Remove the Scene from every source Chapter first. Chapter headings themselves never move.
            for cid in source_chapters:
                remain=[r['id'] for r in allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? AND (scene_id IS NULL OR scene_id<>?) ORDER BY position,id",(cid,sid))]
                for pos,bid in enumerate(remain): con.execute("UPDATE manuscript_blocks SET position=? WHERE id=?",(pos,bid))
            target_rows=[r['id'] for r in allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? AND (scene_id IS NULL OR scene_id<>?) ORDER BY position,id",(target_cid,sid))]
            try: at=target_rows.index(target['id'])
            except ValueError: at=len(target_rows)
            final_ids=target_rows[:at]+source_ids+target_rows[at:]
            for pos,bid in enumerate(final_ids):
                if bid in source_ids: con.execute("UPDATE manuscript_blocks SET tab_id=?,chapter_id=?,position=?,updated_at=? WHERE id=?",(target_ch['tab_id'],target_cid,pos,t,bid))
                else: con.execute("UPDATE manuscript_blocks SET position=? WHERE id=?",(pos,bid))
            for cid in source_chapters|{target_cid}: con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,cid))
            con.execute("UPDATE scenes SET updated_at=? WHERE id=?",(t,sid))
        _renumber_scene_outline(con,pid,int(scene.get('tab_id') or 0),sid,body.before_scene_id,body.after_scene_id)
        storage_invariants.validate_project_invariants(con,pid)
    commit(f"Moved scene: {scene['title']}","structure"); sync_developmental_canvases(pid); return {"ok":True}
