from __future__ import annotations

"""Danger-zone confirmations, project deletion, and local data erasure."""

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend import client_leases, danger_challenges, diagnostics, local_history, recovery_runtime, request_pipeline
from api.project_management.contracts import DangerChallengeCreate, DangerConfirm
from backend.environment import runtime
from runtime.native import require_native_projects
from backend.project_activation import activate_project as _activate_project
from backend.request_context import _bound_project_id, _bound_user_id, _enforce_hosted_new_project_quota, _require_project_permission
from storage import coordination as storage_coordination
from storage import context as storage_context
from storage import database as storage_database
from storage import undo as storage_undo

router=APIRouter()

def _danger_actor_and_target(request:Request,action:str,project_id:str|None=None) -> tuple[str,str]:
    actor_id=request_pipeline._request_user_identity(request) or _bound_user_id() or str(runtime.identity.active_profile_id() or '')
    if action=='delete_project':
        target=str(project_id or request.headers.get('x-breadmaker-project') or '').strip()
        if not target:
            try:target=str(runtime.projects.profile_project_target(actor_id) or runtime.projects.active_id() or '')
            except Exception:target=''
    else:
        target=str(Path(require_native_projects(runtime).root).expanduser().resolve())
    if not actor_id or not target:raise HTTPException(400,detail='Destructive confirmation target is unavailable.')
    return actor_id,target

@router.post("/api/danger/challenge")
def danger_challenge(payload: DangerChallengeCreate,request:Request):
    if runtime.sessions.requires_authentication and payload.action in {'delete_all_data','delete_external_backups'}:
        raise HTTPException(403,detail='HOSTED_ACCOUNT_GLOBAL_ERASURE_DISABLED')
    actor_id,target_id=_danger_actor_and_target(request,payload.action,payload.project_id)
    return danger_challenges._new_danger_challenge(payload.action,actor_id=actor_id,target_id=target_id)

@router.delete("/api/projects/{project_id}")
def delete_workspace_project(project_id:str,payload:DangerConfirm,request:Request):
    _require_project_permission('project.delete',project_id)
    if project_id!=_bound_project_id():
        raise HTTPException(400,detail="Only the project bound to this editor session can be deleted from Project Center.")
    actor_id,target_id=_danger_actor_and_target(request,"delete_project",project_id)
    danger_challenges._consume_danger_challenge(payload.challenge_id,payload.code,"delete_project",actor_id=actor_id,target_id=target_id)
    original=runtime.projects.get(project_id)
    with storage_coordination.project_switch_lock():
        storage_database.release_instance_lock()
        try:
            runtime.projects.delete(project_id,permanent=False)
        except Exception:
            # If filesystem deletion failed before the project disappeared, restore the
            # normal process lock so a failed danger-zone action cannot weaken safety.
            if Path(original["path"]).exists():
                try:storage_database.acquire_instance_lock()
                except Exception as lock_error:
                    diagnostics.log_event("PROJECT_DELETE_RELOCK_001","Failed to reacquire the project lock after a rejected permanent deletion.",level="ERROR",context={"project_id":project_id},error=lock_error,data_safe=False)
            raise
        recovery_runtime._forget_recovery_project(project_id)
        client_leases.clear_project(project_id)
        actor_user_id=_bound_user_id() or runtime.identity.active_profile_id()
        target=runtime.projects.profile_project_target(actor_user_id)
        if not target and not runtime.sessions.requires_authentication:
            replacement=runtime.projects.create((payload.replacement_name or "Untitled Book").strip()[:120] or "Untitled Book",activate=False,owner_user_id=actor_user_id)
            target=replacement['id']
        if target:
            _activate_project(target,user_id=actor_user_id)
    return {"reload":True,"active_project_id":target or ''}

