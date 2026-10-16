from __future__ import annotations

"""Bookmark, annotation, and manuscript-range anchor repair primitives."""

from typing import Optional

from backend.data_access import allrows, one
from storage import values as storage_values

def _context_match_tail(expected:str,actual:str)->int:
    n=0
    for a,b in zip(reversed(expected or ''),reversed(actual or '')):
        if a!=b: break
        n+=1
    return n

def _context_match_head(expected:str,actual:str)->int:
    n=0
    for a,b in zip(expected or '',actual or ''):
        if a!=b: break
        n+=1
    return n

def best_anchor_position(text:str,selected:str,old_offset:int,prefix:str='',suffix:str='') -> Optional[int]:
    """Choose the occurrence that best matches original context and position.

    Repeated prose is common in editing. Never silently jump an annotation to the
    first identical phrase merely because ``str.find`` found it first.
    """
    if not selected: return old_offset if 0<=old_offset<=len(text) else None
    positions=[]; start=0
    while True:
        pos=text.find(selected,start)
        if pos<0: break
        positions.append(pos)
        if len(positions)>=2000: break
        start=pos+max(1,len(selected))
    if not positions: return None
    scored=[]
    for pos in positions:
        before=text[max(0,pos-len(prefix)):pos]; after=text[pos+len(selected):pos+len(selected)+len(suffix)]
        context=_context_match_tail(prefix,before)+_context_match_head(suffix,after)
        distance=abs(pos-old_offset)
        scored.append((context,-distance,pos))
    scored.sort(reverse=True)
    best=scored[0]
    # If two candidates have identical context and are equally close, guessing would
    # be silent corruption. Let the caller mark the anchor stale for review instead.
    if len(scored)>1 and scored[1][:2]==best[:2]: return None
    return best[2]

def _bookmark_context_values(start_text:str,end_text:str,start_offset:int,end_offset:int,same_block:bool=False):
    start_offset=max(0,min(int(start_offset),len(start_text))); end_offset=max(0,min(int(end_offset),len(end_text)))
    if same_block:
        selected=start_text[start_offset:end_offset]; ss=selected[:160]; es=selected[-160:]; es_start=max(start_offset,end_offset-len(es))
        return (ss,start_text[max(0,start_offset-45):start_offset],start_text[start_offset+len(ss):start_offset+len(ss)+45],es,start_text[max(0,es_start-45):es_start],start_text[end_offset:end_offset+45])
    ss=start_text[start_offset:min(len(start_text),start_offset+160)]; es=end_text[max(0,end_offset-160):end_offset]; es_start=max(0,end_offset-len(es))
    return (ss,start_text[max(0,start_offset-45):start_offset],start_text[start_offset+len(ss):start_offset+len(ss)+45],es,end_text[max(0,es_start-45):es_start],end_text[end_offset:end_offset+45])


def reanchor_block_bookmark(con,block_id:str,new_text:str):
    bm=one(con,"SELECT * FROM bookmarks WHERE target_type='range' AND (start_block_id=? OR end_block_id=?)",(block_id,block_id))
    if not bm: return
    updates={}; failed=False
    if bm.get('start_block_id')==block_id:
        needle=str(bm.get('start_selected_text') or '')
        pos=best_anchor_position(new_text,needle,int(bm.get('start_offset') or 0),str(bm.get('start_prefix_context') or ''),str(bm.get('start_suffix_context') or '')) if needle else min(int(bm.get('start_offset') or 0),len(new_text))
        if pos is None: failed=True; pos=min(int(bm.get('start_offset') or 0),len(new_text))
        updates['start_offset']=pos
    if bm.get('end_block_id')==block_id:
        needle=str(bm.get('end_selected_text') or ''); old_start=max(0,int(bm.get('end_offset') or 0)-len(needle))
        pos=best_anchor_position(new_text,needle,old_start,str(bm.get('end_prefix_context') or ''),str(bm.get('end_suffix_context') or '')) if needle else min(int(bm.get('end_offset') or 0),len(new_text))
        if pos is None: failed=True; end=min(int(bm.get('end_offset') or 0),len(new_text))
        else: end=min(len(new_text),pos+len(needle))
        updates['end_offset']=end
    if bm.get('start_block_id')==block_id and bm.get('end_block_id')==block_id and updates.get('end_offset',0)<updates.get('start_offset',0): failed=True; updates['end_offset']=updates['start_offset']
    updates['stale']=1 if failed else int(bool(bm.get('stale',0))); updates['updated_at']=storage_values.now(); sets=','.join(f"{k}=?" for k in updates)
    con.execute(f"UPDATE bookmarks SET {sets} WHERE project_id=?",(*updates.values(),bm['project_id']))
    # If the selected text disappeared from this block, preserve the old context.
    # Enter/split and other structural edits may create the destination block later in
    # the same save request; repair_stale_bookmark() needs the original needle/context
    # to find it there. Refreshing context from the truncated source block here would
    # erase the very evidence used for deterministic re-homing.
    cur=one(con,"SELECT * FROM bookmarks WHERE project_id=?",(bm['project_id'],))
    if not failed and cur and cur.get('start_block_id') and cur.get('end_block_id'):
        a=one(con,"SELECT text_content FROM manuscript_blocks WHERE id=?",(cur['start_block_id'],)); b=one(con,"SELECT text_content FROM manuscript_blocks WHERE id=?",(cur['end_block_id'],))
        if a and b:
            at=new_text if cur['start_block_id']==block_id else str(a.get('text_content') or ''); bt=new_text if cur['end_block_id']==block_id else str(b.get('text_content') or '')
            vals=_bookmark_context_values(at,bt,cur['start_offset'],cur['end_offset'],cur['start_block_id']==cur['end_block_id'])
            con.execute("UPDATE bookmarks SET start_selected_text=?,start_prefix_context=?,start_suffix_context=?,end_selected_text=?,end_prefix_context=?,end_suffix_context=? WHERE project_id=?",(*vals,bm['project_id']))


