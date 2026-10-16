from __future__ import annotations
"""Portable .breadreview sessions, packages, export, and return import."""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pathlib import Path
from datetime import datetime
import re
import json
import mimetypes
import os
import shutil
import tempfile
from auth.sessions import AuthenticationError
from api.reviews.contracts import BreadReviewExportIn, BreadReviewPassphraseIn, ReviewCommentCreate, ReviewHighlightCreate, ReviewFeedbackSubmit, ReviewSelectionIn, ReviewSuggestionCreate
from backend.data_access import allrows, one, project
from backend.dependencies import feature_call
from backend.environment import APP_VERSION, _HOSTED_LIMITS, runtime
from backend.portable_review_runtime import _TEMP_PORTABLE_REVIEWS, _TEMP_PORTABLE_REVIEW_LOCK, _TemporaryPortableReviewState, current_portable_review_path, current_portable_review_passphrase, set_portable_review_passphrase
from backend.request_context import _bound_project_id, _bound_user_id, _require_project_permission
from formats import breadreview
from security import content_security
from workspace.filesystem import safe_name
from security.image_security import UnsafeImage, inspect_hosted_image_bytes
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain as review_domain
from . import feedback as review_feedback
from . import snapshots as review_snapshots
router=APIRouter()
save_upload_limited = feature_call('import_export.uploads', 'save_upload_limited')

def _portable_http_error(exc:breadreview.BreadReviewError,status:int=400):
    raise HTTPException(status,detail=f"{exc.code}: {exc.message}") from exc

def _portable_state_key() -> str:
    principal=runtime.sessions.current()
    if principal is not None:
        return f'session:{principal.session_id}'
    if runtime.sessions.requires_authentication:
        raise AuthenticationError('AUTHENTICATION_REQUIRED')
    return 'local'

def _temporary_portable_state() -> _TemporaryPortableReviewState | None:
    key=_portable_state_key()
    with _TEMP_PORTABLE_REVIEW_LOCK:
        return _TEMP_PORTABLE_REVIEWS.get(key)

def _active_portable_review_path() -> Path | None:
    state=_temporary_portable_state()
    return state.path if state is not None else current_portable_review_path()

def _temporary_portable_review_active() -> bool:
    return _temporary_portable_state() is not None

def _temporary_portable_dirty() -> bool:
    state=_temporary_portable_state()
    return bool(state is not None and state.dirty)

def _temporary_portable_filename() -> str | None:
    state=_temporary_portable_state()
    return state.filename if state is not None else None

def _install_temporary_portable_review(path:Path,root:Path,filename:str) -> None:
    key=_portable_state_key()
    replacement=_TemporaryPortableReviewState(path=path,root=root,filename=filename)
    with _TEMP_PORTABLE_REVIEW_LOCK:
        previous=_TEMP_PORTABLE_REVIEWS.get(key)
        _TEMP_PORTABLE_REVIEWS[key]=replacement
    if previous is not None and previous.root!=root:
        shutil.rmtree(previous.root,ignore_errors=True)

def _cleanup_temporary_portable_review() -> None:
    key=_portable_state_key()
    with _TEMP_PORTABLE_REVIEW_LOCK:
        state=_TEMP_PORTABLE_REVIEWS.pop(key,None)
    if state is not None:
        shutil.rmtree(state.root,ignore_errors=True)

def _prune_expired_temporary_portable_reviews() -> None:
    """Erase detached Review state whose opaque hosted session no longer exists."""
    if not runtime.sessions.requires_authentication:
        return
    active=set(runtime.sessions.active_session_ids())
    stale=[]
    with _TEMP_PORTABLE_REVIEW_LOCK:
        for key,state in list(_TEMP_PORTABLE_REVIEWS.items()):
            if not key.startswith('session:'):
                continue
            if key.removeprefix('session:') not in active:
                stale.append(_TEMP_PORTABLE_REVIEWS.pop(key))
    for state in stale:
        shutil.rmtree(state.root,ignore_errors=True)

def _set_temporary_portable_passphrase(passphrase:str) -> None:
    key=_portable_state_key()
    with _TEMP_PORTABLE_REVIEW_LOCK:
        state=_TEMP_PORTABLE_REVIEWS.get(key)
        if state is None:
            raise HTTPException(404,detail="BREADREVIEW_MODE_001: No portable Review is open.")
        state.passphrase=passphrase

def _mark_temporary_portable_dirty() -> None:
    key=_portable_state_key()
    with _TEMP_PORTABLE_REVIEW_LOCK:
        state=_TEMP_PORTABLE_REVIEWS.get(key)
        if state is not None:
            state.dirty=True

def _active_portable_passphrase() -> str | None:
    state=_temporary_portable_state()
    return state.passphrase if state is not None else current_portable_review_passphrase()

def _portable_package_info() -> dict:
    path=_active_portable_review_path()
    if path is None: raise HTTPException(404,detail="BREADREVIEW_MODE_001: No portable Review is open.")
    try:return breadreview.inspect(path)
    except breadreview.BreadReviewError as exc:_portable_http_error(exc)

def _portable_locked() -> bool:
    info=_portable_package_info()
    return bool(info.get('encrypted') and not _active_portable_passphrase())

def _portable_manifest() -> dict:
    path=_active_portable_review_path()
    if path is None: raise HTTPException(404,detail="BREADREVIEW_MODE_001: No portable Review is open.")
    try:return breadreview.read(path,_active_portable_passphrase(),max_asset_bytes=(_HOSTED_LIMITS.image_bytes if _HOSTED_LIMITS is not None else breadreview.MAX_ASSET_BYTES))
    except breadreview.BreadReviewError as exc:
        status=401 if exc.code in {'BREADREVIEW_037','BREADREVIEW_038'} else 400
        _portable_http_error(exc,status)

def _portable_write(manifest:dict) -> dict:
    path=_active_portable_review_path()
    if path is None: raise HTTPException(404,detail="BREADREVIEW_MODE_001: No portable Review is open.")
    passphrase=_active_portable_passphrase()
    try:
        breadreview.rewrite(path,manifest,passphrase)
        if _temporary_portable_review_active():
            _mark_temporary_portable_dirty()
        return breadreview.read(path,passphrase,max_asset_bytes=(_HOSTED_LIMITS.image_bytes if _HOSTED_LIMITS is not None else breadreview.MAX_ASSET_BYTES))
    except breadreview.BreadReviewError as exc:
        status=401 if exc.code in {'BREADREVIEW_037','BREADREVIEW_038'} else 400
        _portable_http_error(exc,status)

