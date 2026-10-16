from __future__ import annotations

from storage import context as storage_context
from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import values as storage_values

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from workspace import filesystem as workspace_fs
from backups.publication import PUBLICATION_SUFFIX
from workspace import Workspace


@contextmanager
def _current_workspace(tmp_path: Path):
    """Create one isolated current-format project without starting app workers."""
    ws=Workspace(tmp_path/'home');ws.init();project=ws.create_project('7.8.9 safety')
    root=Path(project['path']).resolve();previous=storage_context.PROJECT_ROOT
    with storage_context.project_context(root,str(project['id'])):
        storage_context.configure_project(root);storage_database.init_db()
        try:
            yield ws,project,root
        finally:
            if previous is not None:storage_context.configure_project(previous)


def _published_local(folder: Path, project_id: str):
    publications=sorted(folder.glob(f'breadmaker-{project_id}-*.breadmaker{PUBLICATION_SUFFIX}'))
    assert publications
    publication=publications[-1]
    row=json.loads(publication.read_text(encoding='utf-8'))
    package=folder/row['package_file']
    assert package.is_file()
    return publication,row,package


def test_v789_release_contract_removes_weaker_persistence_seams():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (7,8,9)
    assert not hasattr(storage_recovery,'backup_db')
    for name in ('configured_external_backup_dirs','preflight_external_backup_erasure','erase_managed_external_backups','destination_backup_path'):
        assert not hasattr(Workspace,name)


def test_v789_missing_local_payload_revokes_cached_freshness_and_repairs(tmp_path):
    with _current_workspace(tmp_path) as (ws,project,_root):
        folder=tmp_path/'usb';folder.mkdir()
        destination=ws.add_backup_destination(str(folder),project['id'])
        assert ws.backup_destinations_catch_up(project['id'],destination_ids=[destination['id']],reason='seed',force=True,quiet_seconds=0)['ok'] is True
        _publication,_row,package=_published_local(folder,project['id'])
        package.unlink()
        stale=next(x for x in ws.list_backup_destinations(project['id']) if x['id']==destination['id'])
        assert stale['up_to_date'] is False and stale['status']=='needs_attention'
        repaired=ws.backup_destinations_catch_up(project['id'],destination_ids=[destination['id']],reason='repair',force=False,quiet_seconds=0)
        assert repaired['ok'] is True
        final=next(x for x in ws.list_backup_destinations(project['id']) if x['id']==destination['id'])
        assert final['up_to_date'] is True and final['status']=='up_to_date'
        _published_local(folder,project['id'])


