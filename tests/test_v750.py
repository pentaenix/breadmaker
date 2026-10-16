from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from workspace import workspace

import json
import re
import shutil
import zipfile
from pathlib import Path

import app as app_module
from backend import recovery_runtime
from workspace import Workspace


def _destinations(client):
    response=client.get('/api/backup-destinations')
    assert response.status_code==200,response.text
    return response.json()['destinations']


def test_v750_release_assets_locales_and_ui_contract():
    root=Path(__file__).resolve().parents[1]
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (7,5,0)
    index=(root/'static/index.html').read_text(encoding='utf-8')
    appjs=frontend_source_from_app_path(root/'static/app.js')
    assert 'chooseBackupDestinationKind' in appjs
    assert "destination_kind:destinationKind" in appjs
    assert "backup.destinations_synced_verified_scope" in appjs
    assert "backup.destinations_synced_same_disk_warning" in appjs

    catalogs=[json.loads(path.read_text(encoding='utf-8')) for path in sorted((root/'locales').glob('*.json'))]
    assert len(catalogs)==7
    en=next(cat for cat in catalogs if cat['code']=='en')
    keys=set(en['strings'])
    required={
        'backup.destinations_choose_type','backup.destinations_type_local','backup.destinations_type_synced',
        'backup.destinations_synced_notice','backup.destinations_synced_verified_scope',
        'backup.destinations_synced_same_disk_warning','backup.destinations_last_local_verified',
        'backup.destinations_kind_invalid','danger.erase_external_backups_help',
    }
    assert required<=keys
    token=re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')
    for catalog in catalogs:
        assert set(catalog['strings'])==keys,catalog['code']
        assert catalog.get('source_index')==en.get('source_index'),catalog['code']
        for key in keys:
            assert sorted(token.findall(str(catalog['strings'][key])))==sorted(token.findall(str(en['strings'][key]))),(catalog['code'],key)


def test_v750_old_destination_manifest_version_is_rejected_without_rewrite(client,fresh_project,tmp_path):
    folder=tmp_path/'old-destination';folder.mkdir()
    added=client.post('/api/backup-destinations',json={'path':str(folder)}).json()['destination']
    root=Path(fresh_project['project']['path']);manifest=workspace.read_manifest(root)
    manifest['backup_destinations_version']=1
    manifest['backup_destinations'][0].pop('destination_kind',None)
    workspace._write_manifest(root,manifest)
    before=(root/'manifest.json').read_bytes()
    response=client.get('/api/backup-destinations')
    assert response.status_code==409,response.text
    assert 'BACKUP_DESTINATIONS_VERSION_UNSUPPORTED' in response.text
    assert (root/'manifest.json').read_bytes()==before


def test_v750_synced_folder_is_classification_over_local_folder_provider(client,fresh_project,tmp_path):
    folder=tmp_path/'Dropbox-looking-folder';folder.mkdir()
    synced=client.post('/api/backup-destinations',json={'path':str(folder),'destination_kind':'synced_folder'})
    assert synced.status_code==200,synced.text
    row=synced.json()['destination']
    assert row['provider']=='local_folder'
    assert row['destination_kind']=='synced_folder'
    assert row['status']=='pending'

    # Breadmaker never guesses cloud semantics from a path name. Explicit user
    # classification is the source of truth in 7.5.
    other=tmp_path/'Google Drive but ordinary';other.mkdir()
    local=client.post('/api/backup-destinations',json={'path':str(other),'destination_kind':'local'})
    assert local.status_code==200,local.text
    assert local.json()['destination']['destination_kind']=='local'

    invalid=tmp_path/'invalid-kind';invalid.mkdir()
    rejected=client.post('/api/backup-destinations',json={'path':str(invalid),'destination_kind':'google_drive'})
    assert rejected.status_code==400
    assert 'BACKUP_DESTINATION_KIND_INVALID' in rejected.text


def test_v750_local_and_synced_destinations_share_one_package_fanout(client,fresh_project,tmp_path,monkeypatch):
    local=tmp_path/'usb';synced=tmp_path/'synced';local.mkdir();synced.mkdir()
    # Unit-test fanout without the HTTP endpoint's intentional background
    # reconciliation; otherwise a legitimate worker export races this measurement.
    pid=fresh_project['project']['id']
    workspace.add_backup_destination(str(local),project_id=pid,destination_kind='local')
    workspace.add_backup_destination(str(synced),project_id=pid,destination_kind='synced_folder')
    calls={'count':0};original=workspace.export_project
    def counted(*args,**kwargs):
        calls['count']+=1
        return original(*args,**kwargs)
    monkeypatch.setattr(workspace,'export_project',counted)
    result=workspace.backup_destinations_catch_up(reason='v750-mixed-fanout',force=True,quiet_seconds=0)
    assert result['ok'] is True
    assert len(result['backed_up'])==2
    assert calls['count']==1
    assert list(local.glob('*.breadmaker'))
    assert list(synced.glob('*.breadmaker'))
    rows=_destinations(client)
    assert {row['destination_kind'] for row in rows}=={'local','synced_folder'}
    assert all(row['status']=='up_to_date' for row in rows)


