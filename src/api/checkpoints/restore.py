from __future__ import annotations

"""Destructive Checkpoint manuscript restoration primitives."""

from fastapi import HTTPException

from backend.dependencies import feature_call
from backend.content import VALID_BLOCK_TYPES, VALID_SEMANTIC_STYLES, sanitize_inline_html
from backend.data_access import _delete_polymorphic_references, allrows, one
from security import content_security
from storage import values as storage_values
from . import capture, lineage, restore_support

_detach_map_anchors_for_removed_manuscript = feature_call('maps.anchors', '_detach_map_anchors_for_removed_manuscript')
_ensure_implicit_tab_chapter = feature_call('manuscript.scenes', '_ensure_implicit_tab_chapter')
_sync_scene_outline_to_manuscript = feature_call('manuscript.scenes', '_sync_scene_outline_to_manuscript')
best_anchor_position = feature_call('manuscript.anchors', 'best_anchor_position')
reanchor_block_bookmark = feature_call('manuscript.anchors', 'reanchor_block_bookmark')
rehome_deleted_block_refs = feature_call('manuscript.anchors', 'rehome_deleted_block_refs')
repair_orphaned_bookmark = feature_call('manuscript.anchors', 'repair_orphaned_bookmark')
repair_stale_bookmark = feature_call('manuscript.anchors', 'repair_stale_bookmark')

