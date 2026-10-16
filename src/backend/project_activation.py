from __future__ import annotations

"""Project activation boundary."""

import json
import time
from pathlib import Path

from backend import diagnostics, recovery_runtime
from backend.data_access import project
from backend.dependencies import feature_call
from backend.environment import runtime
from runtime.native import require_native_projects
from backend.request_context import _bound_user_id
from storage import assets as storage_assets
from storage import context as storage_context
from storage import coordination as storage_coordination
from storage import database as storage_database
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import undo as storage_undo
from storage import values as storage_values
from workspace.constants import APP_SCHEMA_VERSION

sync_developmental_canvases = feature_call('canvas.developmental','sync_developmental_canvases')
_queue_destination_reconcile = feature_call('durability.backup_destinations','_queue_destination_reconcile')


def activate_project(project_id: str | None, *, user_id:str|None=None):
    if not project_id: raise RuntimeError('NO_ACTIVE_PROJECT')
    activation_user=str(user_id or _bound_user_id() or '') or None
    rec=runtime.projects.get(project_id)
    if runtime.sessions.requires_authentication and activation_user:
        previous_id=runtime.projects.profile_project_target(activation_user);previous=runtime.projects.get(previous_id) if previous_id else None
    else:previous=runtime.projects.active()
    root=Path(rec['path']).resolve();manifest=require_native_projects(runtime).read_manifest(root)
    schema=int(manifest.get('schema_version') or 0)
    if schema!=APP_SCHEMA_VERSION: raise RuntimeError(f'PREALPHA_PROJECT_INCOMPATIBLE:{schema}')
    if str(manifest.get('package_format') or '')!='breadmaker-project' or int(manifest.get('package_version') or 0)!=2:raise RuntimeError('PREALPHA_PROJECT_INCOMPATIBLE')
    if str(manifest.get('project_id') or '')!=str(project_id):raise RuntimeError('PROJECT_IDENTITY_MISMATCH')
    db=root/'project.sqlite3'
    if db.is_file():
        db_schema=storage_context.inspect_project_schema(root)
        if db_schema!=APP_SCHEMA_VERSION:raise RuntimeError(f'PREALPHA_PROJECT_INCOMPATIBLE:{db_schema}')
    else:
        state=root/'backups'/'recovery_state.json';pristine=False
        try:
            raw=json.loads(state.read_text(encoding='utf-8'));pristine=bool(raw.get('created_with_project')) and str(raw.get('workspace_project_id') or '')==str(project_id)
        except Exception:pristine=False
        if not pristine:raise RuntimeError('PROJECT_DATABASE_MISSING')
    with storage_context.project_context(root,str(project_id)):
        with storage_coordination.project_switch_lock():
            acquired_target_lock=False
            try:
                storage_context.configure_project(root);acquired_target_lock=bool(storage_database.acquire_instance_lock());storage_database.init_db()
                with storage_mutations.tx(operation='project_activation_health',track_generation=False) as con:
                    row=project(con)
                    if not row: raise RuntimeError('PROJECT_ROW_MISSING')
                    if str(row.get('project_uid') or '')!=str(project_id):raise RuntimeError('PROJECT_IDENTITY_MISMATCH')
                    if row['title']!=rec['name']:con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?',(rec['name'],storage_values.now(),row['id']))
                    storage_invariants.validate_project_invariants(con,int(row['id']))
                    quick=str(con.execute('PRAGMA quick_check').fetchone()[0]).lower()
                    if quick!='ok' or con.execute('PRAGMA foreign_key_check').fetchone() is not None:raise RuntimeError('PROJECT_DATABASE_INTEGRITY_FAILED')
                sync_developmental_canvases(int(row['id']))
            except Exception:
                if acquired_target_lock:
                    try:storage_database.release_instance_lock(root)
                    except Exception:pass
                if previous and previous.get('id')!=rec.get('id'):
                    try:
                        previous_root=Path(previous['path']).resolve()
                        with storage_context.project_context(previous_root,str(previous.get('id') or '')):
                            storage_context.configure_project(previous_root);storage_database.acquire_instance_lock();storage_database.init_db()
                    except Exception as rollback_error:
                        diagnostics.log_event('PROJECT_SWITCH_ROLLBACK_001','Failed to restore the previous project after a project-switch error.',level='ERROR',context={'previous_project_id':previous.get('id'),'target_project_id':rec.get('id')},error=rollback_error,data_safe=False)
                raise
            activated=runtime.projects.activate(project_id,user_id=activation_user);storage_undo.clear_session_undo_journal();storage_assets.cleanup_unreferenced_project_images()
            current_generation=storage_mutations.current_project_generation()
            protected_generation=storage_recovery.recovery_scheduler_protected_generation(Path(rec['path']),current_generation,initialize=True)
            scheduler=recovery_runtime._recovery_scheduler_for(str(project_id),make_current=True)
            scheduler.reset(str(project_id),current_generation,time.monotonic(),protected_generation=protected_generation)
            if manifest.get('backup_destinations'):_queue_destination_reconcile(str(project_id))
            return activated

