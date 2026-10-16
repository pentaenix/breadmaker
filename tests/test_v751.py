from __future__ import annotations

from pathlib import Path

import pytest

from workspace import Workspace, WorkspaceError
from workspace import filesystem as workspace_fs


def test_v751_release_version_and_docs_are_clean():
    root=Path(__file__).resolve().parents[1]
    version=(root/'VERSION').read_text(encoding='utf-8').strip();assert tuple(map(int,version.split('.'))) >= (7,5,1)
    index=(root/'static/index.html').read_text(encoding='utf-8')
    readme=(root/'README.md').read_text(encoding='utf-8')
    architecture=(root/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert readme.count('## 7.4.1 Stage 1 completion — recovery, portability, and provider boundaries')==1
    assert architecture.count('## 7.4.1 recovery/portability boundary')==1


def test_v751_rejects_backup_destination_inside_breadmaker_home_before_creation(tmp_path):
    home=tmp_path/'breadmaker-home'
    ws=Workspace(home);ws.init();project=ws.create_project('Recursive backup guard')
    unsafe=home/'projects'/project['id']/'assets'/'Backups'
    assert not unsafe.exists()
    with pytest.raises(WorkspaceError,match='BACKUP_DESTINATION_INSIDE_BREADMAKER_HOME'):
        ws.add_backup_destination(str(unsafe),project['id'],destination_kind='synced_folder')
    assert not unsafe.exists()
    assert ws.list_backup_destinations(project['id'])==[]


def test_v751_changing_destination_cannot_move_it_inside_breadmaker_home(tmp_path):
    home=tmp_path/'breadmaker-home';outside=tmp_path/'safe-backups';outside.mkdir()
    ws=Workspace(home);ws.init();project=ws.create_project('Move guard')
    added=ws.add_backup_destination(str(outside),project['id'],destination_kind='local')
    unsafe=Path(project['path'])/'assets'
    unsafe.mkdir(exist_ok=True)
    with pytest.raises(WorkspaceError,match='BACKUP_DESTINATION_INSIDE_BREADMAKER_HOME'):
        ws.update_backup_destination(added['id'],path_value=str(unsafe),project_id=project['id'])
    row=next(item for item in ws.list_backup_destinations(project['id']) if item['id']==added['id'])
    assert Path(row['path']).resolve()==outside.resolve()


def test_v751_synced_destination_defaults_to_selected_folder_name(tmp_path):
    home=tmp_path/'breadmaker-home';folder=tmp_path/'My Novel — Dropbox';folder.mkdir()
    ws=Workspace(home);ws.init();project=ws.create_project('Naming')
    added=ws.add_backup_destination(str(folder),project['id'],destination_kind='synced_folder')
    assert added['name']==folder.name


def test_v751_privacy_preflight_fails_closed_for_unknown_destination_kind(tmp_path):
    home=tmp_path/'breadmaker-home';folder=tmp_path/'backup';folder.mkdir()
    ws=Workspace(home);ws.init();project=ws.create_project('Privacy guard')
    added=ws.add_backup_destination(str(folder),project['id'],destination_kind='local')
    inventory=ws._load_backup_erasure_inventory();rows=inventory['projects'][project['id']]
    rows[0]['destination_kind']='future_remote_magic';ws._save_backup_erasure_inventory(inventory)
    result=ws.erase_known_managed_backup_data()
    failure=next(item for item in result['failures'] if item.get('destination_id')==added['id'])
    assert failure['reason']=='unsupported_destination_kind'
    assert folder.exists()


def test_v751_reclassifying_auto_named_destination_refreshes_synced_name(tmp_path,monkeypatch):
    home=tmp_path/'breadmaker-home';folder=tmp_path/'Dropbox Novel';folder.mkdir()
    original=workspace_fs._volume_metadata
    monkeypatch.setattr(workspace_fs,'_volume_metadata',lambda path:{'root':str(tmp_path),'label':'Macintosh HD','id':'test-volume'})
    ws=Workspace(home);ws.init();project=ws.create_project('Reclassify naming')
    added=ws.add_backup_destination(str(folder),project['id'],destination_kind='local')
    assert added['name']=='Macintosh HD'
    changed=ws.update_backup_destination(added['id'],destination_kind='synced_folder',project_id=project['id'])
    assert changed['name']=='Dropbox Novel'
    monkeypatch.setattr(workspace_fs,'_volume_metadata',original)
