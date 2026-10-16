from __future__ import annotations

from storage import assets as storage_assets
from storage import context as storage_context
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

import json
import random
import shutil
import sqlite3
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import diagnostics
import app as app_module
from api.durability import backups as backup_api
from backups.frozen_snapshot import capture_frozen_snapshot
from reliability.deterministic import DeterministicStageController
from revision_store.history_gc import run_local_history_gc, run_remote_history_gc
from revision_store.local_store import LocalRevisionStore, StaleRevisionPublication, canonical_state_hash
from revision_store.remote_history import RemoteHistoryRepository
from revision_store.restore import HistoryRestoreStale, restore_local_revision
from workspace import Workspace, WorkspaceError

from test_v777 import MemoryDeleteCapability, MemoryTransport


def _workspace_id() -> str:
    return storage_mutations.configured_project_identity()[0]


def _store(interval: int = 4) -> LocalRevisionStore:
    return LocalRevisionStore(
        Path(storage_context.PROJECT_ROOT) / 'revision_history' / 'v1',
        storage_context.DB_PATH,
        storage_context.ASSET_DIR,
        canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
        checkpoint_interval=interval,
        workspace_project_id=_workspace_id(),
    )


def _repo(transport: MemoryTransport, writer: str = '1' * 32) -> RemoteHistoryRepository:
    return RemoteHistoryRepository(
        transport,
        workspace_project_id=_workspace_id(),
        writer_id=writer,
        canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
    )


def _advance(label: str) -> int:
    with storage_mutations.tx(operation='v778-advance') as con:
        row = con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()
        con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', (label, storage_values.now(), int(row[0])))
    return storage_mutations.current_project_generation()


def _logical_hash(db: Path, assets: Path, tmp_path: Path) -> str:
    frozen = capture_frozen_snapshot(db, assets, tmp_path)
    return canonical_state_hash(frozen.database, storage_schema_contracts._GENERATION_CANONICAL_TABLES, frozen.file_hashes)


def _content_hash_without_project_uid(database: Path, assets: Path, snapshot_root: Path) -> str:
    """Compare canonical project content across an intentional Schema-42 copy.

    ``Workspace.import_project`` creates a new project, so its immutable ``project_uid``
    must differ from the source. Normalize only that identity column before hashing; all
    other canonical rows, schema, and finalized assets remain part of the comparison.
    """
    frozen = capture_frozen_snapshot(database, assets, snapshot_root)
    con = sqlite3.connect(frozen.database)
    try:
        con.execute("UPDATE projects SET project_uid=?", ('0'*32,))
        con.commit()
    finally:
        con.close()
    return canonical_state_hash(frozen.database, storage_schema_contracts._GENERATION_CANONICAL_TABLES, frozen.file_hashes)


def test_v778_release_contract_and_no_format_rewrite():
    root = Path(__file__).resolve().parents[1]
    assert tuple(map(int, root.joinpath('VERSION').read_text().strip().split('.'))) >= (7, 7, 8, 1)
    architecture = root.joinpath('ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '## 7.7.8 Soak, Disaster Recovery & Release Hardening' in architecture
    assert 'does not change Revision Store format' in architecture
    assert '10-second core/API' in architecture
    assert '15-second browser/E2E' in architecture
    assert 'Deterministic' in architecture


def test_v778_deterministic_controller_replaces_timing_sleeps_for_reverse_history_completion(fresh_project):
    store = _store()
    store.capture(label='base', creation_reason='v778', force_checkpoint=True)
    older_generation = _advance('older deterministic generation')
    controller = DeterministicStageController()
    controller.pause('older', 'SNAPSHOT_OBJECT_FINALIZED')
    outcome: dict[str, object] = {}

    def worker() -> None:
        try:
            outcome['row'] = store.capture(label='older', creation_reason='v778-race', stage_hook=controller.hook('older'))
        except Exception as exc:  # expected stale publication
            outcome['error'] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    reached = controller.wait_until('older', 'SNAPSHOT_OBJECT_FINALIZED')
    assert int(reached.context['generation']) == older_generation
    newer_generation = _advance('newer deterministic generation')
    newer = store.capture(label='newer', creation_reason='v778-race')
    controller.release('older', 'SNAPSHOT_OBJECT_FINALIZED')
    thread.join(2)
    assert not thread.is_alive()
    assert isinstance(outcome.get('error'), StaleRevisionPublication)
    assert newer['generation_end'] == newer_generation > older_generation
    assert store.list_revisions()[0]['generation_end'] == newer_generation


def test_v778_deterministic_remote_reverse_completion_cannot_move_remote_head_backwards(fresh_project):
    store = _store()
    older = store.capture(label='remote older', creation_reason='v778', force_checkpoint=True)
    transport = MemoryTransport()
    repo = _repo(transport)
    controller = DeterministicStageController()
    controller.pause('older-remote', 'BEFORE_PUBLICATION')
    errors: list[Exception] = []

    def publish_older() -> None:
        try:
            repo.publish(store, older['revision_id'], stage_hook=controller.hook('older-remote'))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=publish_older, daemon=True)
    thread.start()
    controller.wait_until('older-remote', 'BEFORE_PUBLICATION')
    _advance('remote newer')
    newer = store.capture(label='remote newer', creation_reason='v778')
    repo.publish(store, newer['revision_id'])
    controller.release('older-remote', 'BEFORE_PUBLICATION')
    thread.join(2)
    assert not thread.is_alive() and not errors
    assert repo.newest_valid()['revision_id'] == newer['revision_id']
    assert int(repo.newest_valid()['generation']) > int(older['generation_end'])