@router.post("/api/danger/delete-all-data")
def delete_all_breadmaker_data(payload:DangerConfirm,request:Request):
    """Erase the Breadmaker-owned local home even when project data is unreadable.

    Local privacy deletion deliberately does not require a project manifest/database to
    open. Optional backup erasure is best-effort against independently remembered,
    positively identified Breadmaker backup scopes; an unreachable destination can never
    block deletion of corrupted/unsupported local app data.
    """
    if runtime.sessions.requires_authentication:
        raise HTTPException(403,detail='HOSTED_ACCOUNT_GLOBAL_ERASURE_DISABLED')
    with runtime.history.remote_privacy_barrier(), runtime.backups.privacy_barrier(), storage_coordination.recovery_privacy_barrier(), local_history.activity_barrier():
        actor_id,target_id=_danger_actor_and_target(request,"delete_all_data")
        danger_challenges._consume_danger_challenge(payload.challenge_id,payload.code,"delete_all_data",actor_id=actor_id,target_id=target_id)
        backup_result={'removed':0,'attempted':0,'failures':[]}
        if payload.erase_external_backups:
            if payload.external_warning_acknowledged is not True:
                raise HTTPException(400,detail="Backup deletion requires the separate irreversible-action warning acknowledgement.")
            if not payload.external_challenge_id or not payload.external_code:
                raise HTTPException(400,detail="A separate confirmation is required to erase configured backup copies.")
            external_actor,external_target=_danger_actor_and_target(request,"delete_external_backups")
            danger_challenges._consume_danger_challenge(payload.external_challenge_id,payload.external_code,"delete_external_backups",actor_id=external_actor,target_id=external_target)
            try:
                backup_result=runtime.backups.erase_known_managed_data()
            except Exception as exc:
                # External deletion is intentionally narrower than local Delete All. A
                # provider failure must never strand corrupt/unsupported local app data.
                backup_result={'removed':0,'attempted':0,'failures':[{'reason':str(exc) or exc.__class__.__name__}]}
        root=require_native_projects(runtime).root
        from scripts.reset_workspace import validate_home
        validate_home(root)
        with storage_coordination.project_switch_lock():
            try:storage_database.release_all_instance_locks()
            except Exception:pass
            storage_undo.clear_session_undo_journal(all_projects=True)
            # Detach the process-global local storage pointer *before* the privacy
            # erase. A stale unbound worker must not be able to call ensure_dirs()
            # against the former project while the replacement registry is created.
            storage_context.clear_project()
            if root.exists(): shutil.rmtree(root)
            runtime.projects.init()
            if not runtime.sessions.requires_authentication:
                if not runtime.projects.list():
                    replacement=runtime.projects.create((payload.replacement_name or "Untitled Book").strip()[:120] or "Untitled Book",activate=False)
                    replacement_id=replacement['id']
                else:
                    replacement_id=runtime.projects.active_id()
                _activate_project(replacement_id)
                # Delete All promises that no pre-reset project survives locally. If
                # synchronous reset/bootstrap code exposed a stale writer that recreated
                # an old project directory, remove every unregistered sibling before the
                # privacy barriers reopen. The newly activated replacement is the sole
                # allowed project directory at this point.
                replacement_root=storage_context.current_project_root()
                projects_dir=Path(root).resolve()/'projects'
                if replacement_root is not None and projects_dir.is_dir():
                    replacement_root=Path(replacement_root).resolve()
                    for child in list(projects_dir.iterdir()):
                        try:resolved=child.resolve();resolved.relative_to(projects_dir.resolve())
                        except (OSError,ValueError):continue
                        if resolved==replacement_root:continue
                        if child.is_dir():shutil.rmtree(child,ignore_errors=False)
        # The reset has synchronously created and opened a verified current-format
        # project. Any Recovery state that existed before Delete All referred to bytes
        # that have now been erased and must not survive into the browser reload.
        recovery_runtime.set_recovery_mode(None)
        client_leases.clear_all()
        result={
            "reload":True,"reset":True,"external_backups_erased":bool(payload.erase_external_backups),
            "external_backup_erase":backup_result,
        }
        if runtime.sessions.requires_authentication:
            token=request.cookies.get(runtime.sessions.cookie_name);runtime.sessions.revoke(token)
            response=JSONResponse(result)
            response.delete_cookie(runtime.sessions.cookie_name,path='/',secure=runtime.sessions.cookie_secure,samesite='lax')
            return response
        return result
