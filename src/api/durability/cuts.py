from __future__ import annotations

"""Cuts library and .breadcuts HTTP endpoints."""

import html
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from api.durability.contracts import CutCreate, CutInsert, CutUpdate
from backend.content import _text_from_inline_html, sanitize_inline_html
from backend.data_access import allrows, one, project
from backend.dependencies import feature_call
from backend.environment import APP_VERSION, _HOSTED_LIMITS, runtime
from backend.request_context import _enforce_hosted_project_storage
from security import content_security
from security.image_security import UnsafeImage, inspect_hosted_image_bytes
from storage import mutations as storage_mutations
from storage import values as storage_values
from workspace.filesystem import safe_name

_bookmark_context_values = feature_call('manuscript.anchors', '_bookmark_context_values')
_normalize_image_rows = feature_call('manuscript.images', '_normalize_image_rows')
save_upload_limited = feature_call('import_export.uploads', 'save_upload_limited')

router = APIRouter()

_CUT_ALLOWED={'p','br','strong','b','em','i','u','img','figure','figcaption'}
_CUT_IMAGE_EXTS={'.png','.jpg','.jpeg','.webp','.gif','.avif','.bmp'}
_IMAGE_MIME_EXT={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/gif':'.gif','image/avif':'.avif','image/bmp':'.bmp'}

def _normalize_project_image_ext(ext:str='',content_type:str='')->str:
    value=str(ext or '').strip().lower()
    if value and not value.startswith('.'): value='.'+value
    if value=='.jpeg': value='.jpg'
    if not value: value=_IMAGE_MIME_EXT.get(str(content_type or '').strip().lower(),'')
    if value not in {'.png','.jpg','.webp','.gif','.avif','.bmp'}: raise HTTPException(400,'Image format not supported')
    return value

def _store_content_addressed_image_blob(blob:bytes,ext:str,*,content_type:str=''):
    ext=_normalize_project_image_ext(ext,content_type)
    if _HOSTED_LIMITS is not None:
        try: info=inspect_hosted_image_bytes(blob,ext,byte_limit=_HOSTED_LIMITS.image_bytes)
        except UnsafeImage as exc: raise HTTPException(415,'Hosted Breadmaker accepts only structurally valid PNG, JPEG, or WebP images') from exc
        ext=info.extension
    return runtime.assets.put_blob(blob,ext)

def _cut_image_figure_html(block:dict)->str:
    width=max(5,min(100,int(block.get('image_width') or 100))); row=str(block.get('image_row_id') or '').strip()[:80]
    row_attr=f' data-row="{html.escape(row,quote=True)}"' if row else ''
    footer=str(block.get('image_footer') or ''); caption=f'<figcaption>{html.escape(footer)}</figcaption>' if footer else ''
    return f'<figure data-width="{width}"{row_attr}><img src="{html.escape(str(block.get("image_path") or ""),quote=True)}" alt="{html.escape(str(block.get("image_description") or ""),quote=True)}">{caption}</figure>'

def sanitize_cut_html(fragment:str)->str:
    return content_security.sanitize_cut_html(fragment)

def _cut_text(content_html:str)->str:
    soup=BeautifulSoup(content_html or '', 'html.parser'); parts=[]
    for node in soup.contents:
        if getattr(node,'name',None)=='p': parts.append(_text_from_inline_html(node.decode_contents()))
        elif getattr(node,'name',None)=='img': parts.append('[Image]')
        elif getattr(node,'name',None)=='figure':
            caption=node.find('figcaption'); parts.append('[Image]'+((' '+caption.get_text(' ',strip=True)) if caption else ''))
    return '\n'.join(parts)

def _cut_chapter_label(con:sqlite3.Connection,ch:dict)->str:
    title=str(ch.get('title') or '').strip()
    rows=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(ch['project_id'],ch.get('tab_id')))
    ordinal=next((i+1 for i,row in enumerate(rows) if int(row['id'])==int(ch['id'])),None)
    if title and re.match(r'^chapter\s+\d+\b',title,re.I): return title
    if ordinal and title: return f"Chapter {ordinal}: {title}"
    if ordinal: return f"Chapter {ordinal}"
    return title