def test_v778_deterministic_restore_cannot_overwrite_edit_arriving_mid_restore(fresh_project):
    store = _store(interval=10)
    selected = store.capture(label='restore selected', creation_reason='v778', force_checkpoint=True)
    expected_generation = _advance('restore dialog current')
    controller = DeterministicStageController()
    controller.pause('restore', 'MATERIALIZED_VERIFIED')
    outcome: dict[str, object] = {}

    def restore_hook(stage: str) -> None:
        controller.hook('restore')(stage, {})

    def worker() -> None:
        try:
            outcome['result'] = restore_local_revision(
                selected['revision_id'],
                expected_generation=expected_generation,
                verified_backup=lambda: backup_api.verified_full_backup('v778_deterministic_restore'),
                label='Deterministic restore',
                stage_hook=restore_hook,
            )
        except Exception as exc:
            outcome['error'] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    controller.wait_until('restore', 'MATERIALIZED_VERIFIED')
    newer_generation = _advance('edit while restore is paused')
    controller.release('restore', 'MATERIALIZED_VERIFIED')
    thread.join(3)
    assert not thread.is_alive()
    assert isinstance(outcome.get('error'), HistoryRestoreStale)
    assert storage_mutations.current_project_generation() == newer_generation > expected_generation

def test_v778_remote_publication_fault_before_manifest_leaves_no_false_authority(fresh_project):
    store = _store()
    row = store.capture(label='publication fault', creation_reason='v778', force_checkpoint=True)
    transport = MemoryTransport()
    repo = _repo(transport)

    def crash(stage: str, _context: dict) -> None:
        if stage == 'BEFORE_PUBLICATION':
            raise OSError('simulated kill before remote publication')

    with pytest.raises(OSError, match='simulated kill'):
        repo.publish(store, row['revision_id'], stage_hook=crash)
    assert repo.newest_valid() is None
    assert any(path.endswith('.snapshot.zip') for path in transport.data)
    retry = repo.publish(store, row['revision_id'])
    assert retry.generation == row['generation_end']
    assert repo.newest_valid()['revision_id'] == row['revision_id']


