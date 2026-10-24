from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi import File

from backend import diagnostics
from backend.dependencies import feature_call
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from backend.environment import _HOSTED_LIMITS, runtime
from backend.history_runtime import commit
from backend.project_activation import activate_project as _activate_project
from backend.request_context import (_bound_project_id,_bound_user_id,_enforce_hosted_new_project_quota,_enforce_hosted_project_storage,_project_is_current_for_bound_actor,_public_project_record)
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import values as storage_values
from workspace.filesystem import safe_name

from .uploads import save_upload_limited, validate_docx_archive

_detach_map_anchors_for_removed_manuscript = feature_call('maps.anchors', '_detach_map_anchors_for_removed_manuscript')
_primary_manuscript_tab = feature_call('checkpoints.capture', '_primary_manuscript_tab')
repair_orphaned_bookmark = feature_call('manuscript.anchors', 'repair_orphaned_bookmark')
sync_developmental_canvases = feature_call('canvas.developmental', 'sync_developmental_canvases')
verified_full_backup = feature_call('durability.backups', 'verified_full_backup')

router=APIRouter()

def _imported_tab_title(tab:dict,index:int,total:int,source_stem:str)->str:
    title=(tab.get('title') or '').strip()
    if total==1 and (not title or title.lower()=='manuscript'):
        title=source_stem.strip() or 'Imported document'
    return (title or f'Tab {index+1}')[:140]