def _cut_scene_label(con:sqlite3.Connection,pid:int,chapter_id:int|None,scene_id:int|None)->str:
    if scene_id is None or chapter_id is None: return ''
    scene=one(con,"SELECT s.id,s.title FROM scenes s WHERE s.id=? AND s.project_id=? AND EXISTS(SELECT 1 FROM manuscript_blocks b WHERE b.project_id=? AND b.chapter_id=? AND b.scene_id=s.id)",(scene_id,pid,pid,chapter_id))
    return str(scene.get('title') or '').strip() if scene else ''

def _cut_insert_items(content_html:str)->list[dict]:
    safe=sanitize_cut_html(content_html); soup=BeautifulSoup(safe,'html.parser'); items=[]
    for node in soup.contents:
        if getattr(node,'name',None)=='p':
            inner=node.decode_contents(); text=_text_from_inline_html(inner)
            items.append({'block_type':'paragraph','semantic_style':'body','content_html':sanitize_inline_html(inner,text),'text_content':text,'image_path':''})
        elif getattr(node,'name',None)=='img':
            src=str(node.get('src') or '')
            if content_security.is_safe_project_asset_path(src): items.append({'block_type':'image','semantic_style':'image','content_html':'','text_content':'','image_path':src,'image_width':100,'image_footer':'','image_description':str(node.get('alt') or '')[:12000],'image_row_id':''})
        elif getattr(node,'name',None)=='figure':
            img=node.find('img'); src=str(img.get('src') or '') if img else ''
            if content_security.is_safe_project_asset_path(src):
                try: width=max(5,min(100,int(node.get('data-width') or 100)))
                except (TypeError,ValueError): width=100
                caption=node.find('figcaption'); footer=caption.get_text(' ',strip=True)[:4000] if caption else ''
                items.append({'block_type':'image','semantic_style':'image','content_html':'','text_content':'','image_path':src,'image_width':width,'image_footer':footer,'image_description':str(img.get('alt') or '')[:12000],'image_row_id':str(node.get('data-row') or '')[:80]})
    return items

def _inline_node_len(node)->int:
    if isinstance(node,NavigableString): return len(str(node))
    if getattr(node,'name',None)=='br': return 1
    return sum(_inline_node_len(child) for child in getattr(node,'contents',[]))

def _split_inline_node(node,offset:int)->tuple[str,str]:
    if isinstance(node,NavigableString):
        text=str(node); n=max(0,min(int(offset),len(text))); return html.escape(text[:n]),html.escape(text[n:])
    name=getattr(node,'name',None)
    if name=='br': return ('<br>','') if offset>=1 else ('','<br>')
    children=list(getattr(node,'contents',[])); left=[]; right=[]; remain=max(0,int(offset))
    for child in children:
        length=_inline_node_len(child)
        if remain<=0: right.append(str(child)); continue
        if remain>=length: left.append(str(child)); remain-=length; continue
        a,b=_split_inline_node(child,remain); left.append(a); right.append(b); remain=0
    if not name: return ''.join(left),''.join(right)
    return (f'<{name}>{"".join(left)}</{name}>' if left else '', f'<{name}>{"".join(right)}</{name}>' if right else '')

def _split_inline_html(fragment:str,offset:int)->tuple[str,str]:
    soup=BeautifulSoup(fragment or '', 'html.parser'); left=[]; right=[]; remain=max(0,int(offset))
    for child in list(soup.contents):
        length=_inline_node_len(child)
        if remain<=0: right.append(str(child)); continue
        if remain>=length: left.append(str(child)); remain-=length; continue
        a,b=_split_inline_node(child,remain); left.append(a); right.append(b); remain=0
    return ''.join(left),''.join(right)

