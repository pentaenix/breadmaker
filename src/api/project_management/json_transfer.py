from __future__ import annotations

"""Public Project/Fragment JSON import, preview, and export HTTP surface."""

import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend import diagnostics
from backend.data_access import project
from backend.dependencies import feature_call
from backend.environment import runtime
from backend.project_activation import activate_project as _activate_project
from backend.request_context import (
    _bound_project_id, _bound_user_id, _enforce_hosted_new_project_quota,
    _project_is_current_for_bound_actor, _public_project_record,
)
from formats import fragment_json
from formats.project_json import validation as project_json_validation
from formats.project_json import importer as project_json_importer
from formats.project_json import exporter as project_json_exporter
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from workspace.filesystem import safe_name
from workspace.models import WorkspaceError

router=APIRouter()
_developmental_version_snapshot_tx=feature_call('canvas.developmental','_developmental_version_snapshot_tx')
_sync_developmental_canvases_tx=feature_call('canvas.developmental','_sync_developmental_canvases_tx')
sync_developmental_canvases=feature_call('canvas.developmental','sync_developmental_canvases')

def _unique_workspace_project_name(name:str)->str:
    base=safe_name(name)
    existing={str(p.get("name") or "").strip().lower() for p in runtime.projects.list(user_id=_bound_user_id() or None)}
    if base.lower() not in existing:return base
    number=2
    while f"{base} ({number})".lower() in existing:number+=1
    return f"{base} ({number})"


