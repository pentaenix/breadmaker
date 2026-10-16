from __future__ import annotations

from workspace import workspace

from storage import mutations as storage_mutations
from storage import recovery as storage_recovery

import json
import shutil
import zipfile
from pathlib import Path

import app as app_module
from api.durability import backup_destinations as backup_destinations_api
from workspace import BackupProvider, Workspace


def _package_manifest(path:Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read('manifest.json'))


def _make_destination_backup(client, folder:Path):
    added=client.post('/api/backup-destinations',json={'path':str(folder)})
    assert added.status_code==200,added.text
    destination=added.json()['destination']
    assert destination['status']=='pending'
    caught=client.post('/api/backup-destinations/catch-up')
    assert caught.status_code==200,caught.text
    return destination


def test_v741_fresh_install_discovers_old_project_without_project_id(client,fresh_project,tmp_path):
    usb=tmp_path/'surviving-usb';usb.mkdir()
    _make_destination_backup(client,usb)
    original_id=fresh_project['project']['id']

    replacement=Workspace(tmp_path/'replacement-home');replacement.init()
    blank=replacement.create_project('Blank replacement project')
    destination=replacement.add_backup_destination(str(usb),blank['id'])
    discovered=replacement.discover_destination_backups(destination['id'],blank['id'])
    candidates=[row for row in discovered if row.get('candidate')]
    assert candidates and any(row['project_id']==original_id for row in candidates)
    selected=next(row for row in candidates if row['project_id']==original_id)
    assert selected['name']==fresh_project['name']
    assert selected['verified'] is False
    assert selected['verification_status']=='not_verified'

    with replacement.materialize_destination_backup(destination['id'],selected['file'],blank['id']) as package:
        verified=replacement.verify_package(package)
        assert verified['project_id']==original_id
        restored=replacement.import_project(package,name_override='Recovered novel')
    assert restored['id']!=original_id
    restored_manifest=replacement.read_manifest(Path(restored['path']))
    assert 'backup_destinations' not in restored_manifest


def test_v741_all_project_packages_strip_machine_backup_configuration(client,fresh_project,tmp_path):
    root=Path(fresh_project['project']['path'])
    manifest=workspace.read_manifest(root)
    manifest.update({
        'backup_destinations_version':1,
        'backup_destinations':[{'id':'secret-destination','provider':'local_folder','path':'/Volumes/Writing USB','location_id':'local-secret','volume_id':'volume-secret','device_identity':'disk-secret','last_verified':{'source_token':'token'}}],
        'external_backup_dir':'/legacy/private/path',
        'external_backup_location_id':'legacy-location',
        'external_backup_device':'legacy-disk',
        'cloud_provider_config':{'account_id':'account-secret','oauth_ref':'oauth-secret'},
    })
    workspace._write_manifest(root,manifest)

    exported=workspace.export_project(fresh_project['project']['id'],tmp_path/'portable.breadmaker')
    local_backup=storage_recovery.backup_project('portable-manifest-proof')
    assert local_backup is not None
    forbidden={'backup_destinations','backup_destinations_version','external_backup_dir','external_backup_location_id','external_backup_device','cloud_provider_config'}
    for package in (exported,local_backup):
        package_manifest=_package_manifest(package)
        assert not (forbidden & set(package_manifest))
        assert package_manifest['project_id']==fresh_project['project']['id']

    # Older/contaminated packages remain recoverable, but installation state is
    # discarded on import even though the manifest itself is not part of file_hashes.
    contaminated=tmp_path/'contaminated-old-package.breadmaker'
    with zipfile.ZipFile(exported) as src:
        entries={info.filename:src.read(info.filename) for info in src.infolist() if not info.is_dir()}
    old_manifest=json.loads(entries['manifest.json'])
    old_manifest.update({
        'backup_destinations':[{'id':'old','provider':'local_folder','path':'/old/machine/path','location_id':'old-location'}],
        'external_backup_dir':'/old/legacy/path','provider_auth_reference':'never-import-me',
    })
    entries['manifest.json']=json.dumps(old_manifest,ensure_ascii=False,indent=2).encode('utf-8')
    with zipfile.ZipFile(contaminated,'w',compression=zipfile.ZIP_DEFLATED) as out:
        for name,payload in entries.items():out.writestr(name,payload)
    imported=workspace.import_project(contaminated,name_override='Sanitized import')
    imported_manifest=workspace.read_manifest(Path(imported['path']))
    assert 'backup_destinations' not in imported_manifest
    assert 'external_backup_dir' not in imported_manifest
    assert 'provider_auth_reference' not in imported_manifest


