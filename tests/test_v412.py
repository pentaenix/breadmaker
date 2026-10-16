from __future__ import annotations

from source_contracts import storage_source,frontend_source_from_app_path

from workspace import workspace

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from storage import recovery as storage_recovery

import pytest

from conftest import assert_ok


def _save_text(client, chapter_id:int, text:str):
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[{'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text}]}))


def test_v412_checkpoint_and_history_share_immutable_block_objects(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    _save_text(client,chapter['id'],'Shared immutable paragraph.')
    with storage.tx() as con: pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'Shared base','auto',False)
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Shared'}))
    assert cp['root_hash'] and cp['snapshot_hash']==cp['root_hash']
    with storage.tx() as con:
        row=con.execute('SELECT root_hash,length(snapshot_blob) blob_bytes FROM checkpoints WHERE id=?',(cp['id'],)).fetchone()
        assert row['blob_bytes']==0
        root=json.loads(con.execute('SELECT payload FROM content_objects WHERE hash=?',(row['root_hash'],)).fetchone()[0])
        cp_chunk=json.loads(con.execute('SELECT payload FROM content_objects WHERE hash=?',(root['block_chunks'][0],)).fetchone()[0])
        cp_block_hash=cp_chunk['items'][0]['object_hash']
        tree=json.loads(con.execute('SELECT tree_json FROM revisions WHERE id=?',(rid,)).fetchone()[0])
        hist_chunk=json.loads(con.execute('SELECT payload FROM content_objects WHERE hash=?',(tree['block_groups'][0]['chunks'][0],)).fetchone()[0])
        hist_block_hash=hist_chunk['items'][0]['object_hash']
        assert cp_block_hash==hist_block_hash
        assert con.execute('SELECT kind FROM content_objects WHERE hash=?',(cp_block_hash,)).fetchone()[0]=='manuscript-block'


def test_v412_checkpoint_root_survives_history_prune_and_gc_quarantines_only_after_delete(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    _save_text(client,chapter['id'],'Permanent creative state.')
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Keep me'}))
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        con.execute('DELETE FROM revisions WHERE project_id=?',(pid,))
    first=storage.run_preservation_gc(sweep=False)
    assert first['ok'] is True
    with storage.tx() as con:
        root=con.execute('SELECT root_hash FROM checkpoints WHERE id=?',(cp['id'],)).fetchone()[0]
        assert con.execute('SELECT unreachable_since FROM content_objects WHERE hash=?',(root,)).fetchone()[0] is None
    assert_ok(client.delete(f"/api/checkpoints/{cp['id']}"))
    with storage.tx() as con:
        row=con.execute('SELECT unreachable_since FROM content_objects WHERE hash=?',(root,)).fetchone()
        assert row and row[0]
        old=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat(timespec='microseconds')
        con.execute('UPDATE content_objects SET unreachable_since=? WHERE unreachable_since IS NOT NULL',(old,))
    swept=storage.run_preservation_gc(sweep=True)
    assert swept['ok'] is True
    with storage.tx() as con:
        assert con.execute('SELECT 1 FROM content_objects WHERE hash=?',(root,)).fetchone() is None


def test_v412_cut_root_is_independent_gc_root(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    cut=assert_ok(client.post('/api/cuts',json={'title':'Keep removed prose','content_html':'<p>Discarded but valuable.</p>','source_chapter_id':chapter['id']}))
    with storage.tx() as con:
        row=con.execute('SELECT root_hash FROM cuts WHERE id=?',(cut['id'],)).fetchone(); assert row and row[0]
        root=row[0]; con.execute('DELETE FROM revisions')
    storage.run_preservation_gc(sweep=False)
    with storage.tx() as con:
        assert con.execute('SELECT unreachable_since FROM content_objects WHERE hash=?',(root,)).fetchone()[0] is None


def test_v412_history_restores_deleted_image_from_immutable_asset_bytes(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    name='v412-history-image.png';data=b'\x89PNG\r\n\x1a\nBreadmaker-preserved-image-bytes'
    path=storage.ASSET_DIR/name;path.write_bytes(data)
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'block_type':'image','semantic_style':'body','content_html':'','text_content':'','image_path':f'/api/assets/{name}'}]}))
    with storage.tx() as con: pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'Image exists','auto',False)
    assert path.exists(); path.unlink()
    _save_text(client,chapter['id'],'Image removed from Current.')
    storage.restore_revision(rid,physical_backup=False,preserve_current=False)
    assert path.read_bytes()==data
    restored=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']
    assert restored[0]['block_type']=='image' and restored[0]['image_path']==f'/api/assets/{name}'


