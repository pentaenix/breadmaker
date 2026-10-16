from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from workspace import workspace

from storage import context as storage_context
from storage import database as storage_database
from storage import history as storage_history
from storage import integrity as storage_integrity
from storage import mutations as storage_mutations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

import app as app_module
from storage import database as storage_database
from workspace import Workspace


def test_v730_release_contract():
    root=Path(__file__).resolve().parents[1]
    index=(root/'static/index.html').read_text()
    appjs=frontend_source_from_app_path(root/'static/app.js')
    assert "crashJournalPut" in appjs and "ensureEditBaseline" in appjs
    assert "api('/api/integrity/deep')" in appjs


def _first_chapter(client):
    boot=client.get('/api/bootstrap').json()
    return boot['chapters'][0]


def test_v730_stale_manuscript_write_is_rejected_without_overwrite(client,fresh_project):
    chapter=_first_chapter(client)
    loaded=client.get(f"/api/chapters/{chapter['id']}").json()
    old_revision=loaded['revision']
    changed=client.put(f"/api/chapters/{chapter['id']}/blocks",json={
        'expected_revision':old_revision,
        'blocks':[{'text_content':'new committed text','content_html':'new committed text'}],
    })
    assert changed.status_code==200,changed.text
    stale=client.put(f"/api/chapters/{chapter['id']}/blocks",json={
        'expected_revision':old_revision,
        'blocks':[{'text_content':'stale overwrite','content_html':'stale overwrite'}],
    })
    assert stale.status_code==409,stale.text
    detail=stale.json()['detail']
    assert detail['code']=='MANUSCRIPT_CONFLICT_001'
    current=client.get(f"/api/chapters/{chapter['id']}").json()
    assert current['blocks'][0]['text_content']=='new committed text'


def test_v730_chapter_title_uses_same_revision_boundary(client,fresh_project):
    chapter=_first_chapter(client)
    loaded=client.get(f"/api/chapters/{chapter['id']}").json()
    old=loaded['revision']
    first=client.patch(f"/api/chapters/{chapter['id']}/title",json={'title':'Current title','expected_revision':old})
    assert first.status_code==200,first.text
    stale=client.patch(f"/api/chapters/{chapter['id']}/title",json={'title':'Stale title','expected_revision':old})
    assert stale.status_code==409
    assert client.get(f"/api/chapters/{chapter['id']}").json()['chapter']['title']=='Current title'


def test_v730_registry_is_reconstructible_but_missing_manifest_fails_closed(client,fresh_project,tmp_path):
    source=Path(fresh_project['project']['path'])
    root=tmp_path/'home'; project_id='recoveredproject'
    target=root/'projects'/project_id
    target.parent.mkdir(parents=True)
    shutil.copytree(source,target)

    # 7.8 contract: registry.json is rebuildable discovery metadata, but
    # manifest.json contains non-reconstructible project/safety configuration.
    # Losing the manifest must never manufacture guessed state or silently open
    # the project. The untouched folder is surfaced as a Recovery candidate.
    (target/'manifest.json').unlink(missing_ok=True)
    ws=Workspace(root)
    ws.init()
    assert ws.list_projects()==[]
    assert not (target/'manifest.json').exists()
    candidates=ws.recovery_candidates()
    assert any(row['id']==project_id and row['readable'] is True for row in candidates)

    # Losing the reconstructible registry again must preserve the same fail-closed
    # result; registry reconstruction may not recreate a missing project manifest.
    ws.registry_path.unlink()
    ws2=Workspace(root); ws2.init()
    assert ws2.list_projects()==[]
    assert any(row['id']==project_id and row['readable'] is True for row in ws2.recovery_candidates())
    assert not (target/'manifest.json').exists()


def test_v730_backup_destination_uses_existing_folder_and_never_deletes_unrelated_files(client,fresh_project,tmp_path):
    folder=tmp_path/'existing-backups'; folder.mkdir()
    unrelated=folder/'friend-notes.txt'; unrelated.write_text('keep me',encoding='utf-8')
    added=client.post('/api/backup-destinations',json={'path':str(folder)})
    assert added.status_code==200,added.text
    destination=added.json()['destination']
    made=client.post(f"/api/backup-destinations/{destination['id']}/now")
    assert made.status_code==200,made.text
    rows=workspace.discover_destination_backups(destination['id'])
    assert rows and rows[0]['publication_state']=='published'
    assert (folder/rows[0]['file']).is_file()
    erased=workspace.erase_known_managed_backup_data()
    assert erased['removed']>=1 and not erased['failures']
    assert unrelated.read_text()=='keep me'
    assert folder.exists()