def repair_stale_bookmark(con,chapter_id:int):
    # A workspace project normally owns one SQLite project row, but defensive/imported
    # data may contain more than one. Never let an edit in one
    # project repair (or merely clear the stale flag on) another project's Bookmark.
    chapter=one(con,"SELECT project_id FROM chapters WHERE id=?",(chapter_id,))
    if not chapter: return
    pid=int(chapter['project_id'])
    bm=one(con,"SELECT * FROM bookmarks WHERE project_id=? AND target_type='range' AND stale=1",(pid,))
    if not bm: return
    blocks=allrows(con,"SELECT id,tab_id,text_content,position FROM manuscript_blocks WHERE project_id=? AND chapter_id=? ORDER BY position,id",(pid,chapter_id)); block_ids={str(b['id']) for b in blocks}
    if not blocks: return
    updates={}; unresolved=False
    for edge in ('start','end'):
        current=str(bm.get(f'{edge}_block_id') or ''); needle=str(bm.get(f'{edge}_selected_text') or '')
        if current and current not in block_ids: continue
        if not needle: continue
        matches=[]
        for b in blocks:
            text=str(b.get('text_content') or ''); start=0
            while True:
                pos=text.find(needle,start)
                if pos<0: break
                matches.append((b,pos)); start=pos+max(1,len(needle))
                if len(matches)>1: break
            if len(matches)>1: break
        if len(matches)==1:
            b,pos=matches[0]; updates[f'{edge}_block_id']=b['id']; updates[f'{edge}_offset']=pos if edge=='start' else pos+len(needle)
        else: unresolved=True
    if updates:
        sets=','.join(f"{k}=?" for k in updates); con.execute(f"UPDATE bookmarks SET {sets},updated_at=? WHERE project_id=?",(*updates.values(),storage_values.now(),bm['project_id']))
    cur=one(con,"SELECT * FROM bookmarks WHERE project_id=?",(bm['project_id'],))
    if cur and cur.get('start_block_id') and cur.get('end_block_id') and not unresolved:
        a=one(con,"SELECT tab_id,text_content FROM manuscript_blocks WHERE id=? AND project_id=?",(cur['start_block_id'],pid)); b=one(con,"SELECT tab_id,text_content FROM manuscript_blocks WHERE id=? AND project_id=?",(cur['end_block_id'],pid))
        if a and b and int(a['tab_id'])==int(b['tab_id']):
            at=str(a.get('text_content') or ''); bt=str(b.get('text_content') or '')
            start_needle=str(cur.get('start_selected_text') or ''); end_needle=str(cur.get('end_selected_text') or '')
            so=max(0,min(int(cur.get('start_offset') or 0),len(at))); eo=max(0,min(int(cur.get('end_offset') or 0),len(bt)))
            start_exact=(not start_needle) or at[so:so+len(start_needle)]==start_needle
            end_start=max(0,eo-len(end_needle)); end_exact=(not end_needle) or bt[end_start:eo]==end_needle
            # A range may span Chapters. A repair pass for Chapter A must not clear a
            # stale flag caused by an unresolved endpoint in Chapter B merely because
            # both block IDs still exist. Clear stale only when both stored endpoint
            # excerpts still match their actual anchored prose exactly.
            if start_exact and end_exact:
                vals=_bookmark_context_values(at,bt,so,eo,cur['start_block_id']==cur['end_block_id'])
                con.execute("UPDATE bookmarks SET start_selected_text=?,start_prefix_context=?,start_suffix_context=?,end_selected_text=?,end_prefix_context=?,end_suffix_context=?,stale=0,updated_at=? WHERE project_id=?",(*vals,storage_values.now(),bm['project_id']))


