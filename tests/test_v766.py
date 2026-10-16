from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from workspace import workspace

import json
import os
import stat
import urllib.parse
import zipfile
from pathlib import Path

import app as app_module
from api.durability.backup_destinations import GoogleDriveOAuthStart
from workspace import Workspace
from test_v761 import _google_server, _patch_google


SECRET='GOCSPX-v766-test-secret'


def test_v766_release_metadata_and_secret_ui_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (7,6,6)
    index=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    appjs=frontend_source_from_app_path(root/'static/app.js')
    assert 'id="backupGoogleClientSecret" type="password"' in appjs
    assert 'backup.destinations_google_client_secret' in appjs
    assert 'client_secret:clientSecret' in appjs
    assert 'backup.destinations_google_connect_help' not in appjs
    fields=getattr(GoogleDriveOAuthStart,'model_fields',None) or getattr(GoogleDriveOAuthStart,'__fields__',{})
    assert {'client_id','client_secret'} <= set(fields)


def test_v766_secret_is_not_in_authorization_url_and_is_scrubbed_after_oauth(monkeypatch,fresh_project):
    server,state,base=_google_server();state.client_secret=SECRET
    try:
        _patch_google(monkeypatch,base)
        ws=workspace
        flow=ws.begin_google_drive_oauth(
            'http://127.0.0.1:8765/api/backup-destinations/google-drive/oauth/callback',
            project_id=fresh_project['project']['id'],client_secret=SECRET,
        )
        assert SECRET not in flow['authorization_url']
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(flow['authorization_url']).query)
        assert 'client_secret' not in query
        state_id=flow['state']
        # Pending flow needs the secret only long enough to exchange Google's code.
        assert ws._google_oauth_flows[state_id]['client_secret']==SECRET
        destination=ws.complete_google_drive_oauth(state_id,'good-code')['destination']
        retained=ws._google_oauth_flows[state_id]
        assert retained['status']=='success'
        assert 'client_secret' not in retained and 'code_verifier' not in retained
        # Resolution forces a refresh-token request; the fake Google endpoint rejects it
        # unless Breadmaker supplies the same Client Secret there too.
        row=next(x for x in ws.list_backup_destinations() if x['id']==destination['id'])
        assert row['available'] is True
    finally:
        server.shutdown();server.server_close()


def test_v766_client_secret_is_obscured_at_rest_and_never_exported(monkeypatch,fresh_project,tmp_path):
    server,state,base=_google_server();state.client_secret=SECRET
    try:
        _patch_google(monkeypatch,base)
        ws=workspace
        flow=ws.begin_google_drive_oauth(
            'http://127.0.0.1:8765/api/backup-destinations/google-drive/oauth/callback',
            project_id=fresh_project['project']['id'],client_secret=SECRET,
        )
        destination=ws.complete_google_drive_oauth(flow['state'],'good-code')['destination']
        manifest=ws.read_manifest(Path(fresh_project['project']['path']))
        stored=next(x for x in manifest['backup_destinations'] if x['id']==destination['id'])
        disk=ws.backup_credentials_path.read_text(encoding='utf-8')
        parsed=json.loads(disk)
        assert parsed['version']==2 and 'sealed-v2:' in disk
        assert SECRET not in disk and 'refresh-good-code' not in disk
        raw=ws._load_backup_credentials()[stored['credential_id']]
        assert raw['client_secret']==SECRET
        runtime=ws._backup_credential(stored['credential_id'])
        assert runtime['client_secret']==SECRET
        assert ws.credential_encryption_key_path.is_file()
        if os.name!='nt':
            assert stat.S_IMODE(ws.backup_credentials_path.stat().st_mode)==0o600
            assert stat.S_IMODE(ws.credential_encryption_key_path.stat().st_mode)==0o600
        package=tmp_path/'portable.breadmaker';ws.export_project(fresh_project['project']['id'],package)
        with zipfile.ZipFile(package) as zf:
            package_text='\n'.join(zf.namelist())+'\n'+zf.read('manifest.json').decode('utf-8')
        assert SECRET not in package_text and 'client_secret_obscured' not in package_text
        all_data=tmp_path/'all-data.zip';ws.export_all_data(all_data)
        with zipfile.ZipFile(all_data) as zf:
            names=set(zf.namelist())
            assert 'backup_credentials.json' not in names
            assert 'credential_encryption.key' not in names and 'credential_obfuscation.key' not in names
            assert 'google_oauth_client.json' not in names
    finally:
        server.shutdown();server.server_close()


def test_v766_reconnect_can_reuse_saved_secret_without_returning_it(monkeypatch,fresh_project):
    server,state,base=_google_server();state.client_secret=SECRET
    try:
        _patch_google(monkeypatch,base)
        ws=workspace
        first=ws.begin_google_drive_oauth(
            'http://127.0.0.1:8765/api/backup-destinations/google-drive/oauth/callback',
            project_id=fresh_project['project']['id'],client_secret=SECRET,
        )
        destination=ws.complete_google_drive_oauth(first['state'],'good-code')['destination']
        config=ws.google_oauth_config()
        assert set(config)=={'configured','client_id','source'} and SECRET not in json.dumps(config)
        # Reconnect may leave the masked secret box blank; the backend reuses the secret
        # attached to this already-authorized destination without exposing it to the UI.
        second=ws.begin_google_drive_oauth(
            'http://127.0.0.1:8765/api/backup-destinations/google-drive/oauth/callback',
            project_id=fresh_project['project']['id'],destination_id=destination['id'],
        )
        assert SECRET not in second['authorization_url']
        assert ws._google_oauth_flows[second['state']]['client_secret']==SECRET
    finally:
        server.shutdown();server.server_close()


def test_v766_locale_and_release_exclusion_contract():
    root=Path(__file__).resolve().parents[1]
    catalogs=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((root/'locales').glob('*.json'))]
    en=next(c for c in catalogs if c['code']=='en');keys=set(en['strings'])
    assert en['strings']['backup.destinations_google_client_secret']=='OAuth Client Secret'
    assert all(set(c['strings'])==keys for c in catalogs)
    assert all(c['source_index']==en['source_index'] for c in catalogs)
    packager=(root/'scripts/package_release.py').read_text(encoding='utf-8')
    assert "'credential_encryption.key'" in packager and "'credential_obfuscation.key'" in packager