def _portable_event(manifest:dict,event_type:str,*,entity_type:str='',entity_id:str='',payload:dict|None=None,created_at:str|None=None):
    feedback=manifest.setdefault('feedback',{});events=feedback.setdefault('events',[]);t=created_at or storage_values.now()
    events.append({'id':storage_values.uid('review_event_'),'review_id':manifest['review_id'],'participant_id':manifest['participant_id'],'origin':'portable','event_type':event_type,'entity_type':entity_type,'entity_id':entity_id,'payload':payload or {},'created_at':t})

def _portable_current_bookmark(manifest:dict) -> dict|None:
    snapshot_id=str(manifest.get('snapshot_id') or '')
    events=list((manifest.get('feedback') or {}).get('events') or [])
    for event in reversed(events):
        if not isinstance(event,dict) or str(event.get('event_type') or '') not in {'BOOKMARK_SET','BOOKMARK_CLEARED'}:continue
        payload=event.get('payload') if isinstance(event.get('payload'),dict) else {}
        if str(payload.get('snapshot_id') or '')!=snapshot_id:continue
        if event.get('event_type')=='BOOKMARK_CLEARED':return None
        selection=payload.get('selection')
        return dict(selection) if isinstance(selection,dict) else None
    return None

def _portable_anchor(manifest:dict,selection:ReviewSelectionIn) -> dict:
    snapshot=manifest['snapshot'];blocks=list(snapshot.get('blocks') or [])
    values=review_domain._review_selection_values(blocks,str(manifest['snapshot_id']),selection)
    anchor={'id':storage_values.uid('review_anchor_'),'review_id':manifest['review_id'],'created_at':storage_values.now(),**values}
    manifest.setdefault('feedback',{}).setdefault('anchors',[]).append(anchor)
    return anchor

def _portable_feedback_rows(manifest:dict) -> dict:
    feedback=manifest.get('feedback') or {}; anchors={str(a.get('id')):a for a in feedback.get('anchors',[]) if isinstance(a,dict)}
    participant=manifest.get('participant') or {}; pname=str(participant.get('display_name') or '')
    def decorate(rows):
        out=[]
        for raw in rows or []:
            if not isinstance(raw,dict):continue
            row=dict(raw); anchor=anchors.get(str(row.get('anchor_id') or '')) or {}
            for key in ('snapshot_id','start_block_id','end_block_id','start_offset','end_offset','selected_text','prefix_context','suffix_context'):
                if key in anchor:row[key]=anchor[key]
            row.setdefault('participant_name',pname if str(row.get('participant_id') or '')==str(manifest.get('participant_id') or '') else str(row.get('participant_name') or ''))
            out.append(row)
        return out
    return {'highlights':decorate(feedback.get('highlights')),'comments':decorate(feedback.get('comments')),'suggestions':decorate(feedback.get('suggestions'))}

def _portable_form(manifest:dict) -> dict|None:
    snapshot=manifest.get('snapshot') or {}
    if not bool(snapshot.get('permission_feedback')):return None
    feedback=manifest.get('feedback') or {}; answers=feedback.get('answers') or []; byq={str(a.get('question_id')):str(a.get('answer_text') or '') for a in answers if isinstance(a,dict)}
    questions=list(manifest.get('questions') or [])
    general=feedback.get('general_feedback') or {}
    return {'questions':questions,'answers':[{'question_id':q.get('id'),'answer_text':byq.get(str(q.get('id')),'')} for q in questions],'general_feedback':str(general.get('body') or ''),'saved':bool(answers or str(general.get('body') or ''))}

def _portable_reader_payload(manifest:dict) -> dict:
    review=dict(manifest.get('review') or {}); participant=dict(manifest.get('participant') or {}); snapshot=dict(manifest.get('snapshot') or {})
    if str(review.get('status') or 'open')!='open':raise HTTPException(409,detail="BREADREVIEW_READER_001: This portable Review is not open.")
    if str(participant.get('status') or 'not_started')=='revoked':raise HTTPException(403,detail="BREADREVIEW_READER_002: This participant's access has been revoked.")
    can_read=bool(snapshot.get('permission_read'));can_feedback=bool(snapshot.get('permission_feedback'))
    if not can_read and not can_feedback:raise HTTPException(403,detail="BREADREVIEW_READER_003: This Review does not expose a participant-facing capability.")
    visible=_portable_feedback_rows(manifest) if can_read else {'highlights':[],'comments':[],'suggestions':[]}
    return {'review':review,'participant':participant,'snapshot':snapshot if can_read else {k:v for k,v in snapshot.items() if k not in {'chapters','scenes','blocks'}},'feedback':visible,'form':_portable_form(manifest),'bookmark':_portable_current_bookmark(manifest) if can_read else None}

def _breadreview_filename(review:dict,participant:dict) -> str:
    base=safe_name(f"{str(review.get('name') or 'Review')} - {str(participant.get('display_name') or 'Reviewer')}") or 'BreadReview'
    return base+'.breadreview'

