from __future__ import annotations
from source_contracts import frontend_source_from_app_path

import json
from pathlib import Path

import pytest

import app as app_module
from api.durability.backup_destinations import GoogleDriveOAuthStart
from workspace import Workspace, WorkspaceError


def test_v765_release_metadata_and_google_client_id_field_contract():
    root=Path(__file__).resolve().parents[1]
    # Release metadata is owned by the newest version test; retain the 7.6.5 UI/config contract here.
    appjs=frontend_source_from_app_path(root/'static/app.js')
    assert 'id="backupGoogleClientId"' in appjs
    assert "backup.destinations_google_client_id" in appjs
    assert "client_id:clientId" in appjs
    assert 'backup.destinations_google_connect_help' not in appjs


def test_v765_google_client_id_is_saved_locally_and_reused(monkeypatch,tmp_path):
    monkeypatch.delenv('BREADMAKER_GOOGLE_CLIENT_ID',raising=False)
    ws=Workspace(tmp_path/'home');ws.init();ws.create_project('Drive setup',activate=True)
    assert ws.google_oauth_config()['configured'] is False
    flow=ws.begin_google_drive_oauth(
        'http://127.0.0.1:8765/api/backup-destinations/google-drive/oauth/callback',
        client_id='writer.apps.googleusercontent.com',
        client_secret='GOCSPX-test-secret',
    )
    assert 'client_id=writer.apps.googleusercontent.com' in flow['authorization_url']
    saved=json.loads(ws.google_oauth_config_path.read_text(encoding='utf-8'))
    assert saved=={'version':1,'client_id':'writer.apps.googleusercontent.com'}
    assert ws.google_oauth_config()['client_id']=='writer.apps.googleusercontent.com'
    second=ws.begin_google_drive_oauth('http://127.0.0.1:8765/api/backup-destinations/google-drive/oauth/callback',client_secret='GOCSPX-test-secret')
    assert 'client_id=writer.apps.googleusercontent.com' in second['authorization_url']


def test_v765_google_client_id_validation_and_api_schema(tmp_path):
    ws=Workspace(tmp_path/'home');ws.init()
    with pytest.raises(WorkspaceError,match='GOOGLE_CLIENT_ID_INVALID'):
        ws.save_google_oauth_client_id('bad id with spaces')
    fields=getattr(GoogleDriveOAuthStart,'model_fields',None) or getattr(GoogleDriveOAuthStart,'__fields__',{})
    assert 'client_id' in fields


def test_v765_google_client_id_label_and_locale_parity():
    root=Path(__file__).resolve().parents[1]
    catalogs=[json.loads(path.read_text(encoding='utf-8')) for path in sorted((root/'locales').glob('*.json'))]
    en=next(c for c in catalogs if c['code']=='en')
    keys=set(en['strings'])
    assert all(set(c['strings'])==keys for c in catalogs)
    assert all(c['source_index']==en['source_index'] for c in catalogs)
    assert en['strings']['backup.destinations_google_client_id']=='OAuth Client ID'
