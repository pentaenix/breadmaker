from __future__ import annotations

from pathlib import Path

from conftest import assert_ok


ROOT = Path(__file__).resolve().parents[1]


def test_routed_workspace_selection_is_tab_scoped(client, fresh_project):
    first = fresh_project['project']
    second = assert_ok(client.post('/api/projects', json={'name': 'Second tab project'}))['project']
    assert first['id'] != second['id']

    first_workspace = assert_ok(client.get('/api/workspace', headers={
        'X-Breadmaker-Project': first['id'],
    }))
    second_workspace = assert_ok(client.get('/api/workspace', headers={
        'X-Breadmaker-Project': second['id'],
    }))

    assert first_workspace['active_project_id'] == first['id']
    assert second_workspace['active_project_id'] == second['id']


def test_editor_leases_allow_different_projects_but_not_two_tabs_on_one_project(client, fresh_project):
    first = fresh_project['project']
    second = assert_ok(client.post('/api/projects', json={'name': 'Independent tab project'}))['project']
    try:
        assert client.post('/api/lease', headers={
            'X-Breadmaker-Project': first['id'], 'X-Breadmaker-Client': 'tab-a',
        }).status_code == 200
        assert client.post('/api/lease', headers={
            'X-Breadmaker-Project': second['id'], 'X-Breadmaker-Client': 'tab-b',
        }).status_code == 200
        blocked = client.post('/api/lease', headers={
            'X-Breadmaker-Project': first['id'], 'X-Breadmaker-Client': 'tab-c',
        })
        assert blocked.status_code == 409
        assert 'Another Breadmaker browser tab' in blocked.text
    finally:
        client.post('/api/lease/release', headers={
            'X-Breadmaker-Project': first['id'], 'X-Breadmaker-Client': 'tab-a',
        })
        client.post('/api/lease/release', headers={
            'X-Breadmaker-Project': second['id'], 'X-Breadmaker-Client': 'tab-b',
        })


def test_duplicate_process_lock_is_not_misreported_as_recovery():
    pipeline = (ROOT / 'src/backend/request_pipeline.py').read_text(encoding='utf-8')
    launcher = (ROOT / 'run').read_text(encoding='utf-8')
    lock_message = 'Another Breadmaker Editor process is already using this project folder.'

    assert "LOCAL_PROCESS_LOCK_001" in pipeline
    contention = pipeline.index("if isinstance(exc,RuntimeError)")
    recovery = pipeline.index('RECOVERY_BOOT_001', contention)
    assert contention < recovery and lock_message in pipeline[contention:recovery]
    assert 'version-mismatch' in launcher
    assert 'Stop it before starting Breadmaker Editor' in launcher


def test_launcher_does_not_reopen_an_already_running_editor():
    launcher = (ROOT / 'run').read_text(encoding='utf-8')
    existing_branches = [
        line.strip()
        for line in launcher.splitlines()
        if 'Breadmaker Editor is already running:' in line
    ]
    assert len(existing_branches) == 2
    assert all('open_url' not in line for line in existing_branches)
