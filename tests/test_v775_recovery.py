from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backups.retention import (
    RECOVERY_RETENTION_POLICIES,
    normalize_recovery_retention_profile,
    recovery_retention_partition,
)
from backups.scheduler import RecoveryBackupScheduler


def _row(generation:int, workday:str, created_at:str|None=None):
    return {
        'file':f'g{generation}.breadmaker',
        'project_generation':generation,
        'workday':workday,
        'created_at':created_at or f'{workday}T12:00:00+00:00',
    }


def test_v775_recovery_profiles_match_release_contract():
    assert RECOVERY_RETENTION_POLICIES['compact'].__dict__ == {
        'name':'compact','current_workday':2,'previous_workdays':7,'weekly':4,'monthly':6,'hard_cap':19,
    }
    assert RECOVERY_RETENTION_POLICIES['standard'].__dict__ == {
        'name':'standard','current_workday':3,'previous_workdays':20,'weekly':8,'monthly':12,'hard_cap':43,
    }
    assert RECOVERY_RETENTION_POLICIES['extended'].__dict__ == {
        'name':'extended','current_workday':5,'previous_workdays':45,'weekly':16,'monthly':24,'hard_cap':90,
    }


def test_v775_retention_generation_is_authority_over_future_clock():
    rows=[
        _row(1,'2999-01-01','2999-01-01T00:00:00+00:00'),
        _row(8,'2026-09-01','2026-09-01T00:00:00+00:00'),
        _row(7,'2026-09-01','2026-09-01T23:59:59+00:00'),
    ]
    keep,_=recovery_retention_partition(rows,'standard')
    assert keep[0]['project_generation']==8


def test_v775_current_workday_is_bounded_by_profile():
    rows=[_row(g,'2026-09-07') for g in range(10,0,-1)]
    keep,_=recovery_retention_partition(rows,'standard')
    assert [r['project_generation'] for r in keep]==[10,9,8]


def test_v775_sparse_active_workdays_consume_only_days_with_recovery_points():
    rows=[]
    for generation,day in [(9,'2026-09-07'),(8,'2026-09-01'),(7,'2026-08-15'),(6,'2026-06-01')]:
        rows.append(_row(generation,day))
    keep,_=recovery_retention_partition(rows,'compact')
    assert {r['project_generation'] for r in keep}.issuperset({9,8,7,6})


def test_v775_retention_profile_validation_fails_closed():
    assert normalize_recovery_retention_profile(None)=='standard'
    assert normalize_recovery_retention_profile('COMPACT')=='compact'
    with pytest.raises(ValueError):
        normalize_recovery_retention_profile('delete-everything')


def test_v775_quiet_session_trigger_requires_new_generation_and_quiet_period():
    s=RecoveryBackupScheduler(quiet_seconds=30,long_session_seconds=300,retry_seconds=60)
    s.observe('p',10,0)
    s.observe('p',11,5)
    assert s.due(34) is None
    d=s.due(35)
    assert d and d.trigger=='quiet_session' and d.generation==11 and not d.retry_only


def test_v775_long_session_trigger_fires_without_quiet_period():
    s=RecoveryBackupScheduler(quiet_seconds=30,long_session_seconds=100,retry_seconds=60)
    s.observe('p',1,0)
    for generation,stamp in [(2,1),(3,40),(4,80),(5,101)]:
        s.observe('p',generation,stamp)
    d=s.due(101)
    assert d and d.trigger=='long_session' and d.generation==5


def test_v775_lower_or_duplicate_generation_never_becomes_new_dirty_authority():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.observe('p',10,0)
    s.observe('p',12,1)
    s.observe('p',11,2)
    s.observe('p',12,3)
    assert s.observed_generation==12
    assert s.dirty_generation==12
    assert s.last_change==1


def test_v775_failed_destination_retry_is_throttled_and_retry_only():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.observe('p',1,0);s.observe('p',2,1)
    # The local Recovery point succeeded; only the external destination failed.
    s.mark_local_success(2,4);s.mark_destination_failure(5)
    assert s.due(64) is None
    d=s.due(65)
    assert d and d.trigger=='retry_failed_destinations' and d.retry_only


