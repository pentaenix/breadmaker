from __future__ import annotations

from workspace import workspace

from storage import context as storage_context
from storage import database as storage_database
from storage import history as storage_history
from storage import integrity as storage_integrity
from storage import mutations as storage_mutations
from storage import schema as storage_schema

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

import app as app_module
from conftest import assert_ok
from revision_store.local_store import LocalRevisionStore, RevisionStoreCorruption
from revision_store.remote_history import RemoteHistoryCorruption, RemoteHistoryRepository
from workspace import APP_SCHEMA_VERSION, PACKAGE_VERSION, PUBLICATION_SUFFIX, Workspace, WorkspaceError
from workspace.filesystem import _write_external_backup_marker


def _tables() -> set[str]:
    with storage_mutations.tx(track_generation=False) as con:
        return {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def test_v781_clean_schema_contains_only_current_storage_subsystems(fresh_project):
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION
    assert APP_SCHEMA_VERSION >= 22
    assert storage_database.database_schema_version() == APP_SCHEMA_VERSION
    tables = _tables()
    forbidden = {
        'revisions', 'content_objects', 'preserved_assets', 'preserved_asset_paths',
        'preservation_meta', 'preservation_roots',
    }
    assert not (tables & forbidden)
    assert {'storage_meta', 'cuts', 'checkpoints', 'checkpoint_assets', 'checkpoint_asset_refs'} <= tables


def test_v781_removed_legacy_storage_surfaces_are_really_absent(client, fresh_project):
    root = Path(__file__).resolve().parents[1]
    assert not (root / 'preservation').exists()
    from storage import database as storage_database, history as storage_history_module, recovery as storage_recovery
    owners=(storage_database,storage_history_module,storage_recovery)
    for name in ('commit_revision', 'restore_revision', 'run_preservation_gc', 'needs_v5_migration', 'adopt_legacy_comments'):
        assert all(not hasattr(owner,name) for owner in owners), name
    assert client.get('/api/history').status_code == 404
    assert client.get('/api/revisions/1').status_code == 404
    assert client.get('/api/preservation/health').status_code == 404
    assert client.get('/api/external-backup').status_code == 404


def test_v781_old_schema_is_rejected_read_only(tmp_path, fresh_project):
    root = tmp_path / 'old-project'; root.mkdir()
    db = root / 'project.sqlite3'
    con = sqlite3.connect(db)
    try:
        con.executescript("""
            CREATE TABLE projects(id INTEGER PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE storage_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO projects VALUES(1,'Old','2026-01-01','2026-01-01');
            INSERT INTO storage_meta VALUES('schema_version','21');
        """)
        con.commit()
    finally:
        con.close()
    before = db.read_bytes(); old_root = storage_context.PROJECT_ROOT
    try:
        storage_context.configure_project(root)
        with pytest.raises(storage_database.UnsupportedProjectVersion, match='PREALPHA_PROJECT_INCOMPATIBLE'):
            storage_database.init_db()
        assert db.read_bytes() == before
        assert not (root / 'project.sqlite3-wal').exists()
        assert not (root / 'project.sqlite3-shm').exists()
    finally:
        if old_root is not None:
            storage_context.configure_project(old_root)


def test_v781_package_v1_is_explicitly_unsupported(fresh_project, tmp_path):
    ws = workspace
    current = tmp_path / 'current.breadmaker'
    ws.export_project(fresh_project['project']['id'], current)
    assert PACKAGE_VERSION == 2
    with zipfile.ZipFile(current) as src:
        members = {name: src.read(name) for name in src.namelist()}
    manifest = json.loads(members['manifest.json'])
    manifest['package_version'] = 1
    members['manifest.json'] = json.dumps(manifest).encode('utf-8')
    old = tmp_path / 'old.breadmaker'
    with zipfile.ZipFile(old, 'w', compression=zipfile.ZIP_DEFLATED) as dst:
        for name, data in members.items(): dst.writestr(name, data)
    with pytest.raises(WorkspaceError, match='UNSUPPORTED_PACKAGE_VERSION'):
        ws.verify_package(old)


def test_v781_unpublished_local_payload_is_never_recovery_authority(fresh_project, tmp_path):
    ws = workspace
    folder = tmp_path / 'destination'; folder.mkdir()
    location_id = 'a' * 32
    _write_external_backup_marker(folder, location_id)
    destination = {'id':'b'*32, 'provider':'local_folder', 'destination_kind':'local', 'path':str(folder), 'location_id':location_id}
    payload = folder / f"breadmaker-{fresh_project['project']['id']}-orphan.breadmaker"
    ws.export_project(fresh_project['project']['id'], payload)
    assert ws._discover_local_destination_backups(destination) == []
    with pytest.raises(WorkspaceError, match='BACKUP_NOT_PUBLISHED'):
        ws._backup_providers['local_folder'].materialize(ws, destination, payload.name, tmp_path / 'materialized')


def test_v781_cuts_are_self_contained_current_rows(client, fresh_project):
    chapter = fresh_project['bootstrap']['chapters'][0]
    cut = assert_ok(client.post('/api/cuts', json={'title':'Independent Cut','content_html':'<p>Removed prose</p>','source_chapter_id':chapter['id']}))
    with storage_mutations.tx(track_generation=False) as con:
        columns = {str(row[1]) for row in con.execute('PRAGMA table_info(cuts)')}
        row = con.execute('SELECT title,content_html FROM cuts WHERE id=?', (cut['id'],)).fetchone()
    assert 'root_hash' not in columns
    assert row['title'] == 'Independent Cut'
    assert 'Removed prose' in row['content_html']



def test_v781_fragment_cut_import_uses_current_self_contained_cut_storage(client, fresh_project):
    fragment = {
        'format': 'breadmaker-fragment',
        'schema_version': '1.0',
        'name': 'Current Cut Fragment',
        'cuts': [{'id': 'cut:current', 'title': 'Imported current cut', 'content': 'Preserved as a current Cut.', 'note': 'No legacy root'}],
    }
    raw = json.dumps(fragment).encode('utf-8')
    response = client.post(
        '/api/json-import',
        files={'file': ('current-cut.breadmaker.json', io.BytesIO(raw), 'application/json')},
        data={'context': '{}', 'target_choices': '{}'},
    )
    assert response.status_code == 200, response.text
    with storage_mutations.tx(track_generation=False) as con:
        columns = {str(row[1]) for row in con.execute('PRAGMA table_info(cuts)')}
        row = con.execute("SELECT title,content_html,note FROM cuts WHERE title='Imported current cut'").fetchone()
    assert 'root_hash' not in columns
    assert row is not None
    assert 'Preserved as a current Cut.' in str(row['content_html'])
    assert row['note'] == 'No legacy root'

def test_v781_checkpoint_owns_and_restores_referenced_image_bytes(client, fresh_project):
    chapter = fresh_project['bootstrap']['chapters'][0]
    name = 'v781-checkpoint-image.bin'; payload = b'Breadmaker-7.8.1-checkpoint-image\x00\x01'
    asset = storage_context.ASSET_DIR / name; asset.write_bytes(payload)
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks", json={'blocks':[{
        'block_type':'image','semantic_style':'body','content_html':'','text_content':'','image_path':f'/api/assets/{name}'
    }]}))
    cp = assert_ok(client.post('/api/checkpoints', json={'scope':'chapter','chapter_id':chapter['id'],'name':'Image checkpoint'}))
    with storage_mutations.tx(track_generation=False) as con:
        ref = con.execute('SELECT asset_hash FROM checkpoint_asset_refs WHERE checkpoint_id=?', (cp['id'],)).fetchone()
        blob = con.execute('SELECT payload FROM checkpoint_assets WHERE hash=?', (ref['asset_hash'],)).fetchone()
        assert bytes(blob['payload']) == payload
    asset.unlink()
    pre = assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight", json={'target_chapter_id':chapter['id']}))
    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply", json={'target_chapter_id':chapter['id'],'confirm_conflicts':pre.get('requires_confirmation',False),'preflight_token':pre['preflight_token']}))
    assert asset.read_bytes() == payload