def test_v778_storage_diagnostics_are_allowlisted_and_cannot_log_user_content(monkeypatch, tmp_path):
    path = tmp_path / 'storage_package.jsonl'
    monkeypatch.setattr(diagnostics, '_log_path', lambda: path)
    diagnostics.log_storage_event(
        'STORAGE_TEST_001',
        'Synthetic storage transition.',
        project_id='project-safe-id',
        generation=42,
        revision_id='a' * 32,
        destination_id='b' * 32,
        provider='webdav',
        state_transition='DESTINATION_VERIFIED_TO_PUBLISHED',
        verification_outcome='verified',
        retry_attempt=2,
        queue_depth=3,
        gc_epoch=7,
    )
    row = json.loads(path.read_text(encoding='utf-8'))
    assert row['context']['generation'] == 42
    assert row['context']['state_transition'] == 'DESTINATION_VERIFIED_TO_PUBLISHED'
    text = path.read_text(encoding='utf-8').lower()
    assert 'oauth' not in text and 'password' not in text and 'manuscript' not in text
    with pytest.raises(ValueError, match='FORBIDDEN_FIELD'):
        diagnostics.log_storage_event('STORAGE_BAD', 'must fail', manuscript_content='secret prose')
    with pytest.raises(ValueError, match='UNKNOWN_FIELD'):
        diagnostics.log_storage_event('STORAGE_BAD', 'must fail', arbitrary_blob='x')


def test_v778_clean_machine_recovers_exact_canonical_state_from_recovery_and_remote_history(fresh_project, tmp_path):
    _advance('clean-machine canonical state')
    storage_assets.durable_write_project_asset(storage_context.ASSET_DIR / 'v778-clean.bin', b'clean-machine-asset')
    # Bind the asset into canonical state through a canvas node so its hash is relevant.
    with storage_mutations.tx(operation='v778-clean-asset-reference') as con:
        canvas = con.execute('SELECT id FROM canvases ORDER BY id LIMIT 1').fetchone()
        if canvas is not None:
            node = con.execute('SELECT id FROM canvas_nodes WHERE canvas_id=? ORDER BY id LIMIT 1', (int(canvas[0]),)).fetchone()
            if node is not None:
                con.execute('UPDATE canvas_nodes SET image_path=?,updated_at=? WHERE id=?', ('/api/assets/v778-clean.bin', storage_values.now(), int(node[0])))
    store = _store()
    revision = store.capture(label='clean machine source', creation_reason='v778', force_checkpoint=True)
    expected_hash = str(revision['expected_state_hash'])
    expected_content_hash = _content_hash_without_project_uid(storage_context.DB_PATH, storage_context.ASSET_DIR, tmp_path / 'source-content-snapshot')
    transport = MemoryTransport()
    repo = _repo(transport)
    repo.publish(store, revision['revision_id'])
    package = storage_recovery.backup_project('v778_clean_machine')
    assert package and Path(package).is_file()
    surviving_package = tmp_path / 'surviving.breadmaker'
    shutil.copy2(package, surviving_package)

    # The recovery side below is intentionally disconnected from the live Workspace.
    replacement = Workspace(tmp_path / 'fresh-install')
    replacement.init()
    imported = replacement.import_project(surviving_package)
    imported_root = Path(imported['path'])
    assert imported['id'] != fresh_project['project']['id']
    assert _content_hash_without_project_uid(imported_root / 'project.sqlite3', imported_root / 'assets', tmp_path / 'imported-snapshot') == expected_content_hash

    repaired_store = LocalRevisionStore(
        tmp_path / 'fresh-history',
        tmp_path / 'unused-current.sqlite3',
        tmp_path / 'unused-assets',
        canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
        checkpoint_interval=4,
        workspace_project_id=_workspace_id(),
    )
    repaired = repo.repair_local(repaired_store, revision['revision_id'])
    assert repaired.repaired is True
    materialized = repaired_store.materialize_revision(revision['revision_id'], tmp_path / 'remote-materialized')
    assert materialized['canonical_state_hash'] == expected_hash