def _move_split_anchors(con:sqlite3.Connection,old_id:str,right_id:str,offset:int)->None:
    # A manuscript range can span two blocks, so references/map anchors can preserve
    # their logical endpoint exactly when a Cut insertion splits the paragraph.
    con.execute("UPDATE references_map SET start_block_id=?,start_offset=start_offset-? WHERE start_block_id=? AND start_offset>=?",(right_id,offset,old_id,offset))
    con.execute("UPDATE references_map SET end_block_id=?,end_offset=end_offset-? WHERE end_block_id=? AND end_offset>=?",(right_id,offset,old_id,offset))
    con.execute("UPDATE map_items SET start_block_id=?,start_offset=start_offset-? WHERE start_block_id=? AND start_offset>=?",(right_id,offset,old_id,offset))
    con.execute("UPDATE map_items SET end_block_id=?,end_offset=end_offset-? WHERE end_block_id=? AND end_offset>=?",(right_id,offset,old_id,offset))
    # An annotation cannot span blocks. Move annotations wholly to the right; mark a
    # genuinely split annotation stale rather than silently changing its meaning.
    con.execute("UPDATE annotations SET block_id=?,start_offset=start_offset-?,end_offset=end_offset-?,updated_at=? WHERE block_id=? AND start_offset>=?",(right_id,offset,offset,storage_values.now(),old_id,offset))
    con.execute("UPDATE annotations SET end_offset=?,stale=1,updated_at=? WHERE block_id=? AND start_offset<? AND end_offset>?",(offset,storage_values.now(),old_id,offset,offset))
    # Text Bookmarks can span blocks, so preserve their exact range through the split.
    bm=one(con,"SELECT * FROM bookmarks WHERE target_type='range' AND (start_block_id=? OR end_block_id=?)",(old_id,old_id))
    if bm:
        updates={}
        if bm.get('start_block_id')==old_id and int(bm.get('start_offset') or 0)>=offset:
            updates['start_block_id']=right_id; updates['start_offset']=int(bm.get('start_offset') or 0)-offset
        if bm.get('end_block_id')==old_id and int(bm.get('end_offset') or 0)>offset:
            updates['end_block_id']=right_id; updates['end_offset']=int(bm.get('end_offset') or 0)-offset
        if updates:
            updates['updated_at']=storage_values.now(); sets=','.join(f"{k}=?" for k in updates)
            con.execute(f"UPDATE bookmarks SET {sets} WHERE project_id=?",(*updates.values(),bm['project_id']))
        cur=one(con,"SELECT * FROM bookmarks WHERE project_id=?",(bm['project_id'],))
        if cur and cur.get('start_block_id') and cur.get('end_block_id'):
            a=one(con,"SELECT text_content FROM manuscript_blocks WHERE id=?",(cur['start_block_id'],)); b=one(con,"SELECT text_content FROM manuscript_blocks WHERE id=?",(cur['end_block_id'],))
            if a and b:
                vals=_bookmark_context_values(str(a.get('text_content') or ''),str(b.get('text_content') or ''),int(cur.get('start_offset') or 0),int(cur.get('end_offset') or 0),cur['start_block_id']==cur['end_block_id'])
                con.execute("UPDATE bookmarks SET start_selected_text=?,start_prefix_context=?,start_suffix_context=?,end_selected_text=?,end_prefix_context=?,end_suffix_context=? WHERE project_id=?",(*vals,bm['project_id']))

@router.get("/api/cuts")
def cuts():
    with storage_mutations.tx() as con:
        p=project(con); rows=allrows(con,"SELECT cuts.*,chapters.title source_title FROM cuts LEFT JOIN chapters ON chapters.id=cuts.source_chapter_id WHERE cuts.project_id=? ORDER BY cuts.id DESC",(p['id'],))
        for row in rows:
            row['content_text']=_cut_text(row.get('content_html') or '')
            parts=[str(row.get('source_project') or '').strip(),str(row.get('source_chapter') or '').strip(),str(row.get('source_scene') or '').strip()]
            row['source_display']=' | '.join(part for part in parts if part) or row.get('source_label') or (f"Chapter: {row.get('source_title')}" if row.get('source_title') else '')
        return rows

