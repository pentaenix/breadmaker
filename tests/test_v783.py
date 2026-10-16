from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import history as storage_history

from pathlib import Path

import pytest

import app as app_module
from backend import recovery_runtime
from revision_store.remote_history import RemoteHistoryCorruption, RemoteHistoryRepository

from test_v777 import MemoryTransport
from test_v782 import _delete_all_payload


def test_v783_delete_all_clears_stale_recovery_state_before_response(client, fresh_project):
    recovery_runtime.set_recovery_mode({"active_project_id":fresh_project['project']['id'],"diagnostic":"stale-test"})
    response=client.post('/api/danger/delete-all-data',json=_delete_all_payload(client))
    assert response.status_code==200,response.text
    state=client.get('/api/workspace').json()
    assert recovery_runtime.current_recovery_mode() is None
    assert state['recovery'] is None
    assert len(state['projects'])==1
    assert state['active_project_id']==state['projects'][0]['id']


def test_v783_delete_all_progress_waits_for_server_then_refreshes_and_recovery_keeps_delete_in_settings():
    js=frontend_source_from_app_path(Path(__file__).resolve().parents[1]/'static'/'app.js')
    recovery=js[js.index('async function openRecoveryBoot()'):js.index('function applyCanvasConfig()',js.index('async function openRecoveryBoot()'))]
    assert 'Delete All Data' not in recovery
    assert 'recoveryDeleteAll' not in recovery
    assert 'recoveryProjectSettings' in recovery
    settings=js[js.index('async function openProjectSettings()'):js.index('function backupDestinationStatusLabel')]
    assert "projectSettingsRow('settingsDeleteAll','Delete All Data…'" in settings
    assert 'function beginDeleteAllProgress' in js
    assert "close.disabled=true" in js[js.index('function beginDeleteAllProgress'):js.index('async function completeDeleteAllProgress')]
    run=js[js.index('async function runDangerDelete'):js.index('async function openAllProjects')]
    begin=run.index('beginDeleteAllProgress')
    request=run.index("await api('/api/danger/delete-all-data'",begin)
    complete=run.index('await completeDeleteAllProgress()',request)
    refresh=run.index("location.replace('/')",complete)
    assert begin < request < complete < refresh


def test_v783_partial_remote_authority_blocks_every_writer_without_reconstruction(fresh_project):
    store=storage_history.local_revision_store()
    revision=store.capture(label='authority split',creation_reason='v783',force_checkpoint=True)
    transport=MemoryTransport();workspace_id=str(store.workspace_project_id);owner='1'*32
    repo=RemoteHistoryRepository(transport,workspace_project_id=workspace_id,writer_id=owner,canonical_tables=store.canonical_tables)
    repo.publish(store,revision['revision_id'])
    transport.data.pop(repo._repo_path())
    before=dict(transport.data)
    for writer in ('2'*32,owner):
        with pytest.raises(RemoteHistoryCorruption,match='AUTHORITY_INCOMPLETE'):
            RemoteHistoryRepository(transport,workspace_project_id=workspace_id,writer_id=writer,canonical_tables=store.canonical_tables).claim_writer()
        assert transport.data==before
