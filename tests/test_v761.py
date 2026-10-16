from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from workspace import workspace

from storage import context as storage_context
from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import values as storage_values

import json
import os
import re
import sqlite3
import stat
import threading
import time
import urllib.parse
import zipfile

import pytest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import app as app_module
from backups.providers import google_drive as gdrive
from backups.retention import RECOVERY_RETENTION_POLICIES, recovery_retention_partition
from workspace import Workspace


class _GoogleState:
    def __init__(self):
        self.lock = threading.RLock()
        self.files: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.counter = 0
        self.auth_codes = {'good-code'}
        self.refresh_tokens = {'refresh-1'}
        self.access_tokens = {'access-1'}
        self.client_secret = 'GOCSPX-test-secret'
        self.fail_upload = False
        self.fail_download = False
        self.fail_list = False

    def ident(self, prefix='file'):
        with self.lock:
            self.counter += 1
            return f'{prefix}-{self.counter}'


def _google_server():
    state = _GoogleState()

    def now_iso():
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    def public(row: dict) -> dict:
        result = {k: v for k, v in row.items() if k != 'content'}
        if 'size' in result:
            result['size'] = str(result['size'])
        return result

    def prop_matches(row: dict, q: str) -> bool:
        props = row.get('appProperties') if isinstance(row.get('appProperties'), dict) else {}
        for key, value in re.findall(r"appProperties has \{ key='((?:\\.|[^'])*)' and value='((?:\\.|[^'])*)' \}", q):
            key = key.replace("\\'", "'").replace('\\\\', '\\')
            value = value.replace("\\'", "'").replace('\\\\', '\\')
            if str(props.get(key) or '') != value:
                return False
        return True

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'BreadmakerGoogleFake/1.0'

        def log_message(self, *args):
            pass

        def _raw(self) -> bytes:
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = 0
            return self.rfile.read(length) if length else b''

        def _send(self, status: int, body: bytes = b'', content_type='application/json', headers=None):
            self.send_response(status)
            if content_type:
                self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, status: int, payload: dict, headers=None):
            self._send(status, json.dumps(payload).encode('utf-8'), headers=headers)

        def _error(self, status: int, reason: str):
            self._json(status, {'error': {'errors': [{'reason': reason}], 'message': reason}})

        def _authorized(self) -> bool:
            token = self.headers.get('Authorization', '').removeprefix('Bearer ').strip()
            if token not in state.access_tokens:
                self._error(401, 'invalidCredentials')
                return False
            return True

        def do_POST(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == '/token':
                body = urllib.parse.parse_qs(self._raw().decode('ascii'))
                grant = (body.get('grant_type') or [''])[0]
                if (body.get('client_secret') or [''])[0] != state.client_secret:
                    self._json(400, {'error': 'invalid_request', 'error_description': 'client_secret is missing or invalid'})
                    return
                if grant == 'authorization_code':
                    code = (body.get('code') or [''])[0]
                    if code not in state.auth_codes or not (body.get('code_verifier') or [''])[0]:
                        self._json(400, {'error': 'invalid_grant'})
                        return
                    self._json(200, {'access_token': 'access-1', 'refresh_token': 'refresh-1', 'expires_in': 3600, 'token_type': 'Bearer'})
                    return
                if grant == 'refresh_token':
                    refresh = (body.get('refresh_token') or [''])[0]
                    if refresh not in state.refresh_tokens:
                        self._json(400, {'error': 'invalid_grant'})
                        return
                    self._json(200, {'access_token': 'access-1', 'expires_in': 3600, 'token_type': 'Bearer'})
                    return
                self._json(400, {'error': 'unsupported_grant_type'})
                return

            if parsed.path == '/drive/v3/files':
                if not self._authorized():
                    return
                body = json.loads(self._raw() or b'{}')
                fid = state.ident('folder')
                stamp = now_iso()
                row = {
                    'id': fid,
                    'name': str(body.get('name') or ''),
                    'mimeType': str(body.get('mimeType') or 'application/octet-stream'),
                    'parents': list(body.get('parents') or []),
                    'trashed': False,
                    'size': 0,
                    'createdTime': stamp,
                    'modifiedTime': stamp,
                    'appProperties': dict(body.get('appProperties') or {}),
                    'content': b'',
                }
                with state.lock:
                    state.files[fid] = row
                self._json(200, public(row))
                return

            if parsed.path == '/upload/drive/v3/files':
                if not self._authorized():
                    return
                if state.fail_upload:
                    self._error(403, 'storageQuotaExceeded')
                    return
                params = urllib.parse.parse_qs(parsed.query)
                if (params.get('uploadType') or [''])[0] != 'resumable':
                    self._error(400, 'badUploadType')
                    return
                metadata = json.loads(self._raw() or b'{}')
                sid = state.ident('session')
                with state.lock:
                    state.sessions[sid] = {'metadata': metadata, 'data': bytearray(), 'total': int(self.headers.get('X-Upload-Content-Length') or 0)}
                endpoint = f'http://127.0.0.1:{self.server.server_address[1]}/upload-session/{sid}'
                self._send(200, b'', headers={'Location': endpoint})
                return

            self._error(404, 'notFound')

        def do_PUT(self):
            parsed = urllib.parse.urlsplit(self.path)
            if not parsed.path.startswith('/upload-session/'):
                self._error(404, 'notFound')
                return
            if not self._authorized():
                return
            sid = parsed.path.rsplit('/', 1)[-1]
            with state.lock:
                session = state.sessions.get(sid)
            if session is None:
                self._error(404, 'notFound')
                return
            if state.fail_upload:
                self._error(403, 'storageQuotaExceeded')
                return
            content_range = self.headers.get('Content-Range', '')
            if content_range.startswith('bytes */'):
                received = len(session['data'])
                if received >= session['total'] and session.get('file_id'):
                    with state.lock:
                        row = state.files[session['file_id']]
                    self._json(200, public(row))
                else:
                    headers = {'Range': f'bytes=0-{received - 1}'} if received else {}
                    self._send(308, b'', headers=headers)
                return
            match = re.match(r'bytes (\d+)-(\d+)/(\d+)', content_range)
            if not match:
                self._error(400, 'badContentRange')
                return
            start, end, total = map(int, match.groups())
            chunk = self._raw()
            with state.lock:
                current = len(session['data'])
                if start != current or len(chunk) != end - start + 1:
                    self._error(400, 'badOffset')
                    return
                session['data'].extend(chunk)
                if len(session['data']) < total:
                    self._send(308, b'', headers={'Range': f'bytes=0-{len(session["data"]) - 1}'})
                    return
                metadata = session['metadata']
                fid = state.ident('package')
                stamp = now_iso()
                row = {
                    'id': fid,
                    'name': str(metadata.get('name') or ''),
                    'mimeType': str(metadata.get('mimeType') or 'application/octet-stream'),
                    'parents': list(metadata.get('parents') or []),
                    'trashed': False,
                    'size': len(session['data']),
                    'createdTime': stamp,
                    'modifiedTime': stamp,
                    'appProperties': dict(metadata.get('appProperties') or {}),
                    'content': bytes(session['data']),
                }
                state.files[fid] = row
                session['file_id'] = fid
            self._json(200, public(row))

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if not parsed.path.startswith('/drive/v3/files'):
                self._error(404, 'notFound')
                return
            if not self._authorized():
                return
            if state.fail_list:
                self._error(503, 'backendError')
                return
            if parsed.path == '/drive/v3/files':
                q = (urllib.parse.parse_qs(parsed.query).get('q') or [''])[0]
                with state.lock:
                    rows = list(state.files.values())
                filtered = []
                parent_match = re.search(r"'((?:\\.|[^'])*)' in parents", q)
                name_match = re.search(r"name='((?:\\.|[^'])*)'", q)
                for row in rows:
                    if 'trashed=false' in q and row.get('trashed'):
                        continue
                    if "mimeType='application/vnd.google-apps.folder'" in q and row.get('mimeType') != gdrive.GOOGLE_FOLDER_MIME:
                        continue
                    if parent_match:
                        parent = parent_match.group(1).replace("\\'", "'").replace('\\\\', '\\')
                        if parent not in row.get('parents', []):
                            continue
                    if name_match:
                        name = name_match.group(1).replace("\\'", "'").replace('\\\\', '\\')
                        if row.get('name') != name:
                            continue
                    if not prop_matches(row, q):
                        continue
                    filtered.append(public(row))
                self._json(200, {'files': filtered})
                return
            fid = urllib.parse.unquote(parsed.path.rsplit('/', 1)[-1])
            with state.lock:
                row = state.files.get(fid)
            if row is None:
                self._error(404, 'notFound')
                return
            params = urllib.parse.parse_qs(parsed.query)
            if (params.get('alt') or [''])[0] == 'media':
                if state.fail_download:
                    self._error(503, 'backendError')
                    return
                self._send(200, row.get('content', b''), content_type='application/zip')
                return
            self._json(200, public(row))

        def do_PATCH(self):
            parsed = urllib.parse.urlsplit(self.path)
            if not parsed.path.startswith('/drive/v3/files/'):
                self._error(404, 'notFound')
                return
            if not self._authorized():
                return
            fid = urllib.parse.unquote(parsed.path.rsplit('/', 1)[-1])
            body = json.loads(self._raw() or b'{}')
            with state.lock:
                row = state.files.get(fid)
                if row is None:
                    self._error(404, 'notFound')
                    return
                if isinstance(body.get('appProperties'), dict):
                    row['appProperties'] = dict(body['appProperties'])
                row['modifiedTime'] = now_iso()
                result = public(row)
            self._json(200, result)

        def do_DELETE(self):
            parsed = urllib.parse.urlsplit(self.path)
            if not parsed.path.startswith('/drive/v3/files/'):
                self._error(404, 'notFound')
                return
            if not self._authorized():
                return
            fid = urllib.parse.unquote(parsed.path.rsplit('/', 1)[-1])
            with state.lock:
                existed = fid in state.files
                state.files.pop(fid, None)
            self._send(204 if existed else 404, b'')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    # HTTP/1.1 clients may keep a request thread alive briefly after an assertion
    # completes. Test shutdown must never wait on those connection-handler threads;
    # the production client lifecycle is tested independently.
    server.daemon_threads = True
    server.block_on_close = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    return server, state, base


def _patch_google(monkeypatch, base: str):
    monkeypatch.setenv('BREADMAKER_GOOGLE_CLIENT_ID', 'breadmaker-test.apps.googleusercontent.com')
    monkeypatch.setattr(gdrive, 'GOOGLE_AUTHORIZATION_ENDPOINT', base + '/authorize')
    monkeypatch.setattr(gdrive, 'GOOGLE_TOKEN_ENDPOINT', base + '/token')
    monkeypatch.setattr(gdrive, 'GOOGLE_DRIVE_API_ROOT', base + '/drive/v3')
    monkeypatch.setattr(gdrive, 'GOOGLE_DRIVE_UPLOAD_ROOT', base + '/upload/drive/v3')


def _connect_google(workspace: Workspace, monkeypatch, base: str, *, project_id=None, destination_id=None):
    _patch_google(monkeypatch, base)
    flow = workspace.begin_google_drive_oauth(
        'http://127.0.0.1:8765/api/backup-destinations/google-drive/oauth/callback',
        destination_id=destination_id,
        project_id=project_id,
        client_secret='GOCSPX-test-secret',
    )
    result = workspace.complete_google_drive_oauth(flow['state'], 'good-code')
    return result['destination']


def _google_packages(state: _GoogleState, project_id: str | None = None):
    with state.lock:
        rows = [dict(row) for row in state.files.values() if (row.get('appProperties') or {}).get('breadmaker_kind') == 'backup_package']
    if project_id:
        rows = [row for row in rows if (row.get('appProperties') or {}).get('breadmaker_project_id') == project_id]
    return rows


@pytest.fixture(autouse=True)
def _isolate_app_google_destinations(client):
    """Do not let one fake-Drive test leak a dead endpoint into the next lifespan.

    The application workspace is session-global in the test harness while each
    ``client`` fixture owns a new application lifespan. A Google destination created
    in one test therefore outlives its fake HTTP server unless we remove the
    destination before that lifespan shuts down. On the next startup the real backup
    coordinator would correctly probe the still-configured destination, but the test
    monkeypatch has already been restored and that probe would target Google's real
    endpoint. This cleanup is test isolation only: it removes configuration/credentials
    after each assertion while deliberately leaving provider-side backup bytes alone.
    """
    yield
    ws = workspace
    for project in list(ws.list_projects()):
        try:
            root = Path(project['path'])
            for destination in list(ws._backup_destinations(root)):
                if str(destination.get('provider') or '') == 'google_drive':
                    ws.remove_backup_destination(str(destination['id']), str(project['id']))
        except Exception:
            # Cleanup must not hide the test result; an individual removal contract is
            # covered explicitly below. Any surviving destination will be caught by the
            # next lifecycle/test timeout gate.
            pass


def test_v761_release_google_contract_locale_and_security_surface():
    root = Path(__file__).resolve().parents[1]
    # Release metadata is owned by the newest version test; retain the 7.6.1 security/provider contract here.
    index = (root / 'static/index.html').read_text(encoding='utf-8')
    source = (root / 'src/backups/providers/google_drive.py').read_text(encoding='utf-8')
    assert "GOOGLE_DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.file'" in source
    assert 'code_challenge_method' in source and "'S256'" in source
    assert 'service_account' not in source.lower() and 'api_key' not in source.lower()
    appjs = frontend_source_from_app_path(root / 'static/app.js')
    assert 'promptGoogleDriveConnection' in appjs and '/api/backup-destinations/google-drive/oauth/start' in appjs
    catalogs = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((root / 'locales').glob('*.json'))]
    en = next(c for c in catalogs if c['code'] == 'en'); keys = set(en['strings'])
    assert {'backup.destinations_type_google', 'backup.destinations_google_connect', 'backup.destinations_cloud_retention'} <= keys
    assert all(set(c['strings']) == keys for c in catalogs)
    assert all(c['source_index'] == en['source_index'] for c in catalogs)


