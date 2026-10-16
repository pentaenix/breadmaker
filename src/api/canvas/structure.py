from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.dependencies import feature_call
from security import content_security
import json
from storage import database as storage_database
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import values as storage_values
from api.canvas.contracts import CanvasCreate, CanvasNodeBulkDelete, CanvasNodePositionBatch, CanvasPopulateRequest, CanvasUpdate, CanvasVersionCreate, CanvasVersionUpdate, DevelopmentalApply, DevelopmentalLayout, DevelopmentalPromote, EdgeCreate, EdgeUpdate, NodeCreate, NodeUpdate
from api.shared_contracts import Reorder
from backend.content import (
    CONTENT_CARD_SHAPES,
    DEVELOPMENTAL_FREE_Y,
    DEVELOPMENTAL_LANE_START_X,
    FIGURE_NODE_SHAPES,
    VALID_CANVAS_TYPES,
    VALID_NODE_SHAPES,
    VALID_NODE_TYPES,
    normalize_color,
)
from backend.data_access import (
    _delete_polymorphic_references,
    allrows,
    one,
    project,
)
from backend.environment import STORY_TEMPLATES_CONFIG
from backend.history_runtime import commit

from . import developmental

_sync_scene_outline_to_manuscript = feature_call('manuscript.scenes', '_sync_scene_outline_to_manuscript')

router = APIRouter()

def move_scene_tx(con,sid:int,target_chapter_id:int,before_scene_id:int|None=None):
    """Move one written Scene inside an existing structural transaction.

    Developmental Canvas Apply needs the same manuscript invariant as the public
    Scene move endpoint without opening a nested transaction or History commit.
    Chapter headers stay fixed; only Scene-owned prose moves.
    """
    scene=one(con,"SELECT * FROM scenes WHERE id=?",(sid,))
    if not scene:
        raise HTTPException(404,"Scene not found")
    pid=int(scene['project_id'])
    target=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(target_chapter_id,pid))
    if not target:
        raise HTTPException(404,"Target Chapter no longer exists")
    if int(target.get('tab_id') or 0)!=int(scene.get('tab_id') or 0):
        raise HTTPException(409,"Written Scenes can only move between Chapters in the same source tab")
    source=allrows(con,"""SELECT b.* FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id
        WHERE b.project_id=? AND b.scene_id=? ORDER BY c.position,b.position,b.id""",(pid,sid))
    if not source:
        raise HTTPException(409,"Scene has no manuscript contents")
    source_ids=[str(r['id']) for r in source]
    source_set=set(source_ids); source_chapters={int(r['chapter_id']) for r in source}
    t=storage_values.now()

    # Remove Scene blocks from their old Chapter positions first. The blocks themselves
    # keep stable IDs, annotations, links, Bookmarks, and Map anchors.
    for cid in source_chapters:
        remain=[r['id'] for r in allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(cid,)) if str(r['id']) not in source_set]
        for pos,bid in enumerate(remain):
            con.execute("UPDATE manuscript_blocks SET position=?,updated_at=? WHERE id=?",(pos,t,bid))

    target_rows=[r['id'] for r in allrows(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(target_chapter_id,)) if str(r['id']) not in source_set]
    insert_at=len(target_rows)
    if before_scene_id:
        before=one(con,"""SELECT b.id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id
            WHERE b.project_id=? AND b.scene_id=? AND b.chapter_id=? ORDER BY b.position,b.id LIMIT 1""",(pid,before_scene_id,target_chapter_id))
        if before and before['id'] in target_rows:
            insert_at=target_rows.index(before['id'])
    final_ids=target_rows[:insert_at]+source_ids+target_rows[insert_at:]
    for pos,bid in enumerate(final_ids):
        if bid in source_set:
            con.execute("UPDATE manuscript_blocks SET tab_id=?,chapter_id=?,position=?,updated_at=? WHERE id=?",(target['tab_id'],target_chapter_id,pos,t,bid))
        else:
            con.execute("UPDATE manuscript_blocks SET position=?,updated_at=? WHERE id=?",(pos,t,bid))

    # A Chapter remains a writable structural object even when its last Scene moved out.
    for cid in source_chapters-{int(target_chapter_id)}:
        if not one(con,"SELECT id FROM manuscript_blocks WHERE chapter_id=? LIMIT 1",(cid,)):
            ch=one(con,"SELECT tab_id FROM chapters WHERE id=? AND project_id=?",(cid,pid))
            if ch:
                bid=storage_values.uid('b_')
                con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,ch['tab_id'],cid,None,0,'paragraph','body','','','','',t,t))
    for cid in source_chapters|{int(target_chapter_id)}:
        con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,cid))
    con.execute("UPDATE scenes SET updated_at=? WHERE id=?",(t,sid))
    con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid))


