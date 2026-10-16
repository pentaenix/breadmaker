from __future__ import annotations

"""Checkpoint capture, snapshot encoding, and durable snapshot insertion."""

import json
import re
import zlib

from fastapi import HTTPException

from backend.data_access import allrows, one
from storage import assets as storage_assets
from storage import values as storage_values

def _checkpoint_annotation_rows(con, block_ids:list[str], chapter_ids:list[int]|None=None) -> list[dict]:
    chapter_ids=[int(x) for x in (chapter_ids or [])]
    clauses=[]; args=[]
    if block_ids:
        q=','.join('?' for _ in block_ids); clauses.append(f'a.block_id IN ({q})'); args.extend(block_ids)
    if chapter_ids:
        q=','.join('?' for _ in chapter_ids); clauses.append(f'a.chapter_id IN ({q})'); args.extend(chapter_ids)
    if not clauses: return []
    return allrows(con,f'''SELECT a.id,a.block_id,a.chapter_id,a.annotation_type,a.start_offset,a.end_offset,a.selected_text,a.prefix_context,a.suffix_context,a.thread_id,a.body,a.status,a.stale,a.created_at,
        a.label_id,l.name label_name,l.description label_description,l.color label_color,l.created_at label_created_at
        FROM annotations a LEFT JOIN markup_labels l ON l.id=a.label_id
        WHERE {' OR '.join(clauses)} ORDER BY a.id''',args)

def _checkpoint_comment_rows(con, annotations:list[dict]) -> tuple[list[dict],list[dict]]:
    thread_ids=sorted({int(a.get('thread_id') or 0) for a in annotations if int(a.get('thread_id') or 0)>0})
    if not thread_ids: return [],[]
    q=','.join('?' for _ in thread_ids)
    threads=allrows(con,f'''SELECT ct.id,ct.status,ct.created_at,ct.updated_at
        FROM comment_threads ct WHERE ct.id IN ({q}) ORDER BY ct.id''',thread_ids)
    messages=allrows(con,f'''SELECT id,thread_id,body,created_at,updated_at FROM comment_messages
        WHERE thread_id IN ({q}) ORDER BY thread_id,id''',thread_ids)
    return threads,messages

def _checkpoint_scene_rows(con, scene_ids:list[int]) -> list[dict]:
    if not scene_ids: return []
    q=','.join('?' for _ in scene_ids)
    return allrows(con,f"SELECT id,tab_id,position,title,description,created_at FROM scenes WHERE id IN ({q}) ORDER BY position,id",scene_ids)