def test_v761_oauth_uses_pkce_offline_drive_file_and_local_secret_storage(monkeypatch, fresh_project, tmp_path):
    server, state, base = _google_server()
    try:
        _patch_google(monkeypatch, base)
        ws = workspace
        flow = ws.begin_google_drive_oauth(
            'http://127.0.0.1:8765/api/backup-destinations/google-drive/oauth/callback',
                project_id=fresh_project['project']['id'],
                client_secret='GOCSPX-test-secret',
        )
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(flow['authorization_url']).query)
        assert query['scope'] == [gdrive.GOOGLE_DRIVE_SCOPE]
        assert query['access_type'] == ['offline'] and query['prompt'] == ['consent']
        assert query['code_challenge_method'] == ['S256'] and query['state'] == [flow['state']]
        assert query.get('code_challenge') and 'client_secret' not in query and 'api_key' not in query
        destination = ws.complete_google_drive_oauth(flow['state'], 'good-code')['destination']
        assert destination['provider'] == 'google_drive' and destination['destination_kind'] == 'remote'
        manifest = ws.read_manifest(Path(fresh_project['project']['path']))
        stored = next(row for row in manifest['backup_destinations'] if row['id'] == destination['id'])
        serialized = json.dumps(stored)
        assert 'refresh-1' not in serialized and 'client_id' not in stored and stored.get('credential_id')
        cred = ws._backup_credential(stored['credential_id'])
        assert cred['kind'] == 'google_oauth' and cred['refresh_token'] == 'refresh-1'
        if os.name != 'nt':
            assert stat.S_IMODE(ws.backup_credentials_path.stat().st_mode) == 0o600
        package = tmp_path / 'portable.breadmaker'; ws.export_project(fresh_project['project']['id'], package)
        with zipfile.ZipFile(package) as zf:
            exported = json.loads(zf.read('manifest.json'))
        text = json.dumps(exported)
        assert 'google_drive' not in text and 'credential_id' not in text and 'refresh-1' not in text
        all_data = tmp_path / 'all-data.zip'; ws.export_all_data(all_data)
        with zipfile.ZipFile(all_data) as zf:
            names = zf.namelist()
        assert 'backup_credentials.json' not in names and 'google_oauth_client.json' not in names
    finally:
        server.shutdown(); server.server_close()