def _restore_chapter_snapshot(con,pid:int,snapshot:dict,*,target_chapter_id:int|None=None,force_tab_id:int|None=None,force_position:int|None=None,scene_map_override:dict[int,int]|None=None,force_new:bool=False,preserve_target_title:bool=False,clone_nested:bool=False,report:dict|None=None,reserved_annotation_ids:set[int]|None=None,skip_checkpoint_lineages:set[tuple[int,str]]|None=None,preserved_scene_by_block:dict[str,int|None]|None=None) -> int:
    report=report if report is not None else {}; report.setdefault('losses',[]); report.setdefault('renames',[]); report.setdefault('notices',[])
    include_scenes=bool(snapshot.get('include_scenes',True)); include_markup=bool(snapshot.get('include_markup',True)); include_notes=bool(snapshot.get('include_notes',True))
    snap_ch=snapshot.get('chapter') or {}; source_id=int(snap_ch.get('id') or 0); existing=None
    if target_chapter_id: existing=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(target_chapter_id,pid))
    elif source_id and not force_new:
        created=str(snap_ch.get('created_at') or ''); existing=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=? AND created_at=?",(source_id,pid,created)) if created else None
    if force_tab_id is not None: tab=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(force_tab_id,pid))
    elif existing: tab=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(existing['tab_id'],pid))
    else:
        old_tab=(snapshot.get('tab') or {}).get('id'); tab=one(con,"SELECT * FROM document_tabs WHERE id=? AND project_id=?",(old_tab,pid)) if old_tab else None
        if not tab: tab=capture._primary_manuscript_tab(con,pid)
    if not tab: raise HTTPException(409,"Checkpoint source document no longer exists")
    snapshot_title=str(snap_ch.get('title') or 'Untitled Chapter')[:300]; title=(str(existing.get('title') or snapshot_title)[:300] if existing and preserve_target_title else snapshot_title); t=storage_values.now()
    if existing:
        chapter_id=int(existing['id']); position=int(existing['position'] if force_position is None else force_position)
        con.execute("UPDATE chapters SET tab_id=?,position=?,title=?,description=?,is_implicit=?,updated_at=? WHERE id=?",(tab['id'],position,title,str(snap_ch.get('description') or '')[:12000],int(bool(snap_ch.get('is_implicit',0))),t,chapter_id))
    else:
        count=int(con.execute("SELECT COUNT(*) FROM chapters WHERE project_id=? AND tab_id=?",(pid,tab['id'])).fetchone()[0]); requested=snap_ch.get('position') if force_position is None else force_position
        position=max(0,min(int(requested or 0),count)); con.execute("UPDATE chapters SET position=position+1 WHERE project_id=? AND tab_id=? AND position>=?",(pid,tab['id'],position))
        chapter_id=int(con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(pid,tab['id'],position,title,str(snap_ch.get('description') or ''),int(bool(snap_ch.get('is_implicit',0))),'','',(t if force_new else str(snap_ch.get('created_at') or t)),t)).lastrowid)

    current=allrows(con,"SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(chapter_id,)); current_by={str(b['id']):b for b in current}; current_ids=[str(b['id']) for b in current]
    current_annotations=capture._checkpoint_annotation_rows(con,current_ids,[chapter_id])
    saved_annotations=list(snapshot.get('annotations') or []); saved_by_id={int(a.get('id') or 0):a for a in saved_annotations if int(a.get('id') or 0)>0}

    scene_map=(scene_map_override if scene_map_override is not None else restore_support._checkpoint_scene_map(con,pid,snapshot,force_fresh=clone_nested,force_tab_id=int(tab['id']),report=report)) if include_scenes else {}
    keep=[]; id_map={}; snap_blocks=sorted(snapshot.get('blocks',[]),key=lambda x:(x.get('position',0),str(x.get('id',''))))
    for i,b in enumerate(snap_blocks):
        old_id=str(b.get('id') or storage_values.uid('b_')); owner=one(con,"SELECT chapter_id FROM manuscript_blocks WHERE id=?",(old_id,))
        bid=old_id if (not owner or int(owner['chapter_id'])==chapter_id) else storage_values.uid('b_'); keep.append(bid); id_map[old_id]=bid
        bt=str(b.get('block_type') or 'paragraph'); bt=bt if bt in VALID_BLOCK_TYPES else 'paragraph'; style=str(b.get('semantic_style') or 'body'); style=style if style in VALID_SEMANTIC_STYLES else 'body'
        if bt=='image':
            style='image'; text=''; safe=''; image=str(b.get('image_path') or '') if content_security.is_safe_project_asset_path(str(b.get('image_path') or '')) else ''
            image_width=max(5,min(100,int(b.get('image_width') or 100))); image_footer=str(b.get('image_footer') or '')[:4000]; image_description=str(b.get('image_description') or '')[:12000]; image_row_id=str(b.get('image_row_id') or '')[:80]
        else:
            text=str(b.get('text_content') or ''); safe=sanitize_inline_html(str(b.get('content_html') or ''),text); image=''; image_width=100; image_footer=''; image_description=''; image_row_id=''
        if include_scenes:
            sid=scene_map.get(int(b.get('scene_id') or 0)) if b.get('scene_id') is not None else None
        else:
            # Book-scope prose-only restores capture current Scene membership before
            # any Chapter is rewritten. That lets a Scene follow the same stable block
            # identity even when that block moved between Chapters after the Checkpoint.
            # Chapter-scope restores keep their existing local behavior.
            if preserved_scene_by_block is not None and old_id in preserved_scene_by_block:
                sid=preserved_scene_by_block[old_id]
            else:
                sid=current_by.get(old_id,{}).get('scene_id') if old_id in current_by else None
        digest=storage_values.block_content_hash(safe,text,image,image_width,image_footer,image_description,image_row_id)
        if bid in current_by:
            reanchor_block_bookmark(con,bid,text)
            con.execute("UPDATE manuscript_blocks SET tab_id=?,chapter_id=?,scene_id=?,position=?,block_type=?,semantic_style=?,content_html=?,text_content=?,image_path=?,image_width=?,image_footer=?,image_description=?,image_row_id=?,content_hash=?,updated_at=? WHERE id=?",(tab['id'],chapter_id,sid,i,bt,style,safe,text,image,image_width,image_footer,image_description,image_row_id,digest,t,bid))
        else:
            con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,tab['id'],chapter_id,sid,i,bt,style,safe,text,image,image_width,image_footer,image_description,image_row_id,digest,t,t))
    if not keep:
        bid=storage_values.uid('b_'); keep=[bid]; id_map['']=bid; con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,tab['id'],chapter_id,0,'paragraph','body','','','',t,t))

    restored_blocks=allrows(con,"SELECT id,text_content,position,scene_id FROM manuscript_blocks WHERE chapter_id=? AND id IN (%s) ORDER BY position,id"%','.join('?' for _ in keep),(chapter_id,*keep))
    # Preserve every current editorial annotation if it can be deterministically placed
    # on the restored prose. Same-lineage Checkpoint annotations provide a safe fallback
    # anchor while current body/status/thread/Marker choices remain authoritative.
    lost_ids=[]; current_lineages=set()
    for cur in current_annotations:
        saved=saved_by_id.get(int(cur.get('id') or 0)); same=restore_support._checkpoint_annotation_lineage(cur,saved); current_lineages.add((int(cur.get('id') or 0),str(cur.get('created_at') or '')))
        if cur.get('chapter_id') is not None:
            selected=str(cur.get('selected_text') or '')
            pos=best_anchor_position(title,selected,int(cur.get('start_offset') or 0),str(cur.get('prefix_context') or ''),str(cur.get('suffix_context') or '')) if selected else max(0,min(int(cur.get('start_offset') or 0),len(title)))
            if pos is None and same and saved and saved.get('chapter_id') is not None:
                pos=max(0,min(int(saved.get('start_offset') or 0),len(title)))
            if pos is None:
                kind='Note' if cur.get('annotation_type') in {'comment','note'} else 'Markup' if cur.get('annotation_type')=='highlight' else 'Annotation'
                report['losses'].append({'kind':kind,'id':int(cur.get('id') or 0),'text':selected[:120]}); lost_ids.append(int(cur['id'])); continue
            end=max(pos,min(pos+len(selected),len(title))) if selected else max(pos,min(int(cur.get('end_offset') or pos),len(title)))
            con.execute("UPDATE annotations SET block_id=NULL,chapter_id=?,start_offset=?,end_offset=?,prefix_context=?,suffix_context=?,stale=0,updated_at=? WHERE id=?",(chapter_id,pos,end,title[max(0,pos-45):pos],title[end:end+45],t,cur['id']))
            continue
        anchor=restore_support._checkpoint_anchor_in_blocks(cur,restored_blocks,str(cur.get('block_id') or ''))
        if anchor is None and same:
            mapped=id_map.get(str(saved.get('block_id') or '')); target=next((b for b in restored_blocks if str(b['id'])==str(mapped)),None)
            if target:
                text=str(target.get('text_content') or ''); st=max(0,min(int(saved.get('start_offset') or 0),len(text))); en=max(st,min(int(saved.get('end_offset') or 0),len(text))); anchor=(target,st,en)
        if anchor is None:
            kind='Note' if cur.get('annotation_type') in {'comment','note'} else 'Markup' if cur.get('annotation_type')=='highlight' else 'Annotation'
            report['losses'].append({'kind':kind,'id':int(cur.get('id') or 0),'text':str(cur.get('selected_text') or '')[:120]}); lost_ids.append(int(cur['id'])); continue
        target,st,en=anchor; text=str(target.get('text_content') or ''); selected=str(cur.get('selected_text') or '')
        con.execute("UPDATE annotations SET block_id=?,start_offset=?,end_offset=?,prefix_context=?,suffix_context=?,stale=0,updated_at=? WHERE id=?",(target['id'],st,en,text[max(0,st-45):st],text[en:en+45],t,cur['id']))
    if lost_ids:
        _delete_polymorphic_references(con,pid,{'annotation':lost_ids}); marks=','.join('?' for _ in lost_ids); con.execute(f"DELETE FROM annotations WHERE id IN ({marks})",lost_ids)

    deleting=[old for old in current if str(old['id']) not in keep]
    if not include_scenes and preserved_scene_by_block is None:
        # In Chapter scope there is no global Book overlay to relocate Scene membership.
        # If prose owned by a current Scene is absent from the prose snapshot, applying
        # while Scenes are excluded would necessarily change that current Scene. Block
        # rather than silently deleting/shrinking it or guessing at a replacement block.
        endangered=sorted({int(old['scene_id']) for old in deleting if old.get('scene_id') is not None})
        if endangered:
            marks=','.join('?' for _ in endangered); names={int(r['id']):str(r.get('title') or f"Scene {r['id']}") for r in allrows(con,f"SELECT id,title FROM scenes WHERE project_id=? AND id IN ({marks})",(pid,*endangered))}
            for sid in endangered:
                report.setdefault('hard_conflicts',[]).append(f"Current Scene “{names.get(sid,f'Scene {sid}')}” contains prose that does not exist in this Checkpoint. Because Scenes were excluded, Apply cannot remove that Scene prose without changing current Scene structure.")
    incoming_scene_ids={int(x) for x in (scene_map.get(int(b.get('scene_id') or 0)) for b in snap_blocks if include_scenes and b.get('scene_id') is not None) if x is not None}
    if not include_scenes: incoming_scene_ids={int(b.get('scene_id')) for b in allrows(con,"SELECT scene_id FROM manuscript_blocks WHERE chapter_id=? AND scene_id IS NOT NULL",(chapter_id,))}
    # Restoring Scene context replaces Scene membership inside the checkpoint scope. A
    # current-only Scene can therefore lose its last block even when that block itself
    # survives (its scene_id is simply rewritten). Treat those displaced Scene records
    # exactly like Scenes whose final block was deleted: remove the now-empty identity
    # and its polymorphic links, but never touch a Scene that still owns prose elsewhere.
    old_scene_ids=sorted({int(old['scene_id']) for old in current if old.get('scene_id') is not None}); removed_scene_ids=[]
    for sid in old_scene_ids:
        if sid in incoming_scene_ids: continue
        if not one(con,"SELECT id FROM manuscript_blocks WHERE scene_id=? LIMIT 1",(sid,)):
            removed_scene_ids.append(sid)
    if removed_scene_ids:
        _detach_map_anchors_for_removed_manuscript(con,pid,scene_ids=removed_scene_ids,block_ids=[old['id'] for old in deleting]); _delete_polymorphic_references(con,pid,{'scene':removed_scene_ids})
    for old in deleting:
        rehome_deleted_block_refs(con,old,chapter_id); con.execute("DELETE FROM manuscript_blocks WHERE id=?",(old['id'],))
    # A Scene may become empty only after the deleting blocks above are removed, so do
    # one final deterministic sweep over the candidate identities before validation.
    for sid in old_scene_ids:
        if sid in incoming_scene_ids or sid in removed_scene_ids: continue
        if not one(con,"SELECT id FROM manuscript_blocks WHERE scene_id=? LIMIT 1",(sid,)):
            removed_scene_ids.append(sid); _detach_map_anchors_for_removed_manuscript(con,pid,scene_ids=[sid],block_ids=[old['id'] for old in deleting]); _delete_polymorphic_references(con,pid,{'scene':[sid]})
    for sid in removed_scene_ids: con.execute("DELETE FROM scenes WHERE id=?",(sid,))

    checkpoint_thread_map=restore_support._restore_checkpoint_comment_threads(con,pid,snapshot,force_fresh=clone_nested) if include_notes else {}
    for a in saved_annotations:
        category=str(a.get('annotation_type') or 'highlight')
        if category=='highlight' and not include_markup: continue
        if category in {'comment','note'} and not include_notes: continue
        lineage=(int(a.get('id') or 0),str(a.get('created_at') or ''))
        if lineage in current_lineages or lineage in (skip_checkpoint_lineages or set()): continue  # current same-identity annotation wins
        title_anchor=a.get('chapter_id') is not None
        if title_anchor:
            bid=None; target_chapter_id=chapter_id; text=title
            selected=str(a.get('selected_text') or '')
            pos=best_anchor_position(text,selected,int(a.get('start_offset') or 0),str(a.get('prefix_context') or ''),str(a.get('suffix_context') or '')) if selected else max(0,min(int(a.get('start_offset') or 0),len(text)))
            stale=0 if pos is not None else 1
            start=max(0,min(int(a.get('start_offset') or 0),len(text))) if pos is None else pos
            end=max(start,min(start+len(selected) if selected else int(a.get('end_offset') or start),len(text)))
        else:
            bid=id_map.get(str(a.get('block_id') or ''))
            if not bid: continue
            target_chapter_id=None; block=one(con,"SELECT text_content FROM manuscript_blocks WHERE id=?",(bid,)); text=str(block.get('text_content') or '') if block else ''
            start=max(0,min(int(a.get('start_offset') or 0),len(text))); end=max(start,min(int(a.get('end_offset') or 0),len(text))); stale=int(bool(a.get('stale')))
        label_id=restore_support._checkpoint_ensure_label(con,pid,a,report) if category=='highlight' else None
        old_thread_id=int(a.get('thread_id') or 0); thread_id=checkpoint_thread_map.get(old_thread_id) if old_thread_id else None
        values=(pid,bid,target_chapter_id,category,label_id,start,end,str(a.get('selected_text') or ''),text[max(0,start-45):start],text[end:end+45],thread_id,str(a.get('body') or ''),str(a.get('status') or 'open'),stale)
        requested=int(a.get('id') or 0); owner=one(con,"SELECT id FROM annotations WHERE id=?",(requested,)) if requested else None
        if requested and owner is None and not clone_nested and requested not in (reserved_annotation_ids or set()):
            con.execute("INSERT INTO annotations(id,project_id,block_id,chapter_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,thread_id,body,status,stale,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(requested,*values,str(a.get('created_at') or t),t))
        else:
            con.execute("INSERT INTO annotations(project_id,block_id,chapter_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,thread_id,body,status,stale,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(*values,str(a.get('created_at') or t),t))
    repair_stale_bookmark(con,chapter_id); con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,chapter_id)); con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid)); return chapter_id