async def _read_project_json_upload(file:UploadFile)->tuple[str,dict]:
    filename=file.filename or "project.breadmaker.json"
    lower=filename.lower()
    if not (lower.endswith('.breadmaker.json') or lower.endswith('.json')):
        raise HTTPException(400,detail="PROJECT_JSON_010: Choose a .breadmaker.json or .json file.")
    chunks=[];size=0
    while True:
        chunk=await file.read(1024*1024)
        if not chunk:break
        size+=len(chunk)
        if size>project_json_validation.MAX_JSON_BYTES:
            raise HTTPException(413,detail={"code":"PROJECT_JSON_011","message":f"Project JSON is larger than {project_json_validation.MAX_JSON_BYTES//(1024*1024)} MB.","params":{"max_mb":project_json_validation.MAX_JSON_BYTES//(1024*1024)}})
        chunks.append(chunk)
    try:
        validated=project_json_validation.validate_document(project_json_validation.load_bytes(b''.join(chunks)))
    except project_json_validation.ProjectJSONError as exc:
        raise HTTPException(400,detail=exc.detail()) from exc
    return filename,validated


@router.post("/api/projects/import-json/preview")
async def preview_json_project(file:UploadFile=File(...)):
    filename,validated=await _read_project_json_upload(file)
    return {"ok":True,"filename":filename,"name":_unique_workspace_project_name(validated["document"]["name"]),"source_name":validated["document"]["name"],"summary":validated["summary"],"warnings":validated["warnings"][:100]}


@router.post("/api/projects/import-json")
async def import_json_project(file:UploadFile=File(...)):
    # The whole public JSON is parsed and reference-checked before a project folder is
    # created. Import then happens in one new project; any failure restores the user's
    # previous project and permanently removes the incomplete shell.
    filename,validated=await _read_project_json_upload(file)
    previous_id=_bound_project_id();rec=None
    import_name=_unique_workspace_project_name(validated["document"]["name"])
    validated["document"]["name"]=import_name
    try:
        owner_user_id=_bound_user_id() or None
        _enforce_hosted_new_project_quota(owner_user_id or '')
        rec=runtime.projects.create(import_name,activate=False,owner_user_id=owner_user_id)
        _activate_project(rec["id"],user_id=owner_user_id)
        with runtime.context.bind(rec["id"],user_id=owner_user_id):
            with storage_mutations.tx() as con:
                result=project_json_importer.import_into_active_database(con,validated)
            sync_developmental_canvases(int(result["project_id"]))
            with storage_mutations.tx() as con:
                project_json_importer.configure_developmental_versions(con,validated,result["refs"],_developmental_version_snapshot_tx)
                storage_invariants.validate_project_invariants(con,int(result["project_id"]))
        diagnostics.log_event("PROJECT_JSON_IMPORT_001","Created a Breadmaker project from Project JSON.",context={"project_id":rec["id"],"source_file":filename,"schema_version":project_json_validation.SCHEMA_VERSION})
        return {"ok":True,"reload":True,"project":_public_project_record(runtime.projects.get(rec["id"])),"summary":validated["summary"],"warnings":validated["warnings"]}
    except Exception:
        if rec is not None:
            try:
                if previous_id and _project_is_current_for_bound_actor(rec['id']):_activate_project(previous_id,user_id=_bound_user_id() or None)
            finally:
                try:runtime.projects.delete(rec["id"],permanent=True)
                except Exception as cleanup_error:
                    diagnostics.log_event("PROJECT_JSON_IMPORT_CLEANUP_001","Failed to remove an incomplete Project JSON import.",level="ERROR",context={"project_id":rec["id"]},error=cleanup_error,data_safe=False)
        raise


async def _read_any_json_upload(file:UploadFile)->tuple[str,dict,str]:
    filename=file.filename or "breadmaker.json";chunks=[];size=0
    while True:
        chunk=await file.read(1024*1024)
        if not chunk:break
        size+=len(chunk)
        if size>project_json_validation.MAX_JSON_BYTES:raise HTTPException(413,detail=f"JSON import is larger than {project_json_validation.MAX_JSON_BYTES//(1024*1024)} MB.")
        chunks.append(chunk)
    try:data=project_json_validation.load_bytes(b''.join(chunks))
    except project_json_validation.ProjectJSONError as exc:raise HTTPException(400,detail=exc.detail()) from exc
    fmt=data.get("format")
    try:
        if fmt==project_json_validation.FORMAT:return filename,project_json_validation.validate_document(data),"project"
        if fmt==fragment_json.FORMAT:return filename,fragment_json.validate_fragment(data),"fragment"
    except project_json_validation.ProjectJSONError as exc:raise HTTPException(400,detail=exc.detail()) from exc
    raise HTTPException(400,detail={"code":"JSON_IMPORT_001","message":f"Unsupported format {fmt!r}. Expected {project_json_validation.FORMAT!r} or {fragment_json.FORMAT!r}.","params":{"format":repr(fmt),"project_format":repr(project_json_validation.FORMAT),"fragment_format":repr(fragment_json.FORMAT)}})

def _json_context(value:str)->dict:
    if not value:return {}
    try:data=json.loads(value)
    except json.JSONDecodeError as exc:raise HTTPException(400,detail="JSON_IMPORT_002: Import context is invalid.") from exc
    return data if isinstance(data,dict) else {}

def _json_choices(value:str)->dict:
    if not value:return {}
    try:data=json.loads(value)
    except json.JSONDecodeError as exc:raise HTTPException(400,detail="JSON_IMPORT_003: Target choices are invalid.") from exc
    return data if isinstance(data,dict) else {}

def _enforce_fragment_scope(validated:dict,kind:str,ctx:dict)->None:
    scope=str(ctx.get("fragment_scope") or "").strip().lower()
    if not scope:return
    if scope not in {"canvas","map"}:raise HTTPException(400,detail="JSON_IMPORT_004: Unknown contextual Fragment import type.")
    if kind!="fragment":raise HTTPException(400,detail={"code":"JSON_IMPORT_005","message":f"Import {scope.title()} expects Breadmaker Fragment JSON, not a whole Project JSON file.","params":{"scope":scope.title()}})
    frag=validated["fragment"]
    if scope=="canvas":
        if not frag.get("canvases"):raise HTTPException(400,detail="JSON_IMPORT_006: Import Canvas expects a Fragment containing at least one Canvas.")
        unrelated=bool(frag.get("manuscript",{}).get("tabs") or frag.get("maps") or frag.get("tasks") or frag.get("cuts") or frag.get("mounts"))
        if unrelated:raise HTTPException(400,detail="JSON_IMPORT_007: Import Canvas accepts Canvas content and supporting Markers only. Use Import JSON for mixed Fragments.")
    else:
        if not frag.get("maps"):raise HTTPException(400,detail="JSON_IMPORT_008: Import Map expects a Fragment containing at least one Map.")
        unrelated=bool(frag.get("manuscript",{}).get("tabs") or frag.get("canvases") or frag.get("tasks") or frag.get("cuts") or frag.get("mounts"))
        if unrelated:raise HTTPException(400,detail="JSON_IMPORT_009: Import Map accepts Map content and supporting Markers only. Use Import JSON for mixed Fragments.")

@router.post("/api/json-import/preview")
async def preview_any_json(file:UploadFile=File(...),context:str=Form("")):
    filename,validated,kind=await _read_any_json_upload(file)
    ctx=_json_context(context);_enforce_fragment_scope(validated,kind,ctx)
    if kind=="project":
        return {"ok":True,"kind":"project","filename":filename,"name":_unique_workspace_project_name(validated["document"]["name"]),"source_name":validated["document"]["name"],"summary":validated["summary"],"warnings":validated["warnings"][:100],"targets":[]}
    try:
        with storage_mutations.tx() as con:
            p=project(con);fragment_json.check_marker_conflicts(con,validated);targets=fragment_json.resolve_mount_targets(con,validated,ctx);fragment_json.check_destination_conflicts(con,validated,targets)
    except project_json_validation.ProjectJSONError as exc:raise HTTPException(400,detail=exc.detail()) from exc
    return {"ok":True,"kind":"fragment","filename":filename,"name":validated["fragment"]["name"],"summary":validated["summary"],"warnings":validated["warnings"][:100],"targets":targets}

@router.post("/api/json-import")
async def import_any_json(file:UploadFile=File(...),context:str=Form(""),target_choices:str=Form("")):
    filename,validated,kind=await _read_any_json_upload(file)
    ctx=_json_context(context);_enforce_fragment_scope(validated,kind,ctx)
    if kind=="project":
        previous_id=_bound_project_id();rec=None;import_name=_unique_workspace_project_name(validated["document"]["name"]);validated["document"]["name"]=import_name
        try:
            owner_user_id=_bound_user_id() or None
            _enforce_hosted_new_project_quota(owner_user_id or '')
            rec=runtime.projects.create(import_name,activate=False,owner_user_id=owner_user_id);_activate_project(rec["id"],user_id=owner_user_id)
            with runtime.context.bind(rec["id"],user_id=owner_user_id):
                with storage_mutations.tx() as con:result=project_json_importer.import_into_active_database(con,validated)
                sync_developmental_canvases(int(result["project_id"]))
                with storage_mutations.tx() as con:
                    project_json_importer.configure_developmental_versions(con,validated,result["refs"],_developmental_version_snapshot_tx);storage_invariants.validate_project_invariants(con,int(result["project_id"]))
            return {"ok":True,"kind":"project","reload":True,"project":_public_project_record(runtime.projects.get(rec["id"])),"summary":validated["summary"],"warnings":validated["warnings"]}
        except Exception:
            if rec is not None:
                try:
                    if previous_id and _project_is_current_for_bound_actor(rec['id']):_activate_project(previous_id,user_id=_bound_user_id() or None)
                finally:
                    try:runtime.projects.delete(rec["id"],permanent=True)
                    except Exception:pass
            raise
    choices=_json_choices(target_choices)
    try:
        with storage_mutations.tx() as con:
            p=project(con);fragment_json.check_marker_conflicts(con,validated);targets=fragment_json.resolve_mount_targets(con,validated,ctx,choices)
            if any(t["needs_choice"] for t in targets):raise fragment_json.FragmentJSONError("Choose a destination for every mounted fragment item.","$.mounts","FRAGMENT_JSON_013")
            fragment_json.check_destination_conflicts(con,validated,targets)
            result=fragment_json.import_into_active_database(con,validated,targets);_sync_developmental_canvases_tx(con,int(result["project_id"]));storage_invariants.validate_project_invariants(con,int(result["project_id"]))
        diagnostics.log_event("FRAGMENT_JSON_IMPORT_001","Imported an additive Breadmaker JSON fragment.",context={"project_id":p["id"],"source_file":filename,"schema_version":fragment_json.SCHEMA_VERSION})
        return {"ok":True,"kind":"fragment","reload":False,"summary":validated["summary"],"warnings":validated["warnings"]}
    except project_json_validation.ProjectJSONError as exc:raise HTTPException(400,detail=exc.detail()) from exc

@router.get("/api/projects/{project_id}/export-json")
def export_json_project(project_id:str):
    if project_id!=_bound_project_id():
        raise HTTPException(409,detail="PROJECT_JSON_012: The export request does not match its project context.")
    try:rec=runtime.projects.get(project_id)
    except WorkspaceError as exc:raise HTTPException(404,detail=str(exc))
    try:
        with storage_mutations.tx() as con:
            document,warnings=project_json_exporter.export_active_database(con)
    except project_json_validation.ProjectJSONError as exc:
        raise HTTPException(409,detail=exc.detail()) from exc
    if warnings:
        diagnostics.log_event("PROJECT_JSON_EXPORT_WARN_001","Project JSON export omitted unsupported media.",context={"project_id":project_id,"warnings":warnings})
    fd,tmp_name=tempfile.mkstemp(prefix="breadmaker-project-json-",suffix=".breadmaker.json");os.close(fd);out=Path(tmp_name)
    out.write_text(json.dumps(document,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return FileResponse(out,filename=safe_name(rec["name"])+".breadmaker.json",media_type="application/json",background=BackgroundTask(out.unlink,missing_ok=True))
