from __future__ import annotations

"""Chapter block persistence, dissolve/delete, and reorder routes."""

from typing import Optional
import html

from fastapi import APIRouter, HTTPException

from api.manuscript import anchors as manuscript_anchors
from api.manuscript import images as manuscript_images
from api.manuscript import scenes as manuscript_scenes
from backend.dependencies import feature_call
from api.manuscript.contracts import BlockIn, BlocksUpdate
from api.shared_contracts import Reorder
from backend.content import VALID_BLOCK_TYPES, VALID_SEMANTIC_STYLES, sanitize_inline_html
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from backend.history_runtime import commit
from security import content_security
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import values as storage_values

_cut_chapter_label = feature_call('durability.cuts', '_cut_chapter_label')
_cut_image_figure_html = feature_call('durability.cuts', '_cut_image_figure_html')
_detach_map_anchors_for_removed_manuscript = feature_call('maps.anchors', '_detach_map_anchors_for_removed_manuscript')
_maybe_external_backup = feature_call('durability.backups', '_maybe_external_backup')
sync_developmental_canvases = feature_call('canvas.developmental', 'sync_developmental_canvases')

router = APIRouter()

@router.put("/api/chapters/{cid}/blocks")
def save_blocks(cid:int,body:BlocksUpdate):
    with storage_mutations.tx() as con:
        ch=one(con,"SELECT * FROM chapters WHERE id=?",(cid,));
        if not ch: raise HTTPException(404,"Chapter not found")
        current=storage_values.chapter_revision(con,cid)
        if body.expected_revision and body.expected_revision!=current:
            raise HTTPException(409,detail={"code":"MANUSCRIPT_CONFLICT_001","message":"This Chapter changed after this view was loaded. Your text was not overwritten.","params":{"chapter_id":cid,"current_revision":current}})
        existing={r['id']:dict(r) for r in con.execute("SELECT * FROM manuscript_blocks WHERE chapter_id=?",(cid,))}
        keep=[]; t=storage_values.now(); incoming=body.blocks or [BlockIn()]
        for i,b in enumerate(incoming):
            block_type=b.block_type if b.block_type in VALID_BLOCK_TYPES else 'paragraph'
            semantic_style=b.semantic_style if b.semantic_style in VALID_SEMANTIC_STYLES else 'body'
            if block_type=='image': semantic_style='image'
            bid=b.id if b.id and b.id in existing else storage_values.uid('b_'); keep.append(bid)
            txt=b.text_content if block_type=='paragraph' else ''
            safe_html=sanitize_inline_html(b.content_html,txt) if block_type=='paragraph' else ''
            image_path=b.image_path if content_security.is_safe_project_asset_path(b.image_path or '') else ''
            image_width=max(5,min(100,int(b.image_width or 100))) if block_type=='image' else 100
            image_footer=(b.image_footer or '').strip()[:4000] if block_type=='image' else ''
            image_description=(b.image_description or '').strip()[:12000] if block_type=='image' else ''
            image_row_id=(b.image_row_id or '').strip()[:80] if block_type=='image' else ''
            h=storage_values.block_content_hash(safe_html,txt,image_path,image_width,image_footer,image_description,image_row_id)
            if bid in existing:
                manuscript_anchors.reanchor_block_annotations(con,bid,txt); manuscript_anchors.reanchor_block_bookmark(con,bid,txt)
                con.execute("UPDATE manuscript_blocks SET position=?,block_type=?,semantic_style=?,content_html=?,text_content=?,image_path=?,image_width=?,image_footer=?,image_description=?,image_row_id=?,scene_id=COALESCE(?,scene_id),content_hash=?,updated_at=? WHERE id=?",(i,block_type,semantic_style,safe_html,txt,image_path,image_width,image_footer,image_description,image_row_id,b.scene_id,h,t,bid))
            else:
                con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,ch['project_id'],ch['tab_id'],cid,b.scene_id,i,block_type,semantic_style,safe_html,txt,image_path,image_width,image_footer,image_description,image_row_id,h,t,t))
        # Rehome anchors/tasks/links before deleting a merged paragraph. Scenes are
        # manuscript ranges, so deleting the final block of a Scene deletes the Scene
        # identity too instead of leaving a zero-block planning placeholder behind.
        deleting=[oldrow for bid,oldrow in existing.items() if bid not in keep]
        deleting_ids=[row['id'] for row in deleting]
        candidate_scene_ids=sorted({int(row['scene_id']) for row in deleting if row.get('scene_id') is not None})
        removed_scene_ids=[]
        for sid in candidate_scene_ids:
            if deleting_ids:
                marks=','.join('?' for _ in deleting_ids)
                surviving=one(con,f"SELECT id FROM manuscript_blocks WHERE scene_id=? AND id NOT IN ({marks}) LIMIT 1",(sid,*deleting_ids))
            else: surviving=one(con,"SELECT id FROM manuscript_blocks WHERE scene_id=? LIMIT 1",(sid,))
            if not surviving: removed_scene_ids.append(sid)
        if removed_scene_ids:
            _detach_map_anchors_for_removed_manuscript(con,ch['project_id'],scene_ids=removed_scene_ids,block_ids=deleting_ids)
            _delete_polymorphic_references(con,ch['project_id'],{'scene':removed_scene_ids})
        for oldrow in deleting:
            manuscript_anchors.rehome_deleted_block_refs(con,oldrow,cid)
            con.execute("DELETE FROM manuscript_blocks WHERE id=?",(oldrow['id'],))
        for sid in removed_scene_ids: con.execute("DELETE FROM scenes WHERE id=?",(sid,))
        manuscript_anchors.repair_stale_annotations(con,cid); manuscript_anchors.repair_stale_bookmark(con,cid); manuscript_images._normalize_image_rows(con,cid,t)
        con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,cid)); con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,ch['project_id']))
        revision=storage_values.chapter_revision(con,cid)
    _maybe_external_backup('manuscript_save')
    return {"ok":True,"ids":keep,"revision":revision}

