from __future__ import annotations

from storage import database as storage_database

from workspace import workspace


import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import app as app_module
from backups.frozen_snapshot import FrozenSnapshotError, capture_frozen_snapshot
from workspace import APP_SCHEMA_VERSION, Workspace, WorkspaceError


def _write_minimal_project_db(path: Path, *, schema: int, generation: int = 1, title: str = 'Audit project') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path)
    try:
        con.executescript('''
            CREATE TABLE projects(
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                project_generation INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE storage_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        con.execute(
            'INSERT INTO projects(id,title,created_at,updated_at,project_generation) VALUES(1,?,?,?,?)',
            (title,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',generation),
        )
        con.execute("INSERT INTO storage_meta(key,value) VALUES('schema_version',?)",(str(schema),))
        con.commit()
    finally:
        con.close()


def _manifest(project_id: str, *, schema: int) -> dict:
    return {
        'package_format':'breadmaker-project',
        'package_version':2,
        'schema_version':schema,
        'project_id':project_id,
        'name':'Audit project',
        'created_at':'2026-01-01T00:00:00+00:00',
        'updated_at':'2026-01-01T00:00:00+00:00',
    }


def test_v786_registry_reconciliation_never_registers_unsupported_project_as_current(tmp_path):
    home=tmp_path/'home';project_id='a'*32;root=home/'projects'/project_id
    _write_minimal_project_db(root/'project.sqlite3',schema=21)
    (root/'manifest.json').write_text(json.dumps(_manifest(project_id,schema=21)),encoding='utf-8')
    before_db=(root/'project.sqlite3').read_bytes();before_manifest=(root/'manifest.json').read_bytes()

    ws=Workspace(home);ws.init()

    assert ws.list_projects()==[]
    assert any(row['id']==project_id for row in ws.recovery_candidates())
    assert (root/'project.sqlite3').read_bytes()==before_db
    assert (root/'manifest.json').read_bytes()==before_manifest


def test_v786_restore_from_trash_rejects_unsupported_project_without_move_or_rewrite(tmp_path):
    home=tmp_path/'home';ws=Workspace(home);ws.init()
    project_id='b'*32;trash_id=f'{project_id}-1700000000';root=home/'trash'/trash_id
    _write_minimal_project_db(root/'project.sqlite3',schema=21)
    (root/'manifest.json').write_text(json.dumps(_manifest(project_id,schema=21)),encoding='utf-8')
    before_db=(root/'project.sqlite3').read_bytes();before_manifest=(root/'manifest.json').read_bytes()

    with pytest.raises(WorkspaceError,match='PREALPHA_PROJECT_INCOMPATIBLE'):
        ws.restore_from_trash(trash_id)

    assert root.is_dir()
    assert (root/'project.sqlite3').read_bytes()==before_db
    assert (root/'manifest.json').read_bytes()==before_manifest
    assert ws.list_projects()==[]


def test_v786_frozen_snapshot_rejects_unsupported_schema_before_private_copy(tmp_path):
    root=tmp_path/'old';db=root/'project.sqlite3';assets=root/'assets';assets.mkdir(parents=True)
    _write_minimal_project_db(db,schema=21)
    before=db.read_bytes();target=tmp_path/'snapshot'

    with pytest.raises(FrozenSnapshotError,match='SNAPSHOT_SCHEMA_UNSUPPORTED:21'):
        capture_frozen_snapshot(db,assets,target)

    assert db.read_bytes()==before
    assert not target.exists(), 'unsupported storage must be rejected before snapshot materialization'


def test_v786_portable_export_drops_orphan_publication_protocol_field(fresh_project,tmp_path):
    package=tmp_path/'audit.breadmaker'
    workspace.export_project(fresh_project['project']['id'],package)
    with zipfile.ZipFile(package) as zf:
        manifest=json.loads(zf.read('manifest.json'))
    assert manifest['package_version']==2
    assert manifest['schema_version']==APP_SCHEMA_VERSION
    assert 'backup_publication_protocol' not in manifest
    assert manifest['snapshot_source']=='frozen-canonical-generation'


def test_v786_current_ui_and_locales_do_not_expose_removed_preservation_graph_vocabulary():
    root=Path(__file__).resolve().parents[1]
    catalogs=[json.loads(path.read_text(encoding='utf-8')) for path in sorted((root/'locales').glob('*.json'))]
    en=next(row for row in catalogs if row['code']=='en')
    assert en['strings']['dynamic.html.text.div.80661c8b']=='History & safety'
    assert 'preserved History/Checkpoint/Cut objects' not in en['strings']['dynamic.html.text.p.2b12a962']
    dead_keys={
        'dynamic.html.text.span.15d749df',
        'dynamic.html.text.span.f1ab3a55',
        'dynamic.html.text.span.4b70068b',
        'dynamic.html.text.span.306867eb',
        'dynamic.html.text.span.806583ca',
    }
    assert all(not (dead_keys & set(row['strings'])) for row in catalogs)
    assert all(row['source_index']==en['source_index'] for row in catalogs)
    assert 'Preservation graph' not in en['source_index']
    assert 'Preserved binary assets' not in en['source_index']
    assert 'Reachable preserved assets' not in en['source_index']
    assert 'Reachable preserved content' not in en['source_index']
    assert 'Shared preserved bytes' not in en['source_index']


def test_v786_release_documents_current_only_audit_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (7,8,6)
    architecture=(root/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '## 7.8.6 Final current-format storage audit' in architecture
    assert '4.12 preservation architecture (historical; removed in 7.8.1)' in architecture
    store_doc=(root/'docs'/'REVISION_STORE_FORMAT_V1.md').read_text(encoding='utf-8')
    assert 'Breadmaker 7.8.6 keeps' in store_doc
    assert 'current-format-only' in store_doc


def test_v786_dead_persistence_helpers_are_removed():
    import backups.scheduler as scheduler
    import revision_store.local_store as local_store
    import revision_store.remote_history as remote_history
    import revision_store.history_gc as history_gc
    assert not hasattr(scheduler.RecoveryBackupScheduler,'retry_due')
    assert not hasattr(local_store.LocalRevisionStore,'verify_repository')
    assert not hasattr(remote_history.RemoteHistoryRepository,'verified_publications')
    assert not hasattr(history_gc.Segment,'newest_generation')
    assert not hasattr(storage_database,'rowdict')
