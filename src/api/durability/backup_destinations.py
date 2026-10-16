from __future__ import annotations

"""Backup Destination configuration and provider connection HTTP endpoints."""

import html
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from api.shared_contracts import BaseModel
from backend.environment import runtime
from backend.history_runtime import queue_remote_history as _queue_remote_history
from backend.recovery_runtime import (
    _BACKUP_DESTINATION_RECONCILE,
    _BACKUP_DESTINATION_RECONCILE_LOCK,
    _BACKUP_DESTINATION_RECONCILE_PROJECTS,
    _BACKUP_DESTINATION_WAKE,
)
from backend.request_context import _bound_project_id, _require_hosted_project_management
from workspace.models import WorkspaceError

router = APIRouter()

class BackupDestinationCreate(BaseModel):
    path:str
    destination_kind:str='local'

class BackupDestinationUpdate(BaseModel):
    path:str|None=None
    name:str|None=None
    destination_kind:str|None=None
    recovery_retention_profile:str|None=None
    remote_history_retention:str|None=None

class WebDAVBackupDestinationConnect(BaseModel):
    endpoint:str
    username:str=''
    password:str=''
    name:str|None=None

class GoogleDriveOAuthStart(BaseModel):
    destination_id:str|None=None
    client_id:str|None=None
    client_secret:str|None=None

class ExternalBackupSettings(BaseModel):
    path:str=''

def _choose_local_directory() -> str | None:
    # Native chooser backs the 7.4 local-folder provider. Future direct cloud
    # providers use their own provider-specific connection flow.
    if sys.platform == "darwin":
        result=subprocess.run(
            ["osascript","-e","POSIX path of (choose folder)"],
            capture_output=True,text=True,timeout=3600,
        )
        if result.returncode==0:
            value=result.stdout.strip()
            return str(Path(value).expanduser()) if value else None
        if "User canceled" in result.stderr or "(-128)" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip() or "Folder picker failed")
    if os.name == "nt":
        script=(
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.ShowNewFolderButton = $true; "
            "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::Write($d.SelectedPath) }"
        )
        result=subprocess.run(
            ["powershell","-NoProfile","-STA","-Command",script],
            capture_output=True,text=True,timeout=3600,
        )
        if result.returncode!=0:
            raise RuntimeError(result.stderr.strip() or "Folder picker failed")
        value=result.stdout.strip()
        return str(Path(value).expanduser()) if value else None
    zenity=shutil.which("zenity")
    if zenity:
        result=subprocess.run([zenity,"--file-selection","--directory"],capture_output=True,text=True,timeout=3600)
        if result.returncode==0:
            value=result.stdout.strip()
            return str(Path(value).expanduser()) if value else None
        if result.returncode==1:
            return None
        raise RuntimeError(result.stderr.strip() or "Folder picker failed")
    try:
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk(); root.withdraw()
        try:
            root.attributes('-topmost', True)
        except Exception:
            pass
        value=filedialog.askdirectory(mustexist=False)
        root.destroy()
        return str(Path(value).expanduser()) if value else None
    except Exception as exc:
        raise RuntimeError("No native folder picker is available on this system") from exc

def _queue_destination_reconcile(project_id:str|None=None) -> None:
    pid=str(project_id or _bound_project_id() or '')
    if not pid:return
    with _BACKUP_DESTINATION_RECONCILE_LOCK:
        _BACKUP_DESTINATION_RECONCILE_PROJECTS.add(pid)
        _BACKUP_DESTINATION_RECONCILE.set()
    _BACKUP_DESTINATION_WAKE.set()

def _require_runtime_capability(capability:str, detail:str):
    if not runtime.capabilities.supports(capability):
        raise HTTPException(404,detail=detail)

@router.post("/api/backup-destinations/pick-folder")
def backup_destination_pick_folder():
    _require_hosted_project_management()
    _require_runtime_capability('native_filesystem','This Breadmaker runtime does not expose a native folder picker.')
    try:
        selected=_choose_local_directory()
    except Exception as exc:
        raise HTTPException(400,detail={"code":"BACKUP_DESTINATION_PICKER_001","message":"Breadmaker could not open the folder picker.","params":{"error":str(exc)}}) from exc
    if not selected:
        return {"ok":True,"cancelled":True,"path":""}
    return {"ok":True,"cancelled":False,"path":str(Path(selected).expanduser())}

