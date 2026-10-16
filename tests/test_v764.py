from __future__ import annotations

import json
from pathlib import Path

from workspace import Workspace


def test_v764_release_metadata_and_easy_win_module_layout():
    root = Path(__file__).resolve().parents[1]
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    index=(root/'static/index.html').read_text(encoding='utf-8')

    for path in (
        root / 'src/backups/retention.py',
        root / 'src/backups/providers/google_drive.py',
        root / 'src/backups/providers/webdav.py',
        root / 'scripts/package_release.py',
        root / 'scripts/reset_workspace.py',
    ):
        assert path.is_file(), path

    for obsolete in (
        'backup_retention.py', 'google_drive_backup.py', 'webdav_backup.py',
        'package_release.py', 'reset_workspace.py', 'configure_google_drive.py',
        'configure-google-drive', 'product_config.example.json',
    ):
        assert not (root / obsolete).exists(), obsolete

    workspace_boundary = (root / 'src' / 'workspace' / '__init__.py').read_text(encoding='utf-8')
    workspace_providers = (root / 'src' / 'workspace' / 'providers.py').read_text(encoding='utf-8')
    workspace_backups = '\n'.join(path.read_text(encoding='utf-8') for path in sorted((root / 'src' / 'workspace' / 'backups').glob('*.py')))
    assert 'from backups.providers.webdav import' in workspace_providers
    assert 'from backups.providers.google_drive import' in workspace_providers
    assert 'from backups.retention import' in workspace_backups
    assert 'product_config' not in workspace_boundary
    assert 'scripts/reset_workspace.py --confirm' in (root / 'reset').read_text(encoding='utf-8')


def test_v764_user_owned_google_oauth_readme_and_config_contract(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    readme = (root / 'README.md').read_text(encoding='utf-8')
    assert 'your own Google Desktop OAuth Client ID' in readme
    assert 'google_oauth_client.json' in readme
    assert 'publisher owns one Desktop OAuth' not in readme

    ws = Workspace(tmp_path / 'home')
    ws.init()
    monkeypatch.delenv('BREADMAKER_GOOGLE_CLIENT_ID', raising=False)
    assert ws.google_oauth_config()['configured'] is False
    ws.google_oauth_config_path.write_text(json.dumps({'client_id': 'mine.apps.googleusercontent.com'}), encoding='utf-8')
    assert ws.google_oauth_config() == {
        'configured': True,
        'client_id': 'mine.apps.googleusercontent.com',
        'source': 'local',
    }


def test_v764_google_setup_missing_message_is_short_in_all_locales():
    root = Path(__file__).resolve().parents[1]
    catalogs = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((root / 'locales').glob('*.json'))]
    en = next(c for c in catalogs if c['code'] == 'en')
    keys = set(en['strings'])
    assert all(set(c['strings']) == keys for c in catalogs)
    assert all(c['source_index'] == en['source_index'] for c in catalogs)
    for catalog in catalogs:
        message = catalog['strings']['backup.destinations_google_unavailable']
        assert len(message) < 80
        assert 'OAuth' not in message and 'Cloud' not in message and 'API' not in message
