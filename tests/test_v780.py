from __future__ import annotations

from workspace import workspace
from backend import project_activation

from storage import context as storage_context
from storage import history as storage_history
from storage import integrity as storage_integrity
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

import json
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

import app as app_module
from backend import local_history
from backups.frozen_snapshot import capture_frozen_snapshot
from revision_store.local_store import canonical_state_hash
from revision_store.remote_history import RemoteHistoryRepository, RemoteHistoryCorruption, RemoteHistoryConflict
from workspace import Workspace


class MemoryTransport:
    def __init__(self):
        self.data: dict[str, bytes] = {}
    def put_immutable(self, path: str, data: bytes) -> None:
        if path in self.data:
            if self.data[path] != data:
                raise RemoteHistoryConflict('REMOTE_HISTORY_IMMUTABLE_CONFLICT')
            return
        self.data[path] = bytes(data)
    def read(self, path: str) -> bytes | None:
        return self.data.get(path)
    def put_file_immutable(self, path: str, source: Path) -> None:
        self.put_immutable(path, Path(source).read_bytes())
    def get_file(self, path: str, target: Path) -> bool:
        raw = self.data.get(path)
        if raw is None:
            return False
        Path(target).write_bytes(raw)
        return True
    def list_paths(self, prefix: str) -> list[str]:
        return sorted(path for path in self.data if path.startswith(prefix))


def _workspace_id() -> str:
    return storage_mutations.configured_project_identity()[0]


def _repo(transport: MemoryTransport, writer: str | None = '1' * 32, *, allow_initialize: bool = True):
    return RemoteHistoryRepository(
        transport,
        workspace_project_id=_workspace_id(),
        writer_id=writer,
        canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
        allow_initialize=allow_initialize,
    )


def test_v780_recovery_safety_state_loss_never_claims_current_is_protected(fresh_project):
    root = Path(storage_context.PROJECT_ROOT).resolve()
    baseline = storage_mutations.current_project_generation()
    assert storage_recovery.recovery_scheduler_protected_generation(root, baseline, initialize=True) == baseline
    package = storage_recovery.backup_project('v780-baseline', project_root=root)
    assert package is not None
    with storage_mutations.tx(operation='v780-unprotected-current') as con:
        pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
        con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', ('Unprotected Current', storage_values.now(), pid))
    current = storage_mutations.current_project_generation()
    assert current > baseline
    (root / 'backups' / 'recovery_state.json').unlink()
    # Missing safety metadata means unknown protection, never Current == protected.
    assert storage_recovery.recovery_scheduler_protected_generation(root, current, initialize=True) == 0