def _breadreview_manifest_for_export(con,project_row:dict,review:dict,participant:dict) -> tuple[dict,dict[str,bytes]]:
    if str(review.get('status') or '')!='open':raise HTTPException(409,detail="BREADREVIEW_EXPORT_001: Start the Review before exporting it.")
    if str(participant.get('status') or '')=='revoked':raise HTTPException(409,detail="BREADREVIEW_EXPORT_002: Revoked participants cannot receive a portable Review.")
    snapshot=review_snapshots._review_snapshot_payload(con,str(review['id']))
    if not snapshot:raise HTTPException(409,detail="BREADREVIEW_EXPORT_003: Review snapshot is missing.")
    visible=review_feedback._review_feedback_rows(con,str(review['id']),None if str(review.get('reviewer_visibility') or 'private')=='shared' else str(participant['id']))
    visible_items=[*visible['highlights'],*visible['comments'],*visible['suggestions']]
    anchor_ids=sorted({str(row.get('anchor_id') or '') for row in visible_items if row.get('anchor_id')})
    anchors=[]
    if anchor_ids:
        marks=','.join('?' for _ in anchor_ids)
        anchors=allrows(con,f"SELECT * FROM review_anchors WHERE review_id=? AND id IN ({marks}) ORDER BY created_at,id",(review['id'],*anchor_ids))
    answers=allrows(con,"SELECT * FROM review_question_answers WHERE review_id=? AND snapshot_id=? AND participant_id=? ORDER BY created_at,id",(review['id'],snapshot['id'],participant['id']))
    general=one(con,"SELECT * FROM review_general_feedback WHERE review_id=? AND snapshot_id=? AND participant_id=?",(review['id'],snapshot['id'],participant['id'])) or {}
    assets:dict[str,bytes]={};asset_map={};portable_snapshot=json.loads(json.dumps(snapshot))
    for block in portable_snapshot.get('blocks',[]):
        src=str(block.get('image_path') or '')
        if not content_security.is_safe_project_asset_path(src):
            continue
        name=Path(src.removeprefix('/api/assets/')).name
        try:data=runtime.assets.read_blob(name)
        except FileNotFoundError:data=b''
        if data and len(data)<=(_HOSTED_LIMITS.image_bytes if _HOSTED_LIMITS is not None else breadreview.MAX_ASSET_BYTES):
            package_path=f'assets/{name}';assets[package_path]=data;asset_map[name]=package_path;block['image_path']=f'/api/portable-review/assets/{name}'
        else:block['image_path']=''
    questions=review_feedback._review_questions(con,str(review['id']))
    package_id=storage_values.uid('breadreview_');now=storage_values.now();project_identity=_bound_project_id()
    manifest={
      'package_format':breadreview.PACKAGE_FORMAT,'package_version':breadreview.PACKAGE_VERSION,'app_version':APP_VERSION,
      'package_id':package_id,'created_at':now,'updated_at':now,'project_id':project_identity,'review_id':str(review['id']),
      'snapshot_id':str(snapshot['id']),'round_number':int(snapshot.get('round_number') or 1),'participant_id':str(participant['id']),'assets':asset_map,
      'review':{'id':str(review['id']),'name':str(review.get('name') or ''),'reviewer_visibility':str(review.get('reviewer_visibility') or 'private'),'status':'open'},
      'participant':{k:participant.get(k) for k in ('id','review_id','display_name','status','started_at','completed_at','created_at','updated_at')},
      'snapshot':portable_snapshot,'questions':questions,
      'feedback':{'anchors':anchors,'highlights':visible['highlights'],'comments':visible['comments'],'suggestions':visible['suggestions'],'answers':answers,'general_feedback':general,'events':[{'id':row['id'],'review_id':row['review_id'],'participant_id':row['participant_id'],'origin':row.get('origin') or 'local','event_type':row['event_type'],'entity_type':row.get('entity_type') or '','entity_id':row.get('entity_id') or '','payload':json.loads(row.get('payload_json') or '{}'),'created_at':row['created_at']} for row in allrows(con,"SELECT * FROM review_events WHERE review_id=? AND participant_id=? AND event_type IN ('BOOKMARK_SET','BOOKMARK_CLEARED') ORDER BY created_at,id",(review['id'],participant['id']))]},
    }
    return manifest,assets

def _portable_can_import_to_current_project(manifest:dict|None) -> bool:
    return bool(manifest) and str(manifest.get('project_id') or '')==_bound_project_id()

@router.post('/api/portable-review/open-temporary')
async def portable_review_open_temporary(file:UploadFile=File(...)):
    if current_portable_review_path() is not None:raise HTTPException(409,detail="BREADREVIEW_TEMP_001: A standalone BreadReview session is already active.")
    filename=Path(file.filename or '').name
    if Path(filename).suffix.lower()!='.breadreview':raise HTTPException(400,detail="BREADREVIEW_TEMP_002: Drop one .breadreview file.")
    root=Path(tempfile.mkdtemp(prefix='breadmaker-temp-review-'));target=root/filename
    try:
        await save_upload_limited(file,target,_HOSTED_LIMITS.breadreview_bytes if _HOSTED_LIMITS is not None else breadreview.MAX_PACKAGE_BYTES)
        info=breadreview.inspect(target)
        manifest=None if info.get('encrypted') else breadreview.read(target,max_asset_bytes=(_HOSTED_LIMITS.image_bytes if _HOSTED_LIMITS is not None else breadreview.MAX_ASSET_BYTES))
    except breadreview.BreadReviewError as exc:
        shutil.rmtree(root,ignore_errors=True);_portable_http_error(exc)
    except Exception:
        shutil.rmtree(root,ignore_errors=True);raise
    _install_temporary_portable_review(target,root,filename)
    if manifest is None:
        return {'active':True,'temporary':True,'dirty':False,'locked':True,'encrypted':True,'filename':filename}
    return {'active':True,'temporary':True,'dirty':False,'locked':False,'encrypted':False,'filename':filename,'package_id':manifest['package_id'],'can_import_to_current_project':_portable_can_import_to_current_project(manifest),'payload':_portable_reader_payload(manifest)}

@router.post('/api/portable-review/unlock')
def portable_review_unlock(body:BreadReviewPassphraseIn):
    path=_active_portable_review_path()
    if path is None:raise HTTPException(404,detail="BREADREVIEW_MODE_001: No portable Review is open.")
    try:
        info=breadreview.inspect(path)
        manifest=breadreview.read(path,body.passphrase if info.get('encrypted') else None,max_asset_bytes=(_HOSTED_LIMITS.image_bytes if _HOSTED_LIMITS is not None else breadreview.MAX_ASSET_BYTES))
    except breadreview.BreadReviewError as exc:
        status=401 if exc.code in {'BREADREVIEW_037','BREADREVIEW_038'} else 400
        _portable_http_error(exc,status)
    if info.get('encrypted'):
        if _temporary_portable_review_active():_set_temporary_portable_passphrase(body.passphrase)
        else:set_portable_review_passphrase(body.passphrase)
    return {'active':True,'temporary':_temporary_portable_review_active(),'dirty':_temporary_portable_dirty(),'locked':False,'encrypted':bool(info.get('encrypted')),'filename':(_temporary_portable_filename() if _temporary_portable_review_active() else path.name),'package_id':manifest['package_id'],'can_import_to_current_project':_portable_can_import_to_current_project(manifest),'payload':_portable_reader_payload(manifest)}

@router.get('/api/portable-review/package')
def portable_review_package():
    state=_temporary_portable_state()
    if state is None:raise HTTPException(404,detail="BREADREVIEW_TEMP_003: No temporary BreadReview is open.")
    return FileResponse(state.path,media_type='application/octet-stream',filename=state.filename or state.path.name,headers={'Cache-Control':'no-store, max-age=0'})