@router.post("/api/cuts")
def create_cut(body:CutCreate):
    safe=sanitize_cut_html(body.content_html); title=(body.title.strip() or 'Cut')[:240]; note=(body.note or '').strip()[:10000]
    with storage_mutations.tx() as con:
        p=project(con); source_label=''; source_project=str(p.get('title') or 'Project').strip()[:240]; source_chapter=''; source_scene=''
        if body.source_chapter_id is not None:
            ch=one(con,"SELECT id,project_id,tab_id,position,title FROM chapters WHERE id=? AND project_id=?",(body.source_chapter_id,p['id']))
            if not ch: raise HTTPException(400,"Source chapter no longer exists")
            source_chapter=_cut_chapter_label(con,ch)[:240]; source_label=f"Chapter: {ch['title']}"[:240]
            source_scene=_cut_scene_label(con,p['id'],body.source_chapter_id,body.source_scene_id)[:240]
        x=con.execute("INSERT INTO cuts(project_id,title,content_html,source_chapter_id,created_at,note,source_label,source_project,source_chapter,source_scene) VALUES(?,?,?,?,?,?,?,?,?,?)",(p['id'],title,safe,body.source_chapter_id,storage_values.now(),note,source_label,source_project,source_chapter,source_scene)).lastrowid
        return {"id":x}

@router.patch("/api/cuts/{cid}")
def update_cut(cid:int,body:CutUpdate):
    with storage_mutations.tx() as con:
        p=project(con); row=one(con,"SELECT id FROM cuts WHERE id=? AND project_id=?",(cid,p['id']))
        if not row: raise HTTPException(404,"Cut not found")
        if body.note is not None: con.execute("UPDATE cuts SET note=? WHERE id=?",((body.note or '').strip()[:10000],cid))
    return {"ok":True}

@router.post("/api/cuts/{cid}/insert")
def insert_cut(cid:int,body:CutInsert):
    with storage_mutations.tx() as con:
        p=project(con)
        cut=one(con,"SELECT * FROM cuts WHERE id=? AND project_id=?",(cid,p['id']))
        if not cut: raise HTTPException(404,"Cut not found")
        target=one(con,"SELECT b.*,c.tab_id FROM manuscript_blocks b JOIN chapters c ON c.id=b.chapter_id WHERE b.id=? AND b.chapter_id=? AND b.project_id=?",(body.block_id,body.chapter_id,p['id']))
        if not target or target['block_type']!='paragraph': raise HTTPException(400,"Place the cursor inside manuscript text before inserting a Cut")
        text=str(target.get('text_content') or ''); offset=max(0,min(int(body.offset),len(text))); items=_cut_insert_items(cut['content_html'])
        if not items: raise HTTPException(400,"This Cut has no insertable content")
        t=storage_values.now(); pos=int(target['position']); scene_id=target.get('scene_id'); created_ids=[]; row_ids={}
        if offset==0:
            con.execute("UPDATE manuscript_blocks SET position=position+? WHERE chapter_id=? AND position>=?",(len(items),body.chapter_id,pos))
            first_pos=pos
        elif offset==len(text):
            con.execute("UPDATE manuscript_blocks SET position=position+? WHERE chapter_id=? AND position>?",(len(items),body.chapter_id,pos))
            first_pos=pos+1
        else:
            left_html,right_html=_split_inline_html(str(target.get('content_html') or ''),offset); left_text=text[:offset]; right_text=text[offset:]
            left_html=sanitize_inline_html(left_html,left_text); right_html=sanitize_inline_html(right_html,right_text)
            con.execute("UPDATE manuscript_blocks SET content_html=?,text_content=?,content_hash=?,updated_at=? WHERE id=?",(left_html,left_text,storage_values.sha(left_html+'\0'+left_text+'\0'),t,target['id']))
            con.execute("UPDATE manuscript_blocks SET position=position+? WHERE chapter_id=? AND position>?",(len(items)+1,body.chapter_id,pos))
            first_pos=pos+1; right_id=storage_values.uid('b_'); right_pos=first_pos+len(items)
            con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(right_id,p['id'],target['tab_id'],body.chapter_id,scene_id,right_pos,'paragraph',target.get('semantic_style') or 'body',right_html,right_text,'',storage_values.sha(right_html+'\0'+right_text+'\0'),t,t))
            _move_split_anchors(con,target['id'],right_id,offset)
        for index,item in enumerate(items):
            bid=storage_values.uid('b_'); created_ids.append(bid); ih=item['content_html']; txt=item['text_content']; image=item['image_path']; image_width=int(item.get('image_width') or 100); image_footer=str(item.get('image_footer') or ''); image_description=str(item.get('image_description') or '')
            token=str(item.get('image_row_id') or '').strip(); image_row_id=row_ids.setdefault(token,storage_values.uid('image_row_')) if token else ''
            digest=storage_values.block_content_hash(ih,txt,image,image_width,image_footer,image_description,image_row_id)
            con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,scene_id,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,p['id'],target['tab_id'],body.chapter_id,scene_id,first_pos+index,item['block_type'],item['semantic_style'],ih,txt,image,image_width,image_footer,image_description,image_row_id,digest,t,t))
        _normalize_image_rows(con,body.chapter_id,t)
        con.execute("DELETE FROM cuts WHERE id=?",(cid,)); con.execute("UPDATE chapters SET updated_at=? WHERE id=?",(t,body.chapter_id)); con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,p['id']))
        return {"ok":True,"chapter_id":body.chapter_id,"inserted_block_ids":created_ids}

