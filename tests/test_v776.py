from __future__ import annotations

from workspace import workspace

from storage import context as storage_context
from storage import history as storage_history
from storage import mutations as storage_mutations
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

import json
import shutil
from pathlib import Path

import pytest

import app as app_module
from backend import history_runtime
from api.durability import history as history_api
from revision_store.remote_history import (
    RemoteHistoryRepository, RemoteHistoryConflict, RemoteHistoryCorruption,
)


class MemoryTransport:
    def __init__(self):
        self.data:dict[str,bytes]={};self.order:list[str]=[];self.fail_after_revision=False
    def put_immutable(self,path:str,data:bytes)->None:
        if path in self.data:
            if self.data[path]!=data:raise RemoteHistoryConflict('REMOTE_HISTORY_IMMUTABLE_CONFLICT')
            return
        self.data[path]=bytes(data);self.order.append(path)
        if self.fail_after_revision and path.endswith('.remote.json'):
            self.fail_after_revision=False
            raise TimeoutError('response lost after remote publication')
    def read(self,path:str)->bytes|None:return self.data.get(path)
    def put_file_immutable(self,path:str,source:Path)->None:self.put_immutable(path,Path(source).read_bytes())
    def get_file(self,path:str,target:Path)->bool:
        data=self.data.get(path)
        if data is None:return False
        Path(target).write_bytes(data);return True
    def list_paths(self,prefix:str)->list[str]:return sorted(k for k in self.data if k.startswith(prefix))


def _store():return storage_history.local_revision_store()

def _repo(transport,writer='1'*32):
    workspace_id,_=storage_mutations.configured_project_identity()
    return RemoteHistoryRepository(transport,workspace_project_id=workspace_id,writer_id=writer,canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES)

def _capture(label='Remote test'):
    return storage_history.capture_local_revision(label=label,creation_reason='test',summary=storage_history.history_current_summary(),force_checkpoint=True)