def test_v730_failed_destination_verification_removes_new_package(client,fresh_project,tmp_path,monkeypatch):
    folder=tmp_path/'bad-backup'; folder.mkdir()
    destination=workspace.add_backup_destination(str(folder),project_id=fresh_project['project']['id'])
    original=workspace.verify_package
    calls={'n':0}
    def reject_once(path):
        calls['n']+=1
        if calls['n']==2: raise RuntimeError('forced verification failure')
        return original(path)
    monkeypatch.setattr(workspace,'verify_package',reject_once)
    response=client.post(f"/api/backup-destinations/{destination['id']}/now")
    assert response.status_code>=400
    assert list(folder.glob('*.breadmaker'))==[]
    assert list(folder.glob('*.publication.json'))==[]


def test_v730_project_trash_is_reversible(client,fresh_project,tmp_path):
    source=Path(fresh_project['project']['path'])
    root=tmp_path/'trash-home'; project_id='d'*32
    target=root/'projects'/project_id; target.parent.mkdir(parents=True)
    shutil.copytree(source,target)
    manifest=json.loads((target/'manifest.json').read_text());manifest['project_id']=project_id
    (target/'manifest.json').write_text(json.dumps(manifest))
    with sqlite3.connect(target/'project.sqlite3') as con:con.execute('UPDATE projects SET project_uid=?',(project_id,));con.commit()
    ws=Workspace(root);ws.init()
    ws.delete_project(project_id,permanent=False)
    items=ws.list_trash(); assert len(items)==1
    assert not target.exists()
    restored=ws.restore_from_trash(items[0]['trash_id'])
    assert restored['id']==project_id
    assert (Path(restored['path'])/'project.sqlite3').is_file()


def test_v730_deep_integrity_detects_current_and_history_tampering(client,fresh_project):
    chapter=_first_chapter(client)
    data=client.get(f"/api/chapters/{chapter['id']}").json(); block=data['blocks'][0]
    saved=client.put(f"/api/chapters/{chapter['id']}/blocks",json={'expected_revision':data['revision'],'blocks':[{'id':block['id'],'text_content':'integrity text','content_html':'integrity text'}]})
    assert saved.status_code==200
    with storage_mutations.tx() as con:
        original=con.execute('SELECT content_hash FROM manuscript_blocks WHERE id=?',(block['id'],)).fetchone()[0]
        con.execute('UPDATE manuscript_blocks SET content_hash=? WHERE id=?',('0'*64,block['id']))
    health=storage_integrity.deep_integrity_check()
    assert health['ok'] is False and any('Current block hash mismatch' in x for x in health['errors'])
    with storage_mutations.tx() as con: con.execute('UPDATE manuscript_blocks SET content_hash=? WHERE id=?',(original,block['id']))
    store=storage_history.local_revision_store();revision=store.capture(label='Integrity test',creation_reason='v730',force_checkpoint=True)
    manifest_path=next(store.revisions_dir.glob(f"*-{revision['revision_id']}.revision.json"));original_manifest=manifest_path.read_bytes()
    try:
        manifest_path.write_text('{broken',encoding='utf-8')
        health=storage_integrity.deep_integrity_check()
        assert health['ok'] is False and health['history_v1']['ok'] is False
    finally:manifest_path.write_bytes(original_manifest)
    assert storage_integrity.deep_integrity_check()['ok'] is True


def test_v730_old_schema_rejection_does_not_attempt_migration_backup(fresh_project,tmp_path,monkeypatch):
    root=tmp_path/'old-project';root.mkdir();db=root/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.executescript("CREATE TABLE storage_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL); INSERT INTO storage_meta VALUES('schema_version','21');")
        con.commit()
    finally:con.close()
    before=db.read_bytes();old_root=storage_context.PROJECT_ROOT
    try:
        storage_context.configure_project(root)
        with pytest.raises(storage_database.UnsupportedProjectVersion,match='PREALPHA_PROJECT_INCOMPATIBLE'):storage_database.init_db()
        assert db.read_bytes()==before
        assert not (root/'assets').exists() and not (root/'backups').exists()
    finally:
        if old_root is not None:storage_context.configure_project(old_root)


