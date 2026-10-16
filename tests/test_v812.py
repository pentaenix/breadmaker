from __future__ import annotations

from source_contracts import frontend_styles_source_from_styles_path
from workspace import workspace
from backend import project_activation

from storage import context as storage_context
from storage import database as storage_database

import sqlite3
from pathlib import Path

import pytest

import app as app_module
ROOT=Path(__file__).resolve().parents[1]


def _downgrade_current_project_to_schema22(root: Path) -> None:
    storage_database.release_instance_lock()
    db=root/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute('DROP INDEX IF EXISTS idx_reviews_project')
        con.execute('DROP TABLE IF EXISTS reviews')
        con.execute("UPDATE storage_meta SET value='22' WHERE key='schema_version'")
        con.commit()
    finally:
        con.close()
    manifest=workspace.read_manifest(root)
    manifest['schema_version']=22
    workspace._write_manifest(root,manifest)


def test_v812_release_brand_asset_and_cache_busters():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,1,2)
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    assert 'class="brand-mark" aria-hidden="true"' in html
    assert '>B</span>' not in html
    assert ROOT.joinpath('static','breadmaker_logo.svg').is_file()


def test_v812_old_schema_is_rejected_without_mutation(client,fresh_project):
    rec=fresh_project['project']; root=Path(rec['path'])
    _downgrade_current_project_to_schema22(root)
    before_manifest=(root/'manifest.json').read_bytes()
    before_db=(root/'project.sqlite3').read_bytes()

    with pytest.raises(RuntimeError,match='PREALPHA_PROJECT_INCOMPATIBLE'):
        project_activation.activate_project(rec['id'])

    assert (root/'manifest.json').read_bytes()==before_manifest
    assert (root/'project.sqlite3').read_bytes()==before_db
    assert storage_context.inspect_project_schema(root)==22


def test_v812_registry_does_not_register_old_schema(client,fresh_project):
    rec=fresh_project['project']; root=Path(rec['path'])
    _downgrade_current_project_to_schema22(root)
    workspace.init()
    assert all(row['id']!=rec['id'] for row in workspace.list_projects())
    assert any(row['id']==rec['id'] for row in workspace.recovery_candidates())


def test_v812_delete_all_api_remains_project_independent(client,fresh_project):
    response=client.post('/api/danger/challenge',json={'action':'delete_all_data'})
    assert response.status_code==200
    assert len(response.json()['code'])==10