def _insert_parsed_tabs(con,pid:int,tabs:list[dict],start_position:int,t:str,*,manuscript_first:bool=False,source_stem:str=''):
    if not tabs: return {'document_ids':[],'chapters':0,'images':0}
    # Parsing happens before the destination project exists. Materialize image
    # bytes only after its request/storage context is bound, never in the
    # previously-open project's asset directory.
    pending=tabs[0].get('_pending_images',{})
    materialized={}
    for reference,(blob,ext) in pending.items():
        from .docx_parser import _save_image_blob
        materialized[reference]=_save_image_blob(blob,ext)
    con.execute("UPDATE document_tabs SET position=position+?,updated_at=? WHERE project_id=? AND position>=?",(len(tabs),t,pid,start_position))
    document_ids=[]; chapter_count=0; image_count=0
    for ti,tab in enumerate(tabs):
        title=_imported_tab_title(tab,ti,len(tabs),source_stem)
        tabid=con.execute("INSERT INTO document_tabs(project_id,position,title,is_manuscript,created_at,updated_at) VALUES(?,?,?,?,?,?)",(pid,start_position+ti,title,1 if manuscript_first and ti==0 else 0,t,t)).lastrowid
        document_ids.append(tabid)
        for ci,ch in enumerate(tab['chapters']):
            chapter_count+=1
            cid=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(pid,tabid,ci,(ch.get('title') or f'Chapter {ci+1}')[:240],str(ch.get('description') or '')[:12000], '', '',t,t)).lastrowid
            for bi,b in enumerate(ch['blocks']):
                bid=storage_values.uid('b_'); typ='image' if b.get('kind')=='image' else 'paragraph'; text=b.get('text',''); image_path=materialized.get(b.get('image_path',''),b.get('image_path','')); h=storage_values.sha((b.get('html',''))+'\0'+text+'\0'+image_path)
                if typ=='image': image_count+=1
                con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,tabid,cid,bi,typ,b.get('style','body'),b.get('html',''),text,image_path,h,t,t))
                for hi in b.get('highlights',[]):
                    lname='Imported '+hi['name']; lr=con.execute("SELECT id FROM markup_labels WHERE project_id=? AND name=?",(pid,lname)).fetchone()
                    if lr: lid=lr[0]
                    else: lid=con.execute("INSERT INTO markup_labels(project_id,name,color,created_at,updated_at) VALUES(?,?,?,?,?)",(pid,lname,hi['color'],t,t)).lastrowid
                    con.execute("INSERT INTO annotations(project_id,block_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,body,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(pid,bid,'highlight',lid,hi['start'],hi['end'],hi['text'],text[max(0,hi['start']-45):hi['start']],text[hi['end']:hi['end']+45],'','open',t,t))
    return {'document_ids':document_ids,'chapters':chapter_count,'images':image_count}

async def _parse_import_upload(file:UploadFile,chapter_heading:str='heading1'):
    if chapter_heading not in {f'heading{i}' for i in range(1,7)}: raise HTTPException(400,'Choose a chapter heading from Heading 1 through Heading 6')
    safe_filename=Path(file.filename or 'book.docx').name
    suffix=Path(safe_filename).suffix.lower()
    document_limit=_HOSTED_LIMITS.document_bytes if _HOSTED_LIMITS is not None else 512*1024*1024
    if file.size is not None and file.size>document_limit: raise HTTPException(413,f'Document is too large to import safely ({document_limit//(1024*1024)} MB limit)')
    if suffix not in {'.docx','.doc','.txt'}: raise HTTPException(400,'Import .docx, .doc, or .txt')
    with tempfile.TemporaryDirectory() as td:
        src=Path(td)/(safe_filename or ('book'+suffix)); await save_upload_limited(file,src,document_limit)
        if suffix=='.doc':
            # Legacy Office parsing is a large native attack surface. Never execute it
            # inside the shared hosted service for attacker-controlled uploads. Local
            # desktop conversion remains opt-in by having LibreOffice installed and is
            # isolated to a throwaway user profile/home.
            if runtime.mode!='local':
                raise HTTPException(415,'Legacy .doc import is disabled in hosted Breadmaker. Convert the file to .docx first.')
            try:
                profile=Path(td)/'libreoffice-profile';profile.mkdir(parents=True,exist_ok=True)
                env=dict(os.environ);env['HOME']=td
                subprocess.run(['libreoffice','--headless','--norestore','--nodefault','--nolockcheck','--nofirststartwizard',f'-env:UserInstallation={profile.as_uri()}','--convert-to','docx','--outdir',td,str(src)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,env=env); src=Path(td)/(src.stem+'.docx')
            except Exception as e: raise HTTPException(500,f'Legacy .doc conversion failed: {e}')
        if suffix=='.txt':
            text=src.read_text(encoding='utf-8',errors='replace'); tabs=[{'title':'Manuscript','chapters':[{'title':'Chapter 1','blocks':[{'kind':'paragraph','text':line,'html':html.escape(line),'style':'body'} for line in text.splitlines()]}]}]
        else:
            validate_docx_archive(src)
            # python-docx/lxml is intentionally action-lazy. Importing Breadmaker's
            # HTTP graph must not pay the native/XML parser startup cost unless the
            # writer actually imports a DOCX file.
            from .docx_parser import parse_docx
            images={}
            def stage_image(blob,ext):
                reference=f'import-image-{len(images)}'
                images[reference]=(bytes(blob),ext)
                return reference
            tabs=parse_docx(src,chapter_heading,image_store=stage_image)
            if images:tabs[0]['_pending_images']=images
    return safe_filename,tabs

def _parsed_tabs_text_bytes(tabs:list[dict]) -> int:
    total=sum(len(blob) for blob,_ in (tabs[0].get('_pending_images',{}).values() if tabs else []))
    for tab in tabs or []:
        total+=len(str(tab.get('title') or '').encode('utf-8'))
        for chapter in tab.get('chapters',[]) or []:
            total+=len(str(chapter.get('title') or '').encode('utf-8'))
            for block in chapter.get('blocks',[]) or []:
                total+=len(str(block.get('text') or '').encode('utf-8'))+len(str(block.get('html') or '').encode('utf-8'))+256
    return total

def _manuscript_import_readiness(con,pid:int)->dict:
    """Return whether replacing Main Manuscript is indistinguishable from filling the default blank book.

    Keep this intentionally conservative. If the writer has created any manuscript
    structure worth preserving, the UI must ask where the imported document belongs.
    """
    main_tabs=allrows(con,"SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id",(pid,))
    if len(main_tabs)!=1:
        return {"empty":False,"reason":"main-tab-count"}
    tab_id=int(main_tabs[0]['id'])
    chapters=allrows(con,"SELECT id FROM chapters WHERE project_id=? AND tab_id=? AND is_implicit=0 ORDER BY position,id",(pid,tab_id))
    if len(chapters)!=1:
        return {"empty":False,"reason":"chapter-count"}
    chapter_id=int(chapters[0]['id'])
    blocks=allrows(con,"SELECT id,block_type,text_content,image_path,scene_id FROM manuscript_blocks WHERE project_id=? AND tab_id=? AND chapter_id=? ORDER BY position,id",(pid,tab_id,chapter_id))
    if len(blocks)!=1:
        return {"empty":False,"reason":"block-count"}
    block=blocks[0]
    if block['block_type']!='paragraph' or str(block['text_content'] or '').strip() or str(block['image_path'] or '').strip() or block['scene_id'] is not None:
        return {"empty":False,"reason":"manuscript-content"}
    block_id=block['id']
    if con.execute("SELECT 1 FROM annotations WHERE project_id=? AND (chapter_id=? OR block_id=?) LIMIT 1",(pid,chapter_id,block_id)).fetchone():
        return {"empty":False,"reason":"annotations"}
    if con.execute("SELECT 1 FROM bookmarks WHERE project_id=? LIMIT 1",(pid,)).fetchone():
        return {"empty":False,"reason":"bookmark"}
    # Extra Documents are deliberate project content. Even though replacing Main
    # Manuscript would preserve them, showing the destination chooser is clearer.
    if con.execute("SELECT 1 FROM document_tabs WHERE project_id=? AND id<>? LIMIT 1",(pid,tab_id)).fetchone():
        return {"empty":False,"reason":"extra-tabs"}
    return {"empty":True,"reason":"default-blank"}

def _replace_primary_manuscript_parsed(con,pid:int,tabs:list[dict],safe_filename:str,t:str)->dict:
    # Import replaces the primary manuscript only. Native research/notes Documents,
    # canvases, tasks, markers, Cuts, and other project thinking must never vanish
    # just because the writer imports a new draft.
    manuscript_tabs=[r['id'] for r in allrows(con,"SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1",(pid,))]
    affected_chapters=[]; affected_blocks=[]; affected_scenes=[]; affected_annotations=[]
    if manuscript_tabs:
        marks=','.join('?' for _ in manuscript_tabs)
        affected_chapters=[r['id'] for r in allrows(con,f"SELECT id FROM chapters WHERE project_id=? AND tab_id IN ({marks})",[pid,*manuscript_tabs])]
        if affected_chapters:
            cm=','.join('?' for _ in affected_chapters)
            oldblocks=allrows(con,f"SELECT id,scene_id FROM manuscript_blocks WHERE project_id=? AND chapter_id IN ({cm})",[pid,*affected_chapters])
            affected_blocks=[r['id'] for r in oldblocks]
            affected_scenes=sorted({r['scene_id'] for r in oldblocks if r['scene_id'] is not None})
            affected_annotations=[r['id'] for r in allrows(con,f"SELECT id FROM annotations WHERE project_id=? AND chapter_id IN ({cm})",[pid,*affected_chapters])]
            if affected_blocks:
                bm=','.join('?' for _ in affected_blocks); affected_annotations.extend(r['id'] for r in allrows(con,f"SELECT id FROM annotations WHERE project_id=? AND block_id IN ({bm})",[pid,*affected_blocks]))
            affected_annotations=sorted(set(affected_annotations))
        _detach_map_anchors_for_removed_manuscript(con,pid,chapter_ids=affected_chapters,scene_ids=affected_scenes,block_ids=affected_blocks)
        _delete_polymorphic_references(con,pid,{'document':manuscript_tabs,'chapter':affected_chapters,'scene':affected_scenes,'annotation':affected_annotations})
        clauses=[]; args=[]
        if affected_blocks:
            bm=','.join('?' for _ in affected_blocks); clauses.append(f"start_block_id IN ({bm}) OR end_block_id IN ({bm})"); args.extend(affected_blocks); args.extend(affected_blocks)
        if clauses: con.execute("DELETE FROM references_map WHERE project_id=? AND ("+' OR '.join(clauses)+")",[pid,*args])
        if affected_scenes:
            sm=','.join('?' for _ in affected_scenes)
            con.execute(f"DELETE FROM scenes WHERE project_id=? AND id IN ({sm})",[pid,*affected_scenes])
        if affected_chapters:
            cm=','.join('?' for _ in affected_chapters)
            con.execute(f"DELETE FROM chapters WHERE project_id=? AND id IN ({cm})",[pid,*affected_chapters])
        con.execute(f"DELETE FROM document_tabs WHERE project_id=? AND id IN ({marks})",[pid,*manuscript_tabs])
    inserted=_insert_parsed_tabs(con,pid,tabs,0,t,manuscript_first=True,source_stem=Path(safe_filename).stem)
    main=one(con,"SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1 ORDER BY position,id LIMIT 1",(pid,))
    repair_orphaned_bookmark(con,pid,int(main['id']) if main else None)
    con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid))
    storage_invariants.validate_project_invariants(con,pid)
    return inserted

