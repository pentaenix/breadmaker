from __future__ import annotations

from workspace import workspace

from storage import assets as storage_assets
from storage import context as storage_context
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import schema as storage_schema
from storage import values as storage_values

from api.import_export import uploads as import_api

import asyncio
import json
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import pytest

import app as app_module
from workspace.projects import packages as workspace_projects
from backups.frozen_snapshot import FrozenSnapshotError, capture_frozen_snapshot
from workspace import WorkspaceError


def _generation() -> int:
    return storage_mutations.current_project_generation()


def _first_block_id() -> str:
    with storage_mutations.tx() as con:
        return str(con.execute("SELECT id FROM manuscript_blocks ORDER BY id LIMIT 1").fetchone()[0])


def _set_first_block_image(api_path: str) -> None:
    with storage_mutations.tx(operation='v771-image-reference') as con:
        bid=str(con.execute("SELECT id FROM manuscript_blocks ORDER BY id LIMIT 1").fetchone()[0])
        con.execute(
            "UPDATE manuscript_blocks SET block_type='image',content_html='',text_content='',image_path=?,content_hash=?,updated_at=? WHERE id=?",
            (api_path,storage_values.block_content_hash('','',api_path),storage_values.now(),bid),
        )


def test_v771_frozen_snapshot_binds_database_assets_and_generation(fresh_project,tmp_path):
    asset=storage_context.ASSET_DIR/'frozen.png'
    storage_assets.durable_write_project_asset(asset,b'asset-generation-A')
    _set_first_block_image('/api/assets/frozen.png')
    expected=_generation()

    frozen=capture_frozen_snapshot(storage_context.DB_PATH,storage_context.ASSET_DIR,tmp_path/'snapshot')
    assert frozen.project_generation==expected
    assert frozen.schema_version==storage_schema.SCHEMA_VERSION
    assert frozen.file_hashes['project.sqlite3']==storage_values.sha_file(frozen.database)
    assert frozen.file_hashes['assets/frozen.png']==storage_values.sha_file(frozen.root/'assets/frozen.png')
    assert (frozen.root/'assets/frozen.png').read_bytes()==b'asset-generation-A'

    con=sqlite3.connect(frozen.database)
    try:
        actual=con.execute("SELECT project_generation FROM projects ORDER BY id LIMIT 1").fetchone()[0]
    finally:con.close()
    assert int(actual)==expected


def test_v771_packager_never_returns_to_live_project_after_freeze(fresh_project,tmp_path,monkeypatch):
    ws=workspace;project_id=fresh_project['project']['id']
    asset=storage_context.ASSET_DIR/'race.png'
    storage_assets.durable_write_project_asset(asset,b'bytes-at-frozen-generation')
    _set_first_block_image('/api/assets/race.png')
    frozen_generation=_generation()
    original=workspace_projects.capture_frozen_snapshot

    def capture_then_advance(db_path,asset_dir,target_root):
        snapshot=original(db_path,asset_dir,target_root)
        # Deterministic race: Current advances *after* the private snapshot exists but
        # before the ZIP builder starts consuming it.  Also simulate external tampering
        # of the live asset.  Neither may alter the already-frozen artifact.
        with storage_mutations.tx(operation='v771-newer-current-after-freeze') as con:
            pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()[0])
            con.execute("UPDATE projects SET title=?,updated_at=? WHERE id=?",('Generation after snapshot',storage_values.now(),pid))
        asset.write_bytes(b'live-file-changed-after-freeze')
        return snapshot

    monkeypatch.setattr(workspace_projects,'capture_frozen_snapshot',capture_then_advance)
    package=tmp_path/'frozen-race.breadmaker'
    ws.export_project(project_id,package)
    assert _generation()==frozen_generation+1
    with zipfile.ZipFile(package) as zf:
        manifest=json.loads(zf.read('manifest.json'))
        assert int(manifest['project_generation'])==frozen_generation
        assert manifest['snapshot_source']=='frozen-canonical-generation'
        assert zf.read('assets/race.png')==b'bytes-at-frozen-generation'
        db=tmp_path/'packaged.sqlite3';db.write_bytes(zf.read('project.sqlite3'))
    con=sqlite3.connect(db)
    try:
        row=con.execute("SELECT title,project_generation FROM projects ORDER BY id LIMIT 1").fetchone()
    finally:con.close()
    assert int(row[1])==frozen_generation
    assert row[0]!='Generation after snapshot'