@router.post("/api/chapters/{cid}/dissolve")
def dissolve_chapter_boundary(cid:int,title_style:Optional[str]=None):
    """Remove one visible Chapter boundary without deleting its manuscript prose.

    The Chapter's stable manuscript blocks move, in order, into the Chapter above.
    If there is no Chapter above, an implicit persistence-only container is created
    so deleting the first visible Chapter header produces ordinary unheaded prose.
    This is the structural equivalent of deleting a chapter break in a text editor.
    """
    title_style=(title_style or '').strip().lower() or None
    if title_style not in {None,'body','heading2','heading3','heading4'}: raise HTTPException(400,"Choose Body, H2, H3 or H4 for the former Chapter header")
    commit("Before removing chapter boundary","safety")
    with storage_mutations.tx() as con:
        ch=one(con,"SELECT * FROM chapters WHERE id=?",(cid,))
        if not ch: raise HTTPException(404,"Chapter not found")
        if int(ch.get('is_implicit') or 0): raise HTTPException(409,"Persistence-only Chapters cannot be removed from the writer UI")
        pid=int(ch['project_id']); tab_id=int(ch['tab_id']); t=storage_values.now()
        target=one(con,"SELECT * FROM chapters WHERE project_id=? AND tab_id=? AND (position<? OR (position=? AND id<?)) ORDER BY position DESC,id DESC LIMIT 1",(pid,tab_id,ch['position'],ch['position'],cid))
        if not target:
            # The first visible header may be removed too. Keep its prose before the
            # next visible Chapter in an internal container that never leaks into UI.
            pos=int(ch['position'])
            con.execute("UPDATE chapters SET position=position+1,updated_at=? WHERE project_id=? AND tab_id=? AND position>=?",(t,pid,tab_id,pos))
            target_id=int(con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(pid,tab_id,pos,(ch.get('title') or 'Manuscript')[:300],'',1,'','',ch.get('created_at') or t,t)).lastrowid)
            target=one(con,"SELECT * FROM chapters WHERE id=?",(target_id,))
            # The source Chapter was shifted by the insert; refresh it for ordering.
            ch=one(con,"SELECT * FROM chapters WHERE id=?",(cid,))
        target_id=int(target['id']); target_is_implicit=bool(target.get('is_implicit'))
        source_blocks=allrows(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(cid,))
        base=float(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM manuscript_blocks WHERE chapter_id=?",(target_id,)).fetchone()[0])
        converted_block_id=None
        if title_style:
            title_text=str(ch.get('title') or '').strip() or 'Untitled Chapter'
            prior=one(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position DESC,id DESC LIMIT 1",(target_id,))
            first=source_blocks[0] if source_blocks else None
            scene_id=None
            if prior and first and prior.get('scene_id') is not None and int(prior['scene_id'])==int(first.get('scene_id') or -1): scene_id=int(prior['scene_id'])
            converted_block_id=storage_values.uid('b_'); safe_html=html.escape(title_text); content_hash=storage_values.sha(safe_html+'\0'+title_text+'\0')
            con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(converted_block_id,pid,tab_id,target_id,scene_id,base,'paragraph',title_style,safe_html,title_text,'',content_hash,t,t))
            # A style conversion preserves the title text, so its editorial anchors
            # can move losslessly from the virtual Chapter-title target to the new
            # stable manuscript block.
            con.execute("UPDATE annotations SET block_id=?,chapter_id=NULL,updated_at=? WHERE project_id=? AND chapter_id=? AND block_id IS NULL",(converted_block_id,t,pid,cid))
            base+=1
        for i,row in enumerate(source_blocks):
            con.execute("UPDATE manuscript_blocks SET chapter_id=?,tab_id=?,position=?,updated_at=? WHERE id=?",(target_id,tab_id,base+i,t,row['id']))

        # Chapter-level links follow the surviving writer-facing Chapter when there
        # is one. If the first header becomes unheaded prose, no hidden Chapter is
        # exposed as a link target/source; stable block-range links remain intact.
        if target_is_implicit:
            con.execute("DELETE FROM references_map WHERE project_id=? AND ((source_type='chapter' AND source_id=?) OR (target_type='chapter' AND target_id=?))",(pid,str(cid),str(cid)))
            con.execute("UPDATE canvas_nodes SET chapter_id=NULL,updated_at=? WHERE chapter_id=?",(t,cid))
        else:
            con.execute("UPDATE references_map SET source_id=? WHERE project_id=? AND source_type='chapter' AND source_id=?",(str(target_id),pid,str(cid)))
            con.execute("UPDATE references_map SET target_id=? WHERE project_id=? AND target_type='chapter' AND target_id=?",(str(target_id),pid,str(cid)))
            con.execute("UPDATE canvas_nodes SET chapter_id=?,updated_at=? WHERE chapter_id=?",(target_id,t,cid))

        # If the visible title itself disappears, its title-anchored editorial
        # objects disappear with it. Clean polymorphic links before the Chapter FK
        # cascades those annotations. A style conversion keeps the same annotation IDs
        # by moving them onto converted_block_id above, so their links remain valid.
        if not title_style:
            title_annotation_ids=[int(r['id']) for r in allrows(con,"SELECT id FROM annotations WHERE project_id=? AND chapter_id=? AND block_id IS NULL",(pid,cid))]
            if title_annotation_ids: _delete_polymorphic_references(con,pid,{'annotation':title_annotation_ids})

        bm=one(con,"SELECT * FROM bookmarks WHERE project_id=? AND target_type='chapter' AND chapter_id=?",(pid,cid))
        if bm:
            if converted_block_id:
                text=str(ch.get('title') or '').strip() or 'Untitled Chapter'; end=len(text); context=manuscript_anchors._bookmark_context_values(text,text,0,end,False)
                con.execute("""UPDATE bookmarks SET target_type='range',chapter_id=NULL,scene_id=NULL,start_block_id=?,end_block_id=?,start_offset=0,end_offset=?,start_selected_text=?,start_prefix_context=?,start_suffix_context=?,end_selected_text=?,end_prefix_context=?,end_suffix_context=?,stale=0,updated_at=? WHERE project_id=?""",(converted_block_id,converted_block_id,end,*context,t,pid))
            elif not target_is_implicit:
                con.execute("UPDATE bookmarks SET chapter_id=?,updated_at=? WHERE project_id=?",(target_id,t,pid))
            elif source_blocks:
                first=source_blocks[0]; text=str(first.get('text_content') or ''); end=1 if text else 0
                context=manuscript_anchors._bookmark_context_values(text,text,0,end,True)
                con.execute("""UPDATE bookmarks SET target_type='range',chapter_id=NULL,scene_id=NULL,start_block_id=?,end_block_id=?,start_offset=0,end_offset=?,start_selected_text=?,start_prefix_context=?,start_suffix_context=?,end_selected_text=?,end_prefix_context=?,end_suffix_context=?,stale=1,updated_at=? WHERE project_id=?""",(first['id'],first['id'],end,*context,t,pid))
            else:
                con.execute("DELETE FROM bookmarks WHERE project_id=?",(pid,))

        con.execute("DELETE FROM chapters WHERE id=?",(cid,))
        rows=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab_id))
        for i,row in enumerate(rows): con.execute("UPDATE chapters SET position=?,updated_at=? WHERE id=?",(i,t,row['id']))
        manuscript_scenes._sync_scene_outline_to_manuscript(con,pid,tab_id)
        con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid))
        storage_invariants.validate_project_invariants(con,pid)
        target_title='' if target_is_implicit else str(target.get('title') or '')
        first_block_id=source_blocks[0]['id'] if source_blocks else None
    commit(f"{'Changed chapter header style' if title_style else 'Removed chapter boundary'}: {ch['title']}","structure")
    sync_developmental_canvases(pid)
    return {"ok":True,"removed_chapter_id":cid,"target_chapter_id":target_id,"target_is_implicit":target_is_implicit,"target_title":target_title,"first_block_id":first_block_id,"converted_block_id":converted_block_id,"title_style":title_style}