@router.post('/api/portable-review/close-temporary')
def portable_review_close_temporary():
    if not _temporary_portable_review_active():return {'ok':True}
    _cleanup_temporary_portable_review();return {'ok':True}

@router.post('/api/portable-review/import-current')
def portable_review_import_current():
    if not _temporary_portable_review_active():raise HTTPException(404,detail="BREADREVIEW_TEMP_004: No temporary Bread Review is open.")
    manifest=_portable_manifest();target_project_id=_bound_project_id();user_id=_bound_user_id()
    if not target_project_id or str(manifest.get('project_id') or '')!=target_project_id:
        raise HTTPException(409,detail="BREADREVIEW_IMPORT_020: This returned Review belongs to a different project.")
    # This route is deliberately project-context-free while reading a detached package.
    # Bind the authenticated actor + target project explicitly before any storage_mutations.tx().
    with runtime.context.bind(target_project_id,user_id=user_id or None):
        _require_project_permission('review.process',target_project_id)
        with storage_mutations.tx(operation='breadreview-import') as con:
            p=project(con);result=_merge_breadreview_manifest(con,p,manifest)
    _cleanup_temporary_portable_review()
    return result

@router.get('/api/portable-review/status')
def portable_review_status():
    path=_active_portable_review_path()
    if path is None:return {'active':False}
    info=_portable_package_info()
    locked=bool(info.get('encrypted') and not _active_portable_passphrase())
    base={'active':True,'temporary':_temporary_portable_review_active(),'dirty':_temporary_portable_dirty(),'locked':locked,'encrypted':bool(info.get('encrypted')),'filename':(_temporary_portable_filename() if _temporary_portable_review_active() else path.name)}
    if locked:return base
    manifest=_portable_manifest()
    return {**base,'package_id':manifest['package_id'],'payload':_portable_reader_payload(manifest)}

@router.get('/api/portable-review/reader')
def portable_review_reader():return _portable_reader_payload(_portable_manifest())

@router.post('/api/portable-review/begin')
def portable_review_begin():
    manifest=_portable_manifest();participant=manifest['participant'];status=str(participant.get('status') or 'not_started')
    if status=='revoked':raise HTTPException(409,detail="BREADREVIEW_LIFECYCLE_001: Revoked participants cannot enter this Review.")
    if status in {'not_started','completed'}:
        t=storage_values.now();participant['status']='in_progress';participant['started_at']=participant.get('started_at') or t;participant['completed_at']=None;participant['updated_at']=t;manifest['updated_at']=t;_portable_event(manifest,'PARTICIPANT_STARTED',entity_type='participant',entity_id=manifest['participant_id'],created_at=t);manifest=_portable_write(manifest)
    return manifest['participant']

@router.post('/api/portable-review/complete')
def portable_review_complete():
    manifest=_portable_manifest();participant=manifest['participant']
    if str(participant.get('status') or '')=='revoked':raise HTTPException(409,detail="BREADREVIEW_LIFECYCLE_002: Revoked participants cannot complete this Review.")
    t=storage_values.now();participant['status']='completed';participant['started_at']=participant.get('started_at') or t;participant['completed_at']=t;participant['updated_at']=t;manifest['updated_at']=t;_portable_event(manifest,'PARTICIPANT_COMPLETED',entity_type='participant',entity_id=manifest['participant_id'],created_at=t);manifest=_portable_write(manifest);return manifest['participant']

def _portable_write_access(manifest:dict,permission:str) -> tuple[dict,dict]:
    if str((manifest.get('review') or {}).get('status') or 'open')!='open':raise HTTPException(409,detail="BREADREVIEW_WRITE_001: This Review is not open.")
    participant=manifest['participant'];status=str(participant.get('status') or 'not_started')
    if status=='revoked':raise HTTPException(409,detail="BREADREVIEW_WRITE_003: This participant's access has been revoked.")
    snapshot=manifest['snapshot']
    if not bool(snapshot.get(permission)):raise HTTPException(403,detail="BREADREVIEW_WRITE_004: This permission is not enabled for the Review.")
    return participant,snapshot

@router.put('/api/portable-review/bookmark')
def portable_review_bookmark_set(body:ReviewHighlightCreate):
    manifest=_portable_manifest();participant,snapshot=_portable_write_access(manifest,'permission_read');selection=review_domain._review_selection_values(list(snapshot.get('blocks') or []),str(manifest['snapshot_id']),body.selection);t=storage_values.now()
    _portable_event(manifest,'BOOKMARK_SET',entity_type='bookmark',entity_id=manifest['participant_id'],payload={'snapshot_id':manifest['snapshot_id'],'selection':{k:selection[k] for k in ('start_block_id','end_block_id','start_offset','end_offset')}},created_at=t);manifest['updated_at']=t;_portable_write(manifest);return {'bookmark':{k:selection[k] for k in ('start_block_id','end_block_id','start_offset','end_offset')}}

@router.delete('/api/portable-review/bookmark')
def portable_review_bookmark_clear():
    manifest=_portable_manifest();participant,snapshot=_portable_write_access(manifest,'permission_read');t=storage_values.now();_portable_event(manifest,'BOOKMARK_CLEARED',entity_type='bookmark',entity_id=manifest['participant_id'],payload={'snapshot_id':manifest['snapshot_id']},created_at=t);manifest['updated_at']=t;_portable_write(manifest);return {'ok':True}

@router.post('/api/portable-review/highlights')
def portable_review_highlight(body:ReviewHighlightCreate):
    manifest=_portable_manifest();participant,snapshot=_portable_write_access(manifest,'permission_comment');markers=list(snapshot.get('markers') or []);marker=next((row for row in markers if int(row.get('source_marker_id') or 0)==int(body.marker_id or 0)),None)
    if not marker:raise HTTPException(400,detail='BREADREVIEW_HIGHLIGHT_001: Choose an available Review marker.')
    anchor=_portable_anchor(manifest,body.selection);t=storage_values.now();hid=storage_values.uid('review_highlight_')
    row={'id':hid,'review_id':manifest['review_id'],'participant_id':manifest['participant_id'],'participant_name':participant.get('display_name'),'anchor_id':anchor['id'],'tag':str(marker.get('name') or '')[:240],'marker_name':str(marker.get('name') or ''),'marker_color':str(marker.get('color') or '#f2c94c'),'status':'open','created_at':t,'updated_at':t}
    manifest['feedback']['highlights'].append(row);manifest['updated_at']=t;_portable_event(manifest,'HIGHLIGHT_CREATED',entity_type='highlight',entity_id=hid,payload={'anchor_id':anchor['id']},created_at=t);_portable_write(manifest);return {**row,**{k:anchor[k] for k in ('selected_text','start_block_id','end_block_id','start_offset','end_offset')}}

