from __future__ import annotations

from workspace import workspace
from backend import project_activation

from storage import context as storage_context
from storage import database as storage_database
from storage import history as storage_history
from storage import mutations as storage_mutations
from storage import schema as storage_schema
from storage import schema_contracts as storage_schema_contracts

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import app as app_module
from backups.frozen_snapshot import capture_frozen_snapshot
from revision_store.local_store import (
    CHECKPOINT_VERSION,
    REVISION_VERSION,
    SNAPSHOT_VERSION,
    STORE_FORMAT,
    STORE_VERSION,
    LocalRevisionStore,
    RevisionStoreCorruption,
    canonical_state_hash,
)
from revision_store.history_gc import HistoryGCCorruption, run_local_history_gc
from revision_store.remote_history import (
    REPOSITORY_FORMAT,
    RemoteHistoryCorruption,
    RemoteHistoryRepository,
)
from workspace import APP_SCHEMA_VERSION, PACKAGE_VERSION, Workspace

from test_v777 import MemoryTransport


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob('*'))
        if path.is_file() and path.name != 'repository.json'
    }


def _logical_hash(database: Path, assets: Path, snapshot_root: Path) -> str:
    frozen = capture_frozen_snapshot(database, assets, snapshot_root)
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


def test_v779_release_contract_freezes_77_formats_and_documents_them():
    root = Path(__file__).resolve().parents[1]
    assert tuple(map(int, root.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (7, 7, 9)
    assert STORE_VERSION == SNAPSHOT_VERSION == REVISION_VERSION == CHECKPOINT_VERSION == 1
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION
    assert APP_SCHEMA_VERSION >= 22
    assert PACKAGE_VERSION == 2
    text = root.joinpath('docs', 'REVISION_STORE_FORMAT_V1.md').read_text(encoding='utf-8')
    assert 'Revision Store Format v1' in text
    assert 'revision_history/v1/repository.json' in text
    assert 'Downgrade/write fence' in text
    architecture = root.joinpath('ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '## 7.7.9 Final 7.7 Integration / Format Freeze' in architecture


def test_v779_history_with_data_missing_format_marker_fails_closed(fresh_project):
    store = storage_history.local_revision_store()
    revision = store.capture(label='current history', creation_reason='v779', force_checkpoint=True)
    before = _tree_hashes(store.root)
    store.store_metadata_path.unlink()
    with pytest.raises(RevisionStoreCorruption, match='HISTORY_STORE_METADATA_MISSING_WITH_DATA'):
        storage_history.local_revision_store()
    assert _tree_hashes(store.root) == before
    assert any(revision['revision_id'] in path.name for path in store.revisions_dir.glob('*.revision.json'))


def test_v779_future_local_history_version_fails_closed_before_write(fresh_project):
    store = storage_history.local_revision_store()
    revision = store.capture(label='downgrade fence', creation_reason='v779', force_checkpoint=True)
    before = _tree_hashes(store.root)
    marker = json.loads(store.store_metadata_path.read_text(encoding='utf-8'))
    marker['store_version'] = 2
    store.store_metadata_path.write_text(json.dumps(marker, sort_keys=True), encoding='utf-8')

    with pytest.raises(RevisionStoreCorruption, match='HISTORY_STORE_VERSION_UNSUPPORTED:2'):
        storage_history.local_revision_store()

    assert _tree_hashes(store.root) == before
    assert any(revision['revision_id'] in path.name for path in store.revisions_dir.glob('*.revision.json'))


def test_v779_future_remote_history_repository_version_fails_closed_without_mutation(fresh_project):
    workspace_id, _ = storage_mutations.configured_project_identity()
    transport = MemoryTransport()
    repo = RemoteHistoryRepository(
        transport,
        workspace_project_id=workspace_id,
        writer_id='1' * 32,
        canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
    )
    path = f'history/{workspace_id}/repository.json'
    transport.data[path] = json.dumps({
        'repository_format': REPOSITORY_FORMAT,
        'repository_version': 2,
        'workspace_project_id': workspace_id,
        'writer_id': '1' * 32,
    }, sort_keys=True).encode('utf-8')
    before = dict(transport.data)

    with pytest.raises(RemoteHistoryCorruption, match='REMOTE_HISTORY_REPOSITORY_IDENTITY_INVALID'):
        repo.claim_writer()

    assert transport.data == before


def test_v779_future_gc_state_version_cannot_authorize_deletion(fresh_project):
    store = storage_history.local_revision_store()
    revision = store.capture(label='future gc fence', creation_reason='v779', force_checkpoint=True)
    state_path = store.root / 'gc' / 'state.json'
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        'gc_format': 'breadmaker-history-gc-state',
        'gc_version': 2,
        'epoch': 999,
        'candidates': {},
        'quarantine': {},
    }), encoding='utf-8')
    before = _tree_hashes(store.root)

    with pytest.raises(HistoryGCCorruption, match='HISTORY_GC_STATE_FORMAT_INVALID'):
        run_local_history_gc(store, '30_days', grace_seconds=0)

    assert _tree_hashes(store.root) == before
    store.verify_revision(revision['revision_id'])


