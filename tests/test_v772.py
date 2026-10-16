from __future__ import annotations

from workspace import workspace

from storage import mutations as storage_mutations

import json
import zipfile
from pathlib import Path

import pytest

import app as app_module
from backups.publication import PUBLICATION_SUFFIX
from backups.providers.webdav import WebDAVError
from backups.providers.google_drive import GoogleDriveError


def _destination(client,tmp_path,name='publication-destination'):
    folder=tmp_path/name;folder.mkdir()
    row=client.post('/api/backup-destinations',json={'path':str(folder)})
    assert row.status_code==200,row.text
    return folder,row.json()['destination']


def test_v772_release_contract():
    root=Path(__file__).resolve().parents[1]
    version=(root/'VERSION').read_text(encoding='utf-8').strip();assert tuple(map(int,version.split('.'))) >= (7,7,2)
    text=(root/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '7.7.2' in text and 'PUBLISHED' in text and 'DESTINATION_VERIFIED' in text


def test_v772_success_records_exact_publication_state_machine(client,fresh_project,tmp_path):
    folder,destination=_destination(client,tmp_path)
    result=workspace.backup_destinations_catch_up(reason='v772-state-machine',force=True,quiet_seconds=0)
    assert result['ok'] is True
    row=next(item for item in result['destinations'] if item['id']==destination['id'])
    generation=row['current_generation']
    assert generation>0
    assert row['last_attempted_generation']==generation
    assert row['last_transferred_generation']==generation
    assert row['last_verified_generation']==generation
    assert row['last_published_generation']==generation
    assert row['published_generation']==generation
    assert row['publication_generation']==generation
    assert row['publication_state']=='PUBLISHED'
    assert row['retry_state']['state']=='idle'
    assert row['status']=='up_to_date'
    packages=list(folder.glob('*.breadmaker'));assert len(packages)==1
    publications=list(folder.glob(f'*.breadmaker{PUBLICATION_SUFFIX}'));assert len(publications)==1
    manifest=json.loads(publications[0].read_text(encoding='utf-8'))
    assert manifest['state']=='PUBLISHED' and manifest['project_generation']==generation
    assert manifest['package_file']==packages[0].name


def test_v772_local_crash_after_destination_verify_is_not_recoverable(client,fresh_project,tmp_path,monkeypatch):
    folder,destination=_destination(client,tmp_path,'local-crash-before-publish')
    original=workspace._publish_local_package
    monkeypatch.setattr(workspace,'_publish_local_package',lambda *a,**k:(_ for _ in ()).throw(OSError('simulated crash before publication')))
    result=workspace.backup_destinations_catch_up(reason='v772-crash-before-publish',force=True,quiet_seconds=0)
    assert result['ok'] is False
    row=next(item for item in result['destinations'] if item['id']==destination['id'])
    assert row['verified_generation']==row['current_generation']
    assert row['published_generation']==0
    assert row['publication_state']=='DESTINATION_VERIFIED'
    assert row['status']=='needs_attention'
    assert row['retry_state']['state']=='retry_pending'
    assert list(folder.glob('*.breadmaker'))  # harmless orphan/staging payload may exist
    assert not list(folder.glob(f'*.breadmaker{PUBLICATION_SUFFIX}'))
    assert workspace.discover_destination_backups(destination['id'])==[]
    monkeypatch.setattr(workspace,'_publish_local_package',original)


def test_v772_duplicate_retry_is_idempotent_and_does_not_publish_twice(client,fresh_project,tmp_path):
    folder,destination=_destination(client,tmp_path,'duplicate-retry')
    first=workspace.backup_destinations_catch_up(reason='v772-first',force=True,quiet_seconds=0)
    assert first['ok'] is True
    second=workspace.backup_destinations_catch_up(reason='v772-retry',force=True,quiet_seconds=0)
    assert second['ok'] is True and second['backed_up'][0].get('idempotent') is True
    assert len(list(folder.glob(f'*.breadmaker{PUBLICATION_SUFFIX}')))==1
    discovered=workspace.discover_destination_backups(destination['id'])
    assert len(discovered)==1 and discovered[0]['publication_state']=='published'


def test_v772_unpublished_new_protocol_payload_is_rejected_even_by_filename(client,fresh_project,tmp_path):
    folder,destination=_destination(client,tmp_path,'unpublished-guess')
    package=folder/f"breadmaker-{fresh_project['project']['id']}-orphan.breadmaker"
    workspace.export_project(fresh_project['project']['id'],package)
    with zipfile.ZipFile(package) as zf:
        manifest=json.loads(zf.read('manifest.json'))
    assert int(manifest['package_version'])==2
    assert workspace.discover_destination_backups(destination['id'])==[]
    with pytest.raises(Exception) as exc:
        with workspace.materialize_destination_backup(destination['id'],package.name):
            pass
    assert 'BACKUP_NOT_PUBLISHED' in str(exc.value)


def test_v772_out_of_order_publication_listing_uses_generation_not_time(client,fresh_project,tmp_path):
    # This is a provider-ordering unit test, so create the destination directly.
    # The HTTP endpoint intentionally starts asynchronous initial reconciliation;
    # allowing that worker into this test would introduce a legitimate historical
    # publication before the explicit newer->older ordering we are testing.
    ws=workspace;pid=fresh_project['project']['id']
    folder=tmp_path/'out-of-order-listing';folder.mkdir()
    destination=ws.add_backup_destination(str(folder),project_id=pid)
    older=tmp_path/'older.breadmaker';ws.export_project(pid,older);older_info=ws.verify_package(older)
    with storage_mutations.tx(operation='v772-newer') as con:
        project_db_id=int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()['id'])
        con.execute('UPDATE projects SET title=? WHERE id=?',('New publication authority',project_db_id))
    newer=tmp_path/'newer.breadmaker';ws.export_project(pid,newer);newer_info=ws.verify_package(newer)
    provider=ws._backup_providers['local_folder'];rec=ws.get(pid)
    provider.store(ws,newer,rec,destination,'newer')
    provider.store(ws,older,rec,destination,'older')
    rows=provider.list_backups(ws,rec,destination)
    assert rows and rows[0]['project_generation']==newer_info['project_generation']
    assert all(row['project_generation']!=older_info['project_generation'] for row in rows)