@router.get("/api/import/readiness")
def import_readiness():
    with storage_mutations.tx() as con:
        p=project(con); status=_manuscript_import_readiness(con,p['id'])
    return {"ok":True,"manuscript_empty":bool(status['empty']),"reason":status['reason']}

@router.post("/api/import")
async def import_file(file:UploadFile=File(...), chapter_heading:str='heading1'):
    safe_filename,tabs=await _parse_import_upload(file,chapter_heading)
    _enforce_hosted_project_storage(additional_bytes=_parsed_tabs_text_bytes(tabs))
    commit('Before document import','safety'); verified_full_backup('before_import')
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; t=storage_values.now()
        inserted=_replace_primary_manuscript_parsed(con,pid,tabs,safe_filename,t)
    commit(f"Imported {safe_filename}",'import'); sync_developmental_canvases(pid)
    return {"ok":True,"tabs":len(inserted['document_ids']),"chapters":inserted['chapters'],"images":inserted['images']}

@router.post("/api/import/new-project")
async def import_file_as_new_project(file:UploadFile=File(...), chapter_heading:str='heading1', project_name:str=''):
    owner_user_id=_bound_user_id() or None
    _enforce_hosted_new_project_quota(owner_user_id or '')
    # Parse and validate the entire source before creating/switching projects. A bad
    # document therefore cannot strand the user in a newly-created empty project.
    safe_filename,tabs=await _parse_import_upload(file,chapter_heading)
    previous_id=_bound_project_id(); derived=Path(safe_filename).stem.strip() or 'Imported Book'
    name=safe_name(project_name.strip() or derived)
    rec=None
    try:
        rec=runtime.projects.create(name,activate=False,owner_user_id=owner_user_id)
        _activate_project(rec['id'],user_id=owner_user_id)
        with runtime.context.bind(rec['id'],user_id=owner_user_id):
            _enforce_hosted_project_storage(project_id=rec['id'],additional_bytes=_parsed_tabs_text_bytes(tabs))
            with storage_mutations.tx() as con:
                p=project(con); pid=p['id']; t=storage_values.now()
                inserted=_replace_primary_manuscript_parsed(con,pid,tabs,safe_filename,t)
            commit(f"Imported {safe_filename}",'import'); sync_developmental_canvases(pid)
        diagnostics.log_event("PROJECT_IMPORT_MANUSCRIPT_001","Created a new project from an imported manuscript.",context={"project_id":rec['id'],"source_file":safe_filename})
        return {"ok":True,"reload":True,"project":_public_project_record(rec),"tabs":len(inserted['document_ids']),"chapters":inserted['chapters'],"images":inserted['images']}
    except Exception:
        # Roll back both the active-project switch and the newly-created shell. The
        # writer's original project remains untouched if import cannot finish safely.
        if rec is not None:
            try:
                if previous_id and _project_is_current_for_bound_actor(rec['id']):
                    _activate_project(previous_id,user_id=_bound_user_id() or None)
            finally:
                try: runtime.projects.delete(rec['id'],permanent=True)
                except Exception as cleanup_error:
                    diagnostics.log_event("PROJECT_IMPORT_MANUSCRIPT_CLEANUP_001","Failed to remove an incomplete project created for manuscript import.",level="ERROR",context={"project_id":rec['id']},error=cleanup_error,data_safe=False)
        raise