def test_v778_corrupt_latest_recovery_cannot_destroy_previous_known_good(fresh_project, tmp_path):
    store = _store()
    old = store.capture(label='older recovery', creation_reason='v778', force_checkpoint=True)
    old_content_hash = _content_hash_without_project_uid(storage_context.DB_PATH, storage_context.ASSET_DIR, tmp_path / 'older-content-snapshot')
    old_package = storage_recovery.backup_project('v778_older')
    assert old_package
    old_copy = tmp_path / 'older.breadmaker'
    shutil.copy2(old_package, old_copy)
    _advance('newer recovery state')
    newer = store.capture(label='newer recovery', creation_reason='v778')
    newer_package = storage_recovery.backup_project('v778_newer')
    assert newer_package
    corrupt_copy = tmp_path / 'newer-corrupt.breadmaker'
    shutil.copy2(newer_package, corrupt_copy)
    with zipfile.ZipFile(corrupt_copy, 'r') as src:
        members = [(info, src.read(info.filename)) for info in src.infolist() if not info.is_dir()]
    rewritten = corrupt_copy.with_suffix('.rewrite')
    with zipfile.ZipFile(rewritten, 'w', compression=zipfile.ZIP_DEFLATED) as dst:
        for info, data in members:
            dst.writestr(info.filename, b'corrupt database' if info.filename == 'project.sqlite3' else data)
    rewritten.replace(corrupt_copy)

    replacement = Workspace(tmp_path / 'fallback-install')
    replacement.init()
    with pytest.raises(WorkspaceError):
        replacement.import_project(corrupt_copy)
    imported = replacement.import_project(old_copy)
    imported_root = Path(imported['path'])
    assert imported['id'] != fresh_project['project']['id']
    assert _content_hash_without_project_uid(imported_root / 'project.sqlite3', imported_root / 'assets', tmp_path / 'fallback-snapshot') == old_content_hash
    assert int(newer['generation_end']) > int(old['generation_end'])


