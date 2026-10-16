from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from workspace import workspace

import json
from pathlib import Path

import app as app_module
from conftest import assert_ok


def _challenge(client, action: str) -> dict:
    response=client.post('/api/danger/challenge',json={'action':action})
    assert response.status_code==200,response.text
    return response.json()


def _delete_all_payload(client, *, erase_backups: bool=False, warning_ack: bool=True) -> dict:
    main=_challenge(client,'delete_all_data')
    payload={
        'challenge_id':main['challenge_id'],'code':main['code'],
        'replacement_name':'Clean after delete','erase_external_backups':erase_backups,
    }
    if erase_backups:
        external=_challenge(client,'delete_external_backups')
        payload.update({
            'external_warning_acknowledged':warning_ack,
            'external_challenge_id':external['challenge_id'],'external_code':external['code'],
        })
    return payload


def test_v782_delete_all_local_data_does_not_require_readable_supported_project(client,fresh_project):
    old_root=Path(fresh_project['project']['path'])
    (old_root/'manifest.json').write_text('{broken manifest',encoding='utf-8')
    (old_root/'project.sqlite3').write_bytes(b'unsupported/corrupt pre-alpha bytes')
    response=client.post('/api/danger/delete-all-data',json=_delete_all_payload(client))
    assert response.status_code==200,response.text
    assert not old_root.exists()
    state=assert_ok(client.get('/api/workspace'))
    assert len(state['projects'])==1
    assert state['projects'][0]['name']=='Clean after delete'


def test_v782_external_backup_delete_requires_warning_ack_before_second_code(client,fresh_project,tmp_path):
    folder=tmp_path/'backup';folder.mkdir()
    added=assert_ok(client.post('/api/backup-destinations',json={'path':str(folder)}))['destination']
    assert client.post(f"/api/backup-destinations/{added['id']}/now").status_code==200
    packages=list(folder.glob('breadmaker-*.breadmaker'));assert packages
    root=Path(fresh_project['project']['path'])
    response=client.post('/api/danger/delete-all-data',json=_delete_all_payload(client,erase_backups=True,warning_ack=False))
    assert response.status_code==400
    assert 'warning acknowledgement' in response.text
    assert root.exists()
    assert packages[0].exists()


def test_v782_backup_privacy_delete_uses_independent_inventory_and_preserves_unrelated_files(client,fresh_project,tmp_path):
    folder=tmp_path/'external';folder.mkdir()
    destination=assert_ok(client.post('/api/backup-destinations',json={'path':str(folder)}))['destination']
    assert client.post(f"/api/backup-destinations/{destination['id']}/now").status_code==200
    project_id=fresh_project['project']['id']
    managed=list(folder.glob(f'breadmaker-{project_id}-*.breadmaker'));assert managed
    publication=Path(str(managed[0])+'.publication.json');assert publication.exists()
    unrelated=folder/'family-photo.txt';unrelated.write_text('never touch me',encoding='utf-8')
    foreign=folder/'breadmaker-ffffffffffffffffffffffffffffffff-20990101_000000_000000.breadmaker';foreign.write_bytes(b'not this project')
    old_root=Path(fresh_project['project']['path'])
    assert workspace.backup_erasure_inventory_path.exists()
    # Simulate the precise failure mode this inventory exists for: project metadata is no
    # longer readable, but privacy deletion must still know the last verified backup scope.
    (old_root/'manifest.json').write_text('{corrupt after backup configuration',encoding='utf-8')
    response=client.post('/api/danger/delete-all-data',json=_delete_all_payload(client,erase_backups=True))
    assert response.status_code==200,response.text
    result=response.json()['external_backup_erase']
    assert result['failures']==[]
    assert result['removed']>=1
    assert not managed[0].exists() and not publication.exists()
    assert unrelated.read_text(encoding='utf-8')=='never touch me'
    assert foreign.read_bytes()==b'not this project'
    assert not old_root.exists()


