from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from workspace import workspace

import base64
import json
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import app as app_module
from workspace import Workspace


class _DAVState:
    def __init__(self):
        self.files={}
        self.username='writer'
        self.password='app-pass'
        self.fail_put=False
        self.lock=threading.Lock()


def _dav_server():
    state=_DAVState()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args): pass
        def _authorized(self):
            expected='Basic '+base64.b64encode(f'{state.username}:{state.password}'.encode()).decode()
            return self.headers.get('Authorization')==expected
        def _key(self):
            return urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).removeprefix('/dav/').lstrip('/')
        def _send(self,status,body=b'',headers=None):
            self.send_response(status)
            for k,v in (headers or {}).items():self.send_header(k,v)
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            if body:self.wfile.write(body)
        def _auth(self):
            if self._authorized():return True
            self._send(401,headers={'WWW-Authenticate':'Basic realm="Breadmaker test"'});return False
        def do_PROPFIND(self):
            if not self._auth():return
            depth=self.headers.get('Depth','0')
            hrefs=['/dav/']
            if depth=='1':
                with state.lock:hrefs.extend('/dav/'+urllib.parse.quote(name) for name in state.files)
            body=('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'+''.join(f'<d:response><d:href>{href}</d:href></d:response>' for href in hrefs)+'</d:multistatus>').encode()
            self._send(207,body,{'Content-Type':'application/xml'})
        def do_GET(self):
            if not self._auth():return
            with state.lock:data=state.files.get(self._key())
            if data is None:self._send(404);return
            self._send(200,data)
        def do_PUT(self):
            if not self._auth():return
            if state.fail_put:self._send(507);return
            n=int(self.headers.get('Content-Length') or '0');data=self.rfile.read(n)
            with state.lock:state.files[self._key()]=data
            self._send(201)
        def do_MOVE(self):
            if not self._auth():return
            source=self._key();dest=urllib.parse.urlsplit(self.headers.get('Destination') or '').path
            dest=urllib.parse.unquote(dest).removeprefix('/dav/').lstrip('/')
            with state.lock:
                if source not in state.files:self._send(404);return
                if self.headers.get('Overwrite')=='F' and dest in state.files:self._send(412);return
                state.files[dest]=state.files.pop(source)
            self._send(201)
        def do_DELETE(self):
            if not self._auth():return
            with state.lock:
                existed=self._key() in state.files
                state.files.pop(self._key(),None)
            self._send(204 if existed else 404)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    endpoint=f'http://127.0.0.1:{server.server_address[1]}/dav/'
    return server,state,endpoint


def _connect(client,endpoint):
    response=client.post('/api/backup-destinations/webdav',json={'endpoint':endpoint,'username':'writer','password':'app-pass'})
    assert response.status_code==200,response.text
    return response.json()['destination']


def test_v760_release_provider_and_locale_contract():
    root=Path(__file__).resolve().parents[1]
    version=(root/'VERSION').read_text(encoding='utf-8').strip();assert tuple(map(int,version.split('.'))) >= (7,6,0)
    appjs=frontend_source_from_app_path(root/'static/app.js')
    assert 'promptWebDAVConnection' in appjs
    assert '/api/backup-destinations/webdav' in appjs
    assert 'backup.destinations_remote_verified_scope' in appjs
    catalogs=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((root/'locales').glob('*.json'))]
    en=next(c for c in catalogs if c['code']=='en');keys=set(en['strings'])
    assert {'backup.destinations_type_webdav','backup.destinations_remote_connect','backup.destinations_remote_notice'}<=keys
    assert all(set(c['strings'])==keys for c in catalogs)
    assert all(c['source_index']==en['source_index'] for c in catalogs)


def test_v760_webdav_connect_backup_verify_discover_and_materialize(client,fresh_project):
    server,state,endpoint=_dav_server()
    try:
        destination=_connect(client,endpoint)
        assert destination['provider']=='webdav' and destination['destination_kind']=='remote'
        assert destination['status']=='pending' and destination['available'] is True
        result=workspace.backup_destinations_catch_up(reason='v760-remote',force=True,quiet_seconds=0)
        assert result['ok'] is True and len(result['backed_up'])==1
        current=next(x for x in workspace.list_backup_destinations() if x['id']==destination['id'])
        assert current['status']=='up_to_date' and current['last_verified']['verified'] is True
        discovered=client.get(f"/api/backup-destinations/{destination['id']}/discover")
        assert discovered.status_code==200,discovered.text
        candidate=discovered.json()[0]
        assert candidate['project_id']==fresh_project['project']['id'] and candidate['candidate'] is True and candidate['verified'] is False
        with workspace.materialize_destination_backup(destination['id'],candidate['file']) as path:
            info=workspace.verify_package(path)
            assert info['project_id']==fresh_project['project']['id']
        assert any(name.endswith('.breadmaker') for name in state.files)
        assert not any(name.endswith('.uploading') for name in state.files)
    finally:server.shutdown();server.server_close()