def test_v412_corrupt_history_object_fails_closed_without_mutating_current(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    _save_text(client,chapter['id'],'Historical safe text.')
    with storage.tx() as con: pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'Good history','auto',False)
    _save_text(client,chapter['id'],'Current must survive failed restore.')
    with storage.tx() as con:
        tree=json.loads(con.execute('SELECT tree_json FROM revisions WHERE id=?',(rid,)).fetchone()[0])
        chunk=json.loads(con.execute('SELECT payload FROM content_objects WHERE hash=?',(tree['block_groups'][0]['chunks'][0],)).fetchone()[0])
        body_hash=chunk['items'][0]['object_hash']
        original_payload=con.execute('SELECT payload FROM content_objects WHERE hash=?',(body_hash,)).fetchone()[0]
        con.execute('UPDATE content_objects SET payload=? WHERE hash=?',('{"corrupt":true}',body_hash))
    with pytest.raises(ValueError):
        storage.restore_revision(rid,physical_backup=False,preserve_current=False)
    blocks=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']
    assert blocks[0]['text_content']=='Current must survive failed restore.'
    # Repair the intentional test corruption so subsequent TestClient startups can
    # validate the shared test workspace normally.
    with storage.tx() as con: con.execute('UPDATE content_objects SET payload=? WHERE hash=?',(original_payload,body_hash))


