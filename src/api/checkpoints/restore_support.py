from __future__ import annotations

"""Checkpoint restore support for annotations, comments, Markers, and Scenes."""

import re

from backend.dependencies import feature_call
from backend.data_access import _delete_polymorphic_references, allrows, one
from storage import values as storage_values
from . import capture

_clean_comment_body = feature_call('annotations.routes', '_clean_comment_body')
_context_match_head = feature_call('manuscript.anchors', '_context_match_head')
_context_match_tail = feature_call('manuscript.anchors', '_context_match_tail')
_next_scene_title = feature_call('manuscript.scenes', '_next_scene_title')
best_anchor_position = feature_call('manuscript.anchors', 'best_anchor_position')

def _checkpoint_annotation_lineage(left:dict,right:dict|None)->bool:
    return bool(right and int(left.get('id') or 0)==int(right.get('id') or 0) and str(left.get('created_at') or '') and str(left.get('created_at') or '')==str(right.get('created_at') or ''))


def _checkpoint_unique_label_name(con,pid:int,base:str)->str:
    base=(base or 'Marker').strip()[:120] or 'Marker'; candidate=base; n=2
    while one(con,"SELECT id FROM markup_labels WHERE project_id=? AND name=?",(pid,candidate)):
        suffix=f" {n}"; candidate=(base[:max(1,120-len(suffix))]+suffix); n+=1
    return candidate