def test_v779_future_project_schema_cannot_be_opened_or_mutated(fresh_project):
    ws = workspace
    previous_id = ws.active_id()
    target = ws.create_project('Future Breadmaker Project', activate=False)
    target_root = Path(target['path'])
    manifest = ws.read_manifest(target_root)
    manifest['schema_version'] = APP_SCHEMA_VERSION + 1
    ws._write_manifest(target_root, manifest)
    manifest_bytes = (target_root / 'manifest.json').read_bytes()

    with pytest.raises(RuntimeError, match=f'PREALPHA_PROJECT_INCOMPATIBLE:{APP_SCHEMA_VERSION + 1}'):
        project_activation.activate_project(target['id'])

    assert ws.active_id() == previous_id
    assert (target_root / 'manifest.json').read_bytes() == manifest_bytes
    assert not (target_root / 'project.sqlite3').exists()


def test_v779_old_schema_database_is_rejected_without_mutation(tmp_path):
    root = tmp_path / 'old-project'
    root.mkdir()
    db = root / 'project.sqlite3'
    con = sqlite3.connect(db)
    try:
        con.executescript('''
            CREATE TABLE projects(id INTEGER PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE preservation_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO projects(id,title,created_at,updated_at) VALUES(1,'Old pre-alpha','2026-01-01','2026-01-01');
            INSERT INTO preservation_meta(key,value) VALUES('schema_version','19');
        ''')
        con.commit()
    finally:
        con.close()
    before=db.read_bytes()
    old_root = storage_context.PROJECT_ROOT
    try:
        storage_context.configure_project(root)
        with pytest.raises(storage_database.UnsupportedProjectVersion, match='PREALPHA_PROJECT_INCOMPATIBLE'):
            storage_database.init_db()
        assert db.read_bytes()==before
    finally:
        if old_root is not None:storage_context.configure_project(old_root)


def test_v779_breadmaker_package_v2_is_independent_and_clean_install_recoverable(fresh_project, tmp_path):
    # Ensure there is real 7.7 History beside Current; it must not leak into a
    # portable/recovery .breadmaker package.
    history = storage_history.local_revision_store()
    history.capture(label='package independence', creation_reason='v779', force_checkpoint=True)
    expected = _content_hash_without_project_uid(storage_context.DB_PATH, storage_context.ASSET_DIR, tmp_path / 'source-snapshot')

    package = tmp_path / 'final-v77.breadmaker'
    workspace.export_project(fresh_project['project']['id'], package)
    verified = workspace.verify_package(package)
    assert int(verified['package_version']) == 2
    assert int(verified['schema_version']) == APP_SCHEMA_VERSION

    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read('manifest.json'))
    assert manifest['package_format'] == 'breadmaker-project'
    assert int(manifest['package_version']) == 2
    assert int(manifest['schema_version']) == APP_SCHEMA_VERSION
    assert not any(name.startswith('revision_history/') for name in names)

    clean = Workspace(tmp_path / 'clean-install')
    clean.init()
    imported = clean.import_project(package)
    imported_root = Path(imported['path'])
    assert imported['id'] != fresh_project['project']['id']
    actual = _content_hash_without_project_uid(imported_root / 'project.sqlite3', imported_root / 'assets', tmp_path / 'imported-snapshot')
    assert actual == expected
