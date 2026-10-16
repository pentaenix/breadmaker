from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

import json
import re
from pathlib import Path

import app as app_module
from api.durability import backup_destinations as backup_destinations_api


def test_v731_release_and_recovery_ui_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (7,3,1)
    index=(root/'static/index.html').read_text()
    appjs=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'styles.css?v=' in index and 'app.js?v=' in index
    assert "projectSettingsRow('settingsBackup'" not in appjs
    assert "projectSettingsRow('settingsRecovery',i18nText('backup.recovery_title')" in appjs
    assert "projectSettingsRow('settingsBackups',i18nText('backup.destinations_title')" in appjs
    assert 'externalBackupPath' not in appjs
    assert "/api/backup-destinations/pick-folder" in appjs
    assert 'recovery-backup-list' in appjs
    assert 'b.created_at?i18nDateTime(b.created_at)' in appjs
    assert '.recovery-backup-list{' in css and 'overflow-y:auto' in css
    assert '.backup-folder-copy b{' in css and 'text-overflow:ellipsis' in css


def test_v731_native_folder_picker_endpoint_supports_choose_and_cancel(client,fresh_project,tmp_path,monkeypatch):
    folder=tmp_path/'writer backups'; folder.mkdir()
    monkeypatch.setattr(backup_destinations_api,'_choose_local_directory',lambda:str(folder))
    picked=client.post('/api/backup-destinations/pick-folder')
    assert picked.status_code==200,picked.text
    assert picked.json()=={'ok':True,'cancelled':False,'path':str(folder)}
    configured=client.post('/api/backup-destinations',json={'path':picked.json()['path']})
    assert configured.status_code==200,configured.text
    assert configured.json()['destination']['available'] is True
    monkeypatch.setattr(backup_destinations_api,'_choose_local_directory',lambda:None)
    cancelled=client.post('/api/backup-destinations/pick-folder')
    assert cancelled.status_code==200,cancelled.text
    assert cancelled.json()['cancelled'] is True and cancelled.json()['path']==''


def test_v731_all_locales_have_compact_backup_ui_and_matching_placeholders():
    root=Path(__file__).resolve().parents[1]
    files=sorted((root/'locales').glob('*.json'))
    catalogs=[json.loads(p.read_text(encoding='utf-8')) for p in files]
    assert {c['code'] for c in catalogs}=={'en','es','fr','it','ja','pt-BR','zh-CN'}
    keys={
        'backup.folder_title','backup.folder_description','backup.folder_choose','backup.folder_current',
        'backup.folder_none','backup.folder_last','backup.folder_never','backup.folder_disable',
        'backup.folder_now','backup.folder_saved','backup.folder_disabled','backup.folder_same_disk_warning',
        'backup.folder_unavailable_warning','backup.folder_created','backup.folder_picker_failed',
        'backup.recovery_title','backup.recovery_description','backup.recovery_create','backup.recovery_created',
        'backup.recovery_empty','backup.recovery_restore','backup.recovery_restore_title',
        'backup.recovery_restore_body','backup.recovery_restore_confirm','backup.recovery_unknown',
        'error.BACKUP_DESTINATION_PICKER_001',
    }
    base=set(catalogs[0]['strings'])
    assert all(set(c['strings'])==base for c in catalogs)
    assert all(keys <= set(c['strings']) for c in catalogs)
    token=re.compile(r'\{[^{}]+\}')
    english=next(c for c in catalogs if c['code']=='en')['strings']
    for catalog in catalogs:
        for key in keys:
            assert sorted(token.findall(catalog['strings'][key]))==sorted(token.findall(english[key]))
    ja=next(c for c in catalogs if c['code']=='ja')['strings']
    assert ja['backup.folder_title']=='バックアップ先'
    assert len(ja['backup.folder_description']) < 30