@pytest.mark.parametrize('kind',['manuscript','canvas','cut'])
def test_v771_missing_canonical_asset_fails_snapshot_closed(client,fresh_project,tmp_path,kind):
    missing='/api/assets/missing-v771.png'
    if kind=='manuscript':
        _set_first_block_image(missing)
    elif kind=='canvas':
        canvas=client.post('/api/canvases',json={'name':'Snapshot assets','description':''}).json()
        cid=int(canvas['id']) if 'id' in canvas else int(canvas['canvas']['id'])
        response=client.post(f'/api/canvases/{cid}/nodes',json={
            'node_type':'image','shape':'rect','x':10,'y':10,'w':180,'h':120,'title':'','body':'','image_path':missing,
        })
        assert response.status_code==200,response.text
    else:
        with storage_mutations.tx(operation='v771-cut-asset-reference') as con:
            pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()[0])
            con.execute(
                "INSERT INTO cuts(project_id,title,content_html,created_at,note,source_label,source_project,source_chapter,source_scene) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid,'Missing image cut',f'<p><img src="{missing}"></p>',storage_values.now(),'','','','',''),
            )
    with pytest.raises(FrozenSnapshotError,match='SNAPSHOT_REFERENCED_ASSET_MISSING'):
        capture_frozen_snapshot(storage_context.DB_PATH,storage_context.ASSET_DIR,tmp_path/f'snapshot-{kind}')


def test_v771_interrupted_upload_never_exposes_partial_final_asset(fresh_project,tmp_path):
    class BrokenUpload:
        def __init__(self):self.calls=0
        async def read(self,_size):
            self.calls+=1
            if self.calls==1:return b'partial-bytes'
            raise RuntimeError('simulated upload interruption')

    target=storage_context.ASSET_DIR/'interrupted.png'
    with pytest.raises(RuntimeError,match='simulated upload interruption'):
        asyncio.run(import_api.save_upload_limited(BrokenUpload(),target,50*1024*1024))
    assert not target.exists()
    assert not list(storage_context.ASSET_DIR.glob('.*interrupted.png*.upload-tmp'))


def test_v771_frozen_snapshot_ignores_unpublished_asset_staging_files(fresh_project,tmp_path):
    staging=storage_context.ASSET_DIR/'.future.png.test.upload-tmp'
    staging.write_bytes(b'not-published')
    frozen=capture_frozen_snapshot(storage_context.DB_PATH,storage_context.ASSET_DIR,tmp_path/'snapshot')
    assert 'assets/.future.png.test.upload-tmp' not in frozen.file_hashes
    assert not (frozen.root/'assets'/staging.name).exists()


def test_v771_sqlite_snapshot_is_always_a_committed_generation(fresh_project,tmp_path):
    before=_generation();entered=threading.Event();release=threading.Event();errors=[];result={}
    with storage_mutations.tx() as con:
        old_title=str(con.execute("SELECT title FROM projects ORDER BY id LIMIT 1").fetchone()[0])

    def writer():
        try:
            with storage_mutations.tx(operation='v771-held-current') as con:
                pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()[0])
                con.execute("UPDATE projects SET title=? WHERE id=?",('COMMITTED AFTER HOLD',pid))
                entered.set();assert release.wait(5)
        except Exception as exc:errors.append(exc)

    def snapshotter():
        try:result['snapshot']=capture_frozen_snapshot(storage_context.DB_PATH,storage_context.ASSET_DIR,tmp_path/'snapshot')
        except Exception as exc:errors.append(exc)

    writer_thread=threading.Thread(target=writer);writer_thread.start();assert entered.wait(5)
    snapshot_thread=threading.Thread(target=snapshotter);snapshot_thread.start();time.sleep(.05)
    release.set();writer_thread.join(5);snapshot_thread.join(5);assert not errors
    frozen=result['snapshot']
    con=sqlite3.connect(frozen.database)
    try:row=con.execute("SELECT title,project_generation FROM projects ORDER BY id LIMIT 1").fetchone()
    finally:con.close()
    # Depending on SQLite lock scheduling, capture may land immediately before or
    # immediately after the held commit. Both are valid; an uncommitted hybrid is not.
    assert (row[0],int(row[1])) in {(old_title,before),('COMMITTED AFTER HOLD',before+1)}
    assert _generation()==before+1


def test_v771_recovery_package_uses_frozen_snapshot_contract(fresh_project):
    path=storage_recovery.backup_project('v771-frozen')
    assert path and path.is_file()
    info=workspace.verify_package(path)
    assert int(info['project_generation'])==_generation()
    with zipfile.ZipFile(path) as zf:
        manifest=json.loads(zf.read('manifest.json'))
    assert manifest['snapshot_source']=='frozen-canonical-generation'
    assert int(manifest['project_generation'])==_generation()