@router.delete("/api/cuts/{cid}")
def delete_cut(cid:int):
    with storage_mutations.tx() as con:
        p=project(con);row=one(con,'SELECT id FROM cuts WHERE id=? AND project_id=?',(cid,p['id']))
        if not row: raise HTTPException(404,'Cut not found')
        con.execute('DELETE FROM cuts WHERE id=?',(cid,))
    return {'ok':True}

@router.get("/api/cuts/export")
def export_cuts_package():
    with storage_mutations.tx() as con:
        p=project(con); rows=allrows(con,"SELECT title,content_html,note,source_label,source_project,source_chapter,source_scene,created_at FROM cuts WHERE project_id=? ORDER BY id",(p['id'],)); project_title=str(p.get('title') or 'Project')
    fd,tmp_name=tempfile.mkstemp(prefix='breadmaker-cuts-',suffix='.breadcuts'); os.close(fd); out=Path(tmp_name)
    try:
        manifest={'package_format':'breadmaker-cuts','package_version':1,'app_version':APP_VERSION,'exported_at':storage_values.now(),'source_project':project_title,'cuts':[],'assets':{}}
        asset_paths=set()
        for row in rows:
            safe=sanitize_cut_html(row.get('content_html') or '')
            for img in BeautifulSoup(safe,'html.parser').find_all('img'):
                src=str(img.get('src') or '')
                if content_security.is_safe_project_asset_path(src): asset_paths.add(src)
            manifest['cuts'].append({'title':row.get('title') or 'Cut','content_html':safe,'note':row.get('note') or '','source_label':row.get('source_label') or '','source_project':row.get('source_project') or project_title,'source_chapter':row.get('source_chapter') or '','source_scene':row.get('source_scene') or '','created_at':row.get('created_at') or ''})
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for src in sorted(asset_paths):
                name=Path(src.removeprefix('/api/assets/')).name
                if runtime.assets.exists(name):
                    package_path=f'assets/{name}'; z.writestr(package_path,runtime.assets.read_blob(name)); manifest['assets'][src]=package_path
            z.writestr('cuts.json',json.dumps(manifest,ensure_ascii=False,separators=(',',':')))
    except Exception:
        out.unlink(missing_ok=True); raise
    filename=safe_name(project_title)+' Cuts.breadcuts'
    return FileResponse(out,filename=filename,media_type='application/octet-stream',background=BackgroundTask(out.unlink,missing_ok=True))

