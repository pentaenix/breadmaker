from __future__ import annotations

from workspace import workspace
from backend import project_activation

from storage import assets as storage_assets
from storage import context as storage_context
from storage import history as storage_history
from storage import integrity as storage_integrity
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import values as storage_values

from pathlib import Path

import pytest


def test_recovery_publication_can_reenter_its_coordinator_privacy_barrier():
    import threading
    from storage import coordination

    completed = threading.Event()

    def publish():
        with coordination.recovery_privacy_barrier():
            # backup_project takes this same gate inside a coordinator pass.
            with coordination.recovery_privacy_barrier():
                completed.set()

    worker = threading.Thread(target=publish, daemon=True)
    worker.start()
    assert completed.wait(2), 'nested Recovery publication deadlocked'
    worker.join(2)
    assert not worker.is_alive()


@pytest.mark.parametrize('blocked_gate', ['backup', 'recovery'])
def test_recovery_lookup_waits_for_privacy_erase_without_recreating_project(monkeypatch, tmp_path, blocked_gate):
    """Force the interleaving with events, not scheduling luck or sleeps."""
    import threading
    from contextlib import contextmanager
    from types import SimpleNamespace
    from storage import coordination
    from workspace.models import WorkspaceError

    attempted = threading.Event()
    stopped = threading.Event()
    wake = threading.Event(); wake.set()
    locks = {name: threading.RLock() for name in ('backup', 'recovery')}
    observed = []
    root = tmp_path / 'erased-project'; root.mkdir()

    @contextmanager
    def gate(name):
        if name == blocked_gate:
            attempted.set()
        with locks[name]:
            observed.append(name)
            yield

    def lookup(_pid):
        observed.append('lookup')
        stopped.set()
        assert not root.exists(), 'lookup entered while privacy erasure still held its barrier'
        raise WorkspaceError('PROJECT_NOT_FOUND')

    monkeypatch.setattr(recovery_runtime, 'runtime', SimpleNamespace(
        projects=SimpleNamespace(get=lookup),
        backups=SimpleNamespace(privacy_barrier=lambda: gate('backup')),
    ))
    monkeypatch.setattr(coordination, 'recovery_privacy_barrier', lambda: gate('recovery'))
    monkeypatch.setattr(recovery_runtime, '_BACKUP_DESTINATION_STOP', stopped)
    monkeypatch.setattr(recovery_runtime, '_BACKUP_DESTINATION_WAKE', wake)
    monkeypatch.setattr(recovery_runtime, '_tracked_recovery_project_ids', lambda: ['gone'])
    monkeypatch.setattr(recovery_runtime, '_forget_recovery_project', lambda _pid: None)
    worker = threading.Thread(target=recovery_runtime._backup_destination_worker_loop, daemon=True)
    try:
        with locks[blocked_gate]:
            worker.start()
            assert attempted.wait(2), 'worker did not reach the privacy gate'
            assert 'lookup' not in observed
            root.rmdir()  # Only this disposable, empty test directory.
        worker.join(2)
        assert not worker.is_alive()
        assert observed == ['backup', 'recovery', 'lookup']
        assert not root.exists()
    finally:
        stopped.set(); wake.set()
        worker.join(2)

from backend import diagnostics, lifecycle, recovery_runtime, request_pipeline
from api.durability import backups as backup_api
from api.durability import backup_destinations as backup_destinations_api
from api.durability import history as history_api
from storage import database as storage_database
from storage import mutations as storage_mutations
def _write_first_block(text: str) -> int:
    with storage_mutations.tx(operation='v775-integration-write') as con:
        row=con.execute('SELECT id FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id LIMIT 1').fetchone()
        html=f'<p>{text}</p>'
        con.execute('UPDATE manuscript_blocks SET text_content=?,content_html=?,content_hash=?,updated_at=? WHERE id=?',
                    (text,html,storage_values.block_content_hash(html,text,''),storage_values.now(),str(row[0])))
    return storage_mutations.current_project_generation()


def test_v775_activation_baseline_makes_first_edit_dirty(fresh_project):
    pid=str(fresh_project['project']['id'])
    baseline=storage_mutations.current_project_generation()
    # _activate_project establishes this before the editor can make its first save.
    recovery_runtime._RECOVERY_BACKUP_SCHEDULER.reset(pid,baseline,0,protected_generation=baseline)
    advanced=_write_first_block('first edit after activation')
    recovery_runtime._RECOVERY_BACKUP_SCHEDULER.observe(pid,advanced,1)
    assert advanced==baseline+1
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.dirty_generation==advanced
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.last_success_generation==baseline