def test_v782_backup_identity_uncertainty_never_deletes_unrelated_data_or_blocks_local_reset(client,fresh_project,tmp_path):
    folder=tmp_path/'external';folder.mkdir()
    destination=assert_ok(client.post('/api/backup-destinations',json={'path':str(folder)}))['destination']
    assert client.post(f"/api/backup-destinations/{destination['id']}/now").status_code==200
    project_id=fresh_project['project']['id'];managed=list(folder.glob(f'breadmaker-{project_id}-*.breadmaker'));assert managed
    unrelated=folder/'unrelated.txt';unrelated.write_text('keep',encoding='utf-8')
    marker=folder/'.breadmaker-backup-location.json';data=json.loads(marker.read_text(encoding='utf-8'));data['location_id']='0'*32;marker.write_text(json.dumps(data),encoding='utf-8')
    old_root=Path(fresh_project['project']['path'])
    response=client.post('/api/danger/delete-all-data',json=_delete_all_payload(client,erase_backups=True))
    assert response.status_code==200,response.text
    result=response.json()['external_backup_erase']
    assert result['failures'] and result['failures'][0]['reason']=='identity_mismatch'
    assert managed[0].exists(), 'uncertain ownership must leak rather than delete'
    assert unrelated.read_text(encoding='utf-8')=='keep'
    assert not old_root.exists(), 'external uncertainty must not strand corrupt local Breadmaker data'


def test_v782_recovery_ui_routes_to_always_available_project_settings_and_backup_warning_precedes_second_code():
    js=frontend_source_from_app_path(Path(__file__).resolve().parents[1]/'static'/'app.js')
    recovery_fn=js[js.index('async function openRecoveryBoot()'):js.index('function applyCanvasConfig()',js.index('async function openRecoveryBoot()'))]
    # Recovery is navigation-only. Destructive privacy controls remain exclusively
    # in Project Settings, whose recovery-safe branch does not depend on project DB health.
    assert 'recoveryDeleteAll' not in recovery_fn
    assert 'recoveryProjectSettings' in recovery_fn
    assert 'openProjectSettings()' in recovery_fn
    assert "projectSettingsRow('settingsDeleteAll','Delete All Data…'" in js
    settings=js[js.index('async function openProjectSettings()'):js.index('function backupDestinationStatusLabel')]
    assert "if(recovery)" in settings
    assert "projectSettingsRow('settingsRecovery','Recovery Backups'" in settings
    assert "runDangerDelete('delete_all_data',null,returnToSettings)" in settings
    warning=js.index("danger.external_warning_title")
    external_challenge=js.index("action:'delete_external_backups'",warning)
    external_code=js.index("danger.external_final_title",external_challenge)
    assert warning < external_challenge < external_code
    assert 'external_warning_acknowledged=true' in js


def test_v782_corrupt_manifest_project_id_cannot_broaden_backup_deletion_glob(tmp_path):
    from workspace import Workspace
    from workspace.filesystem import _write_external_backup_marker
    home=tmp_path/'home';project_root=home/'projects'/('a'*32);project_root.mkdir(parents=True)
    folder=tmp_path/'external-malicious';folder.mkdir();location_id='b'*32;_write_external_backup_marker(folder,location_id)
    (project_root/'manifest.json').write_text(json.dumps({
        'project_id':'*',
        'backup_destinations':[{'id':'c'*32,'provider':'local_folder','destination_kind':'local','path':str(folder),'location_id':location_id}],
    }),encoding='utf-8')
    victim=folder/f"breadmaker-{'d'*32}-20990101_000000_000000.breadmaker";victim.write_bytes(b'preserve')
    ws=Workspace(home);ws.init();result=ws.erase_known_managed_backup_data()
    assert result['attempted']==0
    assert victim.read_bytes()==b'preserve'


def test_v782_pending_history_after_delete_all_cannot_recreate_deleted_project(client,fresh_project):
    import time
    chapter=fresh_project['bootstrap']['chapters'][0];old_root=Path(fresh_project['project']['path'])
    changed=client.patch(f"/api/chapters/{chapter['id']}/title",json={'title':'queued just before privacy reset'})
    assert changed.status_code==200,changed.text
    response=client.post('/api/danger/delete-all-data',json=_delete_all_payload(client))
    assert response.status_code==200,response.text
    time.sleep(1.0)  # longer than the local History debounce window
    assert not old_root.exists()
