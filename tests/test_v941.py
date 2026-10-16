from __future__ import annotations
from source_contracts import backend_source

from pathlib import Path

from backups.scheduler import RecoveryBackupScheduler

ROOT=Path(__file__).resolve().parents[1]


def test_v941_release_and_recovery_coordinator_contract():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (9,4,1)
    app=backend_source(ROOT)
    scheduler=ROOT.joinpath('src','backups','scheduler.py').read_text(encoding='utf-8')
    assert 'def has_pending_work(self) -> bool:' in scheduler
    assert 'scheduler.has_pending_work()' in app
    assert 'def _reset_recovery_runtime_state()' in app
    assert 'recovery_runtime._reset_recovery_runtime_state()' in app
    assert 'recovery_runtime._start_backup_destination_worker()' in app


def test_v941_clean_scheduler_is_not_pending_but_dirty_and_retry_states_are():
    scheduler=RecoveryBackupScheduler()
    scheduler.reset('project-a',5,0,protected_generation=5)
    assert scheduler.has_pending_work() is False
    scheduler.observe('project-a',6,1)
    assert scheduler.has_pending_work() is True
    scheduler.mark_local_success(6,2,project_id='project-a')
    assert scheduler.has_pending_work() is False
    scheduler.mark_destination_failure(3,project_id='project-a')
    assert scheduler.has_pending_work() is True