@router.post('/api/portable-review/comments')
def portable_review_comment(body:ReviewCommentCreate):
    manifest=_portable_manifest();participant,snapshot=_portable_write_access(manifest,'permission_comment');text=(body.body or '').strip()[:12000]
    if not text:raise HTTPException(400,detail="BREADREVIEW_COMMENT_001: Comment text is required.")
    anchor=_portable_anchor(manifest,body.selection);t=storage_values.now();cid=storage_values.uid('review_comment_');row={'id':cid,'review_id':manifest['review_id'],'participant_id':manifest['participant_id'],'participant_name':participant.get('display_name'),'anchor_id':anchor['id'],'body':text,'tag':'','status':'open','created_at':t,'updated_at':t}
    manifest['feedback']['comments'].append(row);manifest['updated_at']=t;_portable_event(manifest,'COMMENT_CREATED',entity_type='comment',entity_id=cid,payload={'anchor_id':anchor['id'],'body':text},created_at=t);_portable_write(manifest);return {**row,**{k:anchor[k] for k in ('selected_text','start_block_id','end_block_id','start_offset','end_offset')}}

@router.post('/api/portable-review/suggestions')
def portable_review_suggestion(body:ReviewSuggestionCreate):
    manifest=_portable_manifest();participant,snapshot=_portable_write_access(manifest,'permission_suggest');operation=str(body.operation or '').strip().lower()
    if operation not in {'insert','delete','replace'}:raise HTTPException(400,detail="BREADREVIEW_SUGGEST_001: Unsupported suggestion operation.")
    proposed=str(body.proposed_text or '')[:120000]
    if operation!='delete' and not proposed.strip():raise HTTPException(400,detail="BREADREVIEW_SUGGEST_002: Suggested text is required.")
    anchor=_portable_anchor(manifest,body.selection);t=storage_values.now();sid=storage_values.uid('review_suggestion_');original=str(anchor.get('selected_text') or '')
    row={'id':sid,'review_id':manifest['review_id'],'participant_id':manifest['participant_id'],'participant_name':participant.get('display_name'),'anchor_id':anchor['id'],'operation':operation,'original_text':original,'proposed_text':proposed,'status':'open','applied_generation':None,'created_at':t,'updated_at':t,'resolved_at':None}
    manifest['feedback']['suggestions'].append(row);manifest['updated_at']=t;_portable_event(manifest,'SUGGESTION_CREATED',entity_type='suggestion',entity_id=sid,payload={'anchor_id':anchor['id'],'operation':operation,'original_text':original,'proposed_text':proposed},created_at=t);_portable_write(manifest);return {**row,**{k:anchor[k] for k in ('selected_text','start_block_id','end_block_id','start_offset','end_offset')}}

@router.put('/api/portable-review/feedback')
def portable_review_feedback(body:ReviewFeedbackSubmit):
    manifest=_portable_manifest();participant,snapshot=_portable_write_access(manifest,'permission_feedback');questions=list(manifest.get('questions') or []);q_by_id={str(q.get('id')):q for q in questions};provided={str(a.question_id):str(a.answer_text or '') for a in body.answers}
    unknown=set(provided)-set(q_by_id)
    if unknown:raise HTTPException(400,detail="BREADREVIEW_FORM_001: Feedback contains an unknown question.")
    t=storage_values.now();existing={str(a.get('question_id')):a for a in manifest['feedback'].get('answers',[]) if isinstance(a,dict)};answers=[]
    for q in questions:
        qid=str(q.get('id')); old=existing.get(qid) or {};answers.append({'id':old.get('id') or storage_values.uid('review_answer_'),'review_id':manifest['review_id'],'snapshot_id':manifest['snapshot_id'],'question_id':qid,'participant_id':manifest['participant_id'],'answer_text':provided.get(qid,''),'created_at':old.get('created_at') or t,'updated_at':t})
    general_old=manifest['feedback'].get('general_feedback') or {};general={'id':general_old.get('id') or storage_values.uid('review_general_feedback_'),'review_id':manifest['review_id'],'snapshot_id':manifest['snapshot_id'],'participant_id':manifest['participant_id'],'body':str(body.general_feedback or '')[:240000],'created_at':general_old.get('created_at') or t,'updated_at':t}
    manifest['feedback']['answers']=answers;manifest['feedback']['general_feedback']=general;manifest['updated_at']=t;_portable_event(manifest,'FEEDBACK_SAVED',entity_type='feedback',entity_id=manifest['participant_id'],payload={'question_ids':[str(q.get('id')) for q in questions],'general_feedback_present':bool(general['body'])},created_at=t);_portable_write(manifest);return {'ok':True}

@router.get('/api/portable-review/assets/{asset_name}')
def portable_review_asset(asset_name:str):
    manifest=_portable_manifest();name=Path(str(asset_name or '')).name
    if name!=asset_name or name not in (manifest.get('assets') or {}):raise HTTPException(404)
    package_path=str(manifest['assets'][name])
    try:data=breadreview.read_asset(_active_portable_review_path(),package_path,_active_portable_passphrase(),max_asset_bytes=(_HOSTED_LIMITS.image_bytes if _HOSTED_LIMITS is not None else breadreview.MAX_ASSET_BYTES))
    except breadreview.BreadReviewError as exc:_portable_http_error(exc,404)
    from fastapi.responses import Response
    media=mimetypes.guess_type(name)[0] or 'application/octet-stream'
    if _HOSTED_LIMITS is not None:
        try: info=inspect_hosted_image_bytes(data,Path(name).suffix,byte_limit=_HOSTED_LIMITS.image_bytes)
        except UnsafeImage as exc: raise HTTPException(415,detail='Unsafe or unsupported hosted Review image asset') from exc
        media=info.media_type
    return Response(content=data,media_type=media,headers={'Cache-Control':'no-store, max-age=0','X-Content-Type-Options':'nosniff'})