def _checkpoint_remove_chapter(con,pid:int,chapter_id:int):
    ch=one(con,"SELECT * FROM chapters WHERE id=? AND project_id=?",(chapter_id,pid))
    if not ch: return
    blocks=allrows(con,"SELECT id,scene_id FROM manuscript_blocks WHERE chapter_id=?",(chapter_id,)); bids=[b['id'] for b in blocks]; scene_ids=sorted({b['scene_id'] for b in blocks if b.get('scene_id') is not None}); removed_scene_ids=[]; ann_ids=[r['id'] for r in allrows(con,"SELECT id FROM annotations WHERE chapter_id=?",(chapter_id,))]
    if bids:
        q=','.join('?' for _ in bids); ann_ids.extend(r['id'] for r in allrows(con,f"SELECT id FROM annotations WHERE block_id IN ({q})",bids))
    for sid in scene_ids:
        if not con.execute("SELECT 1 FROM manuscript_blocks WHERE scene_id=? AND chapter_id<>? LIMIT 1",(sid,chapter_id)).fetchone(): removed_scene_ids.append(sid)
    _detach_map_anchors_for_removed_manuscript(con,pid,chapter_ids=(chapter_id,),scene_ids=removed_scene_ids,block_ids=bids); _delete_polymorphic_references(con,pid,{'chapter':(chapter_id,),'annotation':ann_ids,'scene':removed_scene_ids})
    if bids:
        q=','.join('?' for _ in bids); con.execute(f"DELETE FROM references_map WHERE project_id=? AND (start_block_id IN ({q}) OR end_block_id IN ({q}))",(pid,*bids,*bids))
    con.execute("DELETE FROM chapters WHERE id=?",(chapter_id,))
    for sid in removed_scene_ids: con.execute("DELETE FROM scenes WHERE id=?",(sid,))

