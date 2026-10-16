from __future__ import annotations
from source_contracts import backend_source,frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]


def test_v910_profiles_partition_visible_projects_and_remember_last_project(client, fresh_project):
    assert tuple(map(int,ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (9,1,0)
    profiles = assert_ok(client.get('/api/profiles'))
    original = profiles['active']
    assert original['id'].startswith('usr_')

    original_workspace = assert_ok(client.get('/api/workspace'))
    original_ids = {item['id'] for item in original_workspace['projects']}
    assert fresh_project['project']['id'] in original_ids

    created = assert_ok(client.post('/api/profiles', json={'display_name':'Editor'}))['profile']
    assert created['id'] != original['id']
    assert created['display_name'] == 'Editor'
    # Creating a profile does not leak or move the current profile's projects.
    assert {item['id'] for item in assert_ok(client.get('/api/workspace'))['projects']} == original_ids

    switched = assert_ok(client.post(f"/api/profiles/{created['id']}/activate"))
    assert switched['reload'] is True
    editor_workspace = assert_ok(client.get('/api/workspace'))
    assert len(editor_workspace['projects']) == 1
    assert editor_workspace['projects'][0]['name'] == 'Untitled Book'
    assert editor_workspace['projects'][0]['owner_user_id'] == created['id']
    editor_blank = editor_workspace['active_project_id']

    editor_book = assert_ok(client.post('/api/projects', json={'name':'Editor Book'}))['project']
    assert editor_book['owner_user_id'] == created['id']
    assert {item['id'] for item in assert_ok(client.get('/api/workspace'))['projects']} == {editor_blank, editor_book['id']}

    assert_ok(client.post(f"/api/profiles/{original['id']}/activate"))
    restored = assert_ok(client.get('/api/workspace'))
    assert {item['id'] for item in restored['projects']} == original_ids
    assert editor_blank not in {item['id'] for item in restored['projects']}
    assert editor_book['id'] not in {item['id'] for item in restored['projects']}


def test_v910_project_ownership_is_nonportable_local_metadata():
    memberships = ROOT.joinpath('src','workspace','memberships.py').read_text(encoding='utf-8')
    constants = ROOT.joinpath('src','workspace','constants.py').read_text(encoding='utf-8')
    app = backend_source(ROOT)
    js = frontend_source_from_app_path(ROOT.joinpath('static','app.js'))

    assert "return Path(root) / 'local.json'" in memberships
    assert "'memberships':normalized" in memberships
    assert "PORTABLE_PROJECT_MANIFEST_KEYS" in constants
    assert "owner_user_id" not in constants.split('PORTABLE_PROJECT_MANIFEST_KEYS = {',1)[1].split('}',1)[0]
    assert "@app.get('/api/profiles')" in app
    assert "@app.post('/api/profiles/{user_id}/activate')" in app
    assert "data-profile-switch" in js
    assert "profile.switch_profile" in js
