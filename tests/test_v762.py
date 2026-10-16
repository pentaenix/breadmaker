from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from workspace import workspace

import json
import os
import zipfile
from pathlib import Path

import pytest

import app as app_module
from api.durability.backup_destinations import GoogleDriveOAuthStart
from workspace import Workspace


def test_v762_minimal_google_ui_and_locale_parity():
    root = Path(__file__).resolve().parents[1]
    appjs = frontend_source_from_app_path(root / 'static/app.js')
    assert 'backupGoogleClientId' in appjs
    assert 'client_id:clientId' in appjs
    assert 'backup.destinations_google_connect_help' not in appjs
    assert 'client_secret:clientSecret' in appjs
    assert 'backupGoogleClientSecret' in appjs

    catalogs = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((root / 'locales').glob('*.json'))]
    en = next(c for c in catalogs if c['code'] == 'en')
    keys = set(en['strings'])
    assert all(set(c['strings']) == keys for c in catalogs)
    assert all(c['source_index'] == en['source_index'] for c in catalogs)
    assert 'backup.destinations_google_unavailable' in keys
    assert 'backup.destinations_google_client_id' in keys
    assert 'backup.destinations_google_client_id_help' not in keys
    assert 'backup.destinations_google_connect_help' not in keys
    assert en['strings']['backup.destinations_google_connect'] == 'Connect'
    assert en['strings']['backup.destinations_google_reconnect'] == 'Reconnect'


def test_v762_google_oauth_uses_installation_local_user_config(monkeypatch, tmp_path):
    ws = Workspace(tmp_path / 'home')
    ws.init()
    monkeypatch.delenv('BREADMAKER_GOOGLE_CLIENT_ID', raising=False)

    assert ws.google_oauth_config() == {'configured': False, 'client_id': '', 'source': 'none'}

    ws.google_oauth_config_path.parent.mkdir(parents=True, exist_ok=True)
    ws.google_oauth_config_path.write_text(
        json.dumps({'version': 1, 'client_id': 'writer.apps.googleusercontent.com'}),
        encoding='utf-8',
    )
    local = ws.google_oauth_config()
    assert local == {'configured': True, 'client_id': 'writer.apps.googleusercontent.com', 'source': 'local'}

    monkeypatch.setenv('BREADMAKER_GOOGLE_CLIENT_ID', 'development.apps.googleusercontent.com')
    environment = ws.google_oauth_config()
    assert environment == {'configured': True, 'client_id': 'development.apps.googleusercontent.com', 'source': 'environment'}


def test_v762_oauth_start_accepts_user_owned_client_id_and_remembers_it(monkeypatch, tmp_path):
    ws = Workspace(tmp_path / 'home')
    ws.init()
    ws.create_project('Local OAuth', activate=True)
    monkeypatch.delenv('BREADMAKER_GOOGLE_CLIENT_ID', raising=False)

    flow = ws.begin_google_drive_oauth(
        'http://127.0.0.1:8765/api/backup-destinations/google-drive/oauth/callback',
        client_id='writer.apps.googleusercontent.com',
        client_secret='GOCSPX-test-secret',
    )
    assert 'client_id=writer.apps.googleusercontent.com' in flow['authorization_url']
    assert ws.google_oauth_config()['client_id'] == 'writer.apps.googleusercontent.com'

    fields = getattr(GoogleDriveOAuthStart, 'model_fields', None) or getattr(GoogleDriveOAuthStart, '__fields__', {})
    assert 'client_id' in fields


def test_v762_google_oauth_config_is_not_project_or_download_my_data_state(tmp_path, fresh_project):
    ws = workspace
    ws.google_oauth_config_path.write_text(json.dumps({'version': 1, 'client_id': 'writer.apps.googleusercontent.com'}), encoding='utf-8')

    package = tmp_path / 'project.breadmaker'
    ws.export_project(fresh_project['project']['id'], package)
    with zipfile.ZipFile(package) as zf:
        assert 'google_oauth_client.json' not in zf.namelist()
        manifest = json.loads(zf.read('manifest.json'))
    assert 'writer.apps.googleusercontent.com' not in json.dumps(manifest)

    all_data = tmp_path / 'all-data.zip'
    ws.export_all_data(all_data)
    with zipfile.ZipFile(all_data) as zf:
        assert 'google_oauth_client.json' not in zf.namelist()
        assert 'backup_credentials.json' not in zf.namelist()


def test_v762_obsolete_publisher_google_configuration_is_gone():
    root = Path(__file__).resolve().parents[1]
    assert not (root / 'configure_google_drive.py').exists()
    assert not (root / 'configure-google-drive').exists()
    assert not (root / 'product_config.example.json').exists()
    source = (root / 'src' / 'workspace' / '__init__.py').read_text(encoding='utf-8')
    assert 'product_config_path' not in source
    assert 'publisher-owned Google OAuth' not in source