def test_v730_current_history_retention_setting_round_trips(client,fresh_project):
    initial=client.get('/api/history/retention');assert initial.status_code==200,initial.text
    assert initial.json()['local_revision_history_retention'] in {'30_days','90_days','1_year','forever'}
    updated=client.put('/api/history/retention',json={'value':'30_days'});assert updated.status_code==200,updated.text
    assert updated.json()['local_revision_history_retention']=='30_days'
    assert client.get('/api/history/retention').json()['local_revision_history_retention']=='30_days'


def test_v730_release_locales_keep_placeholder_parity():
    catalogs=[json.loads(p.read_text()) for p in sorted((Path(__file__).parents[1]/'locales').glob('*.json'))]
    assert len(catalogs)==7
    en=next(c for c in catalogs if c['code']=='en')
    keys=set(en['strings'])
    required={'error.MANUSCRIPT_CONFLICT_001','backup.folder_title','backup.folder_same_disk_warning','recovery.drafts_title','trash.title','danger.erase_external_backups'}
    assert required<=keys
    import re
    ph=lambda v:sorted(re.findall(r'\{[A-Za-z0-9_]+\}',str(v)))
    for cat in catalogs:
        assert set(cat['strings'])==keys
        for key in required:
            assert cat['strings'][key].strip()
            assert ph(cat['strings'][key])==ph(en['strings'][key])


def test_v730_recovery_mode_boots_when_only_project_database_is_corrupt(fresh_project,tmp_path):
    import os, subprocess, sys
    source=Path(fresh_project['project']['path'])
    root=tmp_path/'recovery-home'; project_id='a'*32
    target=root/'projects'/project_id; target.parent.mkdir(parents=True)
    shutil.copytree(source,target)
    manifest=json.loads((target/'manifest.json').read_text()); manifest['project_id']=project_id
    (target/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    with sqlite3.connect(target/'project.sqlite3') as con:con.execute('UPDATE projects SET project_uid=?',(project_id,));con.commit()
    # Build a valid registry first, then make the only project unreadable.
    ws=Workspace(root); ws.init()
    assert ws.get(project_id)['id']==project_id
    (target/'project.sqlite3').write_bytes(b'not a sqlite database')
    env=dict(os.environ); env['BREADMAKER_HOME']=str(root); env['PYTHONPATH']=str(Path(__file__).parents[1]/'src')
    code="""
import json, app
from backend import recovery_runtime, request_pipeline
from workspace import workspace
request_pipeline.startup()
print(json.dumps({'recovery': bool(recovery_runtime.current_recovery_mode()), 'projects': workspace.list_projects(), 'candidates': workspace.recovery_candidates()}))
"""
    run=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parents[1],env=env,capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout.strip().splitlines()[-1])
    assert result['recovery'] is True
    assert result['projects']==[]
    assert result['candidates'] and result['candidates'][0]['id']==project_id
    assert (target/'project.sqlite3').read_bytes()==b'not a sqlite database'