def _export_breadreview_response(rid:str,participant_id:str,passphrase:str=''):
    if _active_portable_review_path() is not None:raise HTTPException(409,detail="BREADREVIEW_EXPORT_004: Cannot export another Review while a portable Review is open.")
    with storage_mutations.tx() as con:
        p=project(con);review=review_domain._review_for_current_project(con,p['id'],rid);participant=one(con,"SELECT * FROM review_participants WHERE id=? AND review_id=?",(participant_id,rid))
        if not participant:raise HTTPException(404,detail="REVIEW_PARTICIPANT_004: Participant does not belong to this Review.")
        manifest,assets=_breadreview_manifest_for_export(con,p,review,participant);filename=_breadreview_filename(review,participant)
    fd,tmp_name=tempfile.mkstemp(prefix='breadreview-export-',suffix='.breadreview');os.close(fd);out=Path(tmp_name)
    try:
        breadreview.write_new(out,manifest,assets,passphrase or None)
    except breadreview.BreadReviewError as exc:
        out.unlink(missing_ok=True);_portable_http_error(exc)
    except Exception:
        out.unlink(missing_ok=True);raise
    with storage_mutations.tx(operation='breadreview-export-record') as con:
        p=project(con);review_domain._review_for_current_project(con,p['id'],rid)
        snapshot_id=str(manifest.get('snapshot_id') or '')
        review_domain._review_event(con,rid,'BREADREVIEW_EXPORTED',participant_id=participant_id,entity_type='snapshot',entity_id=snapshot_id,payload={'snapshot_id':snapshot_id,'package_id':str(manifest.get('package_id') or '')})
    return FileResponse(out,filename=filename,media_type='application/octet-stream',background=BackgroundTask(out.unlink,missing_ok=True),headers={'X-BreadReview-Encrypted':'1' if passphrase else '0'})

@router.get('/api/reviews/{rid}/participants/{participant_id}/breadreview')
def export_breadreview(rid:str,participant_id:str):
    return _export_breadreview_response(rid,participant_id,'')

@router.post('/api/reviews/{rid}/participants/{participant_id}/breadreview')
def export_breadreview_secure(rid:str,participant_id:str,body:BreadReviewExportIn):
    return _export_breadreview_response(rid,participant_id,body.passphrase)

def _portable_id(value, prefix:str, code:str) -> str:
    text=str(value or '')
    if not text.startswith(prefix) or len(text)>128 or not re.fullmatch(r'[A-Za-z0-9_-]+',text):raise HTTPException(400,detail=f"{code}: Portable Review contains an invalid identity.")
    return text

def _portable_time(value, code:str) -> tuple[str,float]:
    text=str(value or '')
    try:
        parsed=datetime.fromisoformat(text)
        if parsed.tzinfo is None:raise ValueError
    except (TypeError,ValueError):
        raise HTTPException(400,detail=f"{code}: Portable Review contains an invalid timestamp.")
    return text,parsed.timestamp()