def test_v772_webdav_publication_survives_index_loss_and_unpublished_timeout_is_hidden(client,fresh_project,tmp_path,monkeypatch):
    from test_v760 import _dav_server
    ws=workspace;pid=fresh_project['project']['id'];server,state,endpoint=_dav_server()
    try:
        response=client.post('/api/backup-destinations/webdav',json={'endpoint':endpoint,'username':'writer','password':'app-pass'})
        assert response.status_code==200,response.text
        destination=response.json()['destination'];provider=ws._backup_providers['webdav']
        first=ws.backup_destinations_catch_up(pid,destination_ids=[destination['id']],reason='v772-webdav',force=True,quiet_seconds=0)
        assert first['ok'] is True
        # Mutable index is cache-only. Removing it cannot erase publication authority.
        with state.lock:state.files.pop(provider.INDEX,None)
        rows=provider.list_backups(ws,ws.get(pid),destination)
        assert len(rows)==1 and rows[0]['publication_state']=='published'

        # Advance Current, then simulate a timeout exactly at publication MOVE. The
        # transferred+verified payload must remain undiscoverable.
        with storage_mutations.tx(operation='v772-webdav-next') as con:
            project_db_id=int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()['id'])
            con.execute('UPDATE projects SET title=? WHERE id=?',('WebDAV unpublished state',project_db_id))
        original_move=__import__('backups.providers.webdav',fromlist=['WebDAVClient']).WebDAVClient.move
        def fail_publication_move(self,source,target,overwrite=True):
            if str(source).endswith('.publishing'):raise WebDAVError('WEBDAV_UNAVAILABLE')
            return original_move(self,source,target,overwrite)
        monkeypatch.setattr('backups.providers.webdav.WebDAVClient.move',fail_publication_move)
        failed=ws.backup_destinations_catch_up(pid,destination_ids=[destination['id']],reason='v772-webdav-timeout',force=True,quiet_seconds=0)
        assert failed['ok'] is False
        rows=provider.list_backups(ws,ws.get(pid),destination)
        assert len(rows)==1  # only the previously published generation
        status=next(r for r in ws.list_backup_destinations(pid) if r['id']==destination['id'])
        assert status['published_generation']<status['current_generation']
        assert status['status']=='needs_attention'
    finally:
        server.shutdown();server.server_close()


def test_v772_google_upload_without_publication_is_not_discoverable(monkeypatch,fresh_project):
    from test_v761 import _connect_google,_google_server
    ws=workspace;pid=fresh_project['project']['id'];server,state,base=_google_server()
    try:
        destination=_connect_google(ws,monkeypatch,base,project_id=pid)
        provider=ws._backup_providers['google_drive'];client=provider._client(ws,destination)
        monkeypatch.setattr(client.__class__,'create_publication_record',lambda *a,**k:(_ for _ in ()).throw(GoogleDriveError('GOOGLE_UNAVAILABLE')))
        result=ws.backup_destinations_catch_up(pid,destination_ids=[destination['id']],reason='v772-drive-timeout',force=True,quiet_seconds=0)
        assert result['ok'] is False
        # No publication record means no candidate even if provider bytes existed or
        # the upload request succeeded before the simulated timeout.
        assert provider.list_backups(ws,ws.get(pid),destination)==[]
        status=next(r for r in ws.list_backup_destinations(pid) if r['id']==destination['id'])
        assert status['verified_generation']==status['current_generation']
        assert status['published_generation']==0
        assert status['status']=='needs_attention'
    finally:
        server.shutdown();server.server_close()