def test_v761_google_backup_verifies_remote_discovers_and_materializes(monkeypatch, fresh_project):
    server, state, base = _google_server()
    try:
        destination = _connect_google(workspace, monkeypatch, base, project_id=fresh_project['project']['id'])
        assert destination['status'] == 'pending' and destination['available'] is True
        result = workspace.backup_destinations_catch_up(reason='v761-google', force=True, quiet_seconds=0)
        assert result['ok'] is True and len(result['backed_up']) == 1
        row = next(x for x in workspace.list_backup_destinations() if x['id'] == destination['id'])
        assert row['status'] == 'up_to_date' and row['last_verified']['verified'] is True
        packages = _google_packages(state, fresh_project['project']['id'])
        assert len(packages) == 1
        assert packages[0]['appProperties']['breadmaker_kind'] == 'backup_package'
        discovered = workspace.discover_destination_backups(destination['id'])
        assert len(discovered) == 1 and discovered[0]['candidate'] is True and discovered[0]['verified'] is False
        with workspace.materialize_destination_backup(destination['id'], discovered[0]['file']) as path:
            verified = workspace.verify_package(path)
            assert verified['project_id'] == fresh_project['project']['id']
    finally:
        server.shutdown(); server.server_close()


def test_v761_fresh_install_recovers_existing_google_library(monkeypatch, fresh_project, tmp_path):
    server, state, base = _google_server()
    try:
        original_id = fresh_project['project']['id']
        first = _connect_google(workspace, monkeypatch, base, project_id=original_id)
        assert workspace.backup_destinations_catch_up(reason='seed-drive', force=True, quiet_seconds=0)['ok'] is True
        replacement = Workspace(tmp_path / 'replacement-home'); replacement.init(); blank = replacement.create_project('Blank replacement')
        second = _connect_google(replacement, monkeypatch, base, project_id=blank['id'])
        assert second['location_id'] == first['location_id'] and second['folder_id'] == first['folder_id']
        found = replacement.discover_destination_backups(second['id'], blank['id'])
        candidate = next(row for row in found if row['project_id'] == original_id)
        with replacement.materialize_destination_backup(second['id'], candidate['file'], blank['id']) as package:
            assert replacement.verify_package(package)['project_id'] == original_id
    finally:
        server.shutdown(); server.server_close()