@router.delete("/api/chapters/{cid}")
def delete_chapter(cid:int):
    commit("Before deleting chapter","safety")
    with storage_mutations.tx() as con:
        ch=one(con,"SELECT * FROM chapters WHERE id=?",(cid,));
        if not ch: raise HTTPException(404,"Chapter not found")
        blocks=allrows(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position",(cid,)); cut_html=''.join(f"<p>{b['content_html']}</p>" if b['block_type']=='paragraph' else _cut_image_figure_html(b) for b in blocks)
        block_ids=[b['id'] for b in blocks]
        annotation_ids=[r['id'] for r in allrows(con,"SELECT id FROM annotations WHERE chapter_id=?",(cid,))]
        if block_ids:
            marks=','.join('?' for _ in block_ids); annotation_ids.extend(r['id'] for r in allrows(con,f"SELECT id FROM annotations WHERE block_id IN ({marks})",block_ids))
        # Scenes are manuscript ranges, not detached planning placeholders. A Scene
        # crossing another Chapter survives; a Scene whose entire range disappears
        # with this Chapter disappears with it as well.
        scene_ids=sorted({int(b['scene_id']) for b in blocks if b.get('scene_id') is not None})
        removed_scene_ids=[]
        for sid in scene_ids:
            if not one(con,"SELECT id FROM manuscript_blocks WHERE scene_id=? AND chapter_id<>? LIMIT 1",(sid,cid)):
                removed_scene_ids.append(sid)
        _detach_map_anchors_for_removed_manuscript(con,ch['project_id'],chapter_ids=(cid,),scene_ids=removed_scene_ids,block_ids=block_ids)
        _delete_polymorphic_references(con,ch['project_id'],{'chapter':(cid,),'annotation':annotation_ids,'scene':removed_scene_ids})
        clauses=[]; args=[]
        if block_ids:
            marks=','.join('?' for _ in block_ids); clauses.append(f"start_block_id IN ({marks}) OR end_block_id IN ({marks})"); args.extend(block_ids); args.extend(block_ids)
        if clauses: con.execute("DELETE FROM references_map WHERE project_id=? AND ("+' OR '.join(clauses)+")",[ch['project_id'],*args])
        source_project=str(one(con,"SELECT title FROM projects WHERE id=?",(ch['project_id'],)).get('title') or 'Project')[:240]; source_chapter=_cut_chapter_label(con,ch)[:240]; con.execute("INSERT INTO cuts(project_id,title,content_html,source_chapter_id,created_at,note,source_label,source_project,source_chapter,source_scene) VALUES(?,?,?,?,?,?,?,?,?,?)",(ch['project_id'],'Cut: '+ch['title'],cut_html,cid,storage_values.now(),'','Chapter: '+ch['title'],source_project,source_chapter,'')); con.execute("DELETE FROM chapters WHERE id=?",(cid,))
        for sid in removed_scene_ids: con.execute("DELETE FROM scenes WHERE id=?",(sid,))
        rows=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(ch['project_id'],ch['tab_id']))
        if not rows:
            manuscript_scenes._ensure_implicit_tab_chapter(con,ch['project_id'],ch['tab_id'],after_chapter_id=cid)
            rows=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(ch['project_id'],ch['tab_id']))
        for i,r in enumerate(rows): con.execute("UPDATE chapters SET position=? WHERE id=?",(i,r['id']))
        manuscript_anchors.repair_orphaned_bookmark(con,ch['project_id'],ch['tab_id']); storage_invariants.validate_project_invariants(con,ch['project_id'])
    commit(f"Cut chapter: {ch['title']}"); sync_developmental_canvases(ch['project_id']); return {"ok":True}