def test_v775_shutdown_short_session_creates_local_recovery_but_no_change_does_not(fresh_project, monkeypatch, tmp_path):
    pid=str(fresh_project['project']['id'])
    baseline=storage_mutations.current_project_generation()
    recovery_runtime._RECOVERY_BACKUP_SCHEDULER.reset(pid,baseline,0,protected_generation=baseline)
    local_calls=[]; destination_calls=[]
    package=tmp_path/'shutdown.breadmaker';package.write_bytes(b'placeholder')

    def fake_full(reason,**kwargs):
        local_calls.append(reason);return package
    monkeypatch.setattr(recovery_runtime,'verified_full_backup',fake_full)
    monkeypatch.setattr(workspace,'inspect_package_metadata',lambda path:{'project_generation':storage_mutations.current_project_generation()})
    monkeypatch.setattr(workspace,'backup_destinations_catch_up',lambda *a,**k:(destination_calls.append((a,k)) or {'failed':[]}))

    recovery_runtime._best_effort_shutdown_recovery()
    assert local_calls==[]
    assert len(destination_calls)==1

    advanced=_write_first_block('tiny session before closing')
    recovery_runtime._RECOVERY_BACKUP_SCHEDULER.observe(pid,advanced,1)
    recovery_runtime._best_effort_shutdown_recovery()
    assert local_calls==['best_effort_shutdown']
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.last_success_generation==advanced


def test_v775_history_restore_cannot_overwrite_edit_arriving_during_safety_backup(fresh_project, client, monkeypatch):
    _write_first_block('history target')
    selected=storage_history.capture_local_revision(label='History target',creation_reason='manual',force_checkpoint=True)
    _write_first_block('current before History restore')
    # Capture the canonical implementation before monkeypatching its owner.
    # Capture the canonical History backup implementation before monkeypatching it.
    # Feature tests must patch the real owner rather than the composition module.
    original=backup_api.verified_full_backup

    def backup_then_concurrent_edit(reason,**kwargs):
        path=original(reason,**kwargs)
        _write_first_block('concurrent edit must survive')
        return path
    monkeypatch.setattr(backup_api,'verified_full_backup',backup_then_concurrent_edit)

    before=storage_mutations.current_project_generation()
    response=client.post(f"/api/history/v2/revisions/{selected['revision_id']}/restore",json={'expected_generation':before})
    assert response.status_code==409,response.text
    assert storage_mutations.current_project_generation()==before+1
    with storage_mutations.tx(operation='v775-read',track_generation=False) as con:
        text=str(con.execute('SELECT text_content FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id LIMIT 1').fetchone()[0])
    assert text=='concurrent edit must survive'


def test_v775_history_restore_publishes_safety_and_forward_landmark(fresh_project, client):
    _write_first_block('safe target')
    selected=storage_history.capture_local_revision(label='Safe target',creation_reason='manual',force_checkpoint=True)
    _write_first_block('current after target')
    before=storage_mutations.current_project_generation()
    response=client.post(f"/api/history/v2/revisions/{selected['revision_id']}/restore",json={'expected_generation':before})
    assert response.status_code==200,response.text
    assert storage_mutations.current_project_generation()==before+1
    rows=storage_history.list_local_revisions()
    reasons=[str(row.get('creation_reason') or '') for row in rows]
    assert 'pre_restore_safety' in reasons
    assert 'restore' in reasons
    with storage_mutations.tx(operation='v775-read',track_generation=False) as con:
        text=str(con.execute('SELECT text_content FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id LIMIT 1').fetchone()[0])
    assert text=='safe target'


def test_v775_concurrent_local_recovery_same_state_converges_on_one_package(fresh_project):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    before=storage_mutations.current_project_generation()
    barrier=threading.Barrier(3)

    def create(reason):
        barrier.wait(timeout=2)
        return storage_recovery.backup_project(reason)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(create,'concurrent-a')
        second=pool.submit(create,'concurrent-b')
        barrier.wait(timeout=2)
        paths=[first.result(timeout=8),second.result(timeout=8)]

    assert paths[0] is not None and paths[1] is not None
    assert paths[0].resolve()==paths[1].resolve()
    assert paths[0].is_file()
    assert storage_mutations.current_project_generation()==before
    info=storage_recovery._backup_manifest_info(paths[0])
    assert info and int(info['project_generation'])==before
    matching=[]
    for path in storage_context.BACKUP_DIR.glob('breadmaker_*.breadmaker'):
        row=storage_recovery._backup_manifest_info(path)
        if row and int(row['project_generation'])==before and row['fingerprint']==info['fingerprint']:
            matching.append(path)
    assert len(matching)==1