def test_v781_corrupt_checkpoint_asset_fails_closed_without_mutating_current(client, fresh_project):
    chapter = fresh_project['bootstrap']['chapters'][0]
    name = 'v781-corrupt-checkpoint.bin'; asset = storage_context.ASSET_DIR / name; asset.write_bytes(b'original-checkpoint-bytes')
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks", json={'blocks':[{
        'block_type':'image','semantic_style':'body','content_html':'','text_content':'','image_path':f'/api/assets/{name}'
    }]}))
    cp = assert_ok(client.post('/api/checkpoints', json={'scope':'chapter','chapter_id':chapter['id'],'name':'Corruption fence'}))
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks", json={'blocks':[{
        'block_type':'paragraph','semantic_style':'body','content_html':'<p>Current survives</p>','text_content':'Current survives','image_path':''
    }]}))
    generation = storage_mutations.current_project_generation()
    with storage_mutations.tx(track_generation=False) as con:
        ref = con.execute('SELECT asset_hash FROM checkpoint_asset_refs WHERE checkpoint_id=?', (cp['id'],)).fetchone()
        original = bytes(con.execute('SELECT payload FROM checkpoint_assets WHERE hash=?', (ref['asset_hash'],)).fetchone()['payload'])
        con.execute('UPDATE checkpoint_assets SET payload=? WHERE hash=?', (b'corrupt', ref['asset_hash']))
    try:
        failed = client.post(f"/api/checkpoints/{cp['id']}/preflight", json={'target_chapter_id':chapter['id']})
        assert failed.status_code >= 400
        assert 'CHECKPOINT_005' in failed.text
        assert storage_mutations.current_project_generation() == generation
        blocks = assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']
        assert blocks[0]['text_content'] == 'Current survives'
    finally:
        with storage_mutations.tx(track_generation=False) as con:
            con.execute('UPDATE checkpoint_assets SET payload=? WHERE hash=?', (original, ref['asset_hash']))