def _checkpoint_ensure_label(con,pid:int,saved:dict,report:dict|None=None):
    """Resolve a saved Marker by stable identity, recreating it when necessary."""
    marker_id=int(saved.get('label_id') or 0); name=str(saved.get('label_name') or '').strip(); color=str(saved.get('label_color') or '#f2d86b'); created=str(saved.get('label_created_at') or '')
    if not marker_id and not name: return None
    if marker_id:
        row=one(con,"SELECT * FROM markup_labels WHERE id=?",(marker_id,))
        if row and int(row.get('project_id') or 0)==pid and (not created or str(row.get('created_at') or '')==created): return marker_id
        if row is None:
            final_name=name or 'Marker'; collision=one(con,"SELECT id FROM markup_labels WHERE project_id=? AND name=?",(pid,final_name))
            if collision:
                renamed=_checkpoint_unique_label_name(con,pid,final_name)
                if report is not None: report.setdefault('notices',[]).append(f'Marker “{final_name}” will be recreated as “{renamed}” because that name is already in use.')
                final_name=renamed
            t=storage_values.now(); con.execute("INSERT INTO markup_labels(id,project_id,name,description,color,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(marker_id,pid,final_name,str(saved.get('label_description') or '')[:12000],color,created or t,t)); return marker_id
    if name:
        row=one(con,"SELECT id FROM markup_labels WHERE project_id=? AND name=?",(pid,name))
        if row and not marker_id: return int(row['id'])
    final_name=_checkpoint_unique_label_name(con,pid,name or 'Marker'); t=storage_values.now()
    return int(con.execute("INSERT INTO markup_labels(project_id,name,description,color,created_at,updated_at) VALUES(?,?,?,?,?,?)",(pid,final_name,str(saved.get('label_description') or '')[:12000],color,created or t,t)).lastrowid)


def _checkpoint_anchor_in_blocks(annotation:dict,blocks:list[dict],preferred_id:str|None=None):
    """Deterministically place a current annotation in restored prose; never fuzzy-match."""
    selected=str(annotation.get('selected_text') or '')
    if not selected: return None
    prefix=str(annotation.get('prefix_context') or ''); suffix=str(annotation.get('suffix_context') or ''); old=int(annotation.get('start_offset') or 0)
    by_id={str(b['id']):b for b in blocks}
    preferred=by_id.get(str(preferred_id or annotation.get('block_id') or ''))
    if preferred:
        text=str(preferred.get('text_content') or ''); pos=best_anchor_position(text,selected,old,prefix,suffix)
        if pos is not None: return preferred,pos,pos+len(selected)
    candidates=[]
    for b in blocks:
        text=str(b.get('text_content') or ''); scan=0
        while True:
            pos=text.find(selected,scan)
            if pos<0: break
            before=text[max(0,pos-len(prefix)):pos]; after=text[pos+len(selected):pos+len(selected)+len(suffix)]
            score=_context_match_tail(prefix,before)+_context_match_head(suffix,after)
            candidates.append((score,b,pos)); scan=pos+max(1,len(selected))
    if not candidates: return None
    candidates.sort(key=lambda x:(-x[0],str(x[1]['id']),x[2]))
    if len(candidates)>1 and candidates[0][0]==candidates[1][0]: return None
    _,b,pos=candidates[0]; return b,pos,pos+len(selected)


def _checkpoint_scene_title_for_restore(con,pid:int,title:str)->tuple[str,bool]:
    title=(title or 'Scene').strip()[:300] or 'Scene'
    if not one(con,"SELECT id FROM scenes WHERE project_id=? AND title=?",(pid,title)): return title,False
    candidate=_next_scene_title(con,pid)
    while one(con,"SELECT id FROM scenes WHERE project_id=? AND title=?",(pid,candidate)):
        m=re.fullmatch(r'Scene\s+(\d+)',candidate); candidate=f"Scene {int(m.group(1))+1 if m else int(con.execute('SELECT COALESCE(MAX(id),0)+1 FROM scenes').fetchone()[0])+1}"
    return candidate,True


def _checkpoint_scene_map(con,pid:int,snapshot:dict,*,force_fresh:bool=False,force_tab_id:int|None=None,report:dict|None=None) -> dict[int,int]:
    out={}; t=storage_values.now(); blocks_by_scene={}
    for block in snapshot.get('blocks',[]):
        sid=block.get('scene_id')
        if sid is not None: blocks_by_scene.setdefault(int(sid),[]).append(str(block.get('id') or ''))
    for scene in snapshot.get('scenes',[]):
        old=int(scene.get('id') or 0)
        if not old: continue
        created=str(scene.get('created_at') or ''); live=one(con,"SELECT * FROM scenes WHERE id=? AND project_id=?",(old,pid)); target=None
        desired_tab=int(force_tab_id or scene.get('tab_id') or (snapshot.get('tab') or {}).get('id') or 0)
        if not desired_tab: desired_tab=int(capture._primary_manuscript_tab(con,pid)['id'])
        if not force_fresh and live and created and str(live.get('created_at') or '')==created:
            target=live
        bids=[bid for bid in blocks_by_scene.get(old,[]) if bid]
        if not force_fresh and target is None and bids:
            marks=','.join('?' for _ in bids)
            rows=allrows(con,f"SELECT DISTINCT s.* FROM manuscript_blocks b JOIN scenes s ON s.id=b.scene_id WHERE b.project_id=? AND b.id IN ({marks}) AND s.project_id=?",(pid,*bids,pid))
            if len(rows)==1 and (not created or str(rows[0].get('created_at') or '')==created): target=rows[0]
        saved_title=str(scene.get('title') or 'Scene')[:300]; desc=str(scene.get('description') or '')[:12000]; pos=float(scene.get('position') if scene.get('position') is not None else old)
        if target is not None:
            # Stable identity wins. Structural placement is restored, while newer current
            # Scene metadata remains the writer's current metadata.
            nid=int(target['id']); con.execute("UPDATE scenes SET tab_id=?,position=?,updated_at=? WHERE id=? AND project_id=?",(desired_tab,pos,t,nid,pid)); out[old]=nid; continue
        title,renamed=_checkpoint_scene_title_for_restore(con,pid,saved_title)
        if renamed and report is not None:
            report.setdefault('renames',[]).append({'kind':'scene','from':saved_title,'to':title})
        scene_created=created or t
        owner=one(con,"SELECT id FROM scenes WHERE id=?",(old,)) if old else None
        if old and owner is None and not force_fresh:
            con.execute("INSERT INTO scenes(id,project_id,tab_id,position,title,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(old,pid,desired_tab,pos,title,desc,scene_created,t)); nid=old
        else:
            nid=int(con.execute("INSERT INTO scenes(project_id,tab_id,position,title,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(pid,desired_tab,pos,title,desc,scene_created,t)).lastrowid)
        out[old]=int(nid)
    return out


def _restore_checkpoint_comment_threads(con,pid:int,snapshot:dict,*,force_fresh:bool=False) -> dict[int,int]:
    """Restore missing Checkpoint thread data without overwriting newer current discussion."""
    saved_threads=snapshot.get('comment_threads') or []
    if not saved_threads: return {}
    messages_by={}
    for msg in snapshot.get('comment_messages') or []:
        old_tid=int(msg.get('thread_id') or 0)
        if old_tid: messages_by.setdefault(old_tid,[]).append(msg)
    mapping={}; t=storage_values.now()
    for saved in saved_threads:
        old_tid=int(saved.get('id') or 0)
        if not old_tid: continue
        created=str(saved.get('created_at') or ''); owner=one(con,'SELECT * FROM comment_threads WHERE id=?',(old_tid,))
        same=bool(not force_fresh and owner and int(owner.get('project_id') or 0)==pid and created and str(owner.get('created_at') or '')==created)
        status=str(saved.get('status') or 'open'); status=status if status in {'open','resolved'} else 'open'
        if same:
            # Current status/replies win. Checkpoint-only replies are added back by
            # stable message identity, but existing current messages are untouched.
            tid=old_tid
        elif owner is None and not force_fresh:
            tid=old_tid; con.execute('INSERT INTO comment_threads(id,project_id,status,created_at,updated_at) VALUES(?,?,?,?,?)',(tid,pid,status,created or t,str(saved.get('updated_at') or t)))
        else:
            tid=int(con.execute('INSERT INTO comment_threads(project_id,status,created_at,updated_at) VALUES(?,?,?,?)',(pid,status,created or t,str(saved.get('updated_at') or t))).lastrowid)
        mapping[old_tid]=tid
        current_messages=allrows(con,'SELECT * FROM comment_messages WHERE thread_id=? ORDER BY id',(tid,))
        current_lineage={(int(m['id']),str(m.get('created_at') or '')) for m in current_messages}
        for msg in sorted(messages_by.get(old_tid,[]),key=lambda x:int(x.get('id') or 0)):
            body=_clean_comment_body(msg.get('body'))
            if not body: continue
            mid=int(msg.get('id') or 0); mcreated=str(msg.get('created_at') or '')
            if (mid,mcreated) in current_lineage: continue
            existing=one(con,'SELECT id FROM comment_messages WHERE id=?',(mid,)) if mid else None
            if mid and existing is None and not force_fresh:
                con.execute('INSERT INTO comment_messages(id,project_id,thread_id,body,created_at,updated_at) VALUES(?,?,?,?,?,?)',(mid,pid,tid,body,mcreated or created or t,str(msg.get('updated_at') or t)))
            else:
                con.execute('INSERT INTO comment_messages(project_id,thread_id,body,created_at,updated_at) VALUES(?,?,?,?,?)',(pid,tid,body,mcreated or created or t,str(msg.get('updated_at') or t)))
    return mapping

def _restore_current_comment_threads(con,pid:int,annotations:list[dict],threads:list[dict],messages:list[dict])->dict[int,int]:
    """Put captured current discussion state back after a whole-book restore."""
    needed={int(a.get('thread_id') or 0) for a in annotations if int(a.get('thread_id') or 0)>0}; mapping={}; t=storage_values.now(); by_messages={}
    for m in messages: by_messages.setdefault(int(m.get('thread_id') or 0),[]).append(m)
    for th in threads:
        old=int(th.get('id') or 0)
        if not old or old not in needed: continue
        created=str(th.get('created_at') or ''); owner=one(con,'SELECT * FROM comment_threads WHERE id=?',(old,)); same=bool(owner and int(owner.get('project_id') or 0)==pid and created and str(owner.get('created_at') or '')==created)
        status=str(th.get('status') or 'open'); status=status if status in {'open','resolved'} else 'open'
        if same:
            tid=old; con.execute('UPDATE comment_threads SET status=?,updated_at=? WHERE id=?',(status,str(th.get('updated_at') or t),tid))
        elif owner is None:
            tid=old; con.execute('INSERT INTO comment_threads(id,project_id,status,created_at,updated_at) VALUES(?,?,?,?,?)',(tid,pid,status,created or t,str(th.get('updated_at') or t)))
        else:
            tid=int(con.execute('INSERT INTO comment_threads(project_id,status,created_at,updated_at) VALUES(?,?,?,?)',(pid,status,created or t,str(th.get('updated_at') or t))).lastrowid)
        mapping[old]=tid
        for msg in by_messages.get(old,[]):
            mid=int(msg.get('id') or 0); mcreated=str(msg.get('created_at') or ''); body=_clean_comment_body(msg.get('body'))
            if not body: continue
            existing=one(con,'SELECT * FROM comment_messages WHERE id=?',(mid,)) if mid else None
            if existing and int(existing.get('thread_id') or 0)==tid and mcreated and str(existing.get('created_at') or '')==mcreated:
                con.execute('UPDATE comment_messages SET body=?,updated_at=? WHERE id=?',(body,str(msg.get('updated_at') or t),mid))
            elif existing is None and mid:
                con.execute('INSERT INTO comment_messages(id,project_id,thread_id,body,created_at,updated_at) VALUES(?,?,?,?,?,?)',(mid,pid,tid,body,mcreated or created or t,str(msg.get('updated_at') or t)))
            else:
                con.execute('INSERT INTO comment_messages(project_id,thread_id,body,created_at,updated_at) VALUES(?,?,?,?,?)',(pid,tid,body,mcreated or created or t,str(msg.get('updated_at') or t)))
    return mapping


def _overlay_current_annotations_after_book_restore(con,pid:int,current_annotations:list[dict],current_threads:list[dict],current_messages:list[dict],snapshot:dict,tab_id:int,report:dict):
    target_blocks=allrows(con,"SELECT b.id,b.text_content,b.position,b.chapter_id,c.position chapter_position FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.project_id=? AND b.tab_id=? ORDER BY c.position,b.position,b.id",(pid,tab_id))
    target_chapters={int(c['id']):c for c in allrows(con,"SELECT id,title,position FROM chapters WHERE project_id=? AND tab_id=?",(pid,tab_id))}
    saved_by_id={int(a.get('id') or 0):a for a in snapshot.get('annotations',[]) if int(a.get('id') or 0)>0}
    thread_map=_restore_current_comment_threads(con,pid,current_annotations,current_threads,current_messages); t=storage_values.now()
    for cur in current_annotations:
        saved=saved_by_id.get(int(cur.get('id') or 0)); same=_checkpoint_annotation_lineage(cur,saved)
        is_title=cur.get('chapter_id') is not None
        target_block=None; target_chapter=None; st=en=0; text=''
        if is_title:
            candidate_id=int(cur.get('chapter_id') or 0)
            target_chapter=target_chapters.get(candidate_id)
            if target_chapter is None and same and saved and saved.get('chapter_id') is not None:
                target_chapter=target_chapters.get(int(saved.get('chapter_id') or 0))
            if target_chapter is not None:
                text=str(target_chapter.get('title') or ''); selected=str(cur.get('selected_text') or '')
                pos=best_anchor_position(text,selected,int(cur.get('start_offset') or 0),str(cur.get('prefix_context') or ''),str(cur.get('suffix_context') or '')) if selected else max(0,min(int(cur.get('start_offset') or 0),len(text)))
                if pos is None and same and saved:
                    pos=max(0,min(int(saved.get('start_offset') or 0),len(text)))
                if pos is not None:
                    st=pos; en=max(st,min(st+len(selected) if selected else int(cur.get('end_offset') or st),len(text)))
        else:
            anchor=_checkpoint_anchor_in_blocks(cur,target_blocks,str(cur.get('block_id') or ''))
            if anchor is None and same:
                preferred=next((b for b in target_blocks if str(b['id'])==str(saved.get('block_id') or '')),None)
                if preferred:
                    text=str(preferred.get('text_content') or ''); st=max(0,min(int(saved.get('start_offset') or 0),len(text))); en=max(st,min(int(saved.get('end_offset') or 0),len(text))); anchor=(preferred,st,en)
                else:
                    anchor=_checkpoint_anchor_in_blocks(saved,target_blocks,None)
            if anchor is not None:
                target_block,st,en=anchor; text=str(target_block.get('text_content') or '')
        if (is_title and target_chapter is None) or (not is_title and target_block is None):
            kind='Note' if cur.get('annotation_type') in {'comment','note'} else 'Markup' if cur.get('annotation_type')=='highlight' else 'Annotation'
            report.setdefault('losses',[]).append({'kind':kind,'id':int(cur.get('id') or 0),'text':str(cur.get('selected_text') or '')[:120]}); _delete_polymorphic_references(con,pid,{'annotation':[int(cur.get('id') or 0)]}); continue
        aid=int(cur.get('id') or 0); owner=one(con,'SELECT * FROM annotations WHERE id=?',(aid,)) if aid else None
        block_id=target_block['id'] if target_block else None; chapter_id=int(target_chapter['id']) if target_chapter else None
        thread_id=thread_map.get(int(cur.get('thread_id') or 0)) if int(cur.get('thread_id') or 0)>0 else None
        if owner and same and str(owner.get('created_at') or '')==str(saved.get('created_at') or ''):
            con.execute("UPDATE annotations SET block_id=?,chapter_id=?,annotation_type=?,label_id=?,start_offset=?,end_offset=?,selected_text=?,prefix_context=?,suffix_context=?,thread_id=?,body=?,status=?,stale=0,updated_at=? WHERE id=?",(block_id,chapter_id,str(cur.get('annotation_type') or 'highlight'),cur.get('label_id'),st,en,str(cur.get('selected_text') or ''),text[max(0,st-45):st],text[en:en+45],thread_id,str(cur.get('body') or ''),str(cur.get('status') or 'open'),t,aid)); continue
        if owner:
            row=dict(owner); con.execute('DELETE FROM annotations WHERE id=?',(aid,)); cols=[k for k in row if k!='id']; vals=[row[k] for k in cols]; con.execute(f"INSERT INTO annotations({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",vals)
        vals=(aid,pid,block_id,chapter_id,str(cur.get('annotation_type') or 'highlight'),cur.get('label_id'),st,en,str(cur.get('selected_text') or ''),text[max(0,st-45):st],text[en:en+45],thread_id,str(cur.get('body') or ''),str(cur.get('status') or 'open'),0,str(cur.get('created_at') or t),t)
        if aid and one(con,'SELECT id FROM annotations WHERE id=?',(aid,)) is None:
            con.execute("INSERT INTO annotations(id,project_id,block_id,chapter_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,thread_id,body,status,stale,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",vals)
        else:
            con.execute("INSERT INTO annotations(project_id,block_id,chapter_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,thread_id,body,status,stale,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",vals[1:])