@pytest.mark.parametrize('seed', list(range(7780, 7812)))
def test_v778_randomized_cross_layer_soak_preserves_all_live_invariants(seed, fresh_project, monkeypatch, tmp_path):
    """One bounded deterministic shard of the 7.7.8 cross-layer soak.

    The permanent release contract is 10 seconds *per pytest item*.  Keep each shard
    comfortably below that ceiling on slower developer machines instead of relying on
    machine-specific timing margin.  Scale comes from 32 independent seeds x 10
    canonical mutations (320 total), while the expensive GC closure is exercised by a
    separate randomized shard below. The aggregate publication, backup, verification,
    and remote-repair counts are preserved while each item gets teardown headroom.
    """
    import revision_store.local_store as local_store_module

    rng = random.Random(seed)
    clock = [datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(local_store_module, '_utc_now', lambda: clock[0].isoformat(timespec='microseconds'))
    store = _store(interval=4)
    transport = MemoryTransport()
    repo = _repo(transport)
    last_generation = storage_mutations.current_project_generation()
    last_revision = store.capture(label='soak baseline', creation_reason='v778-soak', force_checkpoint=True)
    repo.publish(store, last_revision['revision_id'])
    recovery_hashes: list[tuple[int, str]] = []

    shard = seed - 7780
    for i in range(10):
        # Two synthetic days per mutation lets retention age naturally without making
        # the shard itself longer. Wall time never establishes authority.
        clock[0] += timedelta(days=2)
        with storage_mutations.tx(operation='v778-soak-mutation') as con:
            pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
            mode = rng.randrange(3)
            if mode == 0:
                con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', (f'soak-{seed}-{i}-{rng.randrange(100000)}', storage_values.now(), pid))
            elif mode == 1:
                chapter = con.execute('SELECT id FROM chapters WHERE project_id=? ORDER BY id LIMIT 1', (pid,)).fetchone()
                con.execute('UPDATE chapters SET description=?,updated_at=? WHERE id=?', (f'chapter-soak-{seed}-{i}', storage_values.now(), int(chapter[0])))
            else:
                block = con.execute('SELECT id,image_path FROM manuscript_blocks WHERE project_id=? ORDER BY id LIMIT 1', (pid,)).fetchone()
                text = f'soak words {seed} {i} {rng.randrange(100000)}'
                html = f'<p>{text}</p>'
                con.execute(
                    'UPDATE manuscript_blocks SET text_content=?,content_html=?,content_hash=?,updated_at=? WHERE id=?',
                    (text, html, storage_values.block_content_hash(html, text, str(block[1] or '')), storage_values.now(), str(block[0])),
                )
        generation = storage_mutations.current_project_generation()
        assert generation > last_generation
        last_generation = generation

        if i % 3 == 2:
            last_revision = store.capture(label=f'soak revision {i}', creation_reason='v778-soak')
            verified = store.verify_revision(last_revision['revision_id'])
            assert int(verified['revision']['generation_end']) == generation

        # Preserve the old aggregate publication workload after doubling shard count:
        # every smaller shard performs one result+idempotency publication pair.
        if i == 5:
            result = repo.publish(store, last_revision['revision_id'])
            assert int(result.generation) == int(last_revision['generation_end'])
            assert int(repo.newest_valid()['generation']) == int(last_revision['generation_end'])
            duplicate = repo.publish(store, last_revision['revision_id'])
            assert duplicate.idempotent is True

        # Spread the deliberately expensive remote-loss/repair path over half the
        # shards instead of making every pytest item pay for it.
        # Preserve eight remote-loss/repair exercises across the 32 smaller shards.
        if i == 8 and shard % 4 == 0:
            # Repair means republishing an *already published* revision whose remote
            # payload has gone missing.  Publishing the newer i=8 revision here would
            # correctly be a fresh publication (repaired=False), not a repair.
            newest = repo.newest_valid()
            assert newest is not None
            repair_revision_id = str(newest['revision_id'])
            repair_generation = int(newest['generation'])
            missing_path = str(newest['snapshot_bundle_path'])
            transport.data.pop(missing_path)
            degraded = repo.newest_valid()
            assert degraded is None or int(degraded['generation']) < repair_generation
            repaired = repo.publish(store, repair_revision_id)
            assert repaired.repaired is True
            restored = repo.newest_valid()
            assert restored is not None
            assert int(restored['generation']) == repair_generation
            assert str(restored['revision_id']) == repair_revision_id

        if i == 9:
            package = storage_recovery.backup_project(f'v778_soak_{seed}_{i}')
            info = Workspace(Path(storage_context.PROJECT_ROOT).parent.parent).verify_package(package)
            assert int(info['project_generation']) == generation
            recovery_hashes.append((generation, str(info['project_fingerprint'])))

        # Continuous means after every meaningful History publication, rather than
        # re-hashing four historical SQLite snapshots after every canonical mutation.
        # The dedicated History property suite already reconstructs arbitrary retained
        # revisions at much larger scale.
        if i % 3 == 2:
            for row in store.list_revisions()[:2]:
                store.verify_revision(row['revision_id'])
        assert int(store.list_revisions()[0]['generation_end']) <= storage_mutations.current_project_generation()

    # End every shard by proving the newest local/remote authority still reconstructs.
    newest_local = store.list_revisions()[0]
    store.verify_revision(newest_local['revision_id'])
    repo.publish(store, newest_local['revision_id'])
    newest_remote = repo.newest_valid()
    assert newest_remote is not None
    assert int(newest_remote['generation']) == int(newest_local['generation_end'])
    assert recovery_hashes and max(g for g, _ in recovery_hashes) <= storage_mutations.current_project_generation()


@pytest.mark.parametrize('seed', list(range(7796, 7804)))
def test_v778_randomized_cross_layer_gc_soak_preserves_retained_history(seed, fresh_project, monkeypatch, tmp_path):
    """Randomized GC integration is isolated from the long mutation soak.

    Each seed creates multiple checkpoint segments, publishes them remotely, ages the
    old segment, then performs the required two independent local and remote GC epochs.
    This keeps the destructive/reconstruction path covered without coupling its O(n)
    verification cost to a 40-operation soak item.
    """
    import revision_store.local_store as local_store_module

    rng = random.Random(seed)
    clock = [datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(local_store_module, '_utc_now', lambda: clock[0].isoformat(timespec='microseconds'))
    store = _store(interval=3)
    transport = MemoryTransport()
    repo = _repo(transport)
    delete_cap = MemoryDeleteCapability(transport, _workspace_id())
    remote_gc_state = tmp_path / f'remote-gc-{seed}.json'

    baseline = store.capture(label='gc soak baseline', creation_reason='v778-gc-soak', force_checkpoint=True)
    repo.publish(store, baseline['revision_id'])

    # Six revisions are enough to span multiple bounded checkpoint segments.  Mutate
    # different canonical surfaces so this remains a cross-layer integration test.
    for i in range(6):
        clock[0] += timedelta(days=2)
        with storage_mutations.tx(operation='v778-gc-soak-mutation') as con:
            pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
            if rng.randrange(2):
                con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', (f'gc-soak-{seed}-{i}', storage_values.now(), pid))
            else:
                chapter = con.execute('SELECT id FROM chapters WHERE project_id=? ORDER BY id LIMIT 1', (pid,)).fetchone()
                con.execute('UPDATE chapters SET description=?,updated_at=? WHERE id=?', (f'gc-chapter-{seed}-{i}', storage_values.now(), int(chapter[0])))
        row = store.capture(label=f'gc soak revision {i}', creation_reason='v778-gc-soak')
        repo.publish(store, row['revision_id'])

    # Age the old segment past retention, then create a recent anchor that must survive.
    clock[0] += timedelta(days=40)
    _advance(f'gc recent {seed}')
    recent = store.capture(label='gc recent anchor', creation_reason='v778-gc-soak', force_checkpoint=True)
    repo.publish(store, recent['revision_id'])

    first_local = run_local_history_gc(store, '30_days', now_utc=clock[0], grace_seconds=0)
    second_local = run_local_history_gc(store, '30_days', now_utc=clock[0] + timedelta(seconds=1), grace_seconds=0)
    assert first_local.plan.epoch < second_local.plan.epoch
    for row in store.list_revisions():
        store.verify_revision(row['revision_id'])
    assert store.list_revisions()[0]['revision_id'] == recent['revision_id']

    # Re-publish the retained newest local state before remote GC.  Remote GC must keep
    # it reconstructable while authority-first deletion removes only proven old segments.
    repo.publish(store, recent['revision_id'])
    first_remote = run_remote_history_gc(
        repository=repo,
        delete_capability=delete_cap,
        state_path=remote_gc_state,
        policy='30_days',
        now_utc=clock[0],
        grace_seconds=0,
    )
    second_remote = run_remote_history_gc(
        repository=repo,
        delete_capability=delete_cap,
        state_path=remote_gc_state,
        policy='30_days',
        now_utc=clock[0] + timedelta(seconds=1),
        grace_seconds=0,
    )
    assert first_remote.plan.epoch < second_remote.plan.epoch
    newest_remote = repo.newest_valid()
    assert newest_remote is not None
    assert newest_remote['revision_id'] == recent['revision_id']
    assert all(path.startswith(f'history/{_workspace_id()}/') for path in delete_cap.deleted)

def test_v778_gc_fault_before_remote_authority_delete_retries_from_fresh_plan(fresh_project, monkeypatch, tmp_path):
    import revision_store.local_store as local_store_module

    clock = [datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(local_store_module, '_utc_now', lambda: clock[0].isoformat(timespec='microseconds'))
    store = _store(interval=2)
    old0 = store.capture(label='old anchor', creation_reason='v778', force_checkpoint=True)
    clock[0] += timedelta(days=1)
    _advance('old child')
    old1 = store.capture(label='old child', creation_reason='v778')
    clock[0] += timedelta(days=45)
    _advance('recent anchor')
    recent = store.capture(label='recent', creation_reason='v778', force_checkpoint=True)
    transport = MemoryTransport()
    repo = _repo(transport)
    for row in (old0, old1, recent):
        repo.publish(store, row['revision_id'])
    cap = MemoryDeleteCapability(transport, _workspace_id())
    state_path = tmp_path / 'remote-gc-fault.json'
    run_remote_history_gc(repository=repo, delete_capability=cap, state_path=state_path, policy='30_days', now_utc=clock[0], grace_seconds=0)

    def crash(stage: str, _context: dict) -> None:
        if stage == 'BEFORE_REMOTE_AUTHORITY_DELETE':
            raise OSError('simulated kill before GC authority removal')

    with pytest.raises(OSError, match='simulated kill'):
        run_remote_history_gc(
            repository=repo,
            delete_capability=cap,
            state_path=state_path,
            policy='30_days',
            now_utc=clock[0] + timedelta(seconds=1),
            grace_seconds=0,
            stage_hook=crash,
        )
    assert cap.deleted == []
    assert repo.newest_valid()['revision_id'] == recent['revision_id']
    retry = run_remote_history_gc(
        repository=repo,
        delete_capability=cap,
        state_path=state_path,
        policy='30_days',
        now_utc=clock[0] + timedelta(seconds=2),
        grace_seconds=0,
    )
    assert retry.quarantined
    assert repo.newest_valid()['revision_id'] == recent['revision_id']