def _capture_chapter_checkpoint(con,pid:int,chapter_id:int,*,include_scenes:bool=True,include_markup:bool=True,include_notes:bool=True) -> dict:
    ch=one(con,"SELECT id,tab_id,position,title,description,created_at FROM chapters WHERE id=? AND project_id=?",(chapter_id,pid))
    if not ch: raise HTTPException(404,"Chapter not found")
    tab=one(con,"SELECT id,title,created_at FROM document_tabs WHERE id=? AND project_id=?",(ch['tab_id'],pid))
    blocks=allrows(con,"SELECT id,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,scene_id FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(chapter_id,))
    bids=[b['id'] for b in blocks]; scene_ids=sorted({int(b['scene_id']) for b in blocks if b.get('scene_id') is not None})
    annotations=_checkpoint_annotation_rows(con,bids,[chapter_id])
    annotations=[a for a in annotations if (a.get('annotation_type')=='highlight' and include_markup) or (a.get('annotation_type') in {'comment','note'} and include_notes)]
    comment_threads,comment_messages=_checkpoint_comment_rows(con,[a for a in annotations if a.get('annotation_type') in {'comment','note'}]) if include_notes else ([],[])
    scenes=_checkpoint_scene_rows(con,scene_ids) if include_scenes else []
    return {"version":1,"scope":"chapter","captured_at":storage_values.now(),"tab":tab or {"id":ch['tab_id'],"title":""},"chapter":ch,"blocks":blocks,
            "annotations":annotations,"comment_threads":comment_threads,"comment_messages":comment_messages,"scenes":scenes,
            "include_scenes":bool(include_scenes),"include_markup":bool(include_markup),"include_notes":bool(include_notes)}

def _primary_manuscript_tab(con,pid:int) -> dict:
    tab=one(con,"SELECT id,title,description,is_manuscript,position,created_at FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(pid,))
    if not tab: raise HTTPException(409,"This project has no primary Manuscript tab")
    return tab

def _capture_book_checkpoint(con,pid:int,*,include_scenes:bool=True,include_markup:bool=True,include_notes:bool=True) -> dict:
    tab=_primary_manuscript_tab(con,pid)
    chapters=allrows(con,"SELECT id,tab_id,position,title,description,created_at FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(pid,tab['id']))
    cids=[c['id'] for c in chapters]; blocks=[]
    if cids:
        q=','.join('?' for _ in cids)
        blocks=allrows(con,f"SELECT id,chapter_id,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,scene_id FROM manuscript_blocks WHERE chapter_id IN ({q}) ORDER BY chapter_id,position,id",cids)
    bids=[b['id'] for b in blocks]; scene_ids=sorted({int(b['scene_id']) for b in blocks if b.get('scene_id') is not None})
    annotations=_checkpoint_annotation_rows(con,bids,cids)
    annotations=[a for a in annotations if (a.get('annotation_type')=='highlight' and include_markup) or (a.get('annotation_type') in {'comment','note'} and include_notes)]
    comment_threads,comment_messages=_checkpoint_comment_rows(con,[a for a in annotations if a.get('annotation_type') in {'comment','note'}]) if include_notes else ([],[])
    scenes=_checkpoint_scene_rows(con,scene_ids) if include_scenes else []
    return {"version":1,"scope":"book","captured_at":storage_values.now(),"tab":tab,"chapters":chapters,"blocks":blocks,
            "annotations":annotations,"comment_threads":comment_threads,"comment_messages":comment_messages,"scenes":scenes,
            "include_scenes":bool(include_scenes),"include_markup":bool(include_markup),"include_notes":bool(include_notes)}

def _checkpoint_word_count(snapshot:dict) -> int:
    text=' '.join(str(b.get('text_content') or '') for b in snapshot.get('blocks',[]))
    return len(re.findall(r"[\w’'-]+",text,flags=re.UNICODE))

def _encode_checkpoint_snapshot(snapshot:dict) -> tuple[bytes,str]:
    # Checkpoint snapshots are self-contained, compressed and hash-verified.
    # Referenced image bytes are protected separately by checkpoint_assets.
    raw=json.dumps(snapshot,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode('utf-8')
    return zlib.compress(raw,6),storage_values.sha(raw)

def _decode_checkpoint_snapshot(con,row:dict) -> dict:
    try:
        raw=zlib.decompress(bytes(row['snapshot_blob']))
        if storage_values.sha(raw)!=str(row['snapshot_hash']): raise ValueError('hash mismatch')
        data=json.loads(raw.decode('utf-8'))
        storage_assets.ensure_checkpoint_assets(con,int(row['id']))
    except Exception as exc:
        raise HTTPException(500,f'CHECKPOINT_005: Checkpoint data failed integrity validation: {exc}')
    if data.get('version')!=1 or data.get('scope') not in {'chapter','book'}:
        raise HTTPException(500,'CHECKPOINT_006: Checkpoint format is unsupported')
    data.setdefault('include_scenes',bool(row.get('include_scenes',1)))
    data.setdefault('include_markup',bool(row.get('include_markup',1)))
    data.setdefault('include_notes',bool(row.get('include_notes',1)))
    return data


def _insert_checkpoint(con,pid:int,snapshot:dict,name:str='',description:str='') -> int:
    blob,digest=_encode_checkpoint_snapshot(snapshot);t=storage_values.now();scope=snapshot['scope'];tab=snapshot.get('tab') or {};chapter=snapshot.get('chapter') or {}
    clean_name=' '.join((name or '').strip().split())[:180];clean_desc=(description or '').strip()[:12000]
    source_chapter_id=chapter.get('id');source_chapter_created_at=str(chapter.get('created_at') or '')
    cid=con.execute("""INSERT INTO checkpoints(project_id,scope,name,description,source_tab_id,source_tab_title,source_tab_created_at,source_chapter_id,source_chapter_title,source_chapter_created_at,source_chapter_position,current_source_chapter_id,current_source_chapter_created_at,snapshot_blob,snapshot_hash,word_count,include_scenes,include_markup,include_notes,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(pid,scope,clean_name,clean_desc,tab.get('id'),str(tab.get('title') or ''),str(tab.get('created_at') or ''),source_chapter_id,str(chapter.get('title') or ''),source_chapter_created_at,chapter.get('position'),source_chapter_id,source_chapter_created_at,blob,digest,_checkpoint_word_count(snapshot),int(bool(snapshot.get('include_scenes',True))),int(bool(snapshot.get('include_markup',True))),int(bool(snapshot.get('include_notes',True))),t,t)).lastrowid
    storage_assets.capture_checkpoint_assets(con,int(cid),snapshot)
    return int(cid)