def _merge_breadreview_manifest(con,project_row:dict,manifest:dict) -> dict:
    project_identity=_bound_project_id()
    if str(manifest.get('project_id') or '')!=project_identity:raise HTTPException(409,detail="BREADREVIEW_IMPORT_001: This BreadReview belongs to a different project.")
    rid=str(manifest['review_id']);review=review_domain._review_for_current_project(con,project_row['id'],rid);manifest_snapshot_id=str(manifest.get('snapshot_id') or '');snapshot=review_snapshots._review_snapshot_payload(con,rid,manifest_snapshot_id)
    if not snapshot or str(snapshot.get('id'))!=str(manifest.get('snapshot_id')) or str(snapshot.get('content_hash') or '')!=str((manifest.get('snapshot') or {}).get('content_hash') or ''):
        raise HTTPException(409,detail="BREADREVIEW_IMPORT_002: The Review snapshot does not match this project.")
    participant_id=str(manifest['participant_id']);participant=one(con,"SELECT * FROM review_participants WHERE id=? AND review_id=?",(participant_id,rid))
    if not participant:raise HTTPException(409,detail="BREADREVIEW_IMPORT_003: The Review participant does not exist in this project.")
    if str(participant.get('status') or '')=='revoked':raise HTTPException(409,detail="BREADREVIEW_IMPORT_004: This participant has been revoked; returned feedback was not imported.")
    package_questions=list(manifest.get('questions') or []);local_questions=review_feedback._review_questions(con,rid,str(snapshot['id']))
    if [(str(q['id']),str(q['prompt']),bool(q['required'])) for q in package_questions]!=[(str(q['id']),str(q['prompt']),bool(q['required'])) for q in local_questions]:raise HTTPException(409,detail="BREADREVIEW_IMPORT_005: Questionnaire questions do not match the Review.")
    package_id=_portable_id(manifest.get('package_id'),'breadreview_','BREADREVIEW_IMPORT_006');phash=str(manifest.get('payload_hash') or '')
    receipt=one(con,"SELECT * FROM review_imports WHERE review_id=? AND package_id=?",(rid,package_id))
    if receipt and str(receipt.get('payload_hash') or '')==phash:return {'ok':True,'duplicate':True,'imported':0,'review_id':rid,'participant_id':participant_id}
    feedback=manifest.get('feedback') or {};anchors_raw=[a for a in feedback.get('anchors',[]) if isinstance(a,dict)];anchor_by_id={str(a.get('id')):a for a in anchors_raw}
    local_blocks=allrows(con,"SELECT source_block_id,ordinal,text_content FROM review_snapshot_blocks WHERE snapshot_id=? ORDER BY ordinal",(snapshot['id'],))
    imported=0
    own_item_ids=set()
    def ensure_anchor(anchor_id:str) -> dict:
        nonlocal imported
        aid=_portable_id(anchor_id,'review_anchor_','BREADREVIEW_IMPORT_007');raw=anchor_by_id.get(aid)
        if not raw:raise HTTPException(400,detail="BREADREVIEW_IMPORT_008: Feedback references a missing anchor.")
        selection=ReviewSelectionIn(start_block_id=str(raw.get('start_block_id') or ''),end_block_id=str(raw.get('end_block_id') or ''),start_offset=int(raw.get('start_offset') or 0),end_offset=int(raw.get('end_offset') or 0))
        expected=review_domain._review_selection_values(local_blocks,str(snapshot['id']),selection)
        for key in ('selected_text','prefix_context','suffix_context','context_hash'):
            if str(raw.get(key) or '')!=str(expected.get(key) or ''):raise HTTPException(409,detail="BREADREVIEW_IMPORT_009: Feedback anchor does not match the frozen Review snapshot.")
        existing=one(con,"SELECT * FROM review_anchors WHERE id=?",(aid,))
        if existing:
            if str(existing.get('review_id'))!=rid or str(existing.get('snapshot_id'))!=str(snapshot['id']) or str(existing.get('context_hash'))!=str(expected['context_hash']):raise HTTPException(409,detail="BREADREVIEW_IMPORT_010: Feedback anchor identity conflicts with existing Review data.")
            return existing
        con.execute("INSERT INTO review_anchors(id,review_id,snapshot_id,start_block_id,end_block_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,context_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(aid,rid,snapshot['id'],expected['start_block_id'],expected['end_block_id'],expected['start_offset'],expected['end_offset'],expected['selected_text'],expected['prefix_context'],expected['suffix_context'],expected['context_hash'],str(raw.get('created_at') or storage_values.now())))
        imported+=1;return one(con,"SELECT * FROM review_anchors WHERE id=?",(aid,))
    permissions={field:bool(snapshot.get(field)) for field in review_domain._REVIEW_PERMISSION_FIELDS}
    for kind,rows,prefix,table,needed in (
        ('highlight',feedback.get('highlights',[]),'review_highlight_','review_highlights','permission_comment'),
        ('comment',feedback.get('comments',[]),'review_comment_','review_comments','permission_comment'),
        ('suggestion',feedback.get('suggestions',[]),'review_suggestion_','review_suggestions','permission_suggest')):
        for raw in rows or []:
            if not isinstance(raw,dict) or str(raw.get('participant_id') or '')!=participant_id:continue
            if not permissions[needed]:raise HTTPException(409,detail="BREADREVIEW_IMPORT_011: Returned feedback uses a permission not granted by the frozen Review.")
            item_id=_portable_id(raw.get('id'),prefix,'BREADREVIEW_IMPORT_012');own_item_ids.add(item_id);anchor=ensure_anchor(str(raw.get('anchor_id') or ''))
            existing=one(con,f"SELECT * FROM {table} WHERE id=?",(item_id,))
            if existing:
                if str(existing.get('review_id'))!=rid or str(existing.get('participant_id'))!=participant_id or str(existing.get('anchor_id'))!=str(anchor['id']):raise HTTPException(409,detail="BREADREVIEW_IMPORT_013: Feedback identity conflicts with existing Review data.")
                continue
            t=str(raw.get('created_at') or storage_values.now())
            if kind=='highlight':
                tag=str(raw.get('tag') or raw.get('marker_name') or '')[:240];allowed=one(con,"SELECT name FROM review_snapshot_markers WHERE snapshot_id=? AND name=? COLLATE NOCASE",(snapshot['id'],tag)) if tag else None
                if not allowed:raise HTTPException(409,detail='BREADREVIEW_IMPORT_024: Returned highlight uses an unavailable Review marker.')
                con.execute("INSERT INTO review_highlights(id,review_id,participant_id,anchor_id,tag,status,created_at,updated_at) VALUES(?,?,?,?,?,'open',?,?)",(item_id,rid,participant_id,anchor['id'],str(allowed['name'])[:240],t,t))
            elif kind=='comment':
                body=str(raw.get('body') or '').strip()[:12000]
                if not body:raise HTTPException(400,detail="BREADREVIEW_IMPORT_014: Returned comment is empty.")
                con.execute("INSERT INTO review_comments(id,review_id,participant_id,anchor_id,body,tag,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'open',?,?)",(item_id,rid,participant_id,anchor['id'],body,str(raw.get('tag') or '')[:240],t,t))
            else:
                operation=str(raw.get('operation') or '')
                if operation not in {'insert','delete','replace'}:raise HTTPException(400,detail="BREADREVIEW_IMPORT_015: Returned suggestion operation is invalid.")
                proposed=str(raw.get('proposed_text') or '')[:120000]
                if operation!='delete' and not proposed.strip():raise HTTPException(400,detail="BREADREVIEW_IMPORT_016: Returned suggestion text is empty.")
                con.execute("INSERT INTO review_suggestions(id,review_id,participant_id,anchor_id,operation,original_text,proposed_text,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'open',?,?)",(item_id,rid,participant_id,anchor['id'],operation,str(anchor.get('selected_text') or ''),proposed,t,t))
            imported+=1
    if not permissions['permission_feedback'] and (feedback.get('answers') or feedback.get('general_feedback')):
        raise HTTPException(409,detail="BREADREVIEW_IMPORT_011: Returned feedback uses a permission not granted by the frozen Review.")
    if permissions['permission_feedback']:
        qids={str(q['id']) for q in local_questions};answers=[a for a in feedback.get('answers',[]) if isinstance(a,dict) and str(a.get('participant_id') or '')==participant_id]
        for raw in answers:
            qid=str(raw.get('question_id') or '')
            if qid not in qids:raise HTTPException(409,detail="BREADREVIEW_IMPORT_017: Returned feedback references an unknown question.")
            aid=_portable_id(raw.get('id'),'review_answer_','BREADREVIEW_IMPORT_018');text=str(raw.get('answer_text') or '')[:120000];existing=one(con,"SELECT * FROM review_question_answers WHERE review_id=? AND snapshot_id=? AND question_id=? AND participant_id=?",(rid,snapshot['id'],qid,participant_id));t,t_epoch=_portable_time(raw.get('updated_at') or storage_values.now(),'BREADREVIEW_IMPORT_023')
            if existing:
                _existing_t,existing_epoch=_portable_time(existing.get('updated_at'),'BREADREVIEW_IMPORT_023')
                if t_epoch<=existing_epoch:continue
                con.execute("UPDATE review_question_answers SET answer_text=?,updated_at=? WHERE id=?",(text,t,existing['id']))
            else:
                created,_created_epoch=_portable_time(raw.get('created_at') or t,'BREADREVIEW_IMPORT_023')
                con.execute("INSERT INTO review_question_answers(id,review_id,snapshot_id,question_id,participant_id,answer_text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(aid,rid,snapshot['id'],qid,participant_id,text,created,t))
            imported+=1
        general=feedback.get('general_feedback') or {}
        if isinstance(general,dict) and general:
            gid=str(general.get('id') or '');
            if not (gid.startswith('review_general_') and len(gid)<=128 and re.fullmatch(r'[A-Za-z0-9_-]+',gid)):raise HTTPException(400,detail="BREADREVIEW_IMPORT_019: Portable Review contains an invalid general-feedback identity.")
            body=str(general.get('body') or '')[:240000];existing=one(con,"SELECT * FROM review_general_feedback WHERE review_id=? AND snapshot_id=? AND participant_id=?",(rid,snapshot['id'],participant_id));t,t_epoch=_portable_time(general.get('updated_at') or storage_values.now(),'BREADREVIEW_IMPORT_024')
            if existing:
                _existing_t,existing_epoch=_portable_time(existing.get('updated_at'),'BREADREVIEW_IMPORT_024')
                if t_epoch>existing_epoch:
                    con.execute("UPDATE review_general_feedback SET body=?,updated_at=? WHERE id=?",(body,t,existing['id']));imported+=1
            else:
                created,_created_epoch=_portable_time(general.get('created_at') or t,'BREADREVIEW_IMPORT_024')
                con.execute("INSERT INTO review_general_feedback(id,review_id,snapshot_id,participant_id,body,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(gid,rid,snapshot['id'],participant_id,body,created,t));imported+=1
    status=str((manifest.get('participant') or {}).get('status') or 'not_started');local_status=str(participant.get('status') or 'not_started');t=storage_values.now()
    latest_snapshot=review_snapshots._review_snapshot_summary(con,rid)
    if latest_snapshot and str(latest_snapshot.get('id'))==str(snapshot.get('id')):
        if status=='completed' and local_status!='completed':con.execute("UPDATE review_participants SET status='completed',started_at=COALESCE(started_at,?),completed_at=?,updated_at=? WHERE id=?",(str((manifest['participant']).get('started_at') or t),str((manifest['participant']).get('completed_at') or t),t,participant_id));imported+=1
        elif status=='in_progress' and local_status=='not_started':con.execute("UPDATE review_participants SET status='in_progress',started_at=COALESCE(started_at,?),updated_at=? WHERE id=?",(str((manifest['participant']).get('started_at') or t),t,participant_id));imported+=1
    allowed_events={'PARTICIPANT_STARTED','PARTICIPANT_COMPLETED','HIGHLIGHT_CREATED','COMMENT_CREATED','SUGGESTION_CREATED','FEEDBACK_SAVED','BOOKMARK_SET','BOOKMARK_CLEARED'}
    for event in feedback.get('events',[]) or []:
        if not isinstance(event,dict) or str(event.get('participant_id') or '')!=participant_id:continue
        event_type=str(event.get('event_type') or '');entity_type=str(event.get('entity_type') or '');entity_id=str(event.get('entity_id') or '')
        if event_type not in allowed_events:raise HTTPException(400,detail="BREADREVIEW_IMPORT_020: Portable Review contains an unsupported event.")
        required_permission={'HIGHLIGHT_CREATED':'permission_comment','COMMENT_CREATED':'permission_comment','SUGGESTION_CREATED':'permission_suggest','FEEDBACK_SAVED':'permission_feedback','BOOKMARK_SET':'permission_read','BOOKMARK_CLEARED':'permission_read'}.get(event_type)
        if required_permission and not permissions[required_permission]:raise HTTPException(409,detail="BREADREVIEW_IMPORT_011: Returned feedback uses a permission not granted by the frozen Review.")
        if event_type in {'PARTICIPANT_STARTED','PARTICIPANT_COMPLETED','FEEDBACK_SAVED','BOOKMARK_SET','BOOKMARK_CLEARED'} and entity_id!=participant_id:raise HTTPException(409,detail="BREADREVIEW_IMPORT_021: Portable Review event identity is inconsistent.")
        if event_type in {'HIGHLIGHT_CREATED','COMMENT_CREATED','SUGGESTION_CREATED'} and entity_id not in own_item_ids:raise HTTPException(409,detail="BREADREVIEW_IMPORT_021: Portable Review event identity is inconsistent.")
        eid=_portable_id(event.get('id'),'review_event_','BREADREVIEW_IMPORT_022')
        if one(con,"SELECT id FROM review_events WHERE id=?",(eid,)):continue
        payload=event.get('payload') if isinstance(event.get('payload'),dict) else {};payload_json=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        if len(payload_json.encode('utf-8'))>256*1024:raise HTTPException(400,detail="BREADREVIEW_IMPORT_025: Portable Review event payload is too large.")
        event_time,_event_epoch=_portable_time(event.get('created_at') or t,'BREADREVIEW_IMPORT_026')
        con.execute("INSERT INTO review_events(id,review_id,participant_id,origin,event_type,entity_type,entity_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(eid,rid,participant_id,'portable',event_type,entity_type[:80],entity_id[:128],payload_json,event_time));imported+=1
    if receipt:con.execute("UPDATE review_imports SET participant_id=?,format_version=?,payload_hash=?,imported_at=? WHERE id=?",(participant_id,breadreview.PACKAGE_VERSION,phash,t,receipt['id']))
    else:con.execute("INSERT INTO review_imports(id,review_id,participant_id,package_id,format_version,payload_hash,imported_at) VALUES(?,?,?,?,?,?,?)",(storage_values.uid('review_import_'),rid,participant_id,package_id,breadreview.PACKAGE_VERSION,phash,t))
    return {'ok':True,'duplicate':False,'imported':imported,'review_id':rid,'participant_id':participant_id}

@router.post('/api/reviews/import-breadreview')
async def import_breadreview(file:UploadFile=File(...),passphrase:str=Form('')):
    if _active_portable_review_path() is not None:raise HTTPException(409,detail="BREADREVIEW_IMPORT_021: Cannot import a returned Review while a portable Review is open.")
    if Path(file.filename or '').suffix.lower()!='.breadreview':raise HTTPException(400,detail="BREADREVIEW_IMPORT_022: Choose a .breadreview file.")
    fd,tmp_name=tempfile.mkstemp(prefix='breadreview-import-',suffix='.breadreview');os.close(fd);tmp=Path(tmp_name)
    try:
        await save_upload_limited(file,tmp,_HOSTED_LIMITS.breadreview_bytes if _HOSTED_LIMITS is not None else breadreview.MAX_PACKAGE_BYTES)
        try:manifest=breadreview.read(tmp,passphrase or None,max_asset_bytes=(_HOSTED_LIMITS.image_bytes if _HOSTED_LIMITS is not None else breadreview.MAX_ASSET_BYTES))
        except breadreview.BreadReviewError as exc:_portable_http_error(exc,401 if exc.code in {'BREADREVIEW_037','BREADREVIEW_038'} else 400)
        with storage_mutations.tx(operation='breadreview-import') as con:
            p=project(con);result=_merge_breadreview_manifest(con,p,manifest)
        return result
    finally:tmp.unlink(missing_ok=True)