class _V781MemoryRemoteTransport:
    def __init__(self):
        self.data: dict[str, bytes] = {}
    def put_immutable(self, path: str, data: bytes) -> None:
        current = self.data.get(path)
        if current is not None and current != data:
            raise RuntimeError('immutable conflict')
        self.data[path] = bytes(data)
    def read(self, path: str) -> bytes | None:
        value = self.data.get(path)
        return None if value is None else bytes(value)
    def put_file_immutable(self, path: str, source: Path) -> None:
        self.put_immutable(path, Path(source).read_bytes())
    def get_file(self, path: str, target: Path) -> bool:
        value = self.data.get(path)
        if value is None:
            return False
        Path(target).write_bytes(value)
        return True
    def list_paths(self, prefix: str) -> list[str]:
        return sorted(path for path in self.data if path.startswith(prefix))

def test_v781_partial_remote_history_authority_is_corruption_not_migrated(fresh_project):
    store = storage_history.local_revision_store()
    revision = store.capture(label='authority pair', creation_reason='v781', force_checkpoint=True)
    transport = _V781MemoryRemoteTransport()
    workspace_id = str(store.workspace_project_id)
    writer_id = '7' * 32
    repo = RemoteHistoryRepository(transport, workspace_project_id=workspace_id, writer_id=writer_id, canonical_tables=store.canonical_tables)
    repo.publish(store, revision['revision_id'])
    base = f'history/{workspace_id}'
    original = dict(transport.data)
    for missing in ('repository.json', 'writer-authority.json'):
        transport.data = dict(original)
        transport.data.pop(f'{base}/{missing}')
        before = dict(transport.data)
        with pytest.raises(RemoteHistoryCorruption, match='REMOTE_HISTORY_REPOSITORY_AUTHORITY_INCOMPLETE'):
            RemoteHistoryRepository(transport, workspace_project_id=workspace_id, writer_id=writer_id, canonical_tables=store.canonical_tables).publish(store, revision['revision_id'])
        assert transport.data == before

def test_v781_nonempty_history_without_repository_marker_fails_closed(fresh_project):
    store = storage_history.local_revision_store()
    rev = store.capture(label='format authority', creation_reason='v781', force_checkpoint=True)
    assert store.verify_revision(rev['revision_id'])
    metadata = store.store_metadata_path.read_bytes(); store.store_metadata_path.unlink()
    try:
        with pytest.raises(RevisionStoreCorruption, match='HISTORY_STORE_METADATA_MISSING_WITH_DATA'):
            LocalRevisionStore(store.root, store.database, store.asset_dir, checkpoint_interval=store.checkpoint_interval,
                               canonical_tables=store.canonical_tables, workspace_project_id=store.workspace_project_id)
    finally:
        store.store_metadata_path.write_bytes(metadata)


def test_v781_clean_home_end_to_end_uses_no_legacy_storage(tmp_path):
    home = tmp_path / 'clean-home'
    script = r'''
import json, os, shutil, sqlite3, sys
from pathlib import Path
from fastapi.testclient import TestClient
import app
from backend import project_activation
from storage import history as storage_history
from storage import integrity as storage_integrity
from storage import mutations as storage_mutations
from storage import schema as storage_schema
from workspace import workspace
with TestClient(app.app) as c:
    created=c.post('/api/projects',json={'name':'Clean 7.8.1'}); assert created.status_code==200,created.text
    project=created.json()['project']; boot=c.get('/api/bootstrap').json(); chapter=boot['chapters'][0]
    saved=c.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'block_type':'paragraph','semantic_style':'body','content_html':'<p>clean storage prose</p>','text_content':'clean storage prose','image_path':''}]}); assert saved.status_code==200,saved.text
    cut=c.post('/api/cuts',json={'title':'Clean cut','content_html':'<p>removed</p>','source_chapter_id':chapter['id']}); assert cut.status_code==200,cut.text
    cp=c.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Clean checkpoint'}); assert cp.status_code==200,cp.text
    rev=storage_history.local_revision_store().capture(label='Clean history',creation_reason='v781',force_checkpoint=True)
    assert storage_history.local_revision_store().verify_revision(rev['revision_id'])
    package=Path(os.environ['BREADMAKER_HOME'])/'clean.breadmaker'; workspace.export_project(project['id'],package,recovery_reason='v781-clean')
    info=workspace.verify_package(package); assert info['package_version']==2 and info['schema_version']==storage_schema.SCHEMA_VERSION
    con=sqlite3.connect(Path(project['path'])/'project.sqlite3'); tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}; con.close()
    assert not ({'revisions','content_objects','preserved_assets','preservation_meta'} & tables)
    workspace.delete_project(project['id'],permanent=True)
    imported=workspace.import_project(package,name_override='Recovered clean')
    project_activation.activate_project(imported['id'])
    report=storage_integrity.deep_integrity_check(); assert report['ok'],report
    with storage_mutations.tx(track_generation=False) as con:
        assert con.execute("SELECT COUNT(*) FROM cuts").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 1
print('CLEAN_STORAGE_OK')
'''
    env = os.environ.copy(); env['BREADMAKER_HOME'] = str(home); env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    completed = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1], env=env,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8)
    assert completed.returncode == 0, completed.stdout
    assert 'CLEAN_STORAGE_OK' in completed.stdout