def test_v761_recovery_retention_policy_is_sparse_and_hard_capped():
    newest = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
    rows = []
    # Eight snapshots on newest day -> keep four.
    for i in range(8):
        rows.append({'file': f'today-{i}', 'created_at': (newest - timedelta(hours=i)).isoformat()})
    # 20 daily samples, then 12 weekly samples.
    for day in range(1, 21):
        rows.append({'file': f'day-{day}', 'created_at': (newest - timedelta(days=day, minutes=day)).isoformat()})
    for week in range(4, 16):
        rows.append({'file': f'week-{week}', 'created_at': (newest - timedelta(weeks=week)).isoformat()})
    keep, prune = recovery_retention_partition(rows,'standard')
    assert len([row for row in keep if row['file'].startswith('today-')]) == RECOVERY_RETENTION_POLICIES['standard'].current_workday
    assert len(keep) <= RECOVERY_RETENTION_POLICIES['standard'].hard_cap
    assert len(keep) + len(prune) == len(rows)
    # Policy must not accumulate 20 same-day packages.
    same_day = [{'file': str(i), 'created_at': (newest - timedelta(minutes=i)).isoformat()} for i in range(20)]
    same_keep, _ = recovery_retention_partition(same_day,'standard')
    assert len(same_keep) == 3