@router.delete("/api/chapters/{cid}/permanent")
def delete_chapter_permanently(cid:int):
    """Delete a Chapter and its manuscript contents without creating a Cut.

    Session structural Undo still wraps this action from the client, but unlike the
    normal Chapter DELETE route this command does not add the removed prose to Cuts.
    """
    commit("Before deleting chapter","safety")
    with storage_mutations.tx() as con:
        ch=one(con,"SELECT * FROM chapters WHERE id=?",(cid,))
        if not ch: raise HTTPException(404,"Chapter not found")
        blocks=allrows(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position",(cid,)); block_ids=[b['id'] for b in blocks]
        annotation_ids=[r['id'] for r in allrows(con,"SELECT id FROM annotations WHERE chapter_id=?",(cid,))]
        if block_ids:
            marks=','.join('?' for _ in block_ids); annotation_ids.extend(r['id'] for r in allrows(con,f"SELECT id FROM annotations WHERE block_id IN ({marks})",block_ids))
        scene_ids=sorted({int(b['scene_id']) for b in blocks if b.get('scene_id') is not None}); removed_scene_ids=[]
        for sid in scene_ids:
            if not one(con,"SELECT id FROM manuscript_blocks WHERE scene_id=? AND chapter_id<>? LIMIT 1",(sid,cid)): removed_scene_ids.append(sid)
        _detach_map_anchors_for_removed_manuscript(con,ch['project_id'],chapter_ids=(cid,),scene_ids=removed_scene_ids,block_ids=block_ids)
        _delete_polymorphic_references(con,ch['project_id'],{'chapter':(cid,),'annotation':annotation_ids,'scene':removed_scene_ids})
        if block_ids:
            marks=','.join('?' for _ in block_ids)
            con.execute(f"DELETE FROM references_map WHERE project_id=? AND (start_block_id IN ({marks}) OR end_block_id IN ({marks}))",[ch['project_id'],*block_ids,*block_ids])
        con.execute("DELETE FROM chapters WHERE id=?",(cid,))
        for sid in removed_scene_ids: con.execute("DELETE FROM scenes WHERE id=?",(sid,))
        rows=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(ch['project_id'],ch['tab_id']))
        if not rows:
            manuscript_scenes._ensure_implicit_tab_chapter(con,ch['project_id'],ch['tab_id'],after_chapter_id=cid)
            rows=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(ch['project_id'],ch['tab_id']))
        for i,row in enumerate(rows): con.execute("UPDATE chapters SET position=? WHERE id=?",(i,row['id']))
        manuscript_anchors.repair_orphaned_bookmark(con,ch['project_id'],ch['tab_id'])
        con.execute("UPDATE projects SET updated_at=? WHERE id=?",(storage_values.now(),ch['project_id'])); storage_invariants.validate_project_invariants(con,ch['project_id'])
    commit(f"Deleted chapter: {ch['title']}","structure"); sync_developmental_canvases(ch['project_id']); return {"ok":True}