def test_v412_raw_recovery_backups_are_wal_safe_distinct_and_bounded(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    first=storage.backup_db('v412')
    same=storage.backup_db('v412')
    assert same==first
    for i in range(7):
        _save_text(client,chapter['id'],f'Backup state {i}')
        storage.backup_db('v412')
    paths=sorted(storage.BACKUP_DIR.glob('breadmaker_*.sqlite3'))
    assert len(paths)<=5
    # The newest copy must contain the last committed WAL-visible text.
    newest=max(paths,key=lambda p:p.stat().st_mtime)
    import sqlite3
    con=sqlite3.connect(newest)
    try: assert con.execute('SELECT text_content FROM manuscript_blocks ORDER BY rowid LIMIT 1').fetchone()[0]=='Backup state 6'
    finally: con.close()


def test_v412_storage_diagnostics_and_scrub_are_safe(client,fresh_project):
    report=assert_ok(client.get('/api/preservation/storage'))
    assert report['integrity']['ok'] is True
    assert report['storage']['db_bytes']>0
    assert 'content_objects' in report['storage'] and 'recovery_bytes' in report['storage']
    scrub=assert_ok(client.post('/api/preservation/scrub'))
    assert scrub['ok'] is True and scrub['integrity']['preservation_ok'] is True


def test_v412_static_capture_and_version_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    storage=storage_source(root)
    workspace=(root/'src'/'workspace'/'__init__.py').read_text(encoding='utf-8')
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    assert 'APP_SCHEMA_VERSION = 18' in workspace
    assert '90*1000' in js and "captureAutoHistory('continuous')" in js
    assert 'preserved_assets' in storage and 'root_hash' in storage
    assert 'QUARANTINE_DAYS = 7' in (root/'preservation/gc.py').read_text()

def test_v412_history_compare_and_save_as_checkpoint_share_preserved_state(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    _save_text(client,chapter['id'],'Version from automatic History.')
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'Before rewrite','auto',False)
    _save_text(client,chapter['id'],'Current rewritten version.')
    compared=assert_ok(client.get(f"/api/revisions/{rid}/compare-current?scope=chapter&chapter_id={chapter['id']}"))
    assert compared['scope']=='chapter' and compared['change_count']>=1
    assert any('Version from automatic History.' in __import__('bs4').BeautifulSoup(row['left'],'html.parser').get_text() for row in compared['rows'])
    cp=assert_ok(client.post(f'/api/revisions/{rid}/checkpoint',json={
        'scope':'chapter','chapter_id':chapter['id'],'name':'Keep old version','description':'Pinned from History','tags':['history','rewrite']
    }))
    assert cp['name']=='Keep old version' and cp['root_hash']
    snap=assert_ok(client.get(f"/api/checkpoints/{cp['id']}"))['snapshot']
    assert snap['blocks'][0]['text_content']=='Version from automatic History.'
    with storage.tx() as con:
        # The Checkpoint should reuse the historical immutable paragraph object.
        rev=json.loads(con.execute('SELECT tree_json FROM revisions WHERE id=?',(rid,)).fetchone()[0])
        hist_chunk=json.loads(con.execute('SELECT payload FROM content_objects WHERE hash=?',(rev['block_groups'][0]['chunks'][0],)).fetchone()[0])
        hist_hash=hist_chunk['items'][0]['object_hash']
        root=json.loads(con.execute('SELECT payload FROM content_objects WHERE hash=?',(cp['root_hash'],)).fetchone()[0])
        cp_chunk=json.loads(con.execute('SELECT payload FROM content_objects WHERE hash=?',(root['block_chunks'][0],)).fetchone()[0])
        assert cp_chunk['items'][0]['object_hash']==hist_hash


def test_v412_history_restore_preserves_chapter_creation_identity_and_checkpoint_lineage(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    before=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['chapter']
    _save_text(client,chapter['id'],'Identity-safe version.')
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Identity pin'}))
    with storage.tx() as con: pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'Identity history','auto',False)
    _save_text(client,chapter['id'],'Later version.')
    storage.restore_revision(rid,physical_backup=False,preserve_current=False)
    restored=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['chapter']
    assert restored['created_at']==before['created_at']
    checkpoints=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    saved=next(x for x in checkpoints if x['id']==cp['id'])
    assert saved['source_exists'] is True and saved['current_chapter_id']==chapter['id']


def test_v412_history_browses_in_project_modal_and_compares_in_split_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    assert 'id="historyPanel"' not in html and 'id="historyCompareToolbar"' in html
    assert '<option value="history">History</option>' in js
    assert "api(`/api/revisions/${rev.id}/compare-current?${qs}`)" in js
    assert "/checkpoint`,{method:'POST'" in js
    assert "renderModal('History'" in js
    assert 'data-history-menu="compare"' in js and 'data-history-menu="export"' in js and 'data-history-menu="delete"' in js


def test_v412_recovery_retention_is_tiered_distinct_and_deduplicated(tmp_path,app_module_fixture,monkeypatch):
    storage=app_module_fixture.storage
    backup_dir=tmp_path/'backups'; backup_dir.mkdir()
    from datetime import datetime, timedelta, timezone
    import zipfile, os
    now=datetime.now(timezone.utc)
    def pkg(name,created,fingerprint):
        path=backup_dir/f'breadmaker_{name}.breadmaker'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('manifest.json',json.dumps({'backup_created_at':created.isoformat(timespec='microseconds'),'project_fingerprint':fingerprint,'file_hashes':{}}))
        ts=created.timestamp();os.utime(path,(ts,ts));return path
    # Ten distinct saves today: latest-five tier should retain only five of them.
    recent=[pkg(f'recent{i:02}',now-timedelta(minutes=i),f'recent-{i}') for i in range(10)]
    # Three genuinely different old monthly landmarks should survive indefinitely.
    monthly=[pkg(f'month{i}',now-timedelta(days=70+31*i),f'month-{i}') for i in range(3)]
    # An older duplicate of a retained state has no recovery value and is removed.
    duplicate=pkg('duplicate',now-timedelta(days=400),'month-2')
    result=storage_recovery._retain_recovery_backups(backup_dir=backup_dir)
    survivors=set(backup_dir.glob('*.breadmaker'))
    assert all(p in survivors for p in recent[:5])
    assert all(p in survivors for p in monthly)
    assert duplicate not in survivors
    assert len(survivors)==8
    assert result['removed']==6


def test_v416_pre_alpha_schema_cut_rejects_previous_project_packages(client,fresh_project,app_module_fixture,tmp_path):
    storage=app_module_fixture.storage
    package=storage.backup_project('v416_schema_cut')
    assert package is not None
    import zipfile
    for version in (12,11,10):
        old=tmp_path/f'schema{version}.breadmaker'
        with zipfile.ZipFile(package) as src, zipfile.ZipFile(old,'w',compression=zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                data=src.read(info.filename)
                if info.filename=='manifest.json':
                    manifest=json.loads(data);manifest['schema_version']=version;data=json.dumps(manifest).encode()
                dst.writestr(info.filename,data)
        with pytest.raises(Exception):
            workspace.verify_package(old)


def test_v412_history_based_undo_revision_is_removed(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    with storage.tx() as con: pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    _save_text(client,chapter['id'],'Visible base')
    visible=storage.commit_revision(pid,'Visible base','auto',False)
    with pytest.raises(ValueError,match='History-based structural Undo was removed'):
        storage.commit_revision(pid,'Obsolete session state','undo',False)
    with storage.tx() as con:
        assert con.execute("SELECT COUNT(*) FROM revisions WHERE kind='undo'").fetchone()[0]==0
    assert any(x['id']==visible for x in storage.list_revisions(pid))
