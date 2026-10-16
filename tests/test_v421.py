from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]


def test_v421_archived_tasks_are_read_only_and_unarchive_to_prior_state(client, fresh_project):
    task = assert_ok(client.post('/api/tasks', json={'title':'Archive me','description':'Original','status':'done'}))
    assert_ok(client.patch(f"/api/tasks/{task['id']}", json={'status':'archived'}))
    archived = next(t for t in assert_ok(client.get('/api/tasks')) if t['id']==task['id'])
    assert archived['status']=='archived'
    assert archived['archived_from_status']=='done'

    blocked = client.patch(f"/api/tasks/{task['id']}", json={'title':'Should not change'})
    assert blocked.status_code == 409
    blocked_status = client.patch(f"/api/tasks/{task['id']}", json={'status':'open'})
    assert blocked_status.status_code == 409

    assert_ok(client.patch(f"/api/tasks/{task['id']}", json={'status':'done'}))
    restored = next(t for t in assert_ok(client.get('/api/tasks')) if t['id']==task['id'])
    assert restored['status']=='done'
    assert restored['archived_from_status'] is None


def test_v421_revision_restore_preserves_archived_origin(client, fresh_project):
    task = assert_ok(client.post('/api/tasks', json={'title':'Later archive','status':'later'}))
    assert_ok(client.patch(f"/api/tasks/{task['id']}", json={'status':'archived'}))
    revision = assert_ok(client.post('/api/autocommit?label='+__import__('urllib.parse').parse.quote(str('Archived state'))))['id']
    assert_ok(client.patch(f"/api/tasks/{task['id']}", json={'status':'later'}))
    assert_ok(client.post(f'/api/revisions/{revision}/restore'))
    restored = next(t for t in assert_ok(client.get('/api/tasks')) if t['id']==task['id'])
    assert restored['status']=='archived'
    assert restored['archived_from_status']=='later'


def test_v421_static_interaction_contracts():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'id="focusExitBtn"' not in html
    assert 'syncFocusModeControl' in js and "$('#viewFocusBtn')?.addEventListener('click'" in js
    assert 'id="importManuscriptBtn"' not in html
    assert 'id="importManuscriptAction"' in js
    assert 'Save connector' not in js
    assert 'autoSaveEdge(edge)' in js
    assert 'startExistingEdgeTargetDrag' in js
    assert "event.key==='Escape'&&(state.edgeDrag||state.connector)" in js
    assert 'node-resize-handle' in js
    assert '.canvas-stage{' in css and 'overflow:auto' in css
    assert '.node-icon{' in css and 'border-radius:50%' in css
    assert 'archived-task-note' in css