def _deterministic_bookmark_edge_match(blocks:list[dict], needle:str, prefix:str, suffix:str, old_offset:int):
    """Find one defensible exact-text Bookmark anchor across replacement prose.

    The selected text must match exactly. Context is only used to disambiguate exact
    matches; ties are rejected rather than guessed.
    """
    needle=str(needle or '')
    if not needle:
        return None
    candidates=[]
    for order,b in enumerate(blocks):
        text=str(b.get('text_content') or '')
        start=0
        while True:
            pos=text.find(needle,start)
            if pos<0:
                break
            before=text[max(0,pos-len(prefix)):pos] if prefix else ''
            after=text[pos+len(needle):pos+len(needle)+len(suffix)] if suffix else ''
            context=_context_match_tail(prefix,before)+_context_match_head(suffix,after)
            # Offset proximity is useful when several exact matches occur in one block,
            # but never outranks surrounding textual context.
            proximity=-abs(pos-int(old_offset or 0))
            candidates.append((context,proximity,-order,pos,b))
            start=pos+max(1,len(needle))
    if not candidates:
        return None
    candidates.sort(key=lambda x:(x[0],x[1],x[2],x[3]),reverse=True)
    best=candidates[0]
    # Equal contextual/proximity evidence means there is no deterministic winner.
    if len(candidates)>1 and candidates[1][:2]==best[:2]:
        return None
    return best[4],best[3]