@router.get("/api/backup-destinations")
def backup_destination_list():
    _require_hosted_project_management()
    if not runtime.capabilities.supports('native_filesystem') and not runtime.capabilities.supports('direct_remote_backups'):
        return {"destinations":[]}
    try:return {"destinations":runtime.backups.destinations()}
    except WorkspaceError as exc:raise HTTPException(409,detail=str(exc))

@router.post("/api/backup-destinations")
def backup_destination_add(body:BackupDestinationCreate):
    _require_hosted_project_management()
    if body.destination_kind in {'local','synced_folder'}:
        _require_runtime_capability('native_filesystem','This Breadmaker runtime does not expose local folder backup destinations.')
    try:
        destination=runtime.backups.add_destination(body.path,destination_kind=body.destination_kind)
        # Return the committed configuration state deterministically; only then wake
        # the asynchronous reconcile worker. The HTTP response must not race a worker
        # changing pending -> backing_up between commit and serialization.
        current=next((item for item in runtime.backups.destinations() if str(item.get('id'))==str(destination['id'])),destination)
        _queue_destination_reconcile()
        active_id=_bound_project_id()
        if active_id:_queue_remote_history(active_id,None)
        return {"ok":True,"destination":current}
    except (WorkspaceError,OSError) as exc:raise HTTPException(400,detail={"code":"BACKUP_DESTINATION_002","message":"Breadmaker could not use that backup destination.","params":{"error":str(exc)}})

@router.get("/api/backup-destinations/google-drive/config")
def backup_destination_google_drive_config():
    _require_hosted_project_management()
    _require_runtime_capability('direct_remote_backups','This Breadmaker runtime does not expose direct remote backup connections.')
    config=runtime.backups.google_oauth_config()
    return {"configured":bool(config.get("configured")),"client_id":str(config.get("client_id") or "")}

@router.post("/api/backup-destinations/google-drive/oauth/start")
def backup_destination_google_drive_oauth_start(body:GoogleDriveOAuthStart,request:Request):
    _require_hosted_project_management()
    _require_runtime_capability('direct_remote_backups','This Breadmaker runtime does not expose direct remote backup connections.')
    try:
        port=request.url.port or 80
        redirect_uri=f"http://127.0.0.1:{port}/api/backup-destinations/google-drive/oauth/callback"
        return runtime.backups.begin_google_oauth(redirect_uri,destination_id=body.destination_id,client_id=body.client_id,client_secret=body.client_secret)
    except (WorkspaceError,OSError) as exc:
        raise HTTPException(400,detail={"code":"BACKUP_GOOGLE_001","message":"Breadmaker could not start Google Drive authorization.","params":{"error":str(exc)}})

@router.get("/api/backup-destinations/google-drive/oauth/callback",response_class=HTMLResponse)
def backup_destination_google_drive_oauth_callback(state:str='',code:str='',error:str=''):
    _require_hosted_project_management()
    _require_runtime_capability('direct_remote_backups','This Breadmaker runtime does not expose direct remote backup connections.')
    if error:
        runtime.backups.fail_google_oauth(state,error)
        message=html.escape(str(error))
        return HTMLResponse(f"<!doctype html><meta charset='utf-8'><title>Breadmaker Google Drive</title><body style='font-family:Arial,sans-serif;padding:40px'><h2>Google Drive was not connected</h2><p>{message}</p><p>You can close this tab and return to Breadmaker.</p></body>",status_code=400)
    try:
        completed=runtime.backups.complete_google_oauth(state,code)
        project_id=str(completed.get('project_id') or '')
        _queue_destination_reconcile(project_id or None)
        if project_id:_queue_remote_history(project_id,None)
        return HTMLResponse("<!doctype html><meta charset='utf-8'><title>Google Drive</title><body style='font-family:Arial,sans-serif;padding:40px'><h2>Connected</h2><p>You can return to Breadmaker.</p><script>setTimeout(()=>window.close(),650)</script></body>")
    except (WorkspaceError,OSError) as exc:
        message=html.escape(str(exc))
        return HTMLResponse(f"<!doctype html><meta charset='utf-8'><title>Breadmaker Google Drive</title><body style='font-family:Arial,sans-serif;padding:40px'><h2>Google Drive could not be connected</h2><p>{message}</p><p>You can close this tab and return to Breadmaker.</p></body>",status_code=400)

@router.get("/api/backup-destinations/google-drive/oauth/status/{state}")
def backup_destination_google_drive_oauth_status(state:str):
    _require_hosted_project_management()
    _require_runtime_capability('direct_remote_backups','This Breadmaker runtime does not expose direct remote backup connections.')
    return runtime.backups.google_oauth_status(state)

