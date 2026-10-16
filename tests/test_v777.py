from __future__ import annotations

from workspace import workspace

from storage import context as storage_context
from storage import history as storage_history
from storage import mutations as storage_mutations
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app as app_module
from revision_store.history_gc import (
    HistoryGCCorruption,
    plan_local_history_gc,
    run_local_history_gc,
    run_remote_history_gc,
)
from revision_store.local_store import LocalRevisionStore
from revision_store.remote_history import RemoteHistoryConflict, RemoteHistoryCorruption, RemoteHistoryRepository
from revision_store.history_gc_transports import WebDAVHistoryGCTransport, GoogleDriveHistoryGCTransport


class MemoryTransport:
    def __init__(self):
        self.data: dict[str, bytes] = {}
        self.hidden_prefixes: set[str] = set()

    def put_immutable(self, path: str, data: bytes) -> None:
        if path in self.data and self.data[path] != data:
            raise RemoteHistoryConflict('REMOTE_HISTORY_IMMUTABLE_CONFLICT')
        self.data.setdefault(path, bytes(data))

    def read(self, path: str) -> bytes | None:
        return self.data.get(path)

    def put_file_immutable(self, path: str, source: Path) -> None:
        self.put_immutable(path, Path(source).read_bytes())

    def get_file(self, path: str, target: Path) -> bool:
        data = self.data.get(path)
        if data is None:
            return False
        Path(target).write_bytes(data)
        return True

    def list_paths(self, prefix: str) -> list[str]:
        if prefix in self.hidden_prefixes:
            return []
        return sorted(key for key in self.data if key.startswith(prefix))


class MemoryDeleteCapability:
    def __init__(self, transport: MemoryTransport, workspace_id: str):
        self.transport = transport
        self.base = f'history/{workspace_id}'
        self.deleted: list[str] = []

    def _delete(self, path: str) -> int:
        assert path.startswith(self.base + '/')
        self.transport.data.pop(path, None)
        self.deleted.append(path)
        return 1

    def delete_revision_publication(self, generation: int) -> int:
        return self._delete(f'{self.base}/revisions/g{int(generation):020d}.remote.json')

    def delete_checkpoint(self, checkpoint_id: str) -> int:
        return self._delete(f'{self.base}/checkpoints/{checkpoint_id}.checkpoint.json')

    def delete_snapshot(self, snapshot_object_id: str) -> int:
        return self._delete(f'{self.base}/objects/{snapshot_object_id}.snapshot.zip')


def _store():
    return storage_history.local_revision_store()


def _workspace_id() -> str:
    return storage_mutations.configured_project_identity()[0]


def _repo(transport: MemoryTransport, writer: str = '1' * 32) -> RemoteHistoryRepository:
    return RemoteHistoryRepository(
        transport,
        workspace_project_id=_workspace_id(),
        writer_id=writer,
        canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
    )


def _advance(label: str) -> int:
    with storage_mutations.tx(operation='v777-advance') as con:
        pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
        con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', (label, storage_values.now(), pid))
    return storage_mutations.current_project_generation()


def _capture(label: str, *, checkpoint: bool = False):
    return storage_history.capture_local_revision(
        label=label,
        creation_reason='test',
        summary=storage_history.history_current_summary(),
        force_checkpoint=checkpoint,
    )


def _set_capture_time(monkeypatch, when: datetime) -> None:
    import revision_store.local_store as local_store_module
    monkeypatch.setattr(local_store_module, '_utc_now', lambda: when.astimezone(timezone.utc).isoformat(timespec='microseconds'))


def _two_segments(monkeypatch):
    now = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    _set_capture_time(monkeypatch, now - timedelta(days=120))
    old0 = _capture('old anchor', checkpoint=True)
    _advance('old child state')
    _set_capture_time(monkeypatch, now - timedelta(days=119))
    old1 = _capture('old child')
    _advance('recent anchor state')
    _set_capture_time(monkeypatch, now - timedelta(days=1))
    recent = _capture('recent anchor', checkpoint=True)
    return now, old0, old1, recent


