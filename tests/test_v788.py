from __future__ import annotations

from workspace import workspace
from backend import project_activation

from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import values as storage_values

import shutil
import time
from pathlib import Path

import app as app_module
from backend import recovery_runtime
from backups.scheduler import RecoveryBackupScheduler
from removable_volume_simulation import simulate_removable_volume


def _edit_first_block_direct(text: str) -> int:
    with storage_mutations.tx(operation='v788-direct-edit') as con:
        row=con.execute('SELECT id FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id LIMIT 1').fetchone()
        assert row
        html=f'<p>{text}</p>'
        con.execute(
            'UPDATE manuscript_blocks SET text_content=?,content_html=?,content_hash=?,updated_at=? WHERE id=?',
            (text,html,storage_values.block_content_hash(html,text,''),storage_values.now(),str(row[0])),
        )
    return storage_mutations.current_project_generation()


def test_v788_unknown_scheduler_protection_never_defaults_to_current():
    scheduler=RecoveryBackupScheduler(quiet_seconds=1,long_session_seconds=10,retry_seconds=1)
    scheduler.reset('project',37,100)
    assert scheduler.last_success_generation==0
    assert scheduler.dirty_generation==37
    assert scheduler.due(100.9) is None
    decision=scheduler.due(101.0)
    assert decision is not None and decision.trigger=='quiet_session' and decision.generation==37


def test_v788_real_activation_uses_verified_recovery_high_water(fresh_project):
    pid=str(fresh_project['project']['id'])
    root=Path(fresh_project['project']['path']).resolve()
    baseline=storage_mutations.current_project_generation()
    package=storage_recovery.backup_project('v788-activation-baseline',project_root=root)
    assert package is not None and package.is_file()
    advanced=_edit_first_block_direct('v788 committed after the last Recovery package')
    assert advanced>baseline

    rec=project_activation.activate_project(pid)
    assert rec and str(rec['id'])==pid
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.project_id==pid
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.last_success_generation==baseline
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.dirty_generation==advanced


def test_v788_activation_with_missing_recovery_state_fails_toward_more_backup(fresh_project):
    pid=str(fresh_project['project']['id'])
    root=Path(fresh_project['project']['path']).resolve()
    current=_edit_first_block_direct('v788 uncertainty must cause more backup')
    state=root/'backups'/'recovery_state.json'
    state.unlink(missing_ok=True)

    project_activation.activate_project(pid)
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.last_success_generation==0
    assert recovery_runtime._RECOVERY_BACKUP_SCHEDULER.dirty_generation==current


def test_v788_not_connected_destination_remains_retryable():
    result={
        'failed':[],
        'destinations':[{
            'id':'usb','available':False,'attention':False,'reason':'not_connected',
            'up_to_date':False,'current_generation':12,'published_generation':9,
        }],
    }
    assert recovery_runtime._destination_retry_required(result) is True
    result['destinations'][0]['up_to_date']=True
    assert recovery_runtime._destination_retry_required(result) is False


def test_v788_removable_drive_reinsert_catches_up_without_manual_action(client,fresh_project,tmp_path,monkeypatch):
    pid=str(fresh_project['project']['id'])
    usb=tmp_path/'writing-usb';usb.mkdir()
    simulate_removable_volume(monkeypatch,usb)
    added=client.post('/api/backup-destinations',json={'path':str(usb),'destination_kind':'local'})
    assert added.status_code==200,added.text
    destination=added.json()['destination'];did=str(destination['id'])
    seeded=workspace.backup_destinations_catch_up(pid,destination_ids=[did],reason='v788-seed',force=True,quiet_seconds=0)
    assert seeded['ok'] is True
    initial=next(row for row in seeded['destinations'] if str(row['id'])==did)
    assert initial['up_to_date'] is True

    parked=tmp_path/'writing-usb-unplugged'
    shutil.move(str(usb),str(parked))
    scheduler=recovery_runtime._RECOVERY_BACKUP_SCHEDULER
    old=(scheduler.quiet_seconds,scheduler.long_session_seconds,scheduler.retry_seconds)
    try:
        scheduler.quiet_seconds=0.05;scheduler.long_session_seconds=0.20;scheduler.retry_seconds=0.05
        project_activation.activate_project(pid)
        chapter=client.get('/api/bootstrap').json()['chapters'][0]
        loaded=client.get(f"/api/chapters/{chapter['id']}").json()
        saved=client.put(f"/api/chapters/{chapter['id']}/blocks",json={
            'expected_revision':loaded['revision'],
            'blocks':[{'text_content':'written while the removable drive is away','content_html':'<p>written while the removable drive is away</p>'}],
        })
        assert saved.status_code==200,saved.text
        target=storage_mutations.current_project_generation()

        deadline=time.monotonic()+3.5
        while time.monotonic()<deadline:
            rows=workspace.list_backup_destinations(pid)
            row=next(item for item in rows if str(item['id'])==did)
            if row['status']=='unavailable' and scheduler.destination_retry_due is not None:
                break
            time.sleep(0.05)
        else:
            raise AssertionError('disconnected removable destination was not kept retryable')

        # Reinsert the same volume/folder. No manual catch-up call and no further edit.
        shutil.move(str(parked),str(usb))
        deadline=time.monotonic()+4.0
        while time.monotonic()<deadline:
            row=next(item for item in workspace.list_backup_destinations(pid) if str(item['id'])==did)
            if row['up_to_date'] and int(row['published_generation'])==target:
                break
            time.sleep(0.05)
        else:
            raise AssertionError('removable destination did not automatically catch up after reinsertion')
    finally:
        scheduler.quiet_seconds,scheduler.long_session_seconds,scheduler.retry_seconds=old
        if parked.exists() and not usb.exists():shutil.move(str(parked),str(usb))


def test_v788_same_removable_volume_can_relocate_to_new_mount_root(tmp_path,monkeypatch):
    from workspace import filesystem as workspace_fs
    from workspace import Workspace

    home=tmp_path/'home';mount_a=tmp_path/'Mount A';mount_b=tmp_path/'Mount B'
    mount_a.mkdir();folder=mount_a/'Breadmaker Backups';folder.mkdir()
    ws=Workspace(home);ws.init();project=ws.create_project('Relocation')

    original_volume_metadata=workspace_fs._volume_metadata
    def volume_meta(path):
        path=Path(path).resolve()
        if path==mount_a.resolve() or mount_a.resolve() in path.parents:
            return {'id':'test-volume:stable-uuid','root':str(mount_a.resolve()),'label':'Writing USB'}
        if path==mount_b.resolve() or mount_b.resolve() in path.parents:
            return {'id':'test-volume:stable-uuid','root':str(mount_b.resolve()),'label':'Writing USB'}
        return original_volume_metadata(path)
    monkeypatch.setattr(workspace_fs,'_volume_metadata',volume_meta)
    destination=ws.add_backup_destination(str(folder),project['id'])
    assert destination['available'] is True

    shutil.move(str(mount_a),str(mount_b))
    monkeypatch.setattr(workspace_fs,'_mounted_volume_roots',lambda:[mount_b.resolve()])
    resolved=ws._resolve_local_destination(destination)
    assert resolved['available'] is True
    assert resolved.get('relocated') is True
    assert Path(resolved['path']).resolve()==(mount_b/'Breadmaker Backups').resolve()