@router.post("/api/chapters/reorder")
def reorder_chapters(body:Reorder):
    if len(body.ids)!=len(set(body.ids)) or not body.ids: raise HTTPException(400,"Invalid chapter order")
    # Preflight the proposed manuscript order before writing History or Current.
    with storage_mutations.tx() as con:
        rows=allrows(con,"SELECT id,project_id,tab_id FROM chapters WHERE id IN (%s)"%','.join('?' for _ in body.ids),body.ids)
        if len(rows)!=len(body.ids): raise HTTPException(400,"One or more chapters no longer exist")
        scopes={(r['project_id'],r['tab_id']) for r in rows}
        if len(scopes)!=1: raise HTTPException(400,"Only sibling chapters can be reordered together")
        pid,tab_id=next(iter(scopes)); siblings=[r['id'] for r in allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? AND is_implicit=0 ORDER BY position,id",(pid,tab_id))]
        if set(siblings)!=set(body.ids): raise HTTPException(400,"Reorder must include every chapter in this source tab")
        old_rank={cid:i for i,cid in enumerate(siblings)}; new_rank={cid:i for i,cid in enumerate(body.ids)}
        # A cross-Chapter Scene owns an ordered run of Chapter chunks. Neither
        # reversing those chunks nor inserting unrelated prose between them is safe.
        scene_rows=allrows(con,"""SELECT s.id,s.title,b.chapter_id FROM scenes s JOIN manuscript_blocks b ON b.scene_id=s.id JOIN chapters c ON c.id=b.chapter_id
            WHERE s.project_id=? AND s.tab_id=? ORDER BY s.id,c.position,b.position,b.id""",(pid,tab_id))
        by_scene={}
        for row in scene_rows:
            entry=by_scene.setdefault(int(row['id']),{'title':row['title'],'chapters':[]})
            cid=int(row['chapter_id'])
            if cid not in entry['chapters']: entry['chapters'].append(cid)
        for item in by_scene.values():
            owned=[cid for cid in item['chapters'] if cid in new_rank]
            if len(owned)<2: continue
            if sorted(owned,key=lambda x:new_rank[x])!=owned:
                raise HTTPException(409,detail=f"This Chapter can't be moved because it would reorder Scene “{item['title']}” across the manuscript. Move or adjust that Scene first.")
            lo=min(new_rank[c] for c in owned); hi=max(new_rank[c] for c in owned)
            if body.ids[lo:hi+1]!=owned:
                raise HTTPException(409,detail=f"This Chapter can't be moved here because it would split Scene “{item['title']}” across the manuscript. Move or adjust that Scene first.")
    commit("Before chapter reorder","safety")
    with storage_mutations.tx() as con:
        for i,cid in enumerate(body.ids): con.execute("UPDATE chapters SET position=? WHERE id=?",(100000+i,cid))
        base=int(con.execute("SELECT COUNT(*) FROM chapters WHERE project_id=? AND tab_id=? AND is_implicit=1",(pid,tab_id)).fetchone()[0])
        for i,cid in enumerate(body.ids): con.execute("UPDATE chapters SET position=? WHERE id=?",(base+i,cid))
        manuscript_scenes._sync_scene_outline_to_manuscript(con,pid,tab_id); storage_invariants.validate_project_invariants(con,pid)
    commit("Reordered chapters","structure")
    with storage_mutations.tx() as con:
        scene_ids=[int(row['id']) for row in allrows(con,"SELECT id FROM scenes WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab_id))]
    return {"ok":True,"ids":body.ids,"scene_ids":scene_ids}