def test_v760_remote_failure_isolated_and_clears_after_verified_success(client,fresh_project,tmp_path):
    server,state,endpoint=_dav_server()
    try:
        local=tmp_path/'usb';local.mkdir();client.post('/api/backup-destinations',json={'path':str(local)})
        remote=_connect(client,endpoint)
        state.fail_put=True
        result=workspace.backup_destinations_catch_up(reason='v760-fail',force=True,quiet_seconds=0)
        assert result['ok'] is False
        assert list(local.glob('*.breadmaker'))
        row=next(x for x in workspace.list_backup_destinations() if x['id']==remote['id'])
        assert row['status']=='needs_attention'
        state.fail_put=False
        result=workspace.backup_destinations_catch_up(destination_ids=[remote['id']],reason='v760-recover',force=True,quiet_seconds=0)
        assert result['ok'] is True
        row=next(x for x in workspace.list_backup_destinations() if x['id']==remote['id'])
        assert row['status']=='up_to_date' and not row.get('last_error')
    finally:server.shutdown();server.server_close()


def test_v760_unreachable_is_not_connected_but_bad_auth_needs_attention(client,fresh_project):
    server,state,endpoint=_dav_server()
    try:
        remote=_connect(client,endpoint)
        reauth=client.put(f"/api/backup-destinations/{remote['id']}/webdav",json={'endpoint':endpoint,'username':'writer','password':'wrong'})
        assert reauth.status_code==400
        # Failed reconnect must preserve the known-good credentials/config.
        row=next(x for x in workspace.list_backup_destinations() if x['id']==remote['id'])
        assert row['available'] is True
        # Corrupt the installation-local credential to prove status semantics.
        creds=workspace._load_backup_credentials();cid=next(x for x in workspace._backup_destinations(Path(fresh_project['project']['path'])) if x['id']==remote['id'])['credential_id']
        creds[cid]={'username':'writer','password':'wrong'};workspace._save_backup_credentials(creds)
        row=next(x for x in workspace.list_backup_destinations() if x['id']==remote['id'])
        assert row['status']=='needs_attention' and row['reason']=='webdav_auth_failed'
    finally:server.shutdown();server.server_close()


def test_v760_fresh_install_can_adopt_existing_remote_identity_and_recover(client,fresh_project,tmp_path):
    server,state,endpoint=_dav_server()
    try:
        original=fresh_project['project']['id'];remote=_connect(client,endpoint)
        workspace.backup_destinations_catch_up(reason='v760-seed',force=True,quiet_seconds=0)
        replacement=Workspace(tmp_path/'replacement-home');replacement.init();blank=replacement.create_project('Blank replacement')
        connected=replacement.add_webdav_backup_destination(endpoint,'writer','app-pass',project_id=blank['id'])
        assert connected['location_id']==remote['location_id']
        discovered=replacement.discover_destination_backups(connected['id'],blank['id'])
        candidate=next(x for x in discovered if x['project_id']==original)
        with replacement.materialize_destination_backup(connected['id'],candidate['file'],blank['id']) as package:
            assert replacement.verify_package(package)['project_id']==original
    finally:server.shutdown();server.server_close()


def test_v760_credentials_are_installation_local_and_not_exported(client,fresh_project,tmp_path):
    server,state,endpoint=_dav_server()
    try:
        remote=_connect(client,endpoint)
        manifest=workspace.read_manifest(Path(fresh_project['project']['path']))
        stored=next(x for x in manifest['backup_destinations'] if x['id']==remote['id'])
        assert 'password' not in stored and 'username' not in stored and stored.get('credential_id')
        project_pkg=tmp_path/'portable.breadmaker';workspace.export_project(fresh_project['project']['id'],project_pkg)
        with zipfile.ZipFile(project_pkg) as zf:
            exported=json.loads(zf.read('manifest.json'))
        text=json.dumps(exported)
        assert 'credential_id' not in text and endpoint not in text and 'backup_destinations' not in exported
        all_data=tmp_path/'all-data.zip';workspace.export_all_data(all_data)
        with zipfile.ZipFile(all_data) as zf:
            assert 'backup_credentials.json' not in zf.namelist()
    finally:server.shutdown();server.server_close()