def test_v777_release_contract_is_conservative_and_separate_from_recovery():
    root = Path(__file__).resolve().parents[1]
    assert tuple(int(x) for x in root.joinpath('VERSION').read_text().strip().split('.')) >= (7,7,7)
    text = root.joinpath('ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'Conservative History GC' in text
    from revision_store.history_gc import HistoryGCRemoteDelete
    assert 'delete_revision_publication' in HistoryGCRemoteDelete.__dict__
    assert 'delete_checkpoint' in HistoryGCRemoteDelete.__dict__
    assert 'delete_snapshot' in HistoryGCRemoteDelete.__dict__
    assert 'delete' not in HistoryGCRemoteDelete.__dict__


def test_v777_read_only_planner_promotes_retained_revision_to_whole_checkpoint_segment(fresh_project, monkeypatch):
    now = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    _set_capture_time(monkeypatch, now - timedelta(days=120))
    anchor = _capture('anchor', checkpoint=True)
    _advance('recent child state')
    _set_capture_time(monkeypatch, now - timedelta(days=1))
    child = _capture('recent child')
    plan = plan_local_history_gc(_store(), '30_days', now_utc=now)
    assert anchor['checkpoint_id'] == child['checkpoint_id']
    assert plan.candidate_segments == ()
    assert anchor['checkpoint_id'] in plan.retained_segment_ids


def test_v777_old_checkpoint_segment_becomes_candidate_but_recent_segment_is_retained(fresh_project, monkeypatch):
    now, old0, old1, recent = _two_segments(monkeypatch)
    plan = plan_local_history_gc(_store(), '30_days', now_utc=now)
    assert [s.checkpoint_id for s in plan.candidate_segments] == [old0['checkpoint_id']]
    assert tuple(plan.candidate_segments[0].revision_ids) == (old0['revision_id'], old1['revision_id'])
    assert recent['checkpoint_id'] in plan.retained_segment_ids


def test_v777_invalid_timestamp_fails_toward_retention(fresh_project, monkeypatch):
    now, old0, old1, recent = _two_segments(monkeypatch)
    path = next(_store().revisions_dir.glob(f"*-{old1['revision_id']}.revision.json"))
    row = json.loads(path.read_text(encoding='utf-8')); row['created_at'] = 'not-a-clock'; path.write_text(json.dumps(row, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    plan = plan_local_history_gc(_store(), '30_days', now_utc=now)
    assert old0['checkpoint_id'] in plan.retained_segment_ids
    assert all(s.checkpoint_id != old0['checkpoint_id'] for s in plan.candidate_segments)


def test_v777_local_gc_requires_two_independent_scans_then_quarantines_authority_first(fresh_project, monkeypatch):
    now, old0, old1, recent = _two_segments(monkeypatch)
    first = run_local_history_gc(_store(), '30_days', now_utc=now, grace_seconds=0)
    assert first.tombstoned and not first.quarantined
    assert _store().verify_revision(old1['revision_id'])['revision']['revision_id'] == old1['revision_id']
    second = run_local_history_gc(_store(), '30_days', now_utc=now + timedelta(seconds=1), grace_seconds=0)
    assert second.quarantined == [f"segment:{old0['checkpoint_id']}"]
    listed = {r['revision_id'] for r in _store().list_revisions()}
    assert old0['revision_id'] not in listed and old1['revision_id'] not in listed
    assert _store().verify_revision(recent['revision_id'])['revision']['revision_id'] == recent['revision_id']
    quarantine = _store().root / 'gc' / 'quarantine'
    assert any(quarantine.iterdir())
    third = run_local_history_gc(_store(), '30_days', now_utc=now + timedelta(seconds=2), grace_seconds=0)
    assert third.deleted_quarantine_entries >= 1


def test_v777_crash_during_authority_removal_leaves_valid_older_prefix(fresh_project, monkeypatch):
    now = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    _set_capture_time(monkeypatch, now - timedelta(days=120))
    anchor = _capture('old anchor', checkpoint=True)
    _advance('old middle state');middle = _capture('old middle')
    _advance('old newest state');newest = _capture('old newest')
    _advance('recent state');_set_capture_time(monkeypatch, now - timedelta(days=1));recent = _capture('recent', checkpoint=True)
    run_local_history_gc(_store(), '30_days', now_utc=now, grace_seconds=0)
    import revision_store.history_gc as gc_module
    original = gc_module._move_file_to_quarantine
    calls = {'n': 0}
    def crash_after_first(source, destination):
        original(source, destination);calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('simulated process loss')
    monkeypatch.setattr(gc_module, '_move_file_to_quarantine', crash_after_first)
    with pytest.raises(RuntimeError, match='simulated process loss'):
        run_local_history_gc(_store(), '30_days', now_utc=now + timedelta(seconds=1), grace_seconds=0)
    visible = _store().list_revisions()
    visible_ids = {r['revision_id'] for r in visible}
    assert newest['revision_id'] not in visible_ids
    assert middle['revision_id'] in visible_ids and anchor['revision_id'] in visible_ids and recent['revision_id'] in visible_ids
    for row in visible:
        _store().verify_revision(row['revision_id'])


def test_v777_corrupt_repository_fails_closed_without_deleting_anything(fresh_project, monkeypatch):
    now, old0, old1, recent = _two_segments(monkeypatch)
    before = sorted(p.relative_to(_store().root).as_posix() for p in _store().root.rglob('*') if p.is_file())
    path = next(_store().revisions_dir.glob(f"*-{recent['revision_id']}.revision.json"));path.write_text('{broken', encoding='utf-8')
    with pytest.raises(HistoryGCCorruption, match='REPOSITORY_UNCERTAIN'):
        run_local_history_gc(_store(), '30_days', now_utc=now, grace_seconds=0)
    after = sorted(p.relative_to(_store().root).as_posix() for p in _store().root.rglob('*') if p.is_file())
    assert after == before


def test_v777_recovery_packages_are_outside_gc_capability_and_unchanged(fresh_project, monkeypatch, tmp_path):
    now, old0, old1, recent = _two_segments(monkeypatch)
    fake_recovery = tmp_path / 'recovery' / 'g0001.breadmaker';fake_recovery.parent.mkdir();fake_recovery.write_bytes(b'known-good-recovery')
    before = hashlib.sha256(fake_recovery.read_bytes()).hexdigest()
    run_local_history_gc(_store(), '30_days', now_utc=now, grace_seconds=0)
    run_local_history_gc(_store(), '30_days', now_utc=now + timedelta(seconds=1), grace_seconds=0)
    assert fake_recovery.exists()
    assert hashlib.sha256(fake_recovery.read_bytes()).hexdigest() == before


def test_v777_remote_publish_of_non_checkpoint_revision_includes_complete_reconstruction_closure(fresh_project, monkeypatch, tmp_path):
    now = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    _set_capture_time(monkeypatch, now - timedelta(days=2));anchor = _capture('anchor', checkpoint=True)
    _advance('child state');_set_capture_time(monkeypatch, now - timedelta(days=1));child = _capture('child')
    transport = MemoryTransport();repo = _repo(transport)
    repo.publish(_store(), child['revision_id'])
    publications = sorted(k for k in transport.data if k.endswith('.remote.json'))
    assert len(publications) == 2
    alternate = LocalRevisionStore(
        tmp_path / 'empty-history', storage_context.DB_PATH, storage_context.ASSET_DIR,
        canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
        workspace_project_id=_workspace_id(),
    )
    repaired = repo.repair_local(alternate, child['revision_id'])
    assert repaired.repaired is True
    assert alternate.verify_revision(child['revision_id'])['revision']['parent_revision_id'] == anchor['revision_id']


def test_v777_missing_remote_ancestor_invalidates_target_until_exact_local_repair(fresh_project, monkeypatch):
    now = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    _set_capture_time(monkeypatch, now - timedelta(days=2));anchor = _capture('anchor', checkpoint=True)
    _advance('child state');child = _capture('child')
    transport = MemoryTransport();repo = _repo(transport);repo.publish(_store(), child['revision_id'])
    parent_path = f"history/{_workspace_id()}/revisions/g{int(anchor['generation_end']):020d}.remote.json"
    transport.data.pop(parent_path)
    target_path = f"history/{_workspace_id()}/revisions/g{int(child['generation_end']):020d}.remote.json"
    with pytest.raises(Exception):
        repo.verify_publication(target_path)
    repaired = repo.publish(_store(), child['revision_id'])
    assert parent_path in transport.data
    assert repo.verify_publication(target_path)['revision_id'] == child['revision_id']
    assert repaired.idempotent is True


def test_v777_writer_authority_mirror_blocks_second_writer_even_when_listing_is_delayed(fresh_project):
    rev = _capture('authority', checkpoint=True);transport = MemoryTransport();repo = _repo(transport);repo.publish(_store(), rev['revision_id'])
    transport.data.pop(repo._repo_path())
    transport.hidden_prefixes.add(f'history/{_workspace_id()}/revisions/')
    before=dict(transport.data)
    with pytest.raises(RemoteHistoryCorruption, match='REPOSITORY_AUTHORITY_INCOMPLETE'):
        _repo(transport, '2' * 32).claim_writer()
    assert transport.data==before


def test_v777_remote_gc_two_scans_deletes_only_old_history_namespace(fresh_project, monkeypatch, tmp_path):
    now, old0, old1, recent = _two_segments(monkeypatch)
    transport = MemoryTransport();repo = _repo(transport)
    for revision in (old0, old1, recent):
        repo.publish(_store(), revision['revision_id'])
    recovery_key = 'recovery/do-not-touch.breadmaker';transport.data[recovery_key] = b'recovery'
    delete_cap = MemoryDeleteCapability(transport, _workspace_id());state_path = tmp_path / 'remote-gc.json'
    first = run_remote_history_gc(repository=repo, delete_capability=delete_cap, state_path=state_path, policy='30_days', now_utc=now, grace_seconds=0)
    assert first.tombstoned and not delete_cap.deleted
    second = run_remote_history_gc(repository=repo, delete_capability=delete_cap, state_path=state_path, policy='30_days', now_utc=now + timedelta(seconds=1), grace_seconds=0)
    assert second.quarantined == [f"segment:{old0['checkpoint_id']}"]
    assert transport.data[recovery_key] == b'recovery'
    assert repo.newest_valid()['revision_id'] == recent['revision_id']
    assert all(path.startswith(f'history/{_workspace_id()}/') for path in delete_cap.deleted)


def test_v777_remote_gc_requires_authoritative_writer(fresh_project, monkeypatch, tmp_path):
    now, old0, old1, recent = _two_segments(monkeypatch)
    transport = MemoryTransport();writer = _repo(transport);writer.publish_all(_store())
    reader = _repo(transport, '2' * 32)
    cap = MemoryDeleteCapability(transport, _workspace_id())
    with pytest.raises(RemoteHistoryConflict, match='WRITER_CONFLICT'):
        run_remote_history_gc(repository=reader, delete_capability=cap, state_path=tmp_path/'reader.json', policy='30_days', now_utc=now, grace_seconds=0)
    assert cap.deleted == []


def test_v777_project_local_retention_is_not_inferred_from_destination_metadata(client, fresh_project, tmp_path):
    project_id = fresh_project['project']['id']
    root = Path(fresh_project['project']['path'])
    manifest = workspace.read_manifest(root)
    manifest.pop('revision_history_retention', None)
    # Old per-destination revision_history_retention metadata is deliberately not
    # migrated in 7.8. Local History owns one explicit project-level policy.
    manifest['backup_destinations'] = [
        {'id':'a'*32,'provider':'local_folder','path':str(tmp_path/'a'),'destination_kind':'local','revision_history_retention':'1_year'},
    ]
    manifest['backup_destinations_version']=3
    workspace._write_manifest(root, manifest)
    settings = workspace.history_retention_settings(project_id)
    assert settings['local_revision_history_retention'] == '90_days'
    updated = client.put('/api/history/retention', json={'value':'1_year'})
    assert updated.status_code == 200
    assert updated.json()['local_revision_history_retention'] == '1_year'
    reread = workspace.read_manifest(root)
    assert reread['revision_history_retention'] == '1_year'
    assert reread['backup_destinations'][0]['revision_history_retention'] == '1_year'


def test_v777_typed_provider_gc_adapters_cannot_accept_recovery_paths():
    class FakeWebDAV:
        def __init__(self):self.deleted=[]
        def delete(self,path,missing_ok=False):self.deleted.append(path)
    web = FakeWebDAV();cap = WebDAVHistoryGCTransport(web, 'a'*32)
    cap.delete_revision_publication(7);cap.delete_checkpoint('b'*32);cap.delete_snapshot('g00000000000000000007-'+'c'*32)
    assert all(path.startswith('history/'+'a'*32+'/') for path in web.deleted)
    assert not hasattr(cap, 'delete')

    class FakeDrive:
        def __init__(self):self.rows={};self.deleted=[]
        def list_history_files(self,root,workspace,kind=None,key=None):
            return [row for row in self.rows.values() if row['appProperties']['breadmaker_history_kind']==kind and row['appProperties']['breadmaker_history_key']==key]
        def delete_file(self,file_id,missing_ok=False):self.deleted.append(file_id)
    drive=FakeDrive();workspace='d'*32;generation=8;key=f'g{generation:020d}'
    drive.rows['history-row']={'id':'history-row','appProperties':{'breadmaker_history_workspace_id':workspace,'breadmaker_history_kind':'revision_publication','breadmaker_history_key':key}}
    gcap=GoogleDriveHistoryGCTransport(drive,'root',workspace);gcap.delete_revision_publication(generation)
    assert drive.deleted==['history-row'] and not hasattr(gcap,'delete')


def test_v777_remote_gc_direct_chain_prevents_hidden_segment_member_from_dangling(fresh_project, monkeypatch, tmp_path):
    """A stale provider listing may omit a live publication, but GC membership must not.

    The next checkpoint anchor points directly to the true previous revision, so the
    GC-safe chain must recover that hidden member before deleting the old segment.
    """
    now, old0, old1, recent = _two_segments(monkeypatch)

    class SelectiveListingTransport(MemoryTransport):
        def __init__(self):
            super().__init__(); self.omitted: set[str] = set()
        def list_paths(self, prefix: str) -> list[str]:
            return sorted(key for key in self.data if key.startswith(prefix) and key not in self.omitted)

    transport = SelectiveListingTransport(); repo = _repo(transport)
    for revision in (old0, old1, recent):
        repo.publish(_store(), revision['revision_id'])
    hidden_path = f"history/{_workspace_id()}/revisions/g{int(old1['generation_end']):020d}.remote.json"
    transport.omitted.add(hidden_path)

    chain = repo.gc_verified_chain()
    assert [row['revision_id'] for row in chain] == [old0['revision_id'], old1['revision_id'], recent['revision_id']]

    cap = MemoryDeleteCapability(transport, _workspace_id()); state_path = tmp_path / 'remote-gc-hidden.json'
    run_remote_history_gc(repository=repo, delete_capability=cap, state_path=state_path, policy='30_days', now_utc=now, grace_seconds=0)
    run_remote_history_gc(repository=repo, delete_capability=cap, state_path=state_path, policy='30_days', now_utc=now + timedelta(seconds=1), grace_seconds=0)
    assert hidden_path not in transport.data
    assert repo.newest_valid()['revision_id'] == recent['revision_id']