def test_v771_verifier_rejects_package_missing_canvas_asset(client,fresh_project,tmp_path):
    asset=storage_context.ASSET_DIR/'canvas-package.png';storage_assets.durable_write_project_asset(asset,b'canvas')
    canvas=client.post('/api/canvases',json={'name':'Package canvas','description':''}).json()
    cid=int(canvas['id']) if 'id' in canvas else int(canvas['canvas']['id'])
    response=client.post(f'/api/canvases/{cid}/nodes',json={
        'node_type':'image','shape':'rect','x':10,'y':10,'w':180,'h':120,'title':'','body':'','image_path':'/api/assets/canvas-package.png',
    })
    assert response.status_code==200,response.text
    good=tmp_path/'good.breadmaker';workspace.export_project(fresh_project['project']['id'],good)
    bad=tmp_path/'bad.breadmaker'
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad,'w',compression=zipfile.ZIP_DEFLATED) as dst:
        manifest=json.loads(src.read('manifest.json'))
        manifest['file_hashes'].pop('assets/canvas-package.png',None)
        for info in src.infolist():
            if info.filename=='assets/canvas-package.png':continue
            if info.filename=='manifest.json':dst.writestr(info,json.dumps(manifest))
            else:dst.writestr(info,src.read(info.filename))
    with pytest.raises(WorkspaceError,match='PROJECT_PACKAGE_CURRENT_ASSET_MISSING|PROJECT_PACKAGE_HASH_SET_MISMATCH'):
        workspace.verify_package(bad)


def test_v771_project_asset_publish_refuses_overwrite(fresh_project):
    path=storage_context.ASSET_DIR/'immutable.png'
    storage_assets.durable_write_project_asset(path,b'first')
    with pytest.raises(RuntimeError,match='PROJECT_ASSET_ALREADY_EXISTS'):
        storage_assets.durable_write_project_asset(path,b'second')
    assert path.read_bytes()==b'first'


def test_v771_project_asset_same_content_reuse_is_explicit(fresh_project):
    path=storage_context.ASSET_DIR/'same.png'
    storage_assets.durable_write_project_asset(path,b'same')
    storage_assets.durable_write_project_asset(path,b'same',allow_existing_same=True)
    assert path.read_bytes()==b'same'


def test_v771_staging_package_failure_preserves_existing_export(fresh_project,tmp_path,monkeypatch):
    destination=tmp_path/'known-good.breadmaker'
    destination.write_bytes(b'previous-known-good-export')
    def fail(*_args,**_kwargs):
        raise FrozenSnapshotError('simulated staging verification failure')
    monkeypatch.setattr(workspace_projects,'verify_frozen_package_payload',fail)
    with pytest.raises(FrozenSnapshotError,match='simulated staging verification failure'):
        workspace.export_project(fresh_project['project']['id'],destination)
    assert destination.read_bytes()==b'previous-known-good-export'
    assert not destination.with_suffix(destination.suffix+'.tmp').exists()



def test_v771_unreferenced_finalized_asset_does_not_change_canonical_snapshot(fresh_project,tmp_path):
    orphan=storage_context.ASSET_DIR/'orphan.png';storage_assets.durable_write_project_asset(orphan,b'not-yet-committed')
    generation=_generation()
    frozen=capture_frozen_snapshot(storage_context.DB_PATH,storage_context.ASSET_DIR,tmp_path/'snapshot')
    assert frozen.project_generation==generation
    assert 'assets/orphan.png' not in frozen.file_hashes
    assert not (frozen.root/'assets'/'orphan.png').exists()

def test_v771_asset_change_during_capture_fails_closed(fresh_project,tmp_path,monkeypatch):
    import backups.frozen_snapshot as frozen_module
    asset=storage_context.ASSET_DIR/'changing.png';storage_assets.durable_write_project_asset(asset,b'unchanging-source')
    _set_first_block_image('/api/assets/changing.png')
    real_fstat=frozen_module.os.fstat;calls={'n':0}
    def shifted(fd):
        stat=real_fstat(fd);calls['n']+=1
        if calls['n']==2:
            class Changed:
                st_ino=stat.st_ino;st_dev=stat.st_dev;st_size=stat.st_size;st_mtime_ns=stat.st_mtime_ns+1
            return Changed()
        return stat
    monkeypatch.setattr(frozen_module.os,'fstat',shifted)
    with pytest.raises(FrozenSnapshotError,match='SNAPSHOT_ASSET_CHANGED_DURING_CAPTURE'):
        capture_frozen_snapshot(storage_context.DB_PATH,storage_context.ASSET_DIR,tmp_path/'snapshot')
    assert not (tmp_path/'snapshot'/'assets'/'changing.png').exists()


def test_v771_release_metadata_and_documented_snapshot_contract():
    root=Path(__file__).resolve().parents[1]
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (7,7,1)
    index=(root/'static/index.html').read_text(encoding='utf-8')
    architecture=(root/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '## 7.7.1 Frozen canonical snapshot boundary' in architecture
    assert 'Package construction never reads live Current' in architecture