def test_v775_shutdown_does_not_start_second_backup_or_release_lock_while_worker_alive(monkeypatch):
    import asyncio

    calls=[]
    class StuckWorker:
        def is_alive(self): return True
        def join(self,timeout=None): calls.append(('join',timeout))

    monkeypatch.setattr(request_pipeline,'startup',lambda:None)
    monkeypatch.setattr(recovery_runtime,'_start_backup_destination_worker',lambda:None)
    monkeypatch.setattr(recovery_runtime,'_BACKUP_DESTINATION_THREAD',StuckWorker())
    monkeypatch.setattr(recovery_runtime,'_best_effort_shutdown_recovery',lambda:calls.append(('backup',None)))
    monkeypatch.setattr(storage_database,'release_all_instance_locks',lambda:calls.append(('release',None)))
    monkeypatch.setattr(diagnostics,'log_event',lambda *a,**k:calls.append(('diagnostic',a[0] if a else None)))

    async def exercise():
        async with lifecycle.app_lifespan(None):
            pass
    asyncio.run(exercise())

    assert ('join',2.0) in calls
    assert ('backup',None) not in calls
    assert ('release',None) not in calls
    assert ('diagnostic','BACKUP_SHUTDOWN_003') in calls


def test_v775_deep_integrity_covers_canvas_and_cut_asset_references(fresh_project, client):
    asset=storage_context.ASSET_DIR/'integrity-shared.bin'
    storage_assets.durable_write_project_asset(asset,b'integrity-asset')
    canvas=client.post('/api/canvases',json={'name':'Integrity canvas'}).json()
    node=client.post(f'/api/canvases/{canvas["id"]}/nodes',json={
        'title':'Image node','image_path':'/api/assets/integrity-shared.bin',
    })
    assert node.status_code==200,node.text
    chapter=fresh_project['bootstrap']['chapters'][0]
    cut=client.post('/api/cuts',json={
        'title':'Integrity cut',
        'content_html':'<p><img src="/api/assets/integrity-shared.bin"></p>',
        'source_chapter_id':chapter['id'],
    })
    assert cut.status_code==200,cut.text
    assert storage_integrity.deep_integrity_check()['ok'] is True

    asset.unlink()
    report=storage_integrity.deep_integrity_check()
    assert report['ok'] is False
    assert any('canvas node' in error and 'integrity-shared.bin' in error for error in report['errors'])
    assert any('cut' in error and 'integrity-shared.bin' in error for error in report['errors'])


def test_v775_destination_reconcile_failure_never_marks_local_recovery_failed(fresh_project, monkeypatch):
    import threading

    pid=str(fresh_project['project']['id'])
    baseline=storage_mutations.current_project_generation()
    # This test drives the worker loop directly. Stop the lifespan-owned worker before
    # resetting its compatibility alias; otherwise that real worker can replace the
    # alias between reset() and the assertion and turn this into a scheduling race.
    recovery_runtime._BACKUP_DESTINATION_STOP.set()
    recovery_runtime._BACKUP_DESTINATION_WAKE.set()
    live_worker=recovery_runtime._BACKUP_DESTINATION_THREAD
    if live_worker and live_worker.is_alive():
        live_worker.join(timeout=2)
    assert live_worker is None or not live_worker.is_alive()
    # With the lifespan worker fully stopped, clear rebuildable coordinator memory so
    # pending scheduler objects from an earlier project cannot consume this test's
    # one-shot mocked reconcile failure before the project under test is reached.
    recovery_runtime._reset_recovery_runtime_state()
    recovery_runtime._BACKUP_DESTINATION_STOP.clear()
    recovery_runtime._RECOVERY_BACKUP_SCHEDULER.reset(pid,baseline,0,protected_generation=baseline)
    with recovery_runtime._RECOVERY_BACKUP_SCHEDULERS_LOCK:
        recovery_runtime._RECOVERY_BACKUP_SCHEDULERS[pid]=recovery_runtime._RECOVERY_BACKUP_SCHEDULER
    with recovery_runtime._BACKUP_DESTINATION_RECONCILE_LOCK:
        recovery_runtime._BACKUP_DESTINATION_RECONCILE_PROJECTS.add(pid)
        recovery_runtime._BACKUP_DESTINATION_RECONCILE.set()
    recovery_runtime._BACKUP_DESTINATION_WAKE.set()

    active_root=Path(str(workspace.active()['path'])).resolve()
    monkeypatch.setattr(workspace,'active',lambda:{'id':pid,'path':str(active_root)})
    monkeypatch.setattr(storage_mutations,'current_project_generation',lambda:baseline)
    calls={'count':0}
    def failed_reconcile(*args,**kwargs):
        calls['count']+=1
        recovery_runtime._BACKUP_DESTINATION_STOP.set()
        return {'failed':[{'id':'usb','error':'offline'}]}
    monkeypatch.setattr(workspace,'backup_destinations_catch_up',failed_reconcile)

    worker=threading.Thread(target=recovery_runtime._backup_destination_worker_loop,daemon=True)
    worker.start();worker.join(timeout=2)
    assert not worker.is_alive()
    assert calls['count']==1
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.destination_retry_due is not None
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.local_retry_due is None