@router.post("/api/documents/import")
async def import_document_tabs(file:UploadFile=File(...), after_document_id:int=0, chapter_heading:str='heading1'):
    """Append every source tab from a document after an existing Breadmaker tab.

    This is intentionally non-destructive: imported source tabs are ordinary
    non-manuscript Documents and existing tabs/content are never replaced.
    """
    safe_filename,tabs=await _parse_import_upload(file,chapter_heading)
    _enforce_hosted_project_storage(additional_bytes=_parsed_tabs_text_bytes(tabs))
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']
        anchor=one(con,"SELECT id,position FROM document_tabs WHERE id=? AND project_id=?",(after_document_id,pid))
        if not anchor: raise HTTPException(404,detail="DOCUMENT_004: Import insertion tab not found.")
    commit(f"Before appending document: {safe_filename}",'safety')
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']; t=storage_values.now()
        anchor=one(con,"SELECT id,position FROM document_tabs WHERE id=? AND project_id=?",(after_document_id,pid))
        if not anchor: raise HTTPException(404,detail="DOCUMENT_004: Import insertion tab not found.")
        inserted=_insert_parsed_tabs(con,pid,tabs,int(anchor['position'])+1,t,manuscript_first=False,source_stem=Path(safe_filename).stem)
        con.execute("UPDATE projects SET updated_at=? WHERE id=?",(t,pid))
    commit(f"Appended document: {safe_filename}",'import')
    return {"ok":True,"tabs":len(inserted['document_ids']),"chapters":inserted['chapters'],"images":inserted['images'],"document_ids":inserted['document_ids'],"first_document_id":inserted['document_ids'][0] if inserted['document_ids'] else None}
