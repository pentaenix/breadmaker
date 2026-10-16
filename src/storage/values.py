from __future__ import annotations

"""Canonical storage timestamps, identifiers, and content hashes."""

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sha(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def review_snapshot_content_hash(review_id:str, source_generation:int, scope_type:str, permissions:dict, chapters:list[dict], scenes:list[dict], blocks:list[dict], questions:list[dict]|None=None, markers:list[dict]|None=None) -> str:
    canonical={
        'review_id':str(review_id),'source_generation':int(source_generation),'scope_type':str(scope_type),
        'permissions':{field:bool(permissions.get(field)) for field in ('permission_read','permission_comment','permission_suggest','permission_feedback')},
        'chapters':[{k:row.get(k) for k in ('source_chapter_id','source_tab_id','ordinal','position','title','is_implicit','description')} for row in chapters],
        'scenes':[{k:row.get(k) for k in ('source_scene_id','source_tab_id','ordinal','position','title','description')} for row in scenes],
        'blocks':[{k:row.get(k) for k in ('source_block_id','source_tab_id','source_chapter_id','source_scene_id','ordinal','position','block_type','semantic_style','content_html','text_content','image_path','content_hash')} for row in blocks],
    }
    if questions is not None:
        canonical['questions']=[{'source_questionnaire_id':str(row.get('source_questionnaire_id') or ''),'source_question_id':str(row.get('source_question_id') or ''),'questionnaire_name':str(row.get('questionnaire_name') or ''),'position':int(row.get('position') or 0),'prompt':str(row.get('prompt') or ''),'required':bool(row.get('required'))} for row in questions]
    if markers is not None:
        canonical['markers']=[{'source_marker_id':int(row.get('source_marker_id') or row.get('marker_id') or 0),'position':int(row.get('position') or 0),'name':str(row.get('name') or ''),'description':str(row.get('description') or ''),'color':str(row.get('color') or '')} for row in markers]
    return sha(json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(',',':')))


def sha_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest=hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def uid(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex


def block_content_hash(content_html:str='', text_content:str='', image_path:str='', image_width:int=100, image_footer:str='', image_description:str='', image_row_id:str='') -> str:
    base=str(content_html or '')+'\0'+str(text_content or '')+'\0'+str(image_path or '')
    width=int(image_width or 100); footer=str(image_footer or ''); description=str(image_description or ''); row_id=str(image_row_id or '')
    if width==100 and not footer and not description and not row_id: return sha(base)
    return sha(base+'\0'+str(width)+'\0'+footer+'\0'+description+'\0'+row_id)


def chapter_revision_from_rows(chapter, blocks) -> str:
    chapter_keys=('id','title','description','tab_id','position','is_implicit')
    block_keys=('id','position','scene_id','block_type','semantic_style','content_html','text_content','image_path','image_width','image_footer','image_description','image_row_id','content_hash')
    ch={key:chapter[key] for key in chapter_keys}
    rows=[{key:block[key] for key in block_keys} for block in blocks]
    payload={'chapter':ch,'blocks':rows}
    return sha(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str))


def chapter_revision(con:sqlite3.Connection, chapter_id:int) -> str:
    ch=con.execute("SELECT id,title,description,tab_id,position,is_implicit FROM chapters WHERE id=?",(chapter_id,)).fetchone()
    if not ch: raise ValueError('Chapter not found')
    blocks=[dict(r) for r in con.execute("SELECT id,position,scene_id,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash FROM manuscript_blocks WHERE chapter_id=? ORDER BY position,id",(chapter_id,))]
    return chapter_revision_from_rows(ch,blocks)
