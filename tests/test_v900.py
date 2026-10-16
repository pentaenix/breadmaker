from __future__ import annotations
from source_contracts import backend_source,frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]


def test_v900_local_profile_identity_persists_across_projects(client, fresh_project):
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (9, 0, 0)
    first = assert_ok(client.get('/api/profile'))
    assert first['id'].startswith('usr_')
    assert first['kind'] == 'local'
    assert first['display_name'] == 'Writer'
    assert first['has_avatar'] is False

    updated = assert_ok(client.put('/api/profile', json={'display_name': 'Ada Lovelace'}))
    assert updated['id'] == first['id']
    assert updated['display_name'] == 'Ada Lovelace'

    assert_ok(client.post('/api/projects', json={'name': 'Another Book'}))
    after_project_change = assert_ok(client.get('/api/profile'))
    assert after_project_change['id'] == first['id']
    assert after_project_change['display_name'] == 'Ada Lovelace'


def test_v900_profile_is_installation_state_and_line_avatar_is_name_seeded():
    workspace = '\n'.join((
        ROOT.joinpath('src','workspace','service.py').read_text(encoding='utf-8'),
        ROOT.joinpath('src','workspace','profiles.py').read_text(encoding='utf-8'),
    ))
    app = backend_source(ROOT)
    js = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))

    assert 'self.profiles_dir = self.root / "profiles"' in workspace
    assert 'self.profiles_index_path = self.profiles_dir / "index.json"' in workspace
    assert "f'usr_{uuid.uuid4().hex}'" in workspace
    assert "max(dimensions)>512" in workspace
    assert "@app.get('/api/profile')" in app
    assert "@app.post('/api/profile/avatar')" in app
    assert 'function userAvatarLineSvg(name)' in js
    assert 'userAvatarSeed(name)' in js
    assert "USER_AVATAR_SEED_KEY" not in js
    assert "localProfileLogout" in js
