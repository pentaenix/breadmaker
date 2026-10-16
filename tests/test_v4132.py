from __future__ import annotations

from workspace import workspace
from backend import project_activation

from storage import context as storage_context
from storage import mutations as storage_mutations
import storage as storage_package
from storage import undo as storage_undo
from source_contracts import backend_source, storage_source,frontend_source_from_app_path

from pathlib import Path

import pytest
from conftest import assert_ok


def _begin(client, label: str) -> str:
    return assert_ok(client.post(f"/api/undo/begin?label={label.replace(' ', '%20')}"))["token"]


def test_v4132_static_undo_contract_has_no_history_fallback(app_module_fixture):
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    js=frontend_source_from_app_path(root/'static/app.js')
    app=backend_source(root)
    storage_source_text=storage_source(root)

    assert '/api/undo/version' not in app
    assert '/api/undo/restore/' not in app
    assert '/api/undo/restore/' not in js
    assert "if(target.token)" not in js
    assert '_SESSION_UNDO_TTL_SECONDS' not in storage_source_text
    assert 'flushPendingCanvasNodeSaves' in js
    assert 'canvasNodeSaveInFlight' in js
    assert "historyClear('redo');queueNodeSave" in js

    paths={route.path for route in app_module_fixture.app.routes}
    assert '/api/undo/version' not in paths
    assert '/api/undo/restore/{rid}' not in paths


def test_v4132_every_application_table_is_classified_for_structural_undo(client, fresh_project):
    with storage_mutations.tx() as con:
        coverage=storage_undo.session_undo_schema_coverage(con)
    assert coverage['unclassified']==[]
    assert coverage['missing']==[]
    assert 'manuscript_blocks' in coverage['managed']
    assert 'canvas_nodes' in coverage['managed']
    assert 'map_items' in coverage['managed']
    assert 'cuts' in coverage['managed']
    assert 'revisions' in coverage['excluded']
    assert 'checkpoints' in coverage['excluded']
    assert 'ui_state' in coverage['excluded']


def test_v4132_failed_project_switch_does_not_destroy_current_undo_token(client, fresh_project, app_module_fixture):
    chapter=fresh_project['bootstrap']['chapters'][0]
    original_title=chapter['title']
    token=_begin(client,'Rename before failed switch')
    assert_ok(client.patch(f"/api/chapters/{chapter['id']}/title",json={'title':'Changed before failed switch'}))
    assert_ok(client.post(f'/api/undo/finish/{token}'))

    previous=workspace.active()
    bad=workspace.create_project('Intentionally corrupt target',activate=False)
    bad_db=Path(bad['path'])/'project.sqlite3'
    bad_db.write_bytes(b'not a sqlite database')

    with pytest.raises(Exception):
        project_activation.activate_project(bad['id'])

    assert workspace.active_id()==previous['id']
    assert Path(storage_context.PROJECT_ROOT)==Path(previous['path']).resolve()
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=undo'))
    restored=next(ch for ch in assert_ok(client.get('/api/bootstrap'))['chapters'] if ch['id']==chapter['id'])
    assert restored['title']==original_title


def test_v4132_successful_project_switch_invalidates_session_tokens(client, fresh_project, app_module_fixture):
    chapter=fresh_project['bootstrap']['chapters'][0]
    token=_begin(client,'Token before successful switch')
    assert_ok(client.patch(f"/api/chapters/{chapter['id']}/title",json={'title':'Temporary title'}))
    assert_ok(client.post(f'/api/undo/finish/{token}'))
    original=workspace.active()

    other=workspace.create_project('Successful switch target',activate=False)
    project_activation.activate_project(other['id'])
    project_activation.activate_project(original['id'])

    failed=client.post(f'/api/undo/apply/{token}?direction=undo')
    assert failed.status_code==409
    assert 'no longer available' in failed.text


def test_v4132_history_kind_undo_is_rejected_and_legacy_rows_are_not_visible(client, fresh_project):
    with storage_mutations.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    with pytest.raises(ValueError,match='History-based structural Undo was removed'):
        storage_package.commit_revision(pid,'obsolete','undo',False)
    with storage_mutations.tx() as con:
        assert con.execute("SELECT COUNT(*) FROM revisions WHERE kind='undo'").fetchone()[0]==0


def test_v4132_release_packager_excludes_runtime_state():
    from scripts.package_release import should_include
    assert not should_include(Path('_unconfigured/project.sqlite3'))
    assert not should_include(Path('.pytest_cache/v/cache/nodeids'))
    assert not should_include(Path('tests/__pycache__/test_v4132.pyc'))
    assert not should_include(Path('project.sqlite3-wal'))
    assert not should_include(Path('breadmaker_editor.lock'))
    assert should_include(Path('static/app.js'))
    assert should_include(Path('tests/test_v4132.py'))