def _developmental_manuscript_tab(con,pid:int):
    row=con.execute("SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(pid,)).fetchone()
    return int(row[0]) if row else None


def _developmental_structure_snapshot(con,pid:int,tab_id:int):
    """Return the writer-visible Chapter/Scene order in the Main Manuscript.

    A Scene is shown beneath the Chapter containing its first written block.  This is
    also the structural location a Developmental Canvas changes when Apply runs.
    Planned Scenes without prose are intentionally absent because applying the Canvas
    cannot move manuscript content that does not exist yet.
    """
    chapters=allrows(con,"SELECT id,title FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab_id))
    scene_titles={int(r['id']):r['title'] for r in allrows(con,"SELECT id,title FROM scenes WHERE project_id=? AND tab_id=?",(pid,tab_id))}
    by_chapter={int(ch['id']):[] for ch in chapters}; seen=set()
    blocks=allrows(con,"""SELECT b.scene_id,b.chapter_id FROM manuscript_blocks b
        JOIN chapters c ON c.id=b.chapter_id
        WHERE b.project_id=? AND b.tab_id=? ORDER BY c.position,b.position,b.id""",(pid,tab_id))
    for block in blocks:
        sid=block.get('scene_id')
        if not sid or int(sid) in seen: continue
        sid=int(sid); seen.add(sid)
        by_chapter.setdefault(int(block['chapter_id']),[]).append({'id':sid,'title':scene_titles.get(sid,f'Scene {sid}')})
    return {'chapters':[{'id':int(ch['id']),'title':ch['title'],'scenes':by_chapter.get(int(ch['id']),[])} for ch in chapters]}


def _developmental_snapshot_positions(snapshot):
    positions={}; index=0
    for chapter in snapshot.get('chapters') or []:
        positions[f"chapter:{chapter['id']}"]=(index,None); index+=1
        for scene in chapter.get('scenes') or []:
            positions[f"scene:{scene['id']}"]=(index,int(chapter['id'])); index+=1
    return positions


def _apply_developmental_structure_tx(con,p,ordered_node_ids:list[int]):
    """Execute one Developmental Canvas order using its explicit structural scope."""
    if not ordered_node_ids or len(ordered_node_ids)!=len(set(ordered_node_ids)):
        raise HTTPException(400,"Developmental order is empty or contains duplicate Cards")
    q=','.join('?' for _ in ordered_node_ids)
    nodes=allrows(con,f"SELECT n.*,c.canvas_type,c.developmental_scope,c.project_id canvas_project_id FROM canvas_nodes n JOIN canvases c ON c.id=n.canvas_id WHERE n.id IN ({q})",ordered_node_ids)
    if len(nodes)!=len(ordered_node_ids): raise HTTPException(400,"One or more Canvas Cards no longer exist")
    by_id={n['id']:n for n in nodes}; nodes=[by_id[nid] for nid in ordered_node_ids]
    canvas_ids={n['canvas_id'] for n in nodes}
    if len(canvas_ids)!=1 or any(n['canvas_type']!='developmental' or n['canvas_project_id']!=p['id'] for n in nodes):
        raise HTTPException(400,"Structure can only be applied from one Developmental Canvas")
    scope=(nodes[0].get('developmental_scope') or 'chapters').lower()
    manuscript_tab=_developmental_manuscript_tab(con,p['id'])
    if not manuscript_tab: raise HTTPException(400,"Primary manuscript tab is missing")
    if scope=='scenes':
        ordered=[int(n['scene_id']) for n in nodes if n['node_type']=='scene' and n['scene_id']]
        current=allrows(con,"""SELECT s.id,(SELECT b.chapter_id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.scene_id=s.id AND b.tab_id=? ORDER BY c.position,b.position,b.id LIMIT 1) chapter_id
            FROM scenes s WHERE s.project_id=? AND EXISTS(SELECT 1 FROM manuscript_blocks bx WHERE bx.scene_id=s.id AND bx.tab_id=?)
            ORDER BY (SELECT c2.position FROM manuscript_blocks b2 JOIN chapters c2 ON c2.id=b2.chapter_id WHERE b2.scene_id=s.id AND b2.tab_id=? ORDER BY c2.position,b2.position,b2.id LIMIT 1),
                     (SELECT b3.position FROM manuscript_blocks b3 JOIN chapters c3 ON c3.id=b3.chapter_id WHERE b3.scene_id=s.id AND b3.tab_id=? ORDER BY c3.position,b3.position,b3.id LIMIT 1),s.id""",(manuscript_tab,p['id'],manuscript_tab,manuscript_tab,manuscript_tab))
        expected=[int(r['id']) for r in current]
        if len(ordered)!=len(set(ordered)) or set(ordered)!=set(expected):
            raise HTTPException(400,"Apply requires each written Main Manuscript Scene exactly once on this Developmental Canvas")
        slot_chapters=[int(r['chapter_id']) for r in current]
        desired_by_chapter={}
        for sid,cid in zip(ordered,slot_chapters): desired_by_chapter.setdefault(cid,[]).append(sid)
        for cid,desired in desired_by_chapter.items():
            before=None
            for sid in reversed(desired):
                move_scene_tx(con,sid,cid,before)
                before=sid
        _sync_scene_outline_to_manuscript(con,p['id'],int(manuscript_tab))
        storage_invariants.validate_project_invariants(con,p['id'])
        return {'chapters':0,'scenes':len(ordered),'manuscript_tab_id':int(manuscript_tab),'scope':'scenes'}

    ordered=[int(n['chapter_id']) for n in nodes if n['node_type']=='chapter' and n['chapter_id']]
    expected=[int(r['id']) for r in allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? AND is_implicit=0 ORDER BY position,id",(p['id'],manuscript_tab))]
    if len(ordered)!=len(set(ordered)) or set(ordered)!=set(expected):
        raise HTTPException(400,"Apply requires each Main Manuscript Chapter exactly once on this Developmental Canvas")
    for i,cid in enumerate(ordered): con.execute("UPDATE chapters SET position=? WHERE id=?",(100000+i,cid))
    for i,cid in enumerate(ordered): con.execute("UPDATE chapters SET position=? WHERE id=?",(i,cid))
    _sync_scene_outline_to_manuscript(con,p['id'],int(manuscript_tab))
    storage_invariants.validate_project_invariants(con,p['id'])
    return {'chapters':len(ordered),'scenes':0,'manuscript_tab_id':int(manuscript_tab),'scope':'chapters'}


@router.post("/api/developmental/preflight")
def developmental_preflight(body:DevelopmentalApply):
    """Preview the exact manuscript result of Developmental Apply, then roll it back."""
    with storage_mutations.preview_tx() as con:
        p=project(con); tab_id=_developmental_manuscript_tab(con,p['id'])
        if not tab_id:
            return {'can_apply':False,'error':'Primary manuscript tab is missing','current':{'chapters':[]},'proposed':{'chapters':[]},'changed_keys':[]}
        current=_developmental_structure_snapshot(con,p['id'],tab_id)
        try:
            result=_apply_developmental_structure_tx(con,p,body.ordered_node_ids)
        except HTTPException as exc:
            return {'can_apply':False,'error':str(exc.detail),'current':current,'proposed':current,'changed_keys':[]}
        proposed=_developmental_structure_snapshot(con,p['id'],result['manuscript_tab_id'])
        before=_developmental_snapshot_positions(current); after=_developmental_snapshot_positions(proposed)
        changed=sorted(key for key in set(before)|set(after) if before.get(key)!=after.get(key))
        return {'can_apply':True,'error':'','current':current,'proposed':proposed,'changed_keys':changed,'has_changes':bool(changed),'chapters':result['chapters'],'scenes':result['scenes']}


@router.post("/api/developmental/apply")
def developmental_apply(body:DevelopmentalApply):
    commit("Before applying developmental structure","safety")
    with storage_mutations.tx() as con:
        p=project(con)
        result=_apply_developmental_structure_tx(con,p,body.ordered_node_ids)
    commit("Applied developmental structure","structure"); developmental.sync_developmental_canvases(p['id'])
    return {'ok':True,'chapters':result['chapters'],'scenes':result['scenes']}

