from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from workspace import workspace

from storage import mutations as storage_mutations

from api.durability import backups as backup_api

import json
import shutil
from pathlib import Path

import app as app_module
from backend import recovery_runtime
from removable_volume_simulation import simulate_removable_volume
def _first_chapter(client):
    return client.get('/api/bootstrap').json()['chapters'][0]


def test_v740_multi_destination_fanout_and_independent_status(client,fresh_project,tmp_path):
    one=tmp_path/'usb-one';two=tmp_path/'usb-two';one.mkdir();two.mkdir()
    a=client.post('/api/backup-destinations',json={'path':str(one)})
    b=client.post('/api/backup-destinations',json={'path':str(two)})
    assert a.status_code==200,a.text
    assert b.status_code==200,b.text
    rows=client.get('/api/backup-destinations').json()['destinations']
    assert len(rows)==2 and all(row['status'] in {'pending','backing_up','up_to_date'} for row in rows)
    caught=client.post('/api/backup-destinations/catch-up')
    assert caught.status_code==200,caught.text
    rows=caught.json()['destinations']
    assert all(row['status']=='up_to_date' for row in rows)
    assert list(one.glob('*.breadmaker')) and list(two.glob('*.breadmaker'))

    chapter=_first_chapter(client);loaded=client.get(f"/api/chapters/{chapter['id']}").json()
    saved=client.put(f"/api/chapters/{chapter['id']}/blocks",json={
        'expected_revision':loaded['revision'],
        'blocks':[{'text_content':'multi destination proof','content_html':'multi destination proof'}],
    })
    assert saved.status_code==200,saved.text
    client.post('/api/backup-destinations/catch-up')
    rows=client.get('/api/backup-destinations').json()['destinations']
    assert all(row['status']=='up_to_date' for row in rows)
    assert len(list(one.glob('*.breadmaker')))>=2
    assert len(list(two.glob('*.breadmaker')))>=2


def test_v740_absent_drive_is_normal_and_catches_up_when_it_returns(client,fresh_project,tmp_path,monkeypatch):
    connected=tmp_path/'connected';removable=tmp_path/'removable';connected.mkdir();removable.mkdir()
    simulate_removable_volume(monkeypatch,removable)
    client.post('/api/backup-destinations',json={'path':str(connected)})
    removable_added=client.post('/api/backup-destinations',json={'path':str(removable)}).json()['destination']
    parked=tmp_path/'parked-removable';shutil.move(str(removable),str(parked))

    chapter=_first_chapter(client);loaded=client.get(f"/api/chapters/{chapter['id']}").json()
    saved=client.put(f"/api/chapters/{chapter['id']}/blocks",json={'expected_revision':loaded['revision'],'blocks':[{'text_content':'while usb away','content_html':'while usb away'}]})
    assert saved.status_code==200
    client.post('/api/backup-destinations/catch-up')
    rows=client.get('/api/backup-destinations').json()['destinations']
    away=next(row for row in rows if row['id']==removable_added['id'])
    assert away['status']=='unavailable'

    shutil.move(str(parked),str(removable))
    caught=client.post('/api/backup-destinations/catch-up')
    assert caught.status_code==200,caught.text
    rows=caught.json()['destinations']
    returned=next(row for row in rows if row['id']==removable_added['id'])
    assert returned['status']=='up_to_date'
    assert returned['last_verified']


def test_v740_destination_backups_are_readable_and_restorable(client,fresh_project,tmp_path):
    folder=tmp_path/'recovery-usb';folder.mkdir()
    added=client.post('/api/backup-destinations',json={'path':str(folder)}).json()['destination']
    assert added['status'] in {'pending','backing_up','up_to_date'}
    client.post('/api/backup-destinations/catch-up')
    listing=client.get(f"/api/backup-destinations/{added['id']}/backups")
    assert listing.status_code==200 and listing.json()
    backup=next(row for row in listing.json() if row['candidate'])
    before=len(client.get('/api/workspace').json()['projects'])
    restored=client.post(f"/api/backup-destinations/{added['id']}/backups/{backup['file']}/restore-as-project")
    assert restored.status_code==200,restored.text
    assert len(client.get('/api/workspace').json()['projects'])==before+1


def test_v740_old_single_backup_folder_is_not_imported(client,fresh_project,tmp_path):
    rec=workspace.active();root=Path(rec['path']);folder=tmp_path/'old-single-folder';folder.mkdir()
    manifest=workspace.read_manifest(root)
    manifest['external_backup_dir']=str(folder)
    manifest['external_backup_location_id']='obsolete-location'
    manifest['external_backup_device']='obsolete-device'
    workspace._write_manifest(root,manifest)
    # 7.8 is current-format-only: obsolete single-folder fields are inert and
    # never rewritten into current Backup Destination configuration.
    response=client.get('/api/backup-destinations');assert response.status_code==200,response.text
    assert response.json()['destinations']==[]
    unchanged=workspace.read_manifest(root)
    assert unchanged['external_backup_dir']==str(folder)
    assert 'backup_destinations' not in unchanged


def test_v740_remove_destination_never_removes_existing_packages(client,fresh_project,tmp_path):
    folder=tmp_path/'keep-packages';folder.mkdir()
    added=client.post('/api/backup-destinations',json={'path':str(folder)}).json()['destination']
    client.post('/api/backup-destinations/catch-up')
    packages=list(folder.glob('*.breadmaker'));assert packages
    removed=client.delete(f"/api/backup-destinations/{added['id']}")
    assert removed.status_code==200
    assert all(path.exists() for path in packages)
    assert client.get('/api/backup-destinations').json()['destinations']==[]


