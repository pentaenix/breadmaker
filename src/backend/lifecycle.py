from __future__ import annotations

"""FastAPI process lifespan and persistence-worker shutdown ordering."""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from backend.environment import runtime


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    if runtime.mode!='local':
        # Hosted correctness is request/transaction/object-store based. No local
        # Recovery/History scheduler, SQLite process lock, or project-directory worker
        # participates in a hosted commit path.
        yield
        return

    # Import local persistence owners only in local composition. This matters in Cloud
    # Run: merely importing the hosted app must not initialize BREADMAKER_HOME/SQLite.
    from backend import diagnostics, history_runtime, local_history, portable_review_runtime, recovery_runtime, request_pipeline
    from formats import breadreview
    from storage import database as storage_database

    portable_path=portable_review_runtime.current_portable_review_path()
    if portable_path is not None and runtime.sessions.requires_authentication:
        raise RuntimeError('PORTABLE_REVIEW_STANDALONE_FORBIDDEN_WITH_ACCOUNT_SESSIONS')
    if portable_path is not None:
        info=breadreview.inspect(portable_path)
        if not info.get('encrypted'):
            breadreview.read(portable_path)
        yield
        return
    recovery_runtime._reset_recovery_runtime_state()
    request_pipeline.startup()
    recovery_runtime._start_backup_destination_worker()
    local_history._start_local_history_worker()
    history_runtime.start_remote_history_worker()
    try:
        yield
    finally:
        local_history.request_stop()
        history_runtime.request_remote_history_stop()
        recovery_runtime.request_backup_destination_stop()
        local_history_stopped=local_history.stop_worker(timeout=2.0)
        history_runtime.stop_remote_history_worker(timeout=1.0)
        backup_stopped=recovery_runtime.stop_backup_destination_worker(timeout=2.0)
        if backup_stopped and local_history_stopped:
            recovery_runtime._best_effort_shutdown_recovery()
            storage_database.release_all_instance_locks()
        else:
            diagnostics.log_event(
                'BACKUP_SHUTDOWN_003',
                'A persistence coordinator was still active after the bounded shutdown wait; skipped duplicate shutdown backup and retained the instance lock until process exit.',
                level='WARNING',data_safe=True,
            )