def test_v775_manual_update_is_explicit_and_not_a_timer_side_effect():
    s=RecoveryBackupScheduler()
    s.reset('p',20,0,protected_generation=20)
    assert s.due(9999) is None
    s.request_manual()
    d=s.due(9999)
    assert d and d.trigger=='manual_catch_up' and d.generation==20 and not d.retry_only
    assert s.due(9999) is None


def test_v775_success_at_older_generation_does_not_clear_newer_dirty_generation():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.observe('p',100,0);s.observe('p',101,1);s.observe('p',102,2)
    s.mark_success(101,3)
    assert s.last_success_generation==101
    assert s.dirty_generation==102
    assert s.dirty_since==3 and s.last_change==3


def test_v775_destination_failure_after_local_success_schedules_only_retry():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.observe('p',5,0);s.observe('p',6,1)
    s.mark_success(6,20,destinations_ok=False)
    assert s.dirty_generation==0
    assert s.due(79) is None
    d=s.due(80)
    assert d and d.retry_only and d.generation==6


def test_v775_destination_retry_cannot_block_new_local_recovery_work():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.reset('p',10,0,protected_generation=10)
    s.observe('p',11,1)
    s.mark_local_success(11,5)
    s.mark_destination_failure(5)
    # New Current work arrives while the external destination is still in backoff.
    s.observe('p',12,10)
    assert s.destination_retry_due==65
    d=s.due(20)
    assert d and d.trigger=='quiet_session' and not d.retry_only and d.generation==12


def test_v775_destination_retry_success_never_marks_local_generation_protected():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.reset('p',20,0,protected_generation=20)
    s.observe('p',21,1)
    s.mark_local_success(21,5)
    s.mark_destination_failure(5)
    s.observe('p',22,10)
    before=s.last_success_generation
    s.mark_destination_success()
    assert s.last_success_generation==before==21
    assert s.dirty_generation==22
    d=s.due(20)
    assert d and d.trigger=='quiet_session' and d.generation==22


def test_v775_package_capturing_generation_ahead_of_scheduler_is_not_redirtied_on_observe():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.reset('p',100,0,protected_generation=100)
    # Snapshot capture races with an edit and legitimately captures 101 before the
    # polling loop has observed 101.
    s.mark_local_success(101,5)
    assert s.last_success_generation==101
    s.observe('p',101,6)
    assert s.observed_generation==101
    assert s.dirty_generation==0
    assert s.due(1000) is None


def test_v775_local_failure_backoff_does_not_get_confused_with_destination_backoff():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.reset('p',5,0,protected_generation=5);s.observe('p',6,1)
    s.mark_local_failure(5)
    s.mark_destination_failure(7)
    assert s.local_retry_due==65 and s.destination_retry_due==67
    assert s.due(64) is None
    d=s.due(65)
    assert d and d.trigger=='retry_local_recovery' and not d.retry_only
    # Destination retry remains pending independently.
    assert s.destination_retry_due==67


def test_v775_local_retry_backoff_does_not_delay_due_destination_retry():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.reset('p',5,0,protected_generation=5);s.observe('p',6,1)
    # Local package retry is later than the destination retry. The destination must
    # still retry at its own deadline; only local quiet/long-session work is blocked.
    s.mark_local_failure(10)       # local due at 70
    s.mark_destination_failure(5)  # destination due at 65
    assert s.due(64) is None
    d=s.due(65)
    assert d and d.retry_only and d.trigger=='retry_failed_destinations'
    assert s.local_retry_due==70
    d=s.due(70)
    assert d and not d.retry_only and d.trigger=='retry_local_recovery'


def test_v775_delayed_old_project_results_cannot_mutate_new_project_scheduler_state():
    s=RecoveryBackupScheduler(quiet_seconds=10,long_session_seconds=100,retry_seconds=60)
    s.reset('old',10,0,protected_generation=10);s.observe('old',11,1)
    decision=s.due(11)
    assert decision and decision.project_id=='old' and decision.generation==11

    # A project switch commits before the old backup/provider callback returns.
    s.reset('new',20,12,protected_generation=20)
    assert s.mark_local_success(11,13,project_id='old') is False
    assert s.mark_destination_failure(13,project_id='old') is False
    assert s.project_id=='new'
    assert s.last_success_generation==20
    assert s.local_retry_due is None and s.destination_retry_due is None