def test_v775_explicit_project_root_backup_cannot_follow_later_active_project_switch(fresh_project):
    old=workspace.active();assert old
    old_id=str(old['id']);old_root=Path(str(old['path'])).resolve()
    other=workspace.create_project('7.7.5.2 Other Project',activate=False)
    try:
        project_activation.activate_project(other['id'])
        assert str(workspace.active_id())==str(other['id'])
        package=storage_recovery.backup_project('old-project-after-switch',project_root=old_root)
        assert package is not None and package.parent.resolve()==(old_root/'backups').resolve()
        info=workspace.inspect_package_metadata(package)
        assert str(info.get('project_id'))==old_id
        # The operation must not leak an identically named package into the new root.
        new_root=Path(str(other['path'])).resolve()
        assert not (new_root/'backups'/package.name).exists()
    finally:
        project_activation.activate_project(old_id)


def test_v775_destination_reconcile_queue_is_project_scoped_across_switches(monkeypatch):
    with recovery_runtime._BACKUP_DESTINATION_RECONCILE_LOCK:
        recovery_runtime._BACKUP_DESTINATION_RECONCILE_PROJECTS.clear()
        recovery_runtime._BACKUP_DESTINATION_RECONCILE.clear()
    active={'id':'A'}
    monkeypatch.setattr(workspace,'active_id',lambda:active['id'])
    backup_destinations_api._queue_destination_reconcile()
    active['id']='B'
    backup_destinations_api._queue_destination_reconcile()
    with recovery_runtime._BACKUP_DESTINATION_RECONCILE_LOCK:
        assert recovery_runtime._BACKUP_DESTINATION_RECONCILE_PROJECTS=={'A','B'}
        # Finishing A cannot consume B's pending topology work.
        recovery_runtime._BACKUP_DESTINATION_RECONCILE_PROJECTS.discard('A')
        assert recovery_runtime._BACKUP_DESTINATION_RECONCILE_PROJECTS=={'B'}
        assert recovery_runtime._BACKUP_DESTINATION_RECONCILE.is_set()
        recovery_runtime._BACKUP_DESTINATION_RECONCILE_PROJECTS.clear()
        recovery_runtime._BACKUP_DESTINATION_RECONCILE.clear()