def test_v780_periodic_recovery_scrub_detects_same_size_payload_corruption(fresh_project):
    root = Path(storage_context.PROJECT_ROOT).resolve()
    baseline = storage_mutations.current_project_generation()
    storage_recovery.recovery_scheduler_protected_generation(root, baseline, initialize=True)
    with storage_mutations.tx(operation='v780-scrub-advance') as con:
        pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
        con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', ('Scrub target', storage_values.now(), pid))
    current = storage_mutations.current_project_generation()
    package = storage_recovery.backup_project('v780-scrub', project_root=root)
    assert package is not None
    state_path = root / 'backups' / 'recovery_state.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['last_scrubbed_at'] = '2000-01-01T00:00:00+00:00'
    state_path.write_text(json.dumps(state), encoding='utf-8')
    data = bytearray(package.read_bytes())
    idx = max(32, len(data) // 2)
    data[idx] ^= 0x01
    package.write_bytes(data)  # identical size; cached stat alone cannot detect it
    protected = storage_recovery.recovery_scheduler_protected_generation(root, current, initialize=True)
    assert protected < current


def test_v780_every_canonical_commit_eventually_enters_v1_history(client, fresh_project, monkeypatch):
    # Production deliberately waits for writer-idle so immutable History never contends
    # with interactive editing. Exercise the same asynchronous worker with a bounded
    # test-only debounce rather than requiring the production 90-second window here.
    monkeypatch.setattr(local_history,'_LOCAL_HISTORY_DEBOUNCE_SECONDS',0.02)
    before = storage_mutations.current_project_generation()
    with storage_mutations.tx(operation='v780-structural-canonical') as con:
        pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
        con.execute('INSERT INTO tasks(project_id,title,description,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                    (pid, 'Structural History boundary', '', 'open', storage_values.now(), storage_values.now()))
    target = storage_mutations.current_project_generation()
    assert target > before
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        rows = storage_history.list_local_revisions()
        if any(int(row['generation_end']) == target and row.get('creation_reason') == 'canonical_commit' for row in rows):
            break
        time.sleep(0.05)
    else:
        pytest.fail('canonical structural commit never reached v1 History')


def test_v780_remote_read_repair_does_not_depend_on_local_writer_identity(fresh_project, monkeypatch):
    store = storage_history.local_revision_store()
    revision = store.capture(label='remote repair', creation_reason='v780', force_checkpoint=True)
    transport = MemoryTransport()
    _repo(transport).publish(store, revision['revision_id'])
    manifest_path = next(store.revisions_dir.glob(f"*-{revision['revision_id']}.revision.json"))
    manifest_path.write_text('{broken', encoding='utf-8')
    ws = workspace
    original_writer = ws.remote_history_writer_path.read_bytes() if ws.remote_history_writer_path.exists() else None
    ws.remote_history_writer_path.write_text('{broken', encoding='utf-8')
    destination = {'id': 'v780-read-only', 'provider': 'webdav'}
    monkeypatch.setattr(ws, '_backup_destinations', lambda *a, **k: [dict(destination)])
    monkeypatch.setattr(ws, '_remote_history_transport', lambda *a, **k: transport)
    try:
        result = ws.repair_local_history_from_destination(destination['id'], project_id=fresh_project['project']['id'], revision_id=revision['revision_id'])
        assert result['repaired'] is True
        assert store.verify_revision(revision['revision_id'])['revision']['revision_id'] == revision['revision_id']
    finally:
        if original_writer is None:ws.remote_history_writer_path.unlink(missing_ok=True)
        else:ws.remote_history_writer_path.write_bytes(original_writer)


def test_v780_initialized_remote_repository_never_reclaims_missing_authority_from_listing(fresh_project):
    store = storage_history.local_revision_store()
    revision = store.capture(label='authority', creation_reason='v780', force_checkpoint=True)
    transport = MemoryTransport()
    _repo(transport).publish(store, revision['revision_id'])
    base = f'history/{_workspace_id()}'
    del transport.data[f'{base}/repository.json']
    del transport.data[f'{base}/writer-authority.json']
    # Even if provider discovery were empty, an already-initialized caller is not
    # permitted to turn absence into a new writer claim.
    with pytest.raises(RemoteHistoryCorruption, match='REMOTE_HISTORY_REPOSITORY_AUTHORITY_MISSING'):
        _repo(transport, writer='2' * 32, allow_initialize=False).publish(store, revision['revision_id'])


def test_v780_recovery_identity_is_logical_not_legacy_history_bytes(fresh_project, tmp_path):
    root = Path(storage_context.PROJECT_ROOT).resolve()
    first_package = storage_recovery.backup_project('v780-logical-a', project_root=root)
    assert first_package is not None
    with tempfile.TemporaryDirectory(prefix='v780-freeze-a-') as td:
        a = capture_frozen_snapshot(storage_context.DB_PATH, storage_context.ASSET_DIR, Path(td))
        hash_a = canonical_state_hash(a.database, storage_schema_contracts._GENERATION_CANONICAL_TABLES, a.file_hashes)
        fp_a = a.project_fingerprint
    generation = storage_mutations.current_project_generation()
    with storage_mutations.tx(operation='v780-derived-meta', track_generation=False) as con:
        con.execute("INSERT INTO storage_meta(key,value) VALUES('diagnostic_probe','1') ON CONFLICT(key) DO UPDATE SET value='1'")
    assert storage_mutations.current_project_generation() == generation
    with tempfile.TemporaryDirectory(prefix='v780-freeze-b-') as td:
        b = capture_frozen_snapshot(storage_context.DB_PATH, storage_context.ASSET_DIR, Path(td))
        hash_b = canonical_state_hash(b.database, storage_schema_contracts._GENERATION_CANONICAL_TABLES, b.file_hashes)
        fp_b = b.project_fingerprint
    assert hash_a == hash_b
    assert fp_a != fp_b
    second_package = storage_recovery.backup_project('v780-logical-b', project_root=root)
    assert second_package == first_package


def test_v780_deep_integrity_reports_v1_history_corruption(fresh_project):
    store = storage_history.local_revision_store()
    revision = store.capture(label='health', creation_reason='v780', force_checkpoint=True)
    path = next(store.revisions_dir.glob(f"*-{revision['revision_id']}.revision.json"))
    path.write_text('{broken', encoding='utf-8')
    report = storage_integrity.deep_integrity_check()
    assert report['ok'] is False
    assert report['history_v1']['ok'] is False
    assert any('History v1' in error for error in report['errors'])


def test_v780_old_schema_is_rejected_without_migration(fresh_project):
    ws=workspace;previous=ws.active_id();target=ws.create_project('Old schema rejected',activate=False);root=Path(target['path'])
    con=sqlite3.connect(root/'project.sqlite3')
    try:
        con.executescript("CREATE TABLE projects(id INTEGER PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL); INSERT INTO projects VALUES(1,'Old','2026-01-01','2026-01-01');")
        con.commit()
    finally:con.close()
    manifest=ws.read_manifest(root);manifest['schema_version']=19;ws._write_manifest(root,manifest)
    before=(root/'project.sqlite3').read_bytes()
    try:
        with pytest.raises(RuntimeError,match='PREALPHA_PROJECT_INCOMPATIBLE'):
            project_activation.activate_project(target['id'])
        assert (root/'project.sqlite3').read_bytes()==before
    finally:
        if previous:project_activation.activate_project(previous)


def test_v780_remote_destination_work_begins_in_parallel(fresh_project, monkeypatch):
    # A deterministic barrier proves destination fan-out isn't serial: destination A
    # cannot finish resolve until B has entered resolve, and vice versa.
    store = storage_history.local_revision_store()
    store.capture(label='parallel', creation_reason='v780', force_checkpoint=True)
    ws = workspace
    destinations = [
        {'id': 'parallel-a', 'provider': 'webdav', 'remote_history_repository_initialized': False},
        {'id': 'parallel-b', 'provider': 'webdav', 'remote_history_repository_initialized': False},
    ]
    transports = {row['id']: MemoryTransport() for row in destinations}
    entered = {row['id']: threading.Event() for row in destinations}

    class Provider:
        def resolve(self, _workspace, destination):
            mine = str(destination['id'])
            other = 'parallel-b' if mine == 'parallel-a' else 'parallel-a'
            entered[mine].set()
            if not entered[other].wait(1.0):
                raise RuntimeError('destination fan-out was serialized')
            return {'available': True}

    monkeypatch.setattr(ws, '_backup_destinations', lambda *a, **k: [dict(row) for row in destinations])
    monkeypatch.setattr(ws, '_backup_provider', lambda *a, **k: Provider())
    monkeypatch.setattr(ws, '_remote_history_transport', lambda destination, workspace_id: transports[str(destination['id'])])
    monkeypatch.setattr(ws, '_mark_remote_history_initialized', lambda *a, **k: None)
    result = ws.sync_remote_history(fresh_project['project']['id'])
    assert result['ok'] is True
    assert {row['destination_id'] for row in result['destinations']} == {'parallel-a', 'parallel-b'}


def test_v780_corrupt_project_manifest_is_not_silently_reconstructed(tmp_path):
    home = tmp_path / 'home'
    ws = Workspace(home)
    ws.init()
    project = ws.create_project('Manifest authority', activate=True)
    root = Path(project['path'])
    con = sqlite3.connect(root / 'project.sqlite3')
    try:
        con.execute('CREATE TABLE projects(id INTEGER PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,project_generation INTEGER NOT NULL)')
        con.execute("INSERT INTO projects VALUES(1,'Manifest authority','2026-01-01','2026-01-01',1)")
        con.commit()
    finally:
        con.close()
    broken = b'{this is not recoverable configuration'
    (root / 'manifest.json').write_bytes(broken)
    replacement = Workspace(home)
    replacement.init()
    assert replacement.list_projects() == []
    assert (root / 'manifest.json').read_bytes() == broken
    candidates = replacement.recovery_candidates()
    assert any(row['id'] == project['id'] and row['readable'] is True for row in candidates)


def test_v780_deep_integrity_reports_hidden_older_history_corruption(fresh_project):
    store = storage_history.local_revision_store()
    old = store.capture(label='old health', creation_reason='v780', force_checkpoint=True)
    with storage_mutations.tx(operation='v780-health-newer') as con:
        pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
        con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', ('Newer valid checkpoint', storage_values.now(), pid))
    newer = store.capture(label='new health', creation_reason='v780', force_checkpoint=True)
    assert store.verify_revision(newer['revision_id'])['revision']['revision_id'] == newer['revision_id']
    old_path = next(store.revisions_dir.glob(f"*-{old['revision_id']}.revision.json"))
    old_path.write_text('{broken', encoding='utf-8')
    report = storage_integrity.deep_integrity_check()
    assert report['ok'] is False
    assert report['history_v1']['ok'] is False


def test_v780_shutdown_flushes_pending_structural_history_before_debounce(fresh_project):
    target = storage_mutations.current_project_generation()
    event = {
        'workspace_project_id': fresh_project['project']['id'],
        'project_root': str(Path(storage_context.PROJECT_ROOT).resolve()),
        'project_id': 1,
        'generation': target,
        'operation': 'v780-shutdown-structural',
    }
    work_queue = __import__('queue').Queue()
    stop = threading.Event()
    worker = threading.Thread(target=local_history._local_history_worker_loop, args=(stop, work_queue), daemon=True)
    worker.start()
    work_queue.put(event)
    # Stop immediately, well before the normal 750ms debounce expires.
    stop.set()
    worker.join(timeout=4.0)
    assert not worker.is_alive()
    rows = storage_history.list_local_revisions()
    assert any(int(row['generation_end']) == target and row.get('creation_reason') == 'canonical_commit' for row in rows)


def test_v780_remote_history_privacy_barrier_is_exclusive_without_serializing_normal_activity(tmp_path):
    ws = Workspace(tmp_path / 'privacy-gate')
    first_inside = threading.Event()
    release_first = threading.Event()
    barrier_inside = threading.Event()
    release_barrier = threading.Event()
    second_inside = threading.Event()

    def first_activity():
        with ws.remote_history_activity():
            first_inside.set()
            release_first.wait(2.0)

    def privacy():
        with ws.remote_history_privacy_barrier():
            barrier_inside.set()
            release_barrier.wait(2.0)

    def second_activity():
        with ws.remote_history_activity():
            second_inside.set()

    first = threading.Thread(target=first_activity, daemon=True)
    first.start(); assert first_inside.wait(1.0)
    barrier = threading.Thread(target=privacy, daemon=True)
    barrier.start(); time.sleep(0.05)
    assert not barrier_inside.is_set()
    release_first.set(); assert barrier_inside.wait(1.0)
    second = threading.Thread(target=second_activity, daemon=True)
    second.start(); time.sleep(0.05)
    assert not second_inside.is_set()
    release_barrier.set(); assert second_inside.wait(1.0)
    first.join(1.0); barrier.join(1.0); second.join(1.0)


def test_v780_independent_remote_history_activity_calls_can_overlap(tmp_path):
    ws = Workspace(tmp_path / 'activity-gate')
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()

    def worker(index):
        with ws.remote_history_activity():
            entered[index].set()
            assert entered[1-index].wait(1.0)
            release.wait(1.0)

    a = threading.Thread(target=worker, args=(0,), daemon=True)
    b = threading.Thread(target=worker, args=(1,), daemon=True)
    a.start(); b.start()
    assert entered[0].wait(1.0) and entered[1].wait(1.0)
    release.set(); a.join(1.0); b.join(1.0)
    assert not a.is_alive() and not b.is_alive()


def test_v780_imported_project_starts_recovery_unprotected(fresh_project):
    source_root = Path(storage_context.PROJECT_ROOT).resolve()
    package = storage_recovery.backup_project('v780-import-source', project_root=source_root)
    assert package is not None
    imported = workspace.import_project(package, name_override='Imported safety state')
    imported_root = Path(imported['path']).resolve()
    info = workspace.inspect_package_metadata(package)
    generation = int(info['project_generation'])
    try:
        # Importing through a pristine shell must never turn shell initialization into
        # evidence that the imported canonical generation already has local Recovery.
        assert storage_recovery.recovery_scheduler_protected_generation(imported_root, generation, initialize=True) == 0
    finally:
        workspace.delete_project(imported['id'], permanent=True)


def test_v780_trash_restore_refuses_corrupt_nonreconstructible_manifest(tmp_path):
    ws = Workspace(tmp_path / 'trash-manifest')
    ws.init()
    project = ws.create_project('Trash manifest', activate=True)
    root = Path(project['path'])
    con = sqlite3.connect(root / 'project.sqlite3')
    try:
        con.execute('CREATE TABLE projects(id INTEGER PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,project_generation INTEGER NOT NULL)')
        con.execute("INSERT INTO projects VALUES(1,'Trash manifest','2026-01-01','2026-01-01',1)")
        con.commit()
    finally:
        con.close()
    ws.delete_project(project['id'], permanent=False)
    trash = ws.list_trash()
    assert len(trash) == 1
    trashed_root = Path(trash[0]['path'])
    trashed_root.joinpath('manifest.json').write_text('{broken', encoding='utf-8')
    with pytest.raises(Exception, match='PROJECT_MANIFEST_UNREADABLE'):
        ws.restore_from_trash(trash[0]['trash_id'])
    assert trashed_root.exists()