def test_v741_available_write_failure_is_needs_attention_and_success_clears_it(client,fresh_project,tmp_path,monkeypatch):
    folder=tmp_path/'failing-usb';folder.mkdir()
    destination=_make_destination_backup(client,folder)
    db=Path(fresh_project['project']['path'])/'project.sqlite3';db.touch()
    provider=workspace._backup_providers['local_folder'];original=provider.store

    with storage_mutations.tx(operation='v741-unprotected-generation') as con:
        pid=int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()['id'])
        con.execute('UPDATE projects SET title=? WHERE id=?',('Unprotected write',pid))
    def fail(*args,**kwargs):raise OSError('disk full')
    monkeypatch.setattr(provider,'store',fail)
    result=workspace.backup_destinations_catch_up(reason='failure-state-proof',force=True,quiet_seconds=0)
    row=next(item for item in result['destinations'] if item['id']==destination['id'])
    assert row['available'] is True
    assert row['status']=='needs_attention'
    assert row['reason']=='backup_failed'
    assert row['last_error']=='BACKUP_WRITE_FAILED'

    monkeypatch.setattr(provider,'store',original)
    recovered=workspace.backup_destinations_catch_up(reason='failure-clear-proof',force=True,quiet_seconds=0)
    row=next(item for item in recovered['destinations'] if item['id']==destination['id'])
    assert row['status']=='up_to_date'
    assert 'last_error' not in row


def test_v741_external_erasure_preflight_rejects_disconnected_configured_destination(tmp_path):
    home=tmp_path/'home';usb=tmp_path/'usb';usb.mkdir()
    ws=Workspace(home);ws.init();project=ws.create_project('Privacy project')
    destination=ws.add_backup_destination(str(usb),project['id'])
    parked=tmp_path/'usb-disconnected';shutil.move(str(usb),str(parked))
    result=ws.erase_known_managed_backup_data()
    assert any(item.get('destination_id')==destination['id'] for item in result['failures'])
    assert Path(project['path']).exists()


def test_v741_delete_all_external_failure_never_blocks_local_privacy_reset(client,fresh_project,monkeypatch):
    monkeypatch.setattr(workspace,'erase_known_managed_backup_data',lambda:{'removed':0,'attempted':1,'failures':[{'reason':'not_connected'}]})
    primary=client.post('/api/danger/challenge',json={'action':'delete_all_data'}).json()
    external=client.post('/api/danger/challenge',json={'action':'delete_external_backups'}).json()
    old_root=Path(fresh_project['project']['path'])
    response=client.post('/api/danger/delete-all-data',json={
        'challenge_id':primary['challenge_id'],'code':primary['code'],
        'erase_external_backups':True,'external_warning_acknowledged':True,
        'external_challenge_id':external['challenge_id'],'external_code':external['code'],
        'replacement_name':'Replacement',
    })
    assert response.status_code==200,response.text
    assert response.json()['external_backup_erase']['failures']==[{'reason':'not_connected'}]
    assert not old_root.exists()
    assert workspace.list_projects()[0]['name']=='Replacement'


def test_v741_destination_listing_is_lightweight_but_restore_fully_verifies(client,fresh_project,tmp_path,monkeypatch):
    folder=tmp_path/'listing-usb';folder.mkdir()
    destination=_make_destination_backup(client,folder)
    packages=list(folder.glob('*.breadmaker'));assert packages

    original_verify=workspace.verify_package
    calls={'n':0}
    def forbidden_verify(*args,**kwargs):
        calls['n']+=1;raise AssertionError('listing must not hash/verify package payloads')
    monkeypatch.setattr(workspace,'verify_package',forbidden_verify)
    listing=client.get(f"/api/backup-destinations/{destination['id']}/discover")
    assert listing.status_code==200,listing.text
    assert listing.json() and listing.json()[0]['candidate'] is True
    assert calls['n']==0
    monkeypatch.setattr(workspace,'verify_package',original_verify)

    # Corrupt the payload after cheap discovery. It remains discoverable metadata,
    # but restore must cross the full hash/integrity boundary and reject it.
    package=packages[0]
    with zipfile.ZipFile(package) as src:
        entries={info.filename:src.read(info.filename) for info in src.infolist() if not info.is_dir()}
    payload=bytearray(entries['project.sqlite3']);payload[len(payload)//2]^=0x01;entries['project.sqlite3']=bytes(payload)
    with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_DEFLATED) as out:
        for name,data in entries.items():out.writestr(name,data)
    cheap=client.get(f"/api/backup-destinations/{destination['id']}/discover")
    assert cheap.status_code==200 and cheap.json()[0]['candidate'] is True
    restored=client.post(f"/api/backup-destinations/{destination['id']}/backups/{package.name}/restore-as-project")
    assert restored.status_code==400
    assert 'BACKUP_PUBLICATION_HASH_MISMATCH' in restored.text or 'PROJECT_PACKAGE_HASH_MISMATCH' in restored.text