def test_v775_history_restore_switch_cannot_publish_old_assets_into_new_project(fresh_project, client):
    from revision_store.restore import restore_local_revision, HistoryRestoreStale

    old=workspace.active();assert old
    old_id=str(old['id']);old_root=Path(str(old['path'])).resolve()
    asset=old_root/'assets'/'old-history-only.bin'
    storage_assets.durable_write_project_asset(asset,b'old-history-asset')
    canvas=client.post('/api/canvases',json={'name':'Old history canvas'}).json()
    node=client.post(f'/api/canvases/{canvas["id"]}/nodes',json={
        'title':'Old asset node','image_path':'/api/assets/old-history-only.bin',
    })
    assert node.status_code==200,node.text
    source=storage_history.capture_local_revision(label='Old asset state',creation_reason='test',force_checkpoint=True)
    token=storage_mutations.capture_mutation_token('cross-project-history-restore')
    other=workspace.create_project('7.7.5.2 Restore Switch Target',activate=False)
    switched={'done':False}

    def stage(stage_name):
        if stage_name=='MATERIALIZED_VERIFIED' and not switched['done']:
            switched['done']=True
            project_activation.activate_project(other['id'])

    try:
        with pytest.raises(HistoryRestoreStale):
            restore_local_revision(
                source['revision_id'],
                expected_generation=token.source_generation,
                verified_backup=lambda:backup_api.verified_full_backup('cross-project-restore-safety',project_root=old_root),
                label='Old asset state',
                stage_hook=stage,
                project_root=old_root,
                mutation_token=token,
            )
        new_root=Path(str(other['path'])).resolve()
        assert not (new_root/'assets'/'old-history-only.bin').exists()
        assert asset.is_file()
        assert str(workspace.active_id())==str(other['id'])
    finally:
        project_activation.activate_project(old_id)


def test_v775_history_checkpoint_switch_cannot_copy_old_history_into_new_project(fresh_project, client, monkeypatch):
    old=workspace.active();assert old
    old_id=str(old['id']);old_root=Path(str(old['path'])).resolve()
    source=storage_history.capture_local_revision(label='Checkpoint source',creation_reason='test',force_checkpoint=True)
    other=workspace.create_project('7.7.5.2 Checkpoint Switch Target',activate=False)
    other_root=Path(str(other['path'])).resolve()
    original=history_api._history_v2_snapshot
    switched={'done':False}

    def snapshot_then_switch(revision_id,scope,chapter_id):
        result=original(revision_id,scope,chapter_id)
        if not switched['done']:
            switched['done']=True
            project_activation.activate_project(other['id'])
        return result
    monkeypatch.setattr(history_api,'_history_v2_snapshot',snapshot_then_switch)
    try:
        # 9.4 request scoping deliberately makes a shell switch harmless here. The
        # request remains bound to the source project, so it may finish successfully
        # but must never publish the old History snapshot into the newly selected book.
        response=client.post(
            f"/api/history/v2/revisions/{source['revision_id']}/checkpoint",
            json={'scope':'book','name':'Must not cross projects','description':''},
            headers={'X-Breadmaker-Project':old_id},
        )
        assert response.status_code==200,response.text
        with storage_context.project_context(old_root,old_id):
            with storage_mutations.tx(operation='v775-checkpoint-source-project-read',track_generation=False) as con:
                assert int(con.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0])==1
        with storage_context.project_context(other_root,str(other['id'])):
            with storage_mutations.tx(operation='v775-checkpoint-target-project-read',track_generation=False) as con:
                assert int(con.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0])==0
        assert str(workspace.active_id())==str(other['id'])
    finally:
        project_activation.activate_project(old_id)


def test_v775_cas_token_rejects_different_project_with_same_sqlite_id_and_generation(fresh_project):
    old=workspace.active();assert old
    old_id=str(old['id'])
    # Advance the source project enough that we can deterministically bring the new
    # project to the exact same generation. SQLite projects.id is 1 in both DBs.
    for i in range(3):
        _write_first_block(f'old generation alignment {i}')
    token=storage_mutations.capture_mutation_token('cross-project-cas')
    other=workspace.create_project('7.7.5.2 CAS Collision Target',activate=False)
    try:
        project_activation.activate_project(other['id'])
        while storage_mutations.current_project_generation()<token.source_generation:
            _write_first_block('new project generation alignment')
        assert storage_mutations.current_project_generation()==token.source_generation
        with storage_mutations.tx(operation='v775-read-new-title',track_generation=False) as con:
            row=con.execute('SELECT id,title FROM projects ORDER BY id LIMIT 1').fetchone()
            assert int(row[0])==int(token.project_id)==1
            title_before=str(row[1])
        with pytest.raises(storage_mutations.StaleGenerationError):
            with storage_mutations.tx(token=token,operation='must-not-cross-project') as con:
                con.execute('UPDATE projects SET title=? WHERE id=?',('STALE CROSS PROJECT WRITE',token.project_id))
        with storage_mutations.tx(operation='v775-read-new-title-after',track_generation=False) as con:
            assert str(con.execute('SELECT title FROM projects ORDER BY id LIMIT 1').fetchone()[0])==title_before
    finally:
        project_activation.activate_project(old_id)