def test_v772_stale_publisher_cannot_move_authority_backwards(client,fresh_project,tmp_path):
    folder,destination=_destination(client,tmp_path,'stale-publication')
    ws=workspace;pid=fresh_project['project']['id'];root=Path(fresh_project['project']['path'])
    first=ws.backup_destinations_catch_up(pid,destination_ids=[destination['id']],reason='old',force=True,quiet_seconds=0)
    old=next(r for r in first['destinations'] if r['id']==destination['id'])['published_generation']
    with storage_mutations.tx(operation='v772-authority-newer') as con:
        project_db_id=int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()['id'])
        con.execute('UPDATE projects SET title=? WHERE id=?',('Newest publication',project_db_id))
    second=ws.backup_destinations_catch_up(pid,destination_ids=[destination['id']],reason='new',force=True,quiet_seconds=0)
    newest=next(r for r in second['destinations'] if r['id']==destination['id'])['published_generation'];assert newest>old
    from workspace import BackupOperation
    stale=BackupOperation(pid,old,'delayed-publisher',old,'stale-token')
    assert ws._record_backup_state(root,destination['id'],old,'PUBLISHED') is False
    assert ws._record_backup_success(root,destination['id'],{'project_generation':old,'file':'old.breadmaker','verified':True},stale) is False
    row=next(r for r in ws.list_backup_destinations(pid) if r['id']==destination['id'])
    assert row['published_generation']==newest and row['status']=='up_to_date'


def test_v772_google_timeout_after_publication_success_converges_on_retry(monkeypatch,fresh_project):
    from test_v761 import _connect_google,_google_server
    ws=workspace;pid=fresh_project['project']['id'];server,state,base=_google_server()
    try:
        destination=_connect_google(ws,monkeypatch,base,project_id=pid)
        provider=ws._backup_providers['google_drive'];client_class=provider._client(ws,destination).__class__
        original=client_class.create_publication_record;calls={'n':0}
        def accepted_then_timeout(self,*args,**kwargs):
            result=original(self,*args,**kwargs)
            calls['n']+=1
            if calls['n']==1:raise GoogleDriveError('GOOGLE_UNAVAILABLE')
            return result
        monkeypatch.setattr(client_class,'create_publication_record',accepted_then_timeout)
        first=ws.backup_destinations_catch_up(pid,destination_ids=[destination['id']],reason='accepted-but-timeout',force=True,quiet_seconds=0)
        assert first['ok'] is False
        failed=next(r for r in ws.list_backup_destinations(pid) if r['id']==destination['id'])
        assert failed['published_generation']==0 and failed['status']=='needs_attention'
        # The provider did publish remotely before the simulated response loss.
        remote=provider.list_backups(ws,ws.get(pid),destination)
        assert len(remote)==1 and remote[0]['publication_state']=='published'
        remote_generation=int(remote[0]['project_generation'])

        # Retry does not depend on exactly-once response delivery. It discovers the
        # existing immutable generation and only repairs the local publication cache.
        second=ws.backup_destinations_catch_up(pid,destination_ids=[destination['id']],reason='retry-after-ambiguous-success',force=True,quiet_seconds=0)
        assert second['ok'] is True and second['backed_up'][0].get('idempotent') is True
        recovered=next(r for r in ws.list_backup_destinations(pid) if r['id']==destination['id'])
        assert recovered['published_generation']==remote_generation==recovered['current_generation']
        assert recovered['status']=='up_to_date' and recovered['retry_state']['state']=='idle'
        assert len(provider.list_backups(ws,ws.get(pid),destination))==1
    finally:
        server.shutdown();server.server_close()


def test_v772_local_privacy_erasure_removes_publication_and_payload(client,fresh_project,tmp_path):
    folder,destination=_destination(client,tmp_path,'erase-publication-records')
    result=workspace.backup_destinations_catch_up(reason='seed-for-erasure',force=True,quiet_seconds=0)
    assert result['ok'] is True
    assert list(folder.glob('*.breadmaker')) and list(folder.glob(f'*.breadmaker{PUBLICATION_SUFFIX}'))
    removed=workspace._erase_local_destination(folder,{fresh_project['project']['id']})
    assert removed>=1
    assert not list(folder.glob(f'breadmaker-{fresh_project["project"]["id"]}-*.breadmaker*'))