def repair_orphaned_bookmark(con,pid:int,preferred_tab_id:int|None=None):
    """Repair a text Bookmark after destructive manuscript structure changes.

    Exact selected text + stored context may deterministically re-home the range. If
    that is impossible, keep the Bookmark visible at a safe manuscript location and
    mark it stale instead of silently deleting it or leaving invalid NULL anchors.
    """
    bm=one(con,"SELECT * FROM bookmarks WHERE project_id=? AND target_type='range'",(pid,))
    if not bm or (bm.get('start_block_id') and bm.get('end_block_id')):
        return

    tab_id=preferred_tab_id
    if tab_id is not None:
        exists=one(con,"SELECT id FROM document_tabs WHERE id=? AND project_id=?",(tab_id,pid))
        if not exists:
            tab_id=None
    for key in ('start_block_id','end_block_id'):
        if bm.get(key):
            row=one(con,"SELECT tab_id FROM manuscript_blocks WHERE id=? AND project_id=?",(bm[key],pid))
            if row:
                tab_id=int(row['tab_id'])
    if tab_id is None:
        main=one(con,"SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(pid,))
        tab_id=int(main['id']) if main else None
    if tab_id is None:
        return

    blocks=allrows(con,"SELECT b.id,b.text_content,b.position,c.position AS chapter_position FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.project_id=? AND b.tab_id=? ORDER BY c.position,b.position,b.id",(pid,tab_id))
    if not blocks:
        return

    resolved={}
    unresolved=False
    for edge in ('start','end'):
        existing=bm.get(f'{edge}_block_id')
        if existing:
            row=one(con,"SELECT id,text_content FROM manuscript_blocks WHERE id=? AND project_id=?",(existing,pid))
            if row:
                resolved[edge]=(row,int(bm.get(f'{edge}_offset') or 0))
                continue
        match=_deterministic_bookmark_edge_match(
            blocks,
            str(bm.get(f'{edge}_selected_text') or ''),
            str(bm.get(f'{edge}_prefix_context') or ''),
            str(bm.get(f'{edge}_suffix_context') or ''),
            int(bm.get(f'{edge}_offset') or 0),
        )
        if match:
            row,pos=match
            offset=pos if edge=='start' else pos+len(str(bm.get(f'{edge}_selected_text') or ''))
            resolved[edge]=(row,offset)
        else:
            unresolved=True

    if not unresolved and 'start' in resolved and 'end' in resolved:
        a,so=resolved['start']; b,eo=resolved['end']
        # A text Bookmark may span blocks but never Tabs. Both candidates came from the
        # same replacement Tab, so only range order needs validation here.
        block_order={str(row['id']):i for i,row in enumerate(blocks)}
        ai=block_order.get(str(a['id']),-1); bi=block_order.get(str(b['id']),-1)
        if ai>bi or (ai==bi and eo<so):
            unresolved=True
        else:
            at=str(a.get('text_content') or ''); bt=str(b.get('text_content') or '')
            so=max(0,min(int(so),len(at))); eo=max(0,min(int(eo),len(bt)))
            vals=_bookmark_context_values(at,bt,so,eo,str(a['id'])==str(b['id']))
            con.execute("UPDATE bookmarks SET start_block_id=?,end_block_id=?,start_offset=?,end_offset=?,start_selected_text=?,start_prefix_context=?,start_suffix_context=?,end_selected_text=?,end_prefix_context=?,end_suffix_context=?,stale=0,updated_at=? WHERE project_id=?",(a['id'],b['id'],so,eo,*vals,storage_values.now(),pid))
            return

    # No deterministic exact-text destination exists. Preserve the original context
    # for future recovery/review, but attach the ribbon to valid Current prose so the
    # project remains structurally sound and the Bookmark remains discoverable.
    fallback=blocks[0]
    text=str(fallback.get('text_content') or '')
    offset=min(max(0,int(bm.get('start_offset') or 0)),len(text))
    con.execute("UPDATE bookmarks SET start_block_id=?,end_block_id=?,start_offset=?,end_offset=?,stale=1,updated_at=? WHERE project_id=?",(fallback['id'],fallback['id'],offset,offset,storage_values.now(),pid))


def reanchor_block_annotations(con,block_id:str,new_text:str):
    """Best-effort resilient anchors using text, surrounding context and proximity."""
    anns=allrows(con,"SELECT * FROM annotations WHERE block_id=?",(block_id,))
    for a in anns:
        sel=a['selected_text'] or ''
        if not sel: continue
        pos=best_anchor_position(new_text,sel,a['start_offset'],a['prefix_context'],a['suffix_context'])
        if pos is not None:
            end=pos+len(sel); prefix=new_text[max(0,pos-45):pos]; suffix=new_text[end:end+45]
            con.execute("UPDATE annotations SET start_offset=?,end_offset=?,prefix_context=?,suffix_context=?,stale=0,updated_at=? WHERE id=?",(pos,end,prefix,suffix,storage_values.now(),a['id']))
        else:
            con.execute("UPDATE annotations SET stale=1,updated_at=? WHERE id=?",(storage_values.now(),a['id']))

def reanchor_chapter_title_annotations(con,chapter_id:int,new_title:str):
    """Keep Notes/Markup attached when a Chapter title is edited in place."""
    anns=allrows(con,"SELECT * FROM annotations WHERE chapter_id=? AND block_id IS NULL",(chapter_id,))
    for a in anns:
        sel=a.get('selected_text') or ''
        if not sel: continue
        pos=best_anchor_position(new_title,sel,a.get('start_offset') or 0,a.get('prefix_context') or '',a.get('suffix_context') or '')
        if pos is not None:
            end=pos+len(sel)
            con.execute("UPDATE annotations SET start_offset=?,end_offset=?,prefix_context=?,suffix_context=?,stale=0,updated_at=? WHERE id=?",(pos,end,new_title[max(0,pos-45):pos],new_title[end:end+45],storage_values.now(),a['id']))
        else:
            con.execute("UPDATE annotations SET stale=1,updated_at=? WHERE id=?",(storage_values.now(),a['id']))

def repair_stale_annotations(con,chapter_id:int):
    """Move stale anchors to a surviving block when their selected text moved there.

    This is especially important when Enter splits a paragraph: the original block ID
    remains on the first half, while annotations on the second half must follow text
    into the newly created block instead of silently becoming detached.
    """
    blocks=allrows(con,"SELECT id,text_content,position FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(chapter_id,))
    for a in allrows(con,"SELECT a.* FROM annotations a JOIN manuscript_blocks b ON b.id=a.block_id WHERE b.chapter_id=? AND a.stale=1",(chapter_id,)):
        sel=a['selected_text'] or ''
        if not sel: continue
        candidates=[]
        for b in blocks:
            pos=(b['text_content'] or '').find(sel)
            if pos>=0: candidates.append((b,pos))
        if len(candidates)==1:
            b,pos=candidates[0]; text=b['text_content'] or ''; end=pos+len(sel)
            con.execute("UPDATE annotations SET block_id=?,start_offset=?,end_offset=?,prefix_context=?,suffix_context=?,stale=0,updated_at=? WHERE id=?",(b['id'],pos,end,text[max(0,pos-45):pos],text[end:end+45],storage_values.now(),a['id']))

def _rehome_map_range_anchors_for_block(con,old_block_id:str,candidates:list[dict],nearest:dict):
    """Keep current Map manuscript-range anchors attached through paragraph merges."""
    rows=allrows(con,"SELECT * FROM map_items WHERE (start_anchor_type='manuscript_range' AND start_block_id=?) OR (end_anchor_type='manuscript_range' AND end_block_id=?)",(old_block_id,old_block_id))
    for item in rows:
        parts=[part.strip() for part in (item.get('excerpt') or '').split(' … ') if part.strip()]
        updates={}
        for edge in ('start','end'):
            if item.get(f'{edge}_anchor_type')!='manuscript_range' or item.get(f'{edge}_block_id')!=old_block_id: continue
            needle=(parts[0] if edge=='start' else parts[-1]) if parts else ''
            matches=[]
            if needle:
                for block in candidates:
                    pos=(block.get('text_content') or '').find(needle)
                    if pos>=0: matches.append((block,pos))
            if len(matches)==1:
                target,pos=matches[0]; offset=pos if edge=='start' else pos+len(needle)
            else:
                target=nearest; offset=0 if edge=='start' else len(target.get('text_content') or '')
            updates[f'{edge}_block_id']=target['id']; updates[f'{edge}_offset']=max(0,offset)
        if updates:
            updates['updated_at']=storage_values.now(); sets=', '.join(f"{key}=?" for key in updates)
            con.execute(f"UPDATE map_items SET {sets} WHERE id=?",(*updates.values(),item['id']))


def rehome_deleted_block_refs(con,old:dict,chapter_id:int):
    """Never let a paragraph merge delete editing metadata by FK cascade."""
    candidates=allrows(con,"SELECT id,text_content,position,scene_id FROM manuscript_blocks WHERE chapter_id=? AND id<>? ORDER BY position,id",(chapter_id,old['id']))
    if not candidates: return
    nearest=min(candidates,key=lambda b:abs(float(b['position'])-float(old['position'])))
    for a in allrows(con,"SELECT * FROM annotations WHERE block_id=?",(old['id'],)):
        sel=a['selected_text'] or ''
        matches=[]
        if sel:
            for b in candidates:
                pos=(b['text_content'] or '').find(sel)
                if pos>=0: matches.append((b,pos))
        if len(matches)==1:
            target,pos=matches[0]; stale=0; end=pos+len(sel)
        else:
            target=nearest; pos=0; end=0; stale=1
        con.execute("UPDATE annotations SET block_id=?,start_offset=?,end_offset=?,stale=?,updated_at=? WHERE id=?",(target['id'],pos,end,stale,storage_values.now(),a['id']))
    bm=one(con,"SELECT * FROM bookmarks WHERE target_type='range' AND (start_block_id=? OR end_block_id=?)",(old['id'],old['id']))
    if bm:
        updates={}; stale=int(bool(bm.get('stale',0)))
        for edge in ('start','end'):
            if bm.get(f'{edge}_block_id')!=old['id']: continue
            needle=str(bm.get(f'{edge}_selected_text') or ''); matches=[]
            for b in candidates:
                pos=(b.get('text_content') or '').find(needle) if needle else -1
                if pos>=0: matches.append((b,pos))
            if len(matches)==1:
                target,pos=matches[0]; offset=pos if edge=='start' else pos+len(needle)
            else:
                target=nearest; offset=0 if edge=='start' else len(target.get('text_content') or ''); stale=1
            updates[f'{edge}_block_id']=target['id']; updates[f'{edge}_offset']=offset
        if updates:
            sets=','.join(f"{k}=?" for k in updates); con.execute(f"UPDATE bookmarks SET {sets},stale=?,updated_at=? WHERE project_id=?",(*updates.values(),stale,storage_values.now(),bm['project_id']))
    # Generic manuscript-range links use stable block anchors too. Re-home both
    # ends before deletion so a paragraph merge cannot silently turn a valid
    # reference into a dangling/partial range.
    con.execute("UPDATE references_map SET start_block_id=? WHERE start_block_id=?",(nearest['id'],old['id']))
    con.execute("UPDATE references_map SET end_block_id=? WHERE end_block_id=?",(nearest['id'],old['id']))
    _rehome_map_range_anchors_for_block(con,old['id'],candidates,nearest)