def test_v750_changing_only_destination_kind_preserves_verified_copy(client,fresh_project,tmp_path):
    folder=tmp_path/'classify-me';folder.mkdir()
    added=client.post('/api/backup-destinations',json={'path':str(folder),'destination_kind':'local'}).json()['destination']
    workspace.backup_destinations_catch_up(reason='v750-seed',quiet_seconds=0)
    before=next(item for item in _destinations(client) if item['id']==added['id'])
    assert before['status']=='up_to_date' and before['last_verified']
    recovery_runtime._BACKUP_DESTINATION_WAKE.clear()

    changed=client.put(f"/api/backup-destinations/{added['id']}",json={'destination_kind':'synced_folder'})
    assert changed.status_code==200,changed.text
    after=changed.json()['destination']
    assert after['destination_kind']=='synced_folder'
    assert after['provider']=='local_folder'
    assert after['status']=='up_to_date'
    assert after['last_verified']==before['last_verified']
    assert recovery_runtime._BACKUP_DESTINATION_WAKE.is_set() is False


def test_v750_synced_destination_configuration_does_not_enter_portable_package(client,fresh_project,tmp_path):
    folder=tmp_path/'synced-portability';folder.mkdir()
    client.post('/api/backup-destinations',json={'path':str(folder),'destination_kind':'synced_folder'})
    package=workspace.export_project(fresh_project['project']['id'],tmp_path/'portable.breadmaker')
    with zipfile.ZipFile(package) as zf:
        manifest=json.loads(zf.read('manifest.json'))
    assert 'backup_destinations' not in manifest
    assert 'backup_destinations_version' not in manifest
    assert 'destination_kind' not in json.dumps(manifest,sort_keys=True)
    assert str(folder) not in json.dumps(manifest,sort_keys=True)


def test_v750_synced_folder_is_fresh_install_recovery_source(client,fresh_project,tmp_path):
    original_id=fresh_project['project']['id'];original_name=fresh_project['project']['name']
    folder=tmp_path/'synced-recovery';folder.mkdir()
    first=client.post('/api/backup-destinations',json={'path':str(folder),'destination_kind':'synced_folder'}).json()['destination']
    caught=client.post('/api/backup-destinations/catch-up')
    assert caught.status_code==200,caught.text
    assert list(folder.glob('*.breadmaker'))

    blank=client.post('/api/projects',json={'name':'Fresh replacement'}).json()['project']
    assert blank['id']!=original_id
    attached=client.post('/api/backup-destinations',json={'path':str(folder),'destination_kind':'synced_folder'})
    assert attached.status_code==200,attached.text
    destination=attached.json()['destination']
    discovered=client.get(f"/api/backup-destinations/{destination['id']}/discover")
    assert discovered.status_code==200,discovered.text
    candidate=next(item for item in discovered.json() if item.get('project_id')==original_id)
    assert candidate['name']==original_name
    assert candidate['candidate'] is True and candidate['verified'] is False

    restored=client.post(f"/api/backup-destinations/{destination['id']}/backups/{candidate['file']}/restore-as-project")
    assert restored.status_code==200,restored.text
    restored_project=restored.json()['project']
    assert restored_project['id'] not in {original_id,blank['id']}



def test_v750_unavailable_online_only_synced_package_fails_restore_without_project_change(client,fresh_project,tmp_path,monkeypatch):
    folder=tmp_path/'online-only-sync';folder.mkdir()
    added=client.post('/api/backup-destinations',json={'path':str(folder),'destination_kind':'synced_folder'}).json()['destination']
    workspace.backup_destinations_catch_up(reason='v750-online-only-seed',quiet_seconds=0)
    package=next(folder.glob('*.breadmaker'))
    before_workspace=client.get('/api/workspace').json();before_ids={item['id'] for item in before_workspace['projects']};before_active=before_workspace['active_project_id']
    provider=workspace._backup_providers['local_folder'];original=provider.materialize
    monkeypatch.setattr(provider,'materialize',lambda *a,**k:(_ for _ in ()).throw(OSError('sync client could not hydrate file')))
    try:
        restored=client.post(f"/api/backup-destinations/{added['id']}/backups/{package.name}/restore-as-project")
        assert restored.status_code==400,restored.text
        assert 'sync client could not hydrate file' in restored.text
        after=client.get('/api/workspace').json()
        assert {item['id'] for item in after['projects']}==before_ids
        assert after['active_project_id']==before_active
    finally:
        monkeypatch.setattr(provider,'materialize',original)

def test_v750_synced_folder_participates_in_fail_closed_privacy_preflight(tmp_path):
    home=tmp_path/'private-workspace';folder=tmp_path/'privacy-sync';folder.mkdir()
    ws=Workspace(home);ws.init();project=ws.create_project('Privacy sync')
    added=ws.add_backup_destination(str(folder),project['id'],destination_kind='synced_folder')
    targets=ws._known_backup_erasure_targets()
    assert folder.resolve() in {Path(row['destination']['path']).resolve() for row in targets if row['destination']['provider']=='local_folder'}

    parked=tmp_path/'privacy-sync-away';shutil.move(str(folder),str(parked))
    blocked=ws.erase_known_managed_backup_data()
    assert any(item.get('destination_id')==added['id'] for item in blocked['failures'])
    assert Path(project['path']).exists()