def test_v760_remove_forgets_credentials_but_keeps_remote_backups(client,fresh_project):
    server,state,endpoint=_dav_server()
    try:
        remote=_connect(client,endpoint);workspace.backup_destinations_catch_up(reason='v760-seed',force=True,quiet_seconds=0)
        manifest=workspace.read_manifest(Path(fresh_project['project']['path']));cid=next(x for x in manifest['backup_destinations'] if x['id']==remote['id'])['credential_id']
        packages={k:v for k,v in state.files.items() if k.endswith('.breadmaker')};assert packages
        response=client.delete(f"/api/backup-destinations/{remote['id']}");assert response.status_code==200
        assert workspace._backup_credential(cid) is None
        assert all(state.files.get(k)==v for k,v in packages.items())
    finally:server.shutdown();server.server_close()


def test_v760_delete_all_preflight_and_remote_erase_are_fail_closed(tmp_path):
    server,state,endpoint=_dav_server()
    try:
        ws=Workspace(tmp_path/'home');ws.init();project=ws.create_project('Privacy')
        # Provide a minimal valid project DB through the app's project fixture is cumbersome;
        # privacy behavior only needs a configured remote library/index.
        remote=ws.add_webdav_backup_destination(endpoint,'writer','app-pass',project_id=project['id'])
        provider=ws._backup_providers['webdav'];client=provider._client(ws,remote)
        filename=f"breadmaker-{project['id']}-seed.breadmaker";client.put(filename,b'x')
        provider._write_index(client,[{'file':filename,'project_id':project['id'],'name':'Privacy','created_at':'now','bytes':1,'fingerprint':'x'}])
        erased=ws.erase_known_managed_backup_data()
        assert not erased['failures'] and erased['removed']>=1 and filename not in state.files
        server.shutdown();server.server_close()
        blocked=ws.erase_known_managed_backup_data();assert blocked['failures']
    finally:
        try:server.shutdown();server.server_close()
        except Exception:pass


def test_v760_nonlocal_http_and_url_credentials_are_rejected(client,fresh_project):
    for endpoint in ('http://example.com/backups/','https://user:secret@example.com/backups/'):
        response=client.post('/api/backup-destinations/webdav',json={'endpoint':endpoint,'username':'u','password':'p'})
        assert response.status_code==400


def test_v760_webdav_same_size_bitrot_is_detected_and_repaired(client,fresh_project,tmp_path):
    server,state,endpoint=_dav_server()
    try:
        remote=_connect(client,endpoint);ws=workspace;pid=fresh_project['project']['id']
        assert ws.backup_destinations_catch_up(pid,destination_ids=[remote['id']],reason='bitrot-seed',force=True,quiet_seconds=0)['ok'] is True
        with state.lock:
            packages=[name for name in state.files if name.endswith('.breadmaker')]
            assert len(packages)==1
            name=packages[0];data=bytearray(state.files[name]);size=len(data);data[len(data)//2]^=0x01;state.files[name]=bytes(data)
        result=ws.backup_destinations_catch_up(pid,destination_ids=[remote['id']],reason='bitrot-scrub',force=True,quiet_seconds=0)
        assert result['ok'] is True
        status=next(row for row in ws.list_backup_destinations(pid) if row['id']==remote['id'])
        assert status['up_to_date'] is True and not status.get('last_error')
        with state.lock:
            repaired=[(name,blob) for name,blob in state.files.items() if name.endswith('.breadmaker')]
        assert repaired and all(len(blob)>0 for _name,blob in repaired)
        # The publication repair must not merely preserve the corrupted bytes.
        assert not any(len(blob)==size and blob==bytes(data) for _name,blob in repaired)
        materialized=tmp_path/'webdav-repaired.breadmaker'
        provider=ws._backup_providers['webdav'];dest=next(x for x in ws._backup_destinations(Path(fresh_project['project']['path'])) if x['id']==remote['id'])
        rows=provider._publication_rows(provider._client(ws,dest),pid,strict=True);assert rows
        provider._client(ws,dest).get_file(rows[0]['file'],materialized)
        assert ws.verify_package(materialized)['project_id']==pid
    finally:
        server.shutdown();server.server_close()
