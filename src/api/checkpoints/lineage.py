from __future__ import annotations

"""Checkpoint identity, Chapter lineage resolution, and public metadata projection."""

from fastapi import HTTPException

from backend.data_access import allrows, one
from . import capture

def _checkpoint_live_chapter_id_for_snapshot(con,pid:int,snapshot:dict) -> int | None:
    """Resolve only the Chapter identity captured by the Checkpoint.

    Paragraph ids are useful to disambiguate old same-timestamp imports, but they are
    never sufficient by themselves: prose can legitimately move to another Chapter.
    """
    if snapshot.get('scope')!='chapter': return None
    chapter=snapshot.get('chapter') or {}; source_id=int(chapter.get('id') or 0); created=str(chapter.get('created_at') or '')
    if source_id:
        live=one(con,"SELECT id,created_at FROM chapters WHERE id=? AND project_id=?",(source_id,pid))
        if live and (not created or str(live.get('created_at') or '')==created): return int(live['id'])
    if not created: return None
    candidates=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND created_at=? ORDER BY id",(pid,created))
    bids=[str(b.get('id')) for b in snapshot.get('blocks',[]) if b.get('id')]
    if candidates and bids:
        marks=','.join('?' for _ in bids); candidate_ids=[int(r['id']) for r in candidates]; cm=','.join('?' for _ in candidate_ids)
        rows=allrows(con,f"SELECT DISTINCT chapter_id FROM manuscript_blocks WHERE project_id=? AND chapter_id IN ({cm}) AND id IN ({marks})",(pid,*candidate_ids,*bids))
        ids={int(r['chapter_id']) for r in rows if r.get('chapter_id') is not None}
        if len(ids)==1: return next(iter(ids))
    return None

def _checkpoint_rows_same_chapter_lineage(left:dict,right:dict) -> bool:
    if left.get('scope')!='chapter' or right.get('scope')!='chapter': return False
    source_left=(int(left.get('source_chapter_id') or 0),str(left.get('source_chapter_created_at') or ''))
    source_right=(int(right.get('source_chapter_id') or 0),str(right.get('source_chapter_created_at') or ''))
    if source_left[0] and source_left[1] and source_left==source_right: return True
    current_left=(int(left.get('current_source_chapter_id') or 0),str(left.get('current_source_chapter_created_at') or ''))
    current_right=(int(right.get('current_source_chapter_id') or 0),str(right.get('current_source_chapter_created_at') or ''))
    return bool(current_left[0] and current_left[1] and current_left==current_right)

def _checkpoint_rebind_chapter_locators(con,pid:int,new_chapter_id:int,*identities:tuple[int|None,str|None]) -> None:
    live=one(con,"SELECT id,created_at FROM chapters WHERE id=? AND project_id=?",(new_chapter_id,pid))
    if not live: return
    created=str(live.get('created_at') or ''); clauses=[]; args=[]; seen=set()
    for raw_id,raw_created in identities:
        ident=(int(raw_id or 0),str(raw_created or ''))
        if not ident[0] or not ident[1] or ident in seen: continue
        seen.add(ident)
        clauses.append('(source_chapter_id=? AND source_chapter_created_at=?)'); args.extend(ident)
        clauses.append('(current_source_chapter_id=? AND current_source_chapter_created_at=?)'); args.extend(ident)
    if not clauses: return
    con.execute("UPDATE checkpoints SET current_source_chapter_id=?,current_source_chapter_created_at=? WHERE project_id=? AND scope='chapter' AND ("+' OR '.join(clauses)+')',(new_chapter_id,created,pid,*args))

def _checkpoint_rebind_chapter_lineage(con,pid:int,row:dict,new_chapter_id:int) -> None:
    _checkpoint_rebind_chapter_locators(con,pid,new_chapter_id,
        (row.get('source_chapter_id'),row.get('source_chapter_created_at')),
        (row.get('current_source_chapter_id'),row.get('current_source_chapter_created_at')))

def _checkpoint_meta(con,row:dict) -> dict:
    out={k:v for k,v in row.items() if k!='snapshot_blob'}
    project_row=one(con,"SELECT title FROM projects WHERE id=?",(out['project_id'],)) or {}
    if out['scope']=='chapter':
        locator_id=int(out.get('current_source_chapter_id') or 0); locator_created=str(out.get('current_source_chapter_created_at') or '')
        live=one(con,"SELECT id,created_at FROM chapters WHERE id=? AND project_id=?",(locator_id,out['project_id'])) if locator_id else None
        if live and locator_created and str(live.get('created_at') or '')==locator_created:
            current_id=int(live['id'])
        else:
            snapshot_row=row if 'snapshot_blob' in row else _checkpoint_row(con,int(out['project_id']),int(out['id']))
            current_id=_checkpoint_live_chapter_id_for_snapshot(con,int(out['project_id']),capture._decode_checkpoint_snapshot(con,snapshot_row))
            if current_id is not None:
                resolved=one(con,"SELECT created_at FROM chapters WHERE id=? AND project_id=?",(current_id,out['project_id']))
                con.execute("UPDATE checkpoints SET current_source_chapter_id=?,current_source_chapter_created_at=? WHERE id=?",(current_id,str((resolved or {}).get('created_at') or ''),out['id']))
                out['current_source_chapter_id']=current_id; out['current_source_chapter_created_at']=str((resolved or {}).get('created_at') or '')
        out['current_chapter_id']=current_id; out['source_exists']=current_id is not None
    else:
        out['current_chapter_id']=None
        out['source_exists']=bool(con.execute("SELECT 1 FROM document_tabs WHERE project_id=? AND is_manuscript=1 LIMIT 1",(out['project_id'],)).fetchone())
    fallback=(str(out.get('source_chapter_title') or '').strip() if out['scope']=='chapter' else str(out.get('source_tab_title') or '').strip())
    if not fallback: fallback=str(project_row.get('title') or ('Chapter' if out['scope']=='chapter' else 'Book')).strip()
    out['default_name']=fallback or ('Chapter' if out['scope']=='chapter' else 'Book')
    out['display_name']=str(out.get('name') or '').strip() or out['default_name']
    return out

def _checkpoint_row(con,pid:int,cid:int) -> dict:
    row=one(con,"SELECT * FROM checkpoints WHERE id=? AND project_id=?",(cid,pid))
    if not row: raise HTTPException(404,"Checkpoint not found")
    return row