def test_v761_google_same_day_hygiene_uses_standard_three_current_workday_points(monkeypatch, fresh_project):
    server, state, base = _google_server()
    try:
        ws = workspace; project_id = fresh_project['project']['id']
        destination = _connect_google(ws, monkeypatch, base, project_id=project_id)
        for index in range(7):
            # 7.7.1: retention counts distinct *canonical* states. Unreferenced files
            # sitting in assets/ are intentionally outside project-generation state.
            with storage_mutations.tx(operation=f'hygiene-state-{index}') as con:
                pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()[0])
                con.execute("INSERT INTO tasks(project_id,title,description,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                            (pid,f'Cloud hygiene state {index}','retention proof','open',storage_values.now(),storage_values.now()))
            result = ws.backup_destinations_catch_up(project_id, destination_ids=[destination['id']], reason=f'hygiene-{index}', force=True, quiet_seconds=0)
            assert result['ok'] is True
        packages = _google_packages(state, project_id)
        assert len(packages) == 3
        assert all((row.get('appProperties') or {}).get('breadmaker_kind') == 'backup_package' for row in packages)
    finally:
        server.shutdown(); server.server_close()


def test_v761_google_write_failure_isolated_and_later_clears(monkeypatch, fresh_project, tmp_path):
    server, state, base = _google_server()
    try:
        ws = workspace; project_id = fresh_project['project']['id']
        local_dir = tmp_path / 'usb'; local_dir.mkdir()
        local = ws.add_backup_destination(str(local_dir), project_id)
        remote = _connect_google(ws, monkeypatch, base, project_id=project_id)
        state.fail_upload = True
        result = ws.backup_destinations_catch_up(project_id, reason='gdrive-full', force=True, quiet_seconds=0)
        assert result['ok'] is False and list(local_dir.glob('*.breadmaker'))
        statuses = {row['id']: row for row in ws.list_backup_destinations(project_id)}
        assert statuses[local['id']]['status'] == 'up_to_date'
        assert statuses[remote['id']]['status'] == 'needs_attention'
        state.fail_upload = False
        result = ws.backup_destinations_catch_up(project_id, destination_ids=[remote['id']], reason='gdrive-recover', force=True, quiet_seconds=0)
        assert result['ok'] is True
        recovered = next(row for row in ws.list_backup_destinations(project_id) if row['id'] == remote['id'])
        assert recovered['status'] == 'up_to_date' and not recovered.get('last_error')
    finally:
        server.shutdown(); server.server_close()


def test_v761_google_auth_failure_needs_attention_and_network_absence_not_connected(monkeypatch, tmp_path):
    server, state, base = _google_server()
    ws = Workspace(tmp_path / 'home'); ws.init(); project = ws.create_project('Auth states')
    try:
        remote = _connect_google(ws, monkeypatch, base, project_id=project['id'])
        manifest = ws.read_manifest(Path(project['path']))
        stored = next(row for row in manifest['backup_destinations'] if row['id'] == remote['id'])
        creds = ws._load_backup_credentials(); creds[stored['credential_id']]['refresh_token'] = 'invalid'; ws._save_backup_credentials(creds)
        row = next(x for x in ws.list_backup_destinations(project['id']) if x['id'] == remote['id'])
        assert row['status'] == 'needs_attention' and row['reason'] == 'google_auth_failed'
        # Restore a valid credential, then stop the server to prove ordinary absence is non-alarming.
        creds = ws._load_backup_credentials(); creds[stored['credential_id']]['refresh_token'] = 'refresh-1'; ws._save_backup_credentials(creds)
        server.shutdown(); server.server_close()
        row = next(x for x in ws.list_backup_destinations(project['id']) if x['id'] == remote['id'])
        assert row['status'] in {'unavailable', 'not_connected'} and row['reason'] == 'not_connected'
    finally:
        try: server.shutdown(); server.server_close()
        except Exception: pass


def test_v761_remove_forgets_refresh_token_but_leaves_google_backups(monkeypatch, fresh_project):
    server, state, base = _google_server()
    try:
        ws = workspace; pid = fresh_project['project']['id']
        remote = _connect_google(ws, monkeypatch, base, project_id=pid)
        assert ws.backup_destinations_catch_up(pid, reason='seed', force=True, quiet_seconds=0)['ok'] is True
        manifest = ws.read_manifest(Path(fresh_project['project']['path']))
        cid = next(row for row in manifest['backup_destinations'] if row['id'] == remote['id'])['credential_id']
        before = _google_packages(state, pid); assert before
        ws.remove_backup_destination(remote['id'], pid)
        assert ws._backup_credential(cid) is None
        after = _google_packages(state, pid); assert {r['id'] for r in after} == {r['id'] for r in before}
    finally:
        server.shutdown(); server.server_close()


def test_v761_google_delete_all_remote_erase_is_fail_closed(monkeypatch, tmp_path):
    server, state, base = _google_server()
    try:
        ws = Workspace(tmp_path / 'home'); ws.init(); project = ws.create_project('Privacy Drive')
        remote = _connect_google(ws, monkeypatch, base, project_id=project['id'])
        # Workspace.create_project intentionally creates only the project envelope.
        # Initialize the SQLite payload through Breadmaker's real current-schema path;
        # this privacy test must exercise a package that production code would accept,
        # not a hand-built pre-7.8 database with no storage_meta/schema identity.
        previous_root = storage_context.PROJECT_ROOT
        try:
            project_root=Path(project['path'])
            with storage_context.project_context(project_root,str(project['id'])):
                storage_context.configure_project(project_root)
                storage_database.init_db()
        finally:
            if previous_root is not None:
                storage_context.configure_project(previous_root)
        assert ws.backup_destinations_catch_up(project['id'], destination_ids=[remote['id']], reason='privacy-seed', force=True, quiet_seconds=0)['ok'] is True
        assert _google_packages(state, project['id'])
        erased = ws.erase_known_managed_backup_data()
        assert not erased['failures'] and erased['removed'] >= 1 and not _google_packages(state, project['id'])
        server.shutdown(); server.server_close()
        blocked = ws.erase_known_managed_backup_data()
        assert blocked['failures']
    finally:
        try: server.shutdown(); server.server_close()
        except Exception: pass


def test_v761_google_same_size_bitrot_is_detected_and_repaired(monkeypatch,fresh_project,tmp_path):
    server,state,base=_google_server()
    try:
        ws=workspace;pid=fresh_project['project']['id'];remote=_connect_google(ws,monkeypatch,base,project_id=pid)
        assert ws.backup_destinations_catch_up(pid,destination_ids=[remote['id']],reason='bitrot-seed',force=True,quiet_seconds=0)['ok'] is True
        packages=_google_packages(state,pid);assert len(packages)==1
        file_id=packages[0]['id']
        with state.lock:
            row=state.files[file_id];data=bytearray(row['content']);size=len(data);data[len(data)//2]^=0x01;row['content']=bytes(data);row['size']=len(data)
        result=ws.backup_destinations_catch_up(pid,destination_ids=[remote['id']],reason='bitrot-scrub',force=True,quiet_seconds=0)
        assert result['ok'] is True
        status=next(row for row in ws.list_backup_destinations(pid) if row['id']==remote['id'])
        assert status['up_to_date'] is True and not status.get('last_error')
        repaired=_google_packages(state,pid);assert repaired
        assert not any(len(row['content'])==size and row['content']==bytes(data) for row in repaired)
        # A full read of the published replacement must still be a valid project package.
        dest=next(x for x in ws._backup_destinations(Path(fresh_project['project']['path'])) if x['id']==remote['id'])
        provider=ws._backup_providers['google_drive'];rows=provider._published_rows(provider._client(ws,dest),dest['folder_id'],pid,strict=True);assert rows
        out=tmp_path/'drive-repaired.breadmaker';provider._client(ws,dest).download_file(rows[0]['provider_file_id'],out)
        assert ws.verify_package(out)['project_id']==pid
    finally:
        server.shutdown();server.server_close()