@router.post("/api/cuts/import")
async def import_cuts_package(file:UploadFile=File(...)):
    suffix=Path(file.filename or 'cuts.breadcuts').suffix.lower()
    if suffix!='.breadcuts': raise HTTPException(400,'CUT_IMPORT_001: Choose a .breadcuts file.')
    fd,tmp_name=tempfile.mkstemp(prefix='breadmaker-cuts-import-',suffix='.breadcuts'); os.close(fd); tmp=Path(tmp_name); created_assets=[]; committed=False
    try:
        await save_upload_limited(file,tmp,_HOSTED_LIMITS.cuts_bytes if _HOSTED_LIMITS is not None else 1024*1024*1024)
        try:
            with zipfile.ZipFile(tmp) as z:
                infos=z.infolist(); expanded=sum(i.file_size for i in infos)
                cut_expanded_limit=min(2*1024*1024*1024,_HOSTED_LIMITS.max_project_bytes if _HOSTED_LIMITS is not None else 2*1024*1024*1024)
                if len(infos)>20000 or expanded>cut_expanded_limit: raise HTTPException(413,'CUT_IMPORT_002: Cuts package expands beyond the safe import limit.')
                _enforce_hosted_project_storage(additional_bytes=expanded)
                if 'cuts.json' not in z.namelist(): raise HTTPException(400,'CUT_IMPORT_003: This is not a Breadmaker Cuts package.')
                info=z.getinfo('cuts.json')
                if info.file_size>100*1024*1024: raise HTTPException(413,'CUT_IMPORT_004: Cuts manifest is too large.')
                manifest=json.loads(z.read('cuts.json').decode('utf-8'))
                if manifest.get('package_format')!='breadmaker-cuts' or int(manifest.get('package_version',0))!=1: raise HTTPException(400,'CUT_IMPORT_005: Unsupported Cuts package format.')
                imported=manifest.get('cuts'); asset_map=manifest.get('assets') or {}
                if not isinstance(imported,list) or len(imported)>100000: raise HTTPException(400,'CUT_IMPORT_006: Cuts manifest is invalid.')
                rewritten_assets={}
                for old_src,package_path in asset_map.items():
                    if not isinstance(old_src,str) or not content_security.is_safe_project_asset_path(old_src) or not isinstance(package_path,str) or package_path not in z.namelist(): continue
                    zi=z.getinfo(package_path)
                    image_limit=_HOSTED_LIMITS.image_bytes if _HOSTED_LIMITS is not None else 50*1024*1024
                    if zi.file_size>image_limit: raise HTTPException(413,f'CUT_IMPORT_007: An imported image exceeds {image_limit//(1024*1024)} MB.')
                    ext=Path(package_path).suffix.lower()
                    if ext not in _CUT_IMAGE_EXTS: continue
                    stored=_store_content_addressed_image_blob(z.read(package_path),ext)
                    if stored.created: created_assets.append(stored)
                    rewritten_assets[old_src]=stored.api_path
        except zipfile.BadZipFile: raise HTTPException(400,'CUT_IMPORT_003: This is not a valid Breadmaker Cuts package.')
        count=0
        with storage_mutations.tx() as con:
            p=project(con)
            for raw in imported:
                if not isinstance(raw,dict): continue
                safe=sanitize_cut_html(str(raw.get('content_html') or ''))
                soup=BeautifulSoup(safe,'html.parser')
                for img in soup.find_all('img'):
                    src=str(img.get('src') or '')
                    if src in rewritten_assets: img['src']=rewritten_assets[src]
                    elif content_security.is_safe_project_asset_path(src): img.decompose()
                safe=str(soup); title=(str(raw.get('title') or 'Cut').strip() or 'Cut')[:240]; note=str(raw.get('note') or '').strip()[:10000]; source_label=str(raw.get('source_label') or '').strip()[:240]
                source_project=str(raw.get('source_project') or manifest.get('source_project') or '').strip()[:240]
                source_chapter=str(raw.get('source_chapter') or source_label.removeprefix('Chapter: ').strip()).strip()[:240]
                source_scene=str(raw.get('source_scene') or '').strip()[:240]
                created=str(raw.get('created_at') or '').strip() or storage_values.now()
                # Additive by contract: no DELETE/UPDATE of existing Cuts and no reuse
                # of package IDs. Every imported item receives a fresh local row id.
                con.execute("INSERT INTO cuts(project_id,title,content_html,source_chapter_id,created_at,note,source_label,source_project,source_chapter,source_scene) VALUES(?,?,?,?,?,?,?,?,?,?)",(p['id'],title,safe,None,created,note,source_label,source_project,source_chapter,source_scene)); count+=1
        committed=True
        return {'ok':True,'imported':count,'additive':True}
    except Exception:
        if not committed:
            for stored in created_assets: runtime.assets.discard_uncommitted(stored)
        raise
    finally: tmp.unlink(missing_ok=True)
