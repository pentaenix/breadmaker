from __future__ import annotations

"""Native .breadmaker package and whole-local-data transfer HTTP surface."""

import os
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend import diagnostics
from backend.environment import runtime, _HOSTED_LIMITS
from runtime.native import require_native_projects
from backend.project_activation import activate_project as _activate_project
from backend.request_context import _bound_user_id, _enforce_hosted_new_project_quota, _public_project_record, _require_project_permission
from security.image_security import UnsafeImage, inspect_hosted_image_bytes
from storage import coordination as storage_coordination
from workspace.filesystem import safe_name
from workspace.models import WorkspaceError

router=APIRouter()

@router.get("/api/projects/{project_id}/export")
def export_workspace_project(project_id: str):
    _require_project_permission('project.read',project_id)
    try: rec=runtime.projects.get(project_id)
    except WorkspaceError as exc: raise HTTPException(404,detail=str(exc))
    fd,tmp_name=tempfile.mkstemp(prefix="breadmaker-export-",suffix=".breadmaker")
    os.close(fd); out=Path(tmp_name)
    try: require_native_projects(runtime).export_package(project_id,out)
    except Exception:
        out.unlink(missing_ok=True); raise
    download_name=safe_name(rec["name"])+".breadmaker"
    return FileResponse(out,filename=download_name,media_type="application/octet-stream",background=BackgroundTask(out.unlink,missing_ok=True))

@router.get("/api/data/export")
def export_all_breadmaker_data():
    if runtime.sessions.requires_authentication:
        raise HTTPException(403,detail='HOSTED_ACCOUNT_GLOBAL_EXPORT_DISABLED')
    fd,tmp_name=tempfile.mkstemp(prefix="breadmaker-data-",suffix=".zip")
    os.close(fd);out=Path(tmp_name)
    try:
        # Keep project switching and active-project writes out of the snapshot window.
        with storage_coordination.project_switch_lock():
            require_native_projects(runtime).export_all_data(out)
    except Exception:
        out.unlink(missing_ok=True);raise
    stamp=time.strftime("%Y%m%d-%H%M%S",time.localtime())
    return FileResponse(out,filename=f"breadmaker-data-{stamp}.zip",media_type="application/zip",background=BackgroundTask(out.unlink,missing_ok=True))

@router.post("/api/projects/import")
async def import_workspace_project(file: UploadFile=File(...)):
    suffix=Path(file.filename or "project.breadmaker").suffix.lower()
    if suffix not in {".breadmaker",".zip"}: raise HTTPException(400,detail="PROJECT_IMPORT_001: Choose a .breadmaker project package.")
    owner_user_id=_bound_user_id() or None
    _enforce_hosted_new_project_quota(owner_user_id or '')
    local_limit=4*1024*1024*1024;upload_limit=_HOSTED_LIMITS.project_package_bytes if _HOSTED_LIMITS is not None else local_limit
    fd,tmp_name=tempfile.mkstemp(prefix="breadmaker-import-",suffix=suffix)
    os.close(fd); tmp=Path(tmp_name)
    try:
        size=0
        with tmp.open("wb") as fh:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk: break
                size+=len(chunk)
                if size>upload_limit: raise HTTPException(413,detail={"code":"PROJECT_IMPORT_002","message":"Project package exceeds the safe hosted import limit." if _HOSTED_LIMITS is not None else "Project package is larger than 4 GB.","params":{"max_bytes":upload_limit}})
                fh.write(chunk)
        if _HOSTED_LIMITS is not None:
            try:
                with zipfile.ZipFile(tmp) as archive:
                    infos=[info for info in archive.infolist() if not info.is_dir()]
                    expanded=sum(int(info.file_size or 0) for info in infos)
                    for info in infos:
                        if not str(info.filename).startswith('assets/'):
                            continue
                        if int(info.file_size or 0)>_HOSTED_LIMITS.image_bytes:
                            raise HTTPException(413,detail={'code':'HOSTED_PROJECT_IMAGE_LIMIT','message':'A project image exceeds the hosted image limit.','params':{'max_bytes':_HOSTED_LIMITS.image_bytes}})
                        try:
                            inspect_hosted_image_bytes(archive.read(info),Path(info.filename).suffix,byte_limit=_HOSTED_LIMITS.image_bytes)
                        except UnsafeImage as exc:
                            raise HTTPException(415,detail={'code':'HOSTED_PROJECT_IMAGE_UNSAFE','message':'Hosted project imports accept only structurally valid PNG, JPEG, or WebP image assets.','params':{}}) from exc
            except zipfile.BadZipFile:
                expanded=0
            if expanded>_HOSTED_LIMITS.project_package_expanded_bytes:
                raise HTTPException(413,detail={'code':'HOSTED_PROJECT_PACKAGE_EXPANDED_LIMIT','message':'Project package expands beyond the hosted import limit.','params':{'max_bytes':_HOSTED_LIMITS.project_package_expanded_bytes}})
            _enforce_hosted_new_project_quota(owner_user_id or '',estimated_bytes=expanded)
        try: rec=require_native_projects(runtime).import_package(tmp,owner_user_id=owner_user_id)
        except WorkspaceError as exc: raise HTTPException(400,detail={"code":"PROJECT_IMPORT_003","message":f"Project package import failed: {exc}","params":{}})
        _activate_project(rec["id"],user_id=owner_user_id)
        diagnostics.log_event("PROJECT_IMPORT_010","Imported and verified a Breadmaker project package.",context={"project_id":rec["id"],"source_file":file.filename or ""})
        return {"project":_public_project_record(rec),"reload":True}
    finally: tmp.unlink(missing_ok=True)