def test_v740_locale_and_ui_contract():
    root=Path(__file__).resolve().parents[1];assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (7,4,0);appjs=frontend_source_from_app_path(root/'static/app.js')
    assert 'openBackups' in appjs and '/api/backup-destinations' in appjs
    assert "i18nText('backup.destinations_title')" in appjs
    catalogs=[json.loads(path.read_text(encoding='utf-8')) for path in sorted((root/'locales').glob('*.json'))]
    keys=set(catalogs[0]['strings']);assert all(set(cat['strings'])==keys for cat in catalogs)
    required={'backup.destinations_title','backup.destinations_add','backup.destinations_status_up_to_date','backup.destinations_status_unavailable','backup.destinations_same_disk_warning','error.BACKUP_DESTINATION_002'}
    assert required<=keys

def test_v740_automatic_backup_trigger_is_non_blocking(monkeypatch,fresh_project):
    calls=[]
    def should_not_run(*args,**kwargs):
        calls.append((args,kwargs));raise AssertionError('automatic mutation must not perform backup fan-out inline')
    monkeypatch.setattr(workspace,'backup_destinations_catch_up',should_not_run)
    recovery_runtime._BACKUP_DESTINATION_WAKE.clear()
    result=backup_api._maybe_external_backup('manuscript_save')
    assert result=={'scheduled':True}
    assert recovery_runtime._BACKUP_DESTINATION_WAKE.is_set()
    assert calls==[]
    recovery_runtime._BACKUP_DESTINATION_WAKE.clear()


def test_v740_fanout_builds_source_package_once(client,fresh_project,tmp_path,monkeypatch):
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    pid=fresh_project['project']['id']
    # The canonical transaction below notifies Recovery's asynchronous worker.
    # That worker also uses this reentrant lock for destination reconciliation.
    # Hold it across setup, mutation and the explicit call so background work
    # cannot publish the generation first and manufacture an empty fanout.
    with workspace._backup_lock:
        workspace.add_backup_destination(str(a),project_id=pid)
        workspace.add_backup_destination(str(b),project_id=pid)
        with storage_mutations.tx(operation='v740-fanout-proof') as con:
            db_pid=int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
            con.execute('UPDATE projects SET title=? WHERE id=?',('Fanout proof generation',db_pid))
        calls={'n':0};original=workspace.export_project
        def counted(*args,**kwargs):calls['n']+=1;return original(*args,**kwargs)
        monkeypatch.setattr(workspace,'export_project',counted)
        result=workspace.backup_destinations_catch_up(reason='fanout-proof',quiet_seconds=0)
        assert result['ok'] is True
        assert len(result['backed_up'])==2
        assert calls['n']==1


def test_v740_provider_registry_has_local_stage_one_provider():
    provider=workspace._backup_providers.get('local_folder')
    assert provider is not None
    assert provider.provider_type=='local_folder'

def test_v740_one_destination_failure_does_not_cancel_other_verified_copy(client,fresh_project,tmp_path,monkeypatch):
    # Hold the same destination lock as the background Recovery worker so the
    # source generation cannot be published before fault injection.
    with workspace._backup_lock:
        good=tmp_path/'good';bad=tmp_path/'bad';good.mkdir();bad.mkdir()
        # This test measures one explicit catch-up operation. The HTTP add endpoint
        # intentionally queues asynchronous reconciliation, which can otherwise begin
        # between destination creation and the provider fault injection below. That
        # makes the test observe two overlapping operations rather than the partial
        # failure isolation contract it is intended to prove. Add destinations directly,
        # matching the fan-out test above, so the operation boundary is deterministic.
        project_id=fresh_project['project']['id']
        g=workspace.add_backup_destination(str(good),project_id=project_id)
        b=workspace.add_backup_destination(str(bad),project_id=project_id)
        with storage_mutations.tx(operation='v740-partial-failure-proof') as con:
            pid=int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
            con.execute('UPDATE projects SET title=? WHERE id=?',('Partial failure proof generation',pid))
        provider=workspace._backup_providers['local_folder'];original=provider.store
        def selective(workspace,source,rec,destination,source_token):
            if str(destination.get('id'))==str(b['id']):raise OSError('simulated destination failure')
            return original(workspace,source,rec,destination,source_token)
        monkeypatch.setattr(provider,'store',selective)
        result=workspace.backup_destinations_catch_up(reason='partial-failure-proof',quiet_seconds=0)
        assert result['ok'] is False
        assert [row['id'] for row in result['backed_up']]==[g['id']]
        assert [row['id'] for row in result['failed']]==[b['id']]
        rows={row['id']:row for row in result['destinations']}
        assert rows[g['id']]['status']=='up_to_date'
        assert rows[b['id']]['status']=='needs_attention'
        assert list(good.glob('*.breadmaker'))


def test_v740_delete_all_discovers_every_local_destination(client,fresh_project,tmp_path):
    a=tmp_path/'erase-a';b=tmp_path/'erase-b';a.mkdir();b.mkdir()
    client.post('/api/backup-destinations',json={'path':str(a)})
    client.post('/api/backup-destinations',json={'path':str(b)})
    targets=workspace._known_backup_erasure_targets()
    dirs={Path(row['destination']['path']).resolve() for row in targets if row['destination']['provider']=='local_folder'}
    assert a.resolve() in dirs and b.resolve() in dirs