def test_v789_same_size_local_bitrot_is_detected_and_repaired_on_forced_scrub(tmp_path):
    with _current_workspace(tmp_path) as (ws,project,_root):
        folder=tmp_path/'usb';folder.mkdir()
        destination=ws.add_backup_destination(str(folder),project['id'])
        assert ws.backup_destinations_catch_up(project['id'],destination_ids=[destination['id']],reason='seed',force=True,quiet_seconds=0)['ok'] is True
        publication,row,package=_published_local(folder,project['id'])
        original_size=package.stat().st_size
        data=bytearray(package.read_bytes());assert len(data)>64
        data[len(data)//2]^=0x01;package.write_bytes(data)
        assert package.stat().st_size==original_size
        # Lightweight authority can only prove presence/size. Manual force performs
        # the full hash/read-back scrub and repairs the corrupted immutable point.
        result=ws.backup_destinations_catch_up(project['id'],destination_ids=[destination['id']],reason='manual',force=True,quiet_seconds=0)
        assert result['ok'] is True
        final=next(x for x in ws.list_backup_destinations(project['id']) if x['id']==destination['id'])
        assert final['up_to_date'] is True and not final.get('last_error')
        new_publication,new_row,new_package=_published_local(folder,project['id'])
        assert new_package.is_file()
        assert ws.verify_package(new_package)['project_id']==project['id']
        assert new_row['package_sha256']!=row['package_sha256'] or new_publication!=publication or new_package.read_bytes()!=bytes(data)


def test_v789_same_generation_conflicting_publication_identity_fails_closed(tmp_path):
    with _current_workspace(tmp_path) as (ws,project,_root):
        folder=tmp_path/'usb';folder.mkdir()
        destination=ws.add_backup_destination(str(folder),project['id'])
        assert ws.backup_destinations_catch_up(project['id'],destination_ids=[destination['id']],reason='seed',force=True,quiet_seconds=0)['ok'] is True
        publication,row,_package=_published_local(folder,project['id'])
        conflict=dict(row);conflict['canonical_state_hash']='f'*64 if row.get('canonical_state_hash')!='f'*64 else 'e'*64
        other=folder/f"breadmaker-{project['id']}-conflict.breadmaker{PUBLICATION_SUFFIX}"
        other.write_text(json.dumps(conflict),encoding='utf-8')
        status=next(x for x in ws.list_backup_destinations(project['id']) if x['id']==destination['id'])
        assert status['up_to_date'] is False and status['status']=='needs_attention'
        assert 'same_generation_identity_uncertain' in str(status.get('reason') or '')


def test_v789_removable_location_requires_stable_volume_and_marker(monkeypatch,tmp_path):
    state={'id':'volume-A'}
    monkeypatch.setattr(workspace_fs,'_volume_metadata',lambda path:{'id':state['id'],'root':str(tmp_path),'label':'Test Volume'})
    with _current_workspace(tmp_path) as (ws,project,_root):
        folder=tmp_path/'removable';folder.mkdir()
        destination=ws.add_backup_destination(str(folder),project['id'])
        assert destination.get('volume_id')=='volume-A'
        state['id']='volume-B'
        status=next(x for x in ws.list_backup_destinations(project['id']) if x['id']==destination['id'])
        assert status['available'] is False and status['status']=='needs_attention' and status['reason']=='identity_mismatch'


def test_v789_removed_destination_remains_in_privacy_erasure_ledger(tmp_path):
    with _current_workspace(tmp_path) as (ws,project,_root):
        folder=tmp_path/'usb';folder.mkdir()
        destination=ws.add_backup_destination(str(folder),project['id'])
        fake=folder/f"breadmaker-{project['id']}-orphan.breadmaker";fake.write_bytes(b'managed')
        ws.remove_backup_destination(destination['id'],project['id'])
        assert fake.is_file()
        targets=ws._known_backup_erasure_targets()
        assert any(project['id'] in row['project_ids'] for row in targets)
        erased=ws.erase_known_managed_backup_data()
        assert not erased['failures'] and erased['removed']>=1 and not fake.exists()


def test_v789_recovery_hash_authority_loss_cannot_preserve_old_baseline(tmp_path):
    with _current_workspace(tmp_path) as (_ws,_project,root):
        baseline=storage_mutations.current_project_generation()
        storage_recovery.recovery_scheduler_protected_generation(root,baseline,initialize=True)
        with storage_mutations.tx(operation='v789-advance') as con:
            pid=int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
            con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?',('Protected once',storage_values.now(),pid))
        current=storage_mutations.current_project_generation();assert current>baseline
        package=storage_recovery.backup_project('v789-protection',project_root=root);assert package is not None
        assert storage_recovery.recovery_scheduler_protected_generation(root,current,initialize=False)==current
        state_path=root/'backups'/'recovery_state.json';state=json.loads(state_path.read_text(encoding='utf-8'))
        state.pop('package_sha256',None);state_path.write_text(json.dumps(state),encoding='utf-8')
        assert storage_recovery.recovery_scheduler_protected_generation(root,current,initialize=False)==0


def test_v789_stale_queued_recovery_cannot_recreate_deleted_project_tree(tmp_path):
    with _current_workspace(tmp_path) as (_ws,_project,root):
        # Simulate the state seen by a queued worker after Delete All has already
        # removed its captured old project root.
        import shutil
        shutil.rmtree(root)
        assert storage_recovery.backup_project('stale-after-delete',project_root=root) is None
        assert not root.exists()