def test_v730_package_verifier_rejects_missing_current_asset_even_with_self_consistent_manifest(client,fresh_project,tmp_path):
    chapter=_first_chapter(client); loaded=client.get(f"/api/chapters/{chapter['id']}").json()
    storage_context.ASSET_DIR.mkdir(parents=True,exist_ok=True); asset=storage_context.ASSET_DIR/'only-copy.bin'; asset.write_bytes(b'asset bytes')
    saved=client.put(f"/api/chapters/{chapter['id']}/blocks",json={
        'expected_revision':loaded['revision'],
        'blocks':[{'block_type':'image','semantic_style':'image','image_path':'/api/assets/only-copy.bin'}],
    })
    assert saved.status_code==200,saved.text
    good=workspace.export_project(workspace.active_id(),tmp_path/'good.breadmaker')
    bad=tmp_path/'missing-asset.breadmaker'
    import zipfile, hashlib
    with zipfile.ZipFile(good) as src:
        entries={name:src.read(name) for name in src.namelist() if name!='assets/only-copy.bin'}
    manifest=json.loads(entries['manifest.json'])
    manifest['file_hashes'].pop('assets/only-copy.bin',None)
    entries['manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    with zipfile.ZipFile(bad,'w',compression=zipfile.ZIP_DEFLATED) as out:
        for name,payload in entries.items(): out.writestr(name,payload)
    from workspace import WorkspaceError
    with pytest.raises(WorkspaceError,match='PROJECT_PACKAGE_CURRENT_ASSET_MISSING'):
        workspace.verify_package(bad)


def test_v730_delete_all_requires_second_confirmation_before_external_backup_erasure(client,fresh_project,tmp_path):
    folder=tmp_path/'external';folder.mkdir()
    added=client.post('/api/backup-destinations',json={'path':str(folder)});assert added.status_code==200,added.text
    did=added.json()['destination']['id']
    assert client.post(f'/api/backup-destinations/{did}/now').status_code==200
    packages=list(folder.glob('*.breadmaker'));assert packages
    challenge=client.post('/api/danger/challenge',json={'action':'delete_all_data'}).json()
    response=client.post('/api/danger/delete-all-data',json={'challenge_id':challenge['challenge_id'],'code':challenge['code'],'erase_external_backups':True,'replacement_name':'Replacement'})
    assert response.status_code==400
    assert packages[0].exists()
    assert Path(fresh_project['project']['path']).exists()


def test_v730_trash_project_backup_destination_is_included_in_explicit_external_cleanup(fresh_project,tmp_path):
    source=Path(fresh_project['project']['path']);root=tmp_path/'trash-backup-home';project_id='e'*32
    target=root/'projects'/project_id;target.parent.mkdir(parents=True);shutil.copytree(source,target)
    manifest=json.loads((target/'manifest.json').read_text());manifest['project_id']=project_id
    with sqlite3.connect(target/'project.sqlite3') as con:con.execute('UPDATE projects SET project_uid=?',(project_id,));con.commit()
    backup_folder=tmp_path/'trash-external';backup_folder.mkdir();location_id='c'*32
    from workspace.filesystem import _write_external_backup_marker
    _write_external_backup_marker(backup_folder,location_id)
    manifest['backup_destinations_version']=3
    manifest['backup_destinations']=[{'id':'d'*32,'provider':'local_folder','destination_kind':'local','path':str(backup_folder),'location_id':location_id}]
    (target/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    managed=backup_folder/f'breadmaker-{project_id}-20260101_000000_000000.breadmaker';managed.write_bytes(b'managed')
    publication=backup_folder/(managed.name+'.publication.json');publication.write_text('{}')
    unrelated=backup_folder/'other.txt';unrelated.write_text('preserve')
    ws=Workspace(root);ws.init();ws.delete_project(project_id,permanent=False)
    targets=ws._known_backup_erasure_targets();assert any(Path(row['destination'].get('path','')).resolve()==backup_folder.resolve() for row in targets if row['destination']['provider']=='local_folder')
    result=ws.erase_known_managed_backup_data()
    assert result['removed']==1 and not managed.exists() and not publication.exists()
    assert unrelated.read_text()=='preserve'


def test_v730_low_disk_report_warns_and_marks_critical(monkeypatch):
    import shutil as _shutil
    usage=_shutil._ntuple_diskusage(total=100*1024**3,used=99_600*1024**2,free=400*1024**2)
    monkeypatch.setattr(_shutil,'disk_usage',lambda _path:usage)
    report=storage_integrity.disk_health_report()
    assert report['warning'] is True and report['critical'] is True


def test_v730_disconnected_backup_destination_is_not_silently_recreated(client,fresh_project,tmp_path):
    folder=tmp_path/'removable-drive'/'breadmaker';folder.mkdir(parents=True)
    configured=client.post('/api/backup-destinations',json={'path':str(folder)});assert configured.status_code==200,configured.text
    did=configured.json()['destination']['id'];shutil.rmtree(tmp_path/'removable-drive')
    response=client.post(f'/api/backup-destinations/{did}/now')
    assert response.status_code==409,response.text
    assert not folder.exists(), 'automatic backup must not recreate a disappeared/unmounted destination'


def test_v730_read_only_database_failure_leaves_committed_manuscript_untouched(client,fresh_project,monkeypatch):
    chapter=_first_chapter(client); loaded=client.get(f"/api/chapters/{chapter['id']}").json()
    original_connect=storage_database.connect
    def read_only_connect():
        con=original_connect(); con.execute('PRAGMA query_only=ON'); return con
    monkeypatch.setattr(storage_database,'connect',read_only_connect)
    with pytest.raises(sqlite3.OperationalError,match='readonly'):
        client.put(f"/api/chapters/{chapter['id']}/blocks",json={
            'expected_revision':loaded['revision'],
            'blocks':[{'text_content':'must not commit','content_html':'must not commit'}],
        })
    monkeypatch.setattr(storage_database,'connect',original_connect)
    current=client.get(f"/api/chapters/{chapter['id']}").json()
    assert current['blocks'][0]['text_content']==loaded['blocks'][0]['text_content']


def test_v730_failed_internal_backup_verification_removes_questionable_package(client,fresh_project,monkeypatch):
    before=set(storage_context.BACKUP_DIR.glob('*.breadmaker'))
    original=workspace.verify_package
    monkeypatch.setattr(workspace,'verify_package',lambda _path: (_ for _ in ()).throw(RuntimeError('forced verify failure')))
    response=client.post('/api/backup')
    assert response.status_code==500,response.text
    assert response.json()['detail']['code']=='BACKUP_VERIFY_001'
    assert set(storage_context.BACKUP_DIR.glob('*.breadmaker'))==before
    monkeypatch.setattr(workspace,'verify_package',original)


def test_v730_manuscript_save_triggers_verified_backup_destination(client,fresh_project,tmp_path):
    folder=tmp_path/'auto-external';folder.mkdir()
    configured=client.post('/api/backup-destinations',json={'path':str(folder)});assert configured.status_code==200,configured.text
    did=configured.json()['destination']['id']
    chapter=_first_chapter(client);loaded=client.get(f"/api/chapters/{chapter['id']}").json()
    saved=client.put(f"/api/chapters/{chapter['id']}/blocks",json={'expected_revision':loaded['revision'],'blocks':[{'text_content':'automatic destination proof','content_html':'automatic destination proof'}]})
    assert saved.status_code==200,saved.text
    workspace.backup_destinations_catch_up(reason='test_catch_up',quiet_seconds=0)
    rows=workspace.discover_destination_backups(did);assert rows
    package=folder/rows[0]['file'];assert workspace.verify_package(package)['ok'] is True


def test_v730_same_disk_backup_destination_reports_warning_state(client,fresh_project,tmp_path):
    folder=tmp_path/'same-disk';folder.mkdir()
    result=client.post('/api/backup-destinations',json={'path':str(folder)});assert result.status_code==200,result.text
    expected=(os.stat(folder).st_dev==os.stat(workspace.root).st_dev)
    assert result.json()['destination']['same_disk'] is expected


def test_v730_disk_full_failure_leaves_committed_manuscript_untouched(client,fresh_project,monkeypatch):
    chapter=_first_chapter(client); loaded=client.get(f"/api/chapters/{chapter['id']}").json()
    original_connect=storage_database.connect
    class DiskFullConnection:
        def __init__(self, inner): self._inner=inner
        def execute(self, sql, *args, **kwargs):
            if str(sql).strip().upper().startswith('BEGIN IMMEDIATE'):
                raise sqlite3.OperationalError('database or disk is full')
            return self._inner.execute(sql,*args,**kwargs)
        def __getattr__(self,name): return getattr(self._inner,name)
    monkeypatch.setattr(storage_database,'connect',lambda:DiskFullConnection(original_connect()))
    with pytest.raises(sqlite3.OperationalError,match='disk is full'):
        client.put(f"/api/chapters/{chapter['id']}/blocks",json={
            'expected_revision':loaded['revision'],
            'blocks':[{'text_content':'must not survive disk full','content_html':'must not survive disk full'}],
        })
    monkeypatch.setattr(storage_database,'connect',original_connect)
    current=client.get(f"/api/chapters/{chapter['id']}").json()
    assert current['blocks'][0]['text_content']==loaded['blocks'][0]['text_content']