def _restore_book_snapshot(con,pid:int,snapshot:dict,*,report:dict|None=None) -> list[int]:
    report=report if report is not None else {}; report.setdefault('losses',[]); report.setdefault('renames',[]); report.setdefault('notices',[])
    include_scenes=bool(snapshot.get('include_scenes',True)); tab=capture._primary_manuscript_tab(con,pid); tab_id=int(tab['id'])
    # Capture all current editorial annotations before any Chapter disappears. For a
    # Book restore they are overlaid globally afterward, which preserves Notes/Markup
    # even when prose moved between Chapters since the Checkpoint.
    live_blocks=allrows(con,"SELECT id,scene_id FROM manuscript_blocks WHERE project_id=? AND tab_id=?",(pid,tab_id)); live_bids=[str(b['id']) for b in live_blocks]
    live_chapter_ids=[int(r['id']) for r in allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=?",(pid,tab_id))]
    current_scene_by_block={str(b['id']):(int(b['scene_id']) if b.get('scene_id') is not None else None) for b in live_blocks}
    snapshot_block_ids={str(b.get('id') or '') for b in snapshot.get('blocks',[]) if str(b.get('id') or '')}
    if not include_scenes:
        endangered=sorted({int(b['scene_id']) for b in live_blocks if b.get('scene_id') is not None and str(b['id']) not in snapshot_block_ids})
        if endangered:
            marks=','.join('?' for _ in endangered); names={int(r['id']):str(r.get('title') or f"Scene {r['id']}") for r in allrows(con,f"SELECT id,title FROM scenes WHERE project_id=? AND id IN ({marks})",(pid,*endangered))}
            for sid in endangered:
                report.setdefault('hard_conflicts',[]).append(f"Current Scene “{names.get(sid,f'Scene {sid}')}” contains prose that does not exist in this Checkpoint. Because Scenes were excluded, Apply cannot remove that Scene prose without changing current Scene structure.")
    current_annotations=capture._checkpoint_annotation_rows(con,live_bids,live_chapter_ids); current_threads,current_messages=capture._checkpoint_comment_rows(con,[a for a in current_annotations if a.get('annotation_type')=='comment'])
    reserved_ids={int(a.get('id') or 0) for a in current_annotations if int(a.get('id') or 0)>0}; current_lineages={(int(a.get('id') or 0),str(a.get('created_at') or '')) for a in current_annotations if int(a.get('id') or 0)>0}
    clauses=[]; args=[pid]
    if live_bids:
        marks=','.join('?' for _ in live_bids); clauses.append(f"block_id IN ({marks})"); args.extend(live_bids)
    if live_chapter_ids:
        marks=','.join('?' for _ in live_chapter_ids); clauses.append(f"chapter_id IN ({marks})"); args.extend(live_chapter_ids)
    if clauses: con.execute("DELETE FROM annotations WHERE project_id=? AND ("+' OR '.join(clauses)+")",args)

    snap_chapters=sorted(snapshot.get('chapters',[]),key=lambda x:(x.get('position',0),x.get('id',0))); blocks=snapshot.get('blocks',[]); annotations=snapshot.get('annotations',[])
    target_by_snapshot={}; used=set()
    for ch in snap_chapters:
        cid=int(ch.get('id') or 0); ch_blocks=[b for b in blocks if int(b.get('chapter_id') or 0)==cid]
        mini={'version':1,'scope':'chapter','captured_at':snapshot.get('captured_at'),'tab':snapshot.get('tab'),'chapter':ch,'blocks':ch_blocks}
        live_id=lineage._checkpoint_live_chapter_id_for_snapshot(con,pid,mini)
        if live_id and live_id not in used: target_by_snapshot[cid]=live_id; used.add(live_id)
    scene_map=restore_support._checkpoint_scene_map(con,pid,snapshot,force_tab_id=tab_id,report=report) if include_scenes else {}
    original_chapters=[int(ch['id']) for ch in allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab_id))]
    result=[]
    for pos,ch in enumerate(snap_chapters):
        cid=int(ch.get('id') or 0); ch_blocks=[b for b in blocks if int(b.get('chapter_id') or 0)==cid]; bidset={str(b.get('id')) for b in ch_blocks}; ch_annotations=[a for a in annotations if str(a.get('block_id')) in bidset or int(a.get('chapter_id') or 0)==cid]
        thread_ids={int(a.get('thread_id') or 0) for a in ch_annotations if int(a.get('thread_id') or 0)>0}; ch_threads=[x for x in snapshot.get('comment_threads',[]) if int(x.get('id') or 0) in thread_ids]; ch_messages=[x for x in snapshot.get('comment_messages',[]) if int(x.get('thread_id') or 0) in thread_ids]
        scene_ids={int(b.get('scene_id')) for b in ch_blocks if b.get('scene_id') is not None}; ch_scenes=[scene for scene in snapshot.get('scenes',[]) if int(scene.get('id') or 0) in scene_ids]
        mini={'version':1,'scope':'chapter','captured_at':snapshot.get('captured_at'),'tab':snapshot.get('tab'),'chapter':ch,'blocks':ch_blocks,'annotations':ch_annotations,'comment_threads':ch_threads,'comment_messages':ch_messages,'scenes':ch_scenes,
              'include_scenes':include_scenes,'include_markup':bool(snapshot.get('include_markup',True)),'include_notes':bool(snapshot.get('include_notes',True))}
        prior_live_id=target_by_snapshot.get(cid); new_id=_restore_chapter_snapshot(con,pid,mini,target_chapter_id=prior_live_id,force_tab_id=tab_id,force_position=pos,scene_map_override=scene_map if include_scenes else {},report=report,reserved_annotation_ids=reserved_ids,skip_checkpoint_lineages=current_lineages,preserved_scene_by_block=(current_scene_by_block if not include_scenes else None))
        lineage._checkpoint_rebind_chapter_locators(con,pid,new_id,(cid,ch.get('created_at')),(prior_live_id,ch.get('created_at'))); result.append(new_id)
    for old_id in original_chapters:
        if old_id not in result: _checkpoint_remove_chapter(con,pid,old_id)
    rows=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab_id))
    if not rows:
        _ensure_implicit_tab_chapter(con,pid,tab_id); rows=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab_id))
    for i,r in enumerate(rows): con.execute("UPDATE chapters SET position=? WHERE id=?",(i,r['id']))
    restore_support._overlay_current_annotations_after_book_restore(con,pid,current_annotations,current_threads,current_messages,snapshot,tab_id,report)
    repair_orphaned_bookmark(con,pid,tab_id); _sync_scene_outline_to_manuscript(con,pid,tab_id)
    return result
