"""Synthetic installation authority: no browser, app import, endpoint or real data."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import subprocess
import sys

import pytest

from app_telemetry.installation_preview import SyntheticInstallationTelemetry, MINUTE, WINDOW, DAY, NOTICE


@pytest.fixture
def model(tmp_path):
    clock = [1_800_000_000_000 // WINDOW * WINDOW]
    path = tmp_path/'synthetic-telemetry.sqlite'
    def open_model(version='10.5.66'):
        return SyntheticInstallationTelemetry(path, synthetic_only=True, version=version, clock=lambda: clock[0])
    return clock, path, open_model


def test_pending_declined_and_background_activity_create_no_reports_or_token(model):
    clock,path,open_model=model
    authority=open_model()
    for choice in ('pending','declined'):
        assert authority.choice()==choice
        assert not authority.record_synthetic_activity('manuscript',foreground=True)
        assert not authority.prepare_synthetic_batch()
        assert authority.snapshot()==[]
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT token FROM consent').fetchone()[0] is None
        authority.set_consent(False)
    authority.set_consent(True)
    assert not authority.record_synthetic_activity('manuscript',foreground=False)
    assert not authority.record_synthetic_activity('manuscript',foreground=1)
    assert not authority.record_synthetic_activity('private/path/title',foreground=True)
    assert authority.snapshot()==[]


def test_multiple_windows_and_restarts_share_identity_session_and_minute_union(model):
    clock,path,open_model=model
    a,b=open_model(),open_model()
    assert a.set_consent(True)
    assert a.record_synthetic_activity('manuscript',foreground=True)
    assert b.record_synthetic_activity('canvas',foreground=True)
    for _ in range(5):
        assert b.record_synthetic_activity('canvas',foreground=True)
    row=a.snapshot()[0]
    assert row['active_minutes']==1 and row['sessions']==1
    assert row['workspace_minutes']['manuscript']==row['workspace_minutes']['canvas']==1
    token=row['installation_id']
    clock[0]+=MINUTE
    c=open_model()
    assert c.set_consent(True)  # idempotent grant must not rotate or restart
    c.record_synthetic_activity('tasks',foreground=True)
    assert c.snapshot()[0]['installation_id']==token
    assert c.snapshot()[0]['active_minutes']==2 and c.snapshot()[0]['sessions']==1
    clock[0]+=30*MINUTE
    c.record_synthetic_activity('reviews',foreground=True)
    assert sum(row['sessions'] for row in c.snapshot())==2


def test_simultaneous_process_connections_share_one_delivery_reservation(model):
    clock,path,open_model=model
    owners=[open_model() for _ in range(4)]
    owners[0].set_consent(True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda owner:owner.record_synthetic_activity('maps',foreground=True),owners))
    assert owners[0].snapshot()[0]['active_minutes']==1
    assert owners[0].snapshot()[0]['sessions']==1
    clock[0]+=WINDOW
    with ThreadPoolExecutor(max_workers=4) as pool:
        batches=list(pool.map(lambda owner:owner.prepare_synthetic_batch(),owners))
    assert len([batch for batch in batches if batch])==1
    assert open_model().prepare_synthetic_batch() is None


def test_withdrawal_purges_all_windows_invalidates_batches_and_preserves_budget(model):
    clock,path,open_model=model
    a,b=open_model(),open_model()
    a.set_consent(True);a.record_synthetic_activity('maps',foreground=True)
    old_token=a.snapshot()[0]['installation_id']
    clock[0]+=WINDOW
    batch=a.prepare_synthetic_batch()
    assert b.set_consent(False)
    assert a.choice()=='declined' and a.snapshot()==[]
    assert not a.acknowledge_synthetic_batch(batch.reservation)
    assert not a.record_synthetic_activity('canvas',foreground=True)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT token,last_activity FROM consent').fetchone()==(None,None)
    b.set_consent(True);b.record_synthetic_activity('tasks',foreground=True)
    assert b.snapshot()[0]['installation_id']!=old_token
    assert not b.acknowledge_synthetic_batch(batch.reservation)
    # Clock rollback cannot defeat the persisted budget or mutate prepared windows.
    clock[0]-=1
    assert not b.record_synthetic_activity('canvas',foreground=True)
    assert b.prepare_synthetic_batch() is None


def test_failed_or_stale_acknowledgements_do_not_drop_rows_or_reset_budget(model):
    clock,path,open_model=model
    a=open_model();a.set_consent(True);a.record_synthetic_activity('maps',foreground=True)
    clock[0]+=WINDOW
    first=a.prepare_synthetic_batch()
    assert first and not a.acknowledge_synthetic_batch('00000000-0000-4000-8000-000000000000')
    assert open_model().prepare_synthetic_batch() is None
    clock[0]+=WINDOW
    second=open_model().prepare_synthetic_batch()
    assert second and second.reservation!=first.reservation
    assert not a.acknowledge_synthetic_batch(first.reservation)
    assert a.acknowledge_synthetic_batch(second.reservation)
    assert not a.acknowledge_synthetic_batch(second.reservation)
    assert a.snapshot()==[]


def test_queue_bounds_expiry_payload_projection_and_original_release(model):
    clock,path,open_model=model
    a=open_model();a.set_consent(True)
    for _ in range(110):
        a.record_synthetic_activity('manuscript',foreground=True)
        clock[0]+=WINDOW
    rows=a.snapshot()
    assert len(rows)<=96
    b=open_model(version='10.5.67')
    batch=b.prepare_synthetic_batch()
    assert len(batch.body)<=4096
    payload=json.loads(batch.body)
    assert len(payload['reports'])==4
    assert all(row['app_version']=='10.5.66' for row in payload['reports'])
    assert set(payload['reports'][0])=={'schema','installation_id','notice_version','app_version','bucket_utc_ms','sessions','active_minutes','workspace_minutes'}
    assert payload['reports'][0]['notice_version']==NOTICE
    clock[0]+=DAY
    assert b.snapshot()==[]


def test_database_lock_failure_is_silent_and_failed_withdrawal_stops_instance(model):
    clock,path,open_model=model
    a=open_model();a.set_consent(True)
    with sqlite3.connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        assert not a.record_synthetic_activity('maps',foreground=True)
        assert a.prepare_synthetic_batch() is None
        assert not a.set_consent(False)
        db.rollback()
    assert a.choice()=='declined'
    assert not a.record_synthetic_activity('maps',foreground=True)
    # Failed persistence cannot be represented as global withdrawal.
    assert open_model().choice()=='granted'


def test_explicit_synthetic_guard_and_no_legacy_database_migration(model):
    clock,path,open_model=model
    with pytest.raises(ValueError,match='SYNTHETIC_ONLY_REQUIRED'):
        SyntheticInstallationTelemetry(path,synthetic_only=False)
    assert not path.exists()
    open_model()
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError,match='SCRATCH_SCHEMA_UNSUPPORTED'):
        open_model()


def test_separate_processes_share_the_budget_without_importing_workspace(model):
    clock,path,open_model=model
    a=open_model();a.set_consent(True);a.record_synthetic_activity('maps',foreground=True)
    clock[0]+=WINDOW
    code='''
import sys
from pathlib import Path
from app_telemetry.installation_preview import SyntheticInstallationTelemetry
assert 'workspace' not in sys.modules
a=SyntheticInstallationTelemetry(Path(sys.argv[1]),synthetic_only=True,clock=lambda:int(sys.argv[2]))
print(int(a.prepare_synthetic_batch() is not None))
'''
    processes=[subprocess.Popen([sys.executable,'-c',code,str(path),str(clock[0])],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    try:
        outputs=[process.communicate(timeout=1) for process in processes]
        assert all(process.returncode==0 for process in processes),outputs
        assert sorted(output[0].strip() for output in outputs)==['0','1']
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill();process.wait(timeout=1)


def test_unrelated_sqlite_file_is_rejected_without_changing_its_tables(tmp_path):
    path=tmp_path/'unrelated.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE unrelated (value TEXT)')
    path.chmod(0o600)
    with pytest.raises(ValueError,match='SCRATCH_SCHEMA_UNSUPPORTED'):
        SyntheticInstallationTelemetry(path,synthetic_only=True)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[('unrelated',)]


def test_operations_do_not_recreate_an_erased_scratch_database(model):
    clock,path,open_model=model
    a=open_model();a.set_consent(True);a.record_synthetic_activity('maps',foreground=True)
    path.unlink()
    assert not a.record_synthetic_activity('canvas',foreground=True)
    assert a.snapshot()==[] and a.prepare_synthetic_batch() is None
    assert not a.set_consent(False)
    assert not path.exists()


def test_private_storage_rejects_shared_permissions_links_and_late_mode_changes(model,tmp_path):
    clock,path,open_model=model
    a=open_model();a.set_consent(True)
    path.chmod(0o644)
    assert not a.record_synthetic_activity('maps',foreground=True)
    assert a.choice()=='declined'
    with pytest.raises(ValueError,match='PRIVATE_DATABASE_REQUIRED'):
        open_model()
    path.chmod(0o600)
    alias=tmp_path/'alias.sqlite';alias.symlink_to(path)
    with pytest.raises(ValueError,match='PRIVATE_DATABASE_REQUIRED'):
        SyntheticInstallationTelemetry(alias,synthetic_only=True)
    alias.unlink();alias.hardlink_to(path)
    with pytest.raises(ValueError,match='PRIVATE_DATABASE_REQUIRED'):
        open_model()
    alias.unlink()
    tmp_path.chmod(0o755)
    try:
        assert a.snapshot()==[]
        with pytest.raises(ValueError,match='PRIVATE_DIRECTORY_REQUIRED'):
            open_model()
    finally:
        tmp_path.chmod(0o700)


def test_logical_erasure_is_seen_by_existing_and_reopened_authorities(model):
    clock,path,open_model=model
    a,b=open_model(),open_model()
    a.set_consent(True);a.record_synthetic_activity('maps',foreground=True)
    clock[0]+=WINDOW;batch=a.prepare_synthetic_batch()
    assert b.erase_synthetic_state()
    assert a.choice()==open_model().choice()=='declined'
    assert a.snapshot()==[] and not a.acknowledge_synthetic_batch(batch.reservation)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT token,decided,last_activity FROM consent').fetchone()==(None,None,None)
        assert db.execute('SELECT COUNT(*) FROM windows').fetchone()[0]==0
    assert not a.record_synthetic_activity('manuscript',foreground=True)
    assert b.erase_synthetic_state()  # idempotent refusal tombstone


def test_optional_retention_worker_actually_prunes_idle_file_only_when_running(model):
    import threading
    from app_telemetry.retention_preview import SyntheticRetentionRunner
    clock,path,open_model=model
    a=open_model();a.set_consent(True);a.record_synthetic_activity('maps',foreground=True)
    first,expired=threading.Event(),threading.Event()
    initial=clock[0]
    class Observed:
        def maintain_synthetic_retention(self):
            ok=a.maintain_synthetic_retention()
            first.set()
            if clock[0]>=initial+DAY and ok:
                expired.set()
            return ok
    worker=SyntheticRetentionRunner(Observed(),synthetic_only=True,interval_seconds=.01)
    assert worker.start() and not worker.start()
    try:
        assert first.wait(.5)
        clock[0]+=DAY
        assert expired.wait(.5)
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT COUNT(*) FROM windows').fetchone()[0]==0
            assert db.execute('SELECT last_activity,last_attempt FROM consent').fetchone()==(None,None)
    finally:
        assert worker.close()
    a.record_synthetic_activity('maps',foreground=True)
    clock[0]+=DAY
    with sqlite3.connect(path) as db:
        # No running mechanism: expiry is explicitly not automatic.
        assert db.execute('SELECT COUNT(*) FROM windows').fetchone()[0]==1
    assert a.maintain_synthetic_retention()


def test_retention_worker_failure_is_silent_and_closes():
    import threading
    from app_telemetry.retention_preview import SyntheticRetentionRunner
    called=threading.Event()
    class Broken:
        def maintain_synthetic_retention(self):
            called.set();raise RuntimeError('synthetic outage')
    worker=SyntheticRetentionRunner(Broken(),synthetic_only=True,interval_seconds=.01)
    worker.start()
    assert called.wait(.5)
    assert worker.close()