def test_v776_release_contract_and_transport_has_no_delete():
    root=Path(__file__).resolve().parents[1]
    assert tuple(int(x) for x in root.joinpath('VERSION').read_text().strip().split('.')) >= (7,7,6)
    text=root.joinpath('ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'Remote Revision History' in text and 'manifest' in text and 'LAST' in text
    from revision_store.remote_history import RemoteHistoryTransport
    assert 'delete' not in getattr(RemoteHistoryTransport,'__annotations__',{})


def test_v776_publication_is_manifest_last_and_verified(fresh_project):
    rev=_capture();t=MemoryTransport();repo=_repo(t)
    result=repo.publish(_store(),rev['revision_id'])
    assert result.generation==rev['generation_end']
    assert t.order[-1].endswith('.remote.json')
    pub=repo.newest_valid();assert pub and pub['revision_id']==rev['revision_id']
    assert pub['generation']==rev['generation_end']


def test_v776_timeout_after_publication_success_is_idempotent(fresh_project):
    rev=_capture();t=MemoryTransport();repo=_repo(t);t.fail_after_revision=True
    with pytest.raises(TimeoutError):repo.publish(_store(),rev['revision_id'])
    retry=repo.publish(_store(),rev['revision_id'])
    assert retry.idempotent is True
    assert len([k for k in t.data if k.endswith('.remote.json')])==1


def test_v776_missing_remote_object_self_repairs_from_local(fresh_project):
    rev=_capture();t=MemoryTransport();repo=_repo(t);repo.publish(_store(),rev['revision_id'])
    pub=repo.newest_valid();assert pub
    del t.data[pub['snapshot_bundle_path']]
    repaired=repo.publish(_store(),rev['revision_id'])
    assert repaired.repaired is True
    assert repo.newest_valid()['revision_id']==rev['revision_id']


def test_v776_invalid_newest_falls_back_to_previous_valid(fresh_project):
    first=_capture('first');t=MemoryTransport();repo=_repo(t);repo.publish(_store(),first['revision_id'])
    with storage_mutations.tx(operation='v776-advance') as con:
        pid=int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
        con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?',('second remote state',storage_values.now(),pid))
    second=_capture('second');repo.publish(_store(),second['revision_id'])
    latest=repo.newest_valid();assert latest['revision_id']==second['revision_id']
    t.data[latest['snapshot_bundle_path']]=b'corrupt'
    fallback=repo.newest_valid();assert fallback and fallback['revision_id']==first['revision_id']


def test_v776_second_writer_cannot_publish(fresh_project):
    rev=_capture();t=MemoryTransport();_repo(t,'1'*32).publish(_store(),rev['revision_id'])
    with pytest.raises(RemoteHistoryConflict,match='WRITER_CONFLICT'):
        _repo(t,'2'*32).publish(_store(),rev['revision_id'])


def test_v776_same_generation_conflict_fails_closed_even_if_listing_delayed(fresh_project):
    rev=_capture();t=MemoryTransport();repo=_repo(t);repo.publish(_store(),rev['revision_id'])
    pub_path=next(k for k in t.data if k.endswith('.remote.json'))
    row=json.loads(t.data[pub_path]);row['revision_id']='a'*32
    row['local_revision_manifest']=dict(row['local_revision_manifest'],revision_id='a'*32)
    import hashlib
    payload=json.dumps(row['local_revision_manifest'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    row['revision_manifest_sha256']=hashlib.sha256(payload).hexdigest()
    t.data[pub_path]=json.dumps(row,sort_keys=True,separators=(',',':')).encode()
    with pytest.raises(RemoteHistoryConflict,match='GENERATION_CONFLICT'):
        repo.publish(_store(),rev['revision_id'])


def test_v776_verified_remote_copy_repairs_corrupt_local_history(fresh_project):
    rev=_capture();t=MemoryTransport();repo=_repo(t);repo.publish(_store(),rev['revision_id'])
    store=_store();target=next(store.revisions_dir.glob(f"*-{rev['revision_id']}.revision.json"));target.write_text('{broken',encoding='utf-8')
    repaired=repo.repair_local(store,rev['revision_id'])
    assert repaired.repaired is True
    assert store.verify_revision(rev['revision_id'])['revision']['revision_id']==rev['revision_id']
    assert list((store.root/'quarantine').iterdir())


def test_v776_auto_history_never_calls_remote_provider_inline(client,fresh_project,monkeypatch):
    called=[]
    monkeypatch.setattr(workspace,'sync_remote_history',lambda *a,**k:called.append((a,k)) or {'ok':True})
    queued=[];monkeypatch.setattr(history_api,'_queue_remote_history',lambda *a,**k:queued.append((a,k)))
    response=client.post('/api/autocommit?label=Remote%20boundary&source=quiet')
    assert response.status_code==200,response.text
    assert called==[] and queued


def test_v776_manual_sync_endpoint_is_synchronous_barrier(client,fresh_project,monkeypatch):
    calls=[]
    monkeypatch.setattr(workspace,'sync_remote_history',lambda project_id,**kwargs:calls.append(project_id) or {'ok':True,'destinations':[],'failed':[]})
    response=client.post('/api/history/sync')
    assert response.status_code==200 and response.json()['ok'] is True
    assert calls==[fresh_project['project']['id']]

import base64
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _dav_history_server():
    class State:
        def __init__(self):
            self.files={};self.collections={''};self.username='writer';self.password='app-pass';self.fail_put=False;self.lock=threading.RLock()
    state=State()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def _key(self):return urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).removeprefix('/dav/').strip('/')
        def _auth(self):
            expected='Basic '+base64.b64encode(b'writer:app-pass').decode()
            if self.headers.get('Authorization')==expected:return True
            self._send(401);return False
        def _send(self,status,body=b'',headers=None):
            self.send_response(status)
            for k,v in (headers or {}).items():self.send_header(k,v)
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            if body:self.wfile.write(body)
        def do_PROPFIND(self):
            if not self._auth():return
            key=self._key();depth=self.headers.get('Depth','0')
            with state.lock:
                if key not in state.collections:self._send(404);return
                names=[];prefix=(key+'/' if key else '')
                if depth=='1':
                    for c in state.collections:
                        if c and c.startswith(prefix):
                            rest=c[len(prefix):]
                            if rest and '/' not in rest:names.append(rest+'/')
                    for f in state.files:
                        if f.startswith(prefix):
                            rest=f[len(prefix):]
                            if rest and '/' not in rest:names.append(rest)
            base='/dav/'+(key+'/' if key else '')
            hrefs=[base]+[base+urllib.parse.quote(n.rstrip('/'))+('/' if n.endswith('/') else '') for n in sorted(set(names))]
            body=('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'+''.join(f'<d:response><d:href>{h}</d:href></d:response>' for h in hrefs)+'</d:multistatus>').encode()
            self._send(207,body,{'Content-Type':'application/xml'})
        def do_MKCOL(self):
            if not self._auth():return
            key=self._key();parent='/'.join(key.split('/')[:-1])
            with state.lock:
                if key in state.collections:self._send(405);return
                if parent not in state.collections:self._send(409);return
                state.collections.add(key)
            self._send(201)
        def do_GET(self):
            if not self._auth():return
            with state.lock:data=state.files.get(self._key())
            self._send(200,data) if data is not None else self._send(404)
        def do_PUT(self):
            if not self._auth():return
            if state.fail_put:self._send(507);return
            key=self._key();parent='/'.join(key.split('/')[:-1]);n=int(self.headers.get('Content-Length') or 0);data=self.rfile.read(n)
            with state.lock:
                if parent not in state.collections:self._send(409);return
                state.files[key]=data
            self._send(201)
        def do_MOVE(self):
            if not self._auth():return
            src=self._key();dest=urllib.parse.unquote(urllib.parse.urlsplit(self.headers.get('Destination') or '').path).removeprefix('/dav/').strip('/')
            with state.lock:
                if src not in state.files:self._send(404);return
                if self.headers.get('Overwrite')=='F' and dest in state.files:self._send(412);return
                state.files[dest]=state.files.pop(src)
            self._send(201)
        def do_DELETE(self):
            if not self._auth():return
            key=self._key();removed=False
            with state.lock:
                if key in state.files:state.files.pop(key);removed=True
                if key in state.collections:
                    prefix=key.rstrip('/')+'/'
                    for f in list(state.files):
                        if f.startswith(prefix):state.files.pop(f);removed=True
                    for c in list(state.collections):
                        if c==key or c.startswith(prefix):state.collections.discard(c);removed=True
            self._send(204 if removed else 404)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    return server,state,f'http://127.0.0.1:{server.server_address[1]}/dav/'


def test_v776_webdav_sync_and_explicit_privacy_erasure(client,fresh_project):
    server,state,endpoint=_dav_history_server()
    try:
        added=client.post('/api/backup-destinations/webdav',json={'endpoint':endpoint,'username':'writer','password':'app-pass'})
        assert added.status_code==200,added.text
        rev=_capture('webdav remote history')
        result=workspace.sync_remote_history(fresh_project['project']['id'])
        assert result['ok'] is True and result['destinations']
        prefix=f"history/{fresh_project['project']['id']}/"
        assert any(k.startswith(prefix+'revisions/') and k.endswith('.remote.json') for k in state.files)
        provider=workspace._backup_providers['webdav']
        provider.erase_managed(workspace,added.json()['destination'],{fresh_project['project']['id']})
        assert not any(k.startswith(prefix) for k in state.files)
    finally:server.shutdown();server.server_close()


def test_v776_remote_destination_failure_isolated(client,fresh_project):
    good_server,good_state,good_endpoint=_dav_history_server();bad_server,bad_state,bad_endpoint=_dav_history_server()
    try:
        good=client.post('/api/backup-destinations/webdav',json={'endpoint':good_endpoint,'username':'writer','password':'app-pass'}).json()['destination']
        bad=client.post('/api/backup-destinations/webdav',json={'endpoint':bad_endpoint,'username':'writer','password':'app-pass'}).json()['destination']
        _capture('destination isolation');before=storage_mutations.current_project_generation();bad_state.fail_put=True
        result=workspace.sync_remote_history(fresh_project['project']['id'])
        assert result['ok'] is False and len(result['failed'])==1 and result['failed'][0]['destination_id']==bad['id']
        assert any(row['destination_id']==good['id'] for row in result['destinations'])
        assert storage_mutations.current_project_generation()==before
        assert storage_history.list_local_revisions()
    finally:
        good_server.shutdown();good_server.server_close();bad_server.shutdown();bad_server.server_close()


def test_v776_google_drive_sync_and_privacy_erasure(monkeypatch,fresh_project):
    from tests.test_v761 import _google_server,_connect_google
    server,state,base=_google_server()
    try:
        destination=_connect_google(workspace,monkeypatch,base,project_id=fresh_project['project']['id'])
        _capture('google remote history')
        result=workspace.sync_remote_history(fresh_project['project']['id'])
        assert result['ok'] is True and result['destinations']
        with state.lock:
            history_rows=[dict(r) for r in state.files.values() if (r.get('appProperties') or {}).get('breadmaker_history_workspace_id')==fresh_project['project']['id']]
        assert history_rows
        provider=workspace._backup_providers['google_drive']
        provider.erase_managed(workspace,destination,{fresh_project['project']['id']})
        with state.lock:
            remaining=[r for r in state.files.values() if (r.get('appProperties') or {}).get('breadmaker_history_workspace_id')==fresh_project['project']['id']]
        assert remaining==[]
    finally:server.shutdown();server.server_close()


def test_v776_second_installation_can_read_repair_without_writer_claim(fresh_project,tmp_path):
    from revision_store.local_store import LocalRevisionStore
    rev=_capture('portable repair');t=MemoryTransport();writer_a=_repo(t,'1'*32);writer_a.publish(_store(),rev['revision_id'])
    workspace_id,_=storage_mutations.configured_project_identity()
    alternate=LocalRevisionStore(tmp_path/'other-install-history',storage_context.DB_PATH,storage_context.ASSET_DIR,
                                 canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,workspace_project_id=workspace_id)
    reader=RemoteHistoryRepository(t,workspace_project_id=workspace_id,writer_id='2'*32,canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES)
    result=reader.repair_local(alternate,rev['revision_id'])
    assert result.repaired is True
    assert alternate.verify_revision(rev['revision_id'])['revision']['revision_id']==rev['revision_id']
    with pytest.raises(RemoteHistoryConflict,match='WRITER_CONFLICT'):
        reader.publish(alternate,rev['revision_id'])


class DelayedListingTransport(MemoryTransport):
    def list_paths(self,prefix:str)->list[str]:return []


def test_v776_publication_and_idempotency_do_not_depend_on_provider_listing(fresh_project):
    rev=_capture('delayed listing');t=DelayedListingTransport();repo=_repo(t)
    first=repo.publish(_store(),rev['revision_id']);second=repo.publish(_store(),rev['revision_id'])
    assert first.idempotent is False and second.idempotent is True
    assert len([k for k in t.data if k.endswith('.remote.json')])==1


def test_v776_writer_identity_creation_is_thread_safe(tmp_path):
    from workspace import Workspace
    home=tmp_path/'writer-home';ws=Workspace(home)
    barrier=threading.Barrier(8);values=[];errors=[];lock=threading.Lock()
    def worker():
        try:
            barrier.wait(timeout=1)
            value=ws._remote_history_writer_id()
            with lock:values.append(value)
        except Exception as exc:
            with lock:errors.append(exc)
    threads=[threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=2)
    assert errors==[]
    assert len(values)==8 and len(set(values))==1
    persisted=json.loads(ws.remote_history_writer_path.read_text(encoding='utf-8'))
    assert persisted['writer_id']==values[0]


def test_v776_missing_writer_claim_with_published_data_fails_closed(fresh_project):
    rev=_capture('claim loss');t=MemoryTransport();repo=_repo(t);repo.publish(_store(),rev['revision_id'])
    repository_path=repo._repo_path();assert repository_path in t.data
    del t.data[repository_path]
    with pytest.raises(RemoteHistoryCorruption,match='REPOSITORY_AUTHORITY_INCOMPLETE'):
        _repo(t,'2'*32).claim_writer()


def test_v776_google_duplicate_logical_identity_with_different_bytes_fails_closed(monkeypatch,fresh_project):
    from tests.test_v761 import _google_server,_connect_google
    server,state,base=_google_server()
    try:
        _connect_google(workspace,monkeypatch,base,project_id=fresh_project['project']['id'])
        _capture('google duplicate authority')
        first=workspace.sync_remote_history(fresh_project['project']['id'])
        assert first['ok'] is True
        with state.lock:
            source=next(dict(row) for row in state.files.values() if (row.get('appProperties') or {}).get('breadmaker_history_kind')=='revision_publication')
            duplicate=dict(source)
            duplicate['id']=state.ident('duplicate')
            duplicate['content']=bytes(source['content'])+b'corrupt-duplicate'
            duplicate['size']=len(duplicate['content'])
            state.files[duplicate['id']]=duplicate
        retry=workspace.sync_remote_history(fresh_project['project']['id'])
        assert retry['ok'] is False and retry['failed']
        assert 'REMOTE_HISTORY_DUPLICATE_CONFLICT' in retry['failed'][0]['error']
    finally:server.shutdown();server.server_close()


def test_v776_background_remote_failure_retries_without_another_edit(fresh_project,monkeypatch):
    # The lifespan worker is already running under TestClient. A provider failure must
    # schedule a bounded retry by itself; another manuscript edit is not required.
    calls=[];succeeded=threading.Event()
    def fake_sync(project_id,revision_id=None,**kwargs):
        calls.append((project_id,revision_id))
        if len(calls)==1:return {'ok':False,'destinations':[],'failed':[{'destination_id':'remote','error':'temporary'}]}
        succeeded.set();return {'ok':True,'destinations':[],'failed':[]}
    monkeypatch.setattr(workspace,'sync_remote_history',fake_sync)
    monkeypatch.setattr(history_runtime,'_REMOTE_HISTORY_RETRY_SECONDS',.05)
    history_runtime.queue_remote_history(fresh_project['project']['id'],'f'*32)
    assert succeeded.wait(.8),calls
    assert len(calls)>=2
    assert calls[0][0]==fresh_project['project']['id'] and calls[-1][0]==fresh_project['project']['id']


def test_v776_existing_corrupt_writer_identity_never_silently_rotates(tmp_path):
    from workspace import Workspace,WorkspaceError
    ws=Workspace(tmp_path/'writer-corrupt-home');ws.root.mkdir(parents=True,exist_ok=True)
    ws.remote_history_writer_path.write_text('{broken',encoding='utf-8')
    before=ws.remote_history_writer_path.read_bytes()
    with pytest.raises(WorkspaceError,match='REMOTE_HISTORY_WRITER_ID_CORRUPT'):
        ws._remote_history_writer_id()
    assert ws.remote_history_writer_path.read_bytes()==before