def test_v741_add_and_change_destination_only_schedule_initial_catchup(client,fresh_project,tmp_path,monkeypatch):
    calls=[];queued=[]
    def should_not_run_inline(*args,**kwargs):
        calls.append((args,kwargs));raise AssertionError('configuration request performed backup catch-up inline')
    monkeypatch.setattr(workspace,'backup_destinations_catch_up',should_not_run_inline)
    # Observe queueing without letting the background worker race this endpoint test.
    monkeypatch.setattr(backup_destinations_api,'_queue_destination_reconcile',lambda:queued.append('queued'))
    one=tmp_path/'one';one.mkdir()
    created=client.post('/api/backup-destinations',json={'path':str(one)})
    assert created.status_code==200,created.text
    destination=created.json()['destination']
    assert destination['status'] in {'pending','backing_up','up_to_date'}
    assert queued==['queued'] and calls==[]

    two=tmp_path/'two';two.mkdir()
    changed=client.put(f"/api/backup-destinations/{destination['id']}",json={'path':str(two)})
    assert changed.status_code==200,changed.text
    assert changed.json()['destination']['status'] in {'pending','backing_up','up_to_date'}
    assert queued==['queued','queued'] and calls==[]



def test_v741_changing_failed_destination_clears_old_error_to_pending(client,fresh_project,tmp_path,monkeypatch):
    one=tmp_path/'failing-old';one.mkdir();destination=_make_destination_backup(client,one)
    provider=workspace._backup_providers['local_folder'];original=provider.store
    with storage_mutations.tx(operation='v741-change-failed-unprotected') as con:
        pid=int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()['id'])
        con.execute('UPDATE projects SET title=? WHERE id=?',('Unprotected before failure',pid))
    monkeypatch.setattr(provider,'store',lambda *a,**k:(_ for _ in ()).throw(OSError('disk full')))
    workspace.backup_destinations_catch_up(reason='seed-failure',force=True,quiet_seconds=0)
    failed=next(row for row in workspace.list_backup_destinations() if row['id']==destination['id'])
    assert failed['status']=='needs_attention'
    monkeypatch.setattr(provider,'store',original)
    two=tmp_path/'replacement-folder';two.mkdir()
    changed=client.put(f"/api/backup-destinations/{destination['id']}",json={'path':str(two)})
    assert changed.status_code==200,changed.text
    assert changed.json()['destination']['status'] in {'pending','backing_up','up_to_date'}
    assert 'last_error' not in changed.json()['destination']

def test_v741_generic_restore_uses_provider_materialization_not_local_path(client,fresh_project,tmp_path,monkeypatch):
    source=workspace.export_project(fresh_project['project']['id'],tmp_path/'remote-source.breadmaker')

    class FakeRemoteProvider(BackupProvider):
        provider_type='fake_remote'
        def resolve(self,workspace,destination):return {'available':True,'attention':False,'path':None,'reason':''}
        def store(self,*args,**kwargs):raise NotImplementedError
        def list_backups(self,workspace,rec,destination):return [workspace.inspect_package_metadata(source)]
        def discover(self,workspace,destination):return [workspace.inspect_package_metadata(source)]
        def materialize(self,workspace,destination,filename,target_dir):
            target=target_dir/'downloaded.breadmaker';shutil.copy2(source,target);return target

    provider=FakeRemoteProvider();workspace._backup_providers[provider.provider_type]=provider
    root=Path(fresh_project['project']['path'])
    workspace._save_backup_destinations(root,[{
        'id':'remote-destination','provider':'fake_remote','name':'Remote test','created_at':'2026-01-01T00:00:00+00:00','last_verified':None,
    }])
    assert not hasattr(workspace,'destination_backup_path')
    try:
        discovered=client.get('/api/backup-destinations/remote-destination/discover')
        assert discovered.status_code==200 and discovered.json()[0]['project_id']==fresh_project['project']['id']
        before=len(client.get('/api/workspace').json()['projects'])
        restored=client.post('/api/backup-destinations/remote-destination/backups/remote-package.breadmaker/restore-as-project')
        assert restored.status_code==200,restored.text
        assert len(client.get('/api/workspace').json()['projects'])==before+1
    finally:
        workspace._backup_providers.pop(provider.provider_type,None)


def test_v741_locale_version_and_placeholder_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (7,4,1)
    catalogs=[json.loads(path.read_text(encoding='utf-8')) for path in sorted((root/'locales').glob('*.json'))]
    assert len(catalogs)==7
    en=next(cat for cat in catalogs if cat['code']=='en');keys=set(en['strings'])
    required={
        'backup.destinations_write_failed','backup.destinations_recoverable_projects',
        'backup.destinations_project','backup.destinations_candidate_unverified',
        'error.EXTERNAL_BACKUP_ERASE_PREFLIGHT_001','error.EXTERNAL_BACKUP_ERASE_002',
    }
    assert required<=keys
    import re
    ph=lambda value:sorted(re.findall(r'\{[A-Za-z0-9_]+\}',str(value)))
    for cat in catalogs:
        assert set(cat['strings'])==keys
        for key in required:
            assert cat['strings'][key].strip()
            assert ph(cat['strings'][key])==ph(en['strings'][key])