@router.post("/api/backup-destinations/webdav")
def backup_destination_add_webdav(body:WebDAVBackupDestinationConnect):
    _require_hosted_project_management()
    _require_runtime_capability('direct_remote_backups','This Breadmaker runtime does not expose direct remote backup connections.')
    try:
        destination=runtime.backups.add_webdav_destination(body.endpoint,body.username,body.password,body.name)
        current=next((item for item in runtime.backups.destinations() if str(item.get('id'))==str(destination['id'])),destination)
        _queue_destination_reconcile()
        active_id=_bound_project_id()
        if active_id:_queue_remote_history(active_id,None)
        return {"ok":True,"destination":current}
    except (WorkspaceError,OSError) as exc:raise HTTPException(400,detail={"code":"BACKUP_REMOTE_001","message":"Breadmaker could not connect to that remote backup destination.","params":{"error":str(exc)}})

@router.put("/api/backup-destinations/{destination_id}/webdav")
def backup_destination_reconnect_webdav(destination_id:str,body:WebDAVBackupDestinationConnect):
    _require_hosted_project_management()
    _require_runtime_capability('direct_remote_backups','This Breadmaker runtime does not expose direct remote backup connections.')
    try:
        destination=runtime.backups.reconnect_webdav_destination(destination_id,body.endpoint,body.username,body.password,body.name)
        _queue_destination_reconcile()
        active_id=_bound_project_id()
        if active_id:_queue_remote_history(active_id,None)
        return {"ok":True,"destination":destination}
    except (WorkspaceError,OSError) as exc:raise HTTPException(400,detail={"code":"BACKUP_REMOTE_002","message":"Breadmaker could not reconnect that remote backup destination.","params":{"error":str(exc)}})

@router.put("/api/backup-destinations/{destination_id}")
def backup_destination_update(destination_id:str,body:BackupDestinationUpdate):
    _require_hosted_project_management()
    if body.path is not None or body.destination_kind is not None:
        _require_runtime_capability('native_filesystem','This Breadmaker runtime does not expose local folder backup destinations.')
    try:
        destination=runtime.backups.update_destination(destination_id,path_value=body.path,name=body.name,destination_kind=body.destination_kind,recovery_retention_profile=body.recovery_retention_profile,remote_history_retention=body.remote_history_retention)
        if body.path is not None:_queue_destination_reconcile()
        if body.remote_history_retention is not None:
            active_id=_bound_project_id()
            if active_id:_queue_remote_history(active_id,None)
        current=next((item for item in runtime.backups.destinations() if str(item.get('id'))==destination_id),destination)
        return {"ok":True,"destination":current}
    except (WorkspaceError,OSError) as exc:raise HTTPException(400,detail={"code":"BACKUP_DESTINATION_002","message":"Breadmaker could not update that backup destination.","params":{"error":str(exc)}})

@router.delete("/api/backup-destinations/{destination_id}")
def backup_destination_remove(destination_id:str):
    _require_hosted_project_management()
    try:return runtime.backups.remove_destination(destination_id)
    except WorkspaceError as exc:raise HTTPException(404,detail=str(exc))

@router.post("/api/backup-destinations/{destination_id}/now")
def backup_destination_now(destination_id:str):
    _require_hosted_project_management()
    try:
        result=runtime.backups.catch_up(destination_ids=[destination_id],reason='manual_destination_backup',force=True,quiet_seconds=0)
        if result.get('failed'):
            raise HTTPException(409,detail={"code":"BACKUP_DESTINATION_003","message":"Breadmaker could not complete that backup safely.","params":{}})
        completed={str(row.get('id')) for row in result.get('backed_up',[])}
        requested=next((row for row in result.get('destinations',[]) if str(row.get('id'))==str(destination_id)),None)
        if str(destination_id) not in completed and requested and requested.get('available') is False:
            raise HTTPException(409,detail={"code":"BACKUP_DESTINATION_003","message":"Breadmaker could not complete that backup safely.","params":{"reason":str(requested.get('reason') or 'unavailable')}})
        return result
    except HTTPException:raise
    except WorkspaceError as exc:raise HTTPException(409,detail=str(exc))

@router.post("/api/backup-destinations/catch-up")
def backup_destination_catch_up():
    _require_hosted_project_management()
    """Explicit synchronous barrier for Backup Destination catch-up."""
    try:
        return runtime.backups.catch_up(reason='explicit_catch_up',force=True,quiet_seconds=0)
    except WorkspaceError as exc:
        raise HTTPException(409,detail=str(exc))
