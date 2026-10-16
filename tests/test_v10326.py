from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v10326_release_schema_and_review_plan_are_in_sync():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,26)
    assert 'SCHEMA_VERSION = 42' in (ROOT/'src/storage/schema.py').read_text(encoding='utf-8')
    assert 'APP_SCHEMA_VERSION = 42' in (ROOT/'src/workspace/constants.py').read_text(encoding='utf-8')


def test_v10326_every_new_project_has_one_immutable_workspace_database_manifest_identity(fresh_project):
    rec=fresh_project['project']
    project_id=str(rec['id'])
    assert re.fullmatch(r'[0-9a-f]{32}',project_id)
    root=Path(rec['path'])
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['project_id']==project_id
    con=sqlite3.connect(root/'project.sqlite3')
    try:
        rows=con.execute('SELECT project_uid FROM projects ORDER BY id').fetchall()
    finally:
        con.close()
    assert rows==[(project_id,)]


def test_v10326_schema_42_is_current_only_with_no_pre_alpha_migration_path():
    database=(ROOT/'src/storage/database.py').read_text(encoding='utf-8')
    schema=(ROOT/'src/storage/schema.py').read_text(encoding='utf-8')
    assert 'project_uid TEXT NOT NULL UNIQUE' in schema
    assert "version!=SCHEMA_VERSION" in database
    assert "PREALPHA_PROJECT_INCOMPATIBLE" in database
    assert 'ALTER TABLE projects ADD COLUMN project_uid' not in database
    assert 'PROJECT_IDENTITY_MISMATCH' in database


def test_v10326_hosted_project_routes_are_stable_and_project_explorer_is_not_added():
    routes=(ROOT/'static/js/core/routing.js').read_text(encoding='utf-8')
    boot=(ROOT/'static/js/core/boot.js').read_text(encoding='utf-8')
    runtime_routes=(ROOT/'src/backend/runtime_routes.py').read_text(encoding='utf-8')
    lifecycle=(ROOT/'static/js/features/projects/lifecycle.js').read_text(encoding='utf-8')
    assert "PROJECT_ROUTE_RE=/^\\/p\\/([0-9a-f]{32})" in routes
    assert "return `/p/${pid}${VIEW_ROUTES[view]||''}`" in routes
    for suffix in ('canvas','maps','tasks','reviews'):
        assert f'/p/{{project_id}}/{suffix}' in runtime_routes
    assert '/p/{project_id}' in runtime_routes
    assert "bootHostedBlankProject()" in boot
    assert "createHostedProjectFromBlankEditor" in boot
    assert "sessionStorage.setItem(HOSTED_FIRST_DRAFT_KEY" in boot
    assert "openWorkspaceProject" in lifecycle
    assert '/projects' not in routes


def test_v10326_zero_project_hosted_state_does_not_create_on_auth_callback():
    routes=(ROOT/'src/backend/runtime_routes.py').read_text(encoding='utf-8')
    delete=(ROOT/'src/api/project_management/destructive_actions.py').read_text(encoding='utf-8')
    assert "runtime.projects.create('Untitled Book'" not in routes
    assert "if not target and not runtime.sessions.requires_authentication:" in delete
    assert "active_project_id\":target or ''" in delete
