from __future__ import annotations

from workspace import workspace
from api.canvas import developmental as canvas_developmental

from storage import context as storage_context
from storage import database as storage_database
from storage import history as storage_history
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import schema as storage_schema
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

import json
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import pytest

import app as app_module
from workspace import BackupOperation, APP_SCHEMA_VERSION


def _project_row():
    with storage_mutations.tx() as con:
        return dict(con.execute("SELECT * FROM projects ORDER BY id LIMIT 1").fetchone())


def _generation() -> int:
    return storage_mutations.current_project_generation()


def test_v770_schema_and_bootstrap_expose_canonical_generation(client, fresh_project):
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION
    assert APP_SCHEMA_VERSION >= 22
    assert storage_database.database_schema_version() == APP_SCHEMA_VERSION
    boot=client.get('/api/bootstrap')
    assert boot.status_code==200,boot.text
    generation=int(boot.json()['project']['project_generation'])
    assert generation>=1
    assert generation==_generation()
    with storage_mutations.tx() as con:
        classification=storage_schema_contracts.generation_schema_classification(con)
    assert classification['unclassified']==[]


def test_v770_one_canonical_transaction_advances_generation_exactly_once(fresh_project):
    before=_generation()
    with storage_mutations.tx(operation='multi-row-proof') as con:
        p=con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
        pid=int(p['id'])
        chapter=con.execute("SELECT id FROM chapters WHERE project_id=? ORDER BY id LIMIT 1",(pid,)).fetchone()
        con.execute("UPDATE chapters SET description=? WHERE id=?",('first canonical change',chapter['id']))
        for index in range(12):
            con.execute("INSERT INTO tasks(project_id,title,description,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                        (pid,f'Generation task {index}','generation proof','open',storage_values.now(),storage_values.now()))
        con.execute("UPDATE projects SET updated_at=? WHERE id=?",(storage_values.now(),pid))
    assert _generation()==before+1


def test_v770_read_only_and_derived_history_do_not_advance_current(fresh_project):
    before=_generation()
    with storage_mutations.tx() as con:
        pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
        con.execute("SELECT COUNT(*) FROM manuscript_blocks WHERE project_id=?",(pid,)).fetchone()
    assert _generation()==before
    storage_history.local_revision_store().capture(label='Derived History proof',creation_reason='v770-derived',force_checkpoint=True)
    assert _generation()==before
    with storage_mutations.tx() as con:
        con.execute("INSERT INTO ui_state(project_id,key,value_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(project_id,key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                    (pid,'generation-proof','{}',storage_values.now()))
    assert _generation()==before


def test_v770_sqlite_refuses_generation_decrease(fresh_project):
    before=_generation()
    with pytest.raises(sqlite3.IntegrityError,match='PROJECT_GENERATION_DECREASE_FORBIDDEN'):
        with storage_mutations.tx() as con:
            pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
            con.execute("UPDATE projects SET project_generation=? WHERE id=?",(before-1,pid))
    assert _generation()==before


def test_v770_mutation_boundary_serializes_overlapping_current_writes(fresh_project):
    """A later transaction may start waiting, but cannot finish ahead of the holder."""
    start=_generation();entered=threading.Event();release=threading.Event();second_started=threading.Event();second_done=threading.Event();errors=[]

    def first():
        try:
            with storage_mutations.tx(operation='save-100') as con:
                pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
                con.execute("UPDATE projects SET title=? WHERE id=?",('Save 100',pid))
                entered.set()
                assert release.wait(5)
        except Exception as exc:errors.append(exc)

    def second():
        try:
            assert entered.wait(5);second_started.set()
            with storage_mutations.tx(operation='save-101') as con:
                pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
                con.execute("UPDATE projects SET title=? WHERE id=?",('Save 101',pid))
            second_done.set()
        except Exception as exc:errors.append(exc)

    a=threading.Thread(target=first);b=threading.Thread(target=second)
    a.start();b.start();assert second_started.wait(5)
    time.sleep(.05)
    assert not second_done.is_set(), 'save 101 must not pass the serialized commit boundary while save 100 owns it'
    release.set();a.join(5);b.join(5)
    assert not errors
    assert second_done.is_set()
    assert _project_row()['title']=='Save 101'
    assert _generation()==start+2


@pytest.mark.parametrize('operation',['async_callback','retry','shutdown','duplicate_request','prepared_save'])
def test_v770_delayed_operation_token_cannot_overwrite_newer_current(operation,fresh_project):
    """Deterministic N starts -> N+1 commits -> N resumes schedule."""
    token=storage_mutations.capture_mutation_token(operation)
    before=token.source_generation
    with storage_mutations.tx(operation='newer-current') as con:
        pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
        con.execute("UPDATE projects SET title=? WHERE id=?",(f'Newer than {operation}',pid))
    assert _generation()==before+1
    with pytest.raises(storage_mutations.StaleGenerationError,match='PROJECT_GENERATION_STALE_OPERATION'):
        with storage_mutations.tx(token=token,operation=operation) as con:
            con.execute("UPDATE projects SET title=? WHERE id=?",('STALE MUST NEVER WIN',token.project_id))
    row=_project_row()
    assert row['title']==f'Newer than {operation}'
    assert int(row['project_generation'])==before+1


def test_v770_retrying_same_generation_token_cannot_apply_twice(fresh_project):
    token=storage_mutations.capture_mutation_token('idempotent-retry')
    with storage_mutations.tx(token=token,operation='idempotent-retry') as con:
        con.execute("UPDATE projects SET title=? WHERE id=?",('Applied once',token.project_id))
    committed=_generation()
    assert committed==token.source_generation+1
    with pytest.raises(storage_mutations.StaleGenerationError):
        with storage_mutations.tx(token=token,operation='idempotent-retry') as con:
            con.execute("UPDATE projects SET title=? WHERE id=?",('Applied twice',token.project_id))
    assert _generation()==committed
    assert _project_row()['title']=='Applied once'


def test_v770_cancelled_transaction_changes_neither_current_nor_generation(fresh_project):
    before=_project_row();generation=int(before['project_generation'])
    with pytest.raises(RuntimeError,match='simulated cancellation'):
        with storage_mutations.tx(operation='cancelled-save') as con:
            con.execute("UPDATE projects SET title=? WHERE id=?",('Must roll back',before['id']))
            raise RuntimeError('simulated cancellation')
    after=_project_row()
    assert after['title']==before['title']
    assert int(after['project_generation'])==generation


def test_v770_old_schema_project_is_rejected_without_mutation(tmp_path):
    root=tmp_path/'old-project';root.mkdir()
    db=root/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.executescript("""
            CREATE TABLE projects(id INTEGER PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE storage_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO projects(id,title,created_at,updated_at) VALUES(1,'Old pre-alpha','2026-01-01','2026-01-01');
            INSERT INTO storage_meta(key,value) VALUES('schema_version','19');
        """)
        con.commit()
    finally:con.close()
    before=db.read_bytes();old_root=storage_context.PROJECT_ROOT
    try:
        storage_context.configure_project(root)
        with pytest.raises(storage_database.UnsupportedProjectVersion,match='PREALPHA_PROJECT_INCOMPATIBLE'):
            storage_database.init_db()
        assert db.read_bytes()==before
    finally:
        if old_root is not None:storage_context.configure_project(old_root)

def test_v770_export_and_verifier_bind_package_to_snapshot_generation(fresh_project,tmp_path):
    ws=workspace;pid=fresh_project['project']['id'];expected=_generation()
    package=tmp_path/'generation.breadmaker';ws.export_project(pid,package)
    verified=ws.verify_package(package)
    assert int(verified['project_generation'])==expected
    with zipfile.ZipFile(package) as zf:
        manifest=json.loads(zf.read('manifest.json'))
        assert int(manifest['project_generation'])==expected
        db=tmp_path/'snapshot.sqlite3';db.write_bytes(zf.read('project.sqlite3'))
    con=sqlite3.connect(db)
    try:actual=int(con.execute("SELECT project_generation FROM projects ORDER BY id LIMIT 1").fetchone()[0])
    finally:con.close()
    assert actual==expected


def test_v770_destination_publication_ignores_stale_success_and_failure(client,fresh_project,tmp_path):
    folder=tmp_path/'generation-destination';folder.mkdir()
    destination=client.post('/api/backup-destinations',json={'path':str(folder)}).json()['destination']
    first=workspace.backup_destinations_catch_up(reason='generation-first',force=True,quiet_seconds=0)
    row=next(x for x in first['destinations'] if x['id']==destination['id'])
    first_generation=int(row['last_verified']['project_generation']);assert first_generation>=1

    # Advance Current and publish a newer generation normally.
    with storage_mutations.tx(operation='newer-before-stale-callback') as con:
        pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
        con.execute("UPDATE projects SET title=? WHERE id=?",('Newest Current',pid))
    second=workspace.backup_destinations_catch_up(reason='generation-second',force=True,quiet_seconds=0)
    row=next(x for x in second['destinations'] if x['id']==destination['id'])
    newest=int(row['last_verified']['project_generation']);assert newest>first_generation

    root=Path(fresh_project['project']['path'])
    stale=BackupOperation(fresh_project['project']['id'],first_generation,'delayed-old-backup',first_generation,'old-token')
    # Give the stale completion a future wall-clock string to prove time cannot win.
    accepted=workspace._record_backup_success(root,destination['id'],{
        'file':'stale.breadmaker','verified':True,'created_at':'2999-01-01T00:00:00+00:00',
        'project_generation':first_generation,'source_token':'old-token','fingerprint':'stale'
    },stale)
    assert accepted is False
    failed=workspace._record_backup_failure(root,destination['id'],stale)
    assert failed is False
    current=next(x for x in workspace.list_backup_destinations() if x['id']==destination['id'])
    assert int(current['last_verified']['project_generation'])==newest
    assert current['status']=='up_to_date'
    assert not current.get('last_error')


def test_v770_backup_operation_records_generation_and_stale_current_stays_pending(client,fresh_project,tmp_path):
    folder=tmp_path/'operation-metadata';folder.mkdir()
    destination=client.post('/api/backup-destinations',json={'path':str(folder)}).json()['destination']
    result=workspace.backup_destinations_catch_up(reason='operation-proof',force=True,quiet_seconds=0)
    operation=result['operation']
    assert operation['project_id']==fresh_project['project']['id']
    assert operation['target_operation']=='operation-proof'
    assert operation['source_generation']==operation['expected_authoritative_generation']
    row=next(x for x in result['destinations'] if x['id']==destination['id'])
    assert row['verified_generation']==row['current_generation']==int(row['last_verified']['project_generation'])

    with storage_mutations.tx(operation='make-backup-stale') as con:
        pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
        con.execute("UPDATE projects SET updated_at=? WHERE id=?",(storage_values.now(),pid))
    stale=next(x for x in workspace.list_backup_destinations() if x['id']==destination['id'])
    assert stale['current_generation']>stale['verified_generation']
    assert stale['status']=='pending'


def test_v770_schema_init_and_repeated_bootstrap_do_not_create_false_generations(client,fresh_project):
    before=_generation()
    storage_database.init_db()
    assert _generation()==before
    first=client.get('/api/bootstrap');second=client.get('/api/bootstrap')
    assert first.status_code==200 and second.status_code==200
    assert _generation()==before


def test_v770_developmental_reconciliation_is_idempotent_for_generation(client,fresh_project):
    created=client.post('/api/canvases',json={'name':'Generation Developmental','description':'','canvas_type':'developmental','developmental_scope':'chapters'})
    assert created.status_code==200,created.text
    with storage_mutations.tx() as con:
        pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
    settled=_generation()
    canvas_developmental.sync_developmental_canvases(pid)
    after_first=_generation()
    canvas_developmental.sync_developmental_canvases(pid)
    after_second=_generation()
    assert after_first==settled
    assert after_second==settled


def test_v770_recovery_retention_canonical_generation_beats_future_wall_clock():
    from backups.retention import RECOVERY_RETENTION_POLICIES, recovery_retention_partition
    base=[]
    # Generation 40 is authoritative even though a stale generation claims year 2999.
    for generation in range(1,41):
        base.append({
            'file':f'g{generation}.breadmaker',
            'project_generation':generation,
            'created_at':f'2026-08-{(generation % 20)+1:02d}T12:00:00+00:00',
        })
    stale=next(row for row in base if row['project_generation']==10)
    stale['created_at']='2999-01-01T00:00:00+00:00'
    keep,prune=recovery_retention_partition(base,'standard')
    assert len(keep)<=RECOVERY_RETENTION_POLICIES['standard'].hard_cap
    assert keep[0]['project_generation']==40
    assert any(row['project_generation']==40 for row in keep)
    assert not any(row['project_generation']==40 for row in prune)


def test_v770_local_provider_discards_late_lower_generation_package(client,fresh_project,tmp_path):
    ws=workspace;project_id=fresh_project['project']['id']
    destination_dir=tmp_path/'generation-local-provider';destination_dir.mkdir()
    destination=client.post('/api/backup-destinations',json={'path':str(destination_dir)}).json()['destination']

    older=tmp_path/'older.breadmaker';ws.export_project(project_id,older)
    older_info=ws.verify_package(older);older_generation=int(older_info['project_generation'])
    with storage_mutations.tx(operation='newer-provider-artifact') as con:
        pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
        con.execute("UPDATE projects SET title=? WHERE id=?",('Provider generation winner',pid))
    newer=tmp_path/'newer.breadmaker';ws.export_project(project_id,newer)
    newer_info=ws.verify_package(newer);newer_generation=int(newer_info['project_generation'])
    assert newer_generation>older_generation

    newer_meta=ws._store_package_in_local_destination(newer,ws.get(project_id),destination,'newer-token')
    before_rows=ws._backup_providers['local_folder'].list_backups(ws,ws.get(project_id),destination)
    before={(row['file'],int(row.get('project_generation') or 0)) for row in before_rows}
    stale_meta=ws._store_package_in_local_destination(older,ws.get(project_id),destination,'stale-token')
    assert int(newer_meta['project_generation'])==newer_generation
    assert int(stale_meta['project_generation'])==newer_generation
    assert stale_meta.get('discarded_stale') is True

    after_rows=ws._backup_providers['local_folder'].list_backups(ws,ws.get(project_id),destination)
    after={(row['file'],int(row.get('project_generation') or 0)) for row in after_rows}
    assert after==before  # the late stale retry published no new authority
    generations=[generation for _,generation in after]
    assert max(generations)==newer_generation


def test_v770_webdav_physical_reverse_completion_cannot_prune_newer_generation(fresh_project,tmp_path):
    from test_v760 import _dav_server
    ws=workspace;project_id=fresh_project['project']['id']
    older=tmp_path/'webdav-older.breadmaker';ws.export_project(project_id,older)
    older_generation=int(ws.verify_package(older)['project_generation'])
    with storage_mutations.tx(operation='webdav-newer-generation') as con:
        pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
        con.execute("UPDATE projects SET title=? WHERE id=?",('WebDAV newer authority',pid))
    newer=tmp_path/'webdav-newer.breadmaker';ws.export_project(project_id,newer)
    newer_generation=int(ws.verify_package(newer)['project_generation']);assert newer_generation>older_generation

    server,state,endpoint=_dav_server()
    try:
        destination=ws.add_webdav_backup_destination(endpoint,'writer','app-pass',project_id=project_id)
        provider=ws._backup_providers['webdav'];rec=ws.get(project_id)
        first=provider.store(ws,newer,rec,destination,'newer-token')
        stale=provider.store(ws,older,rec,destination,'stale-token')
        assert int(first['project_generation'])==newer_generation
        assert int(stale['project_generation'])==newer_generation
        assert stale.get('discarded_stale') is True
        rows=provider.list_backups(ws,rec,destination)
        generations=[int(row.get('project_generation') or 0) for row in rows]
        assert newer_generation in generations
        assert older_generation not in generations
        with state.lock:
            package_names=[name for name in state.files if name.endswith('.breadmaker')]
        assert len(package_names)==1
    finally:
        server.shutdown();server.server_close()


def test_v770_google_drive_physical_reverse_completion_cannot_prune_newer_generation(monkeypatch,fresh_project,tmp_path):
    from test_v761 import _connect_google, _google_packages, _google_server
    ws=workspace;project_id=fresh_project['project']['id']
    older=tmp_path/'drive-older.breadmaker';ws.export_project(project_id,older)
    older_generation=int(ws.verify_package(older)['project_generation'])
    with storage_mutations.tx(operation='drive-newer-generation') as con:
        pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
        con.execute("UPDATE projects SET title=? WHERE id=?",('Drive newer authority',pid))
    newer=tmp_path/'drive-newer.breadmaker';ws.export_project(project_id,newer)
    newer_generation=int(ws.verify_package(newer)['project_generation']);assert newer_generation>older_generation

    server,state,base=_google_server()
    try:
        destination=_connect_google(ws,monkeypatch,base,project_id=project_id)
        provider=ws._backup_providers['google_drive'];rec=ws.get(project_id)
        first=provider.store(ws,newer,rec,destination,'newer-token')
        stale=provider.store(ws,older,rec,destination,'stale-token')
        assert int(first['project_generation'])==newer_generation
        assert int(stale['project_generation'])==newer_generation
        assert stale.get('discarded_stale') is True
        packages=_google_packages(state,project_id)
        generations=[int((row.get('appProperties') or {}).get('breadmaker_generation') or 0) for row in packages]
        assert newer_generation in generations
        assert older_generation not in generations
        assert len(packages)==1
    finally:
        server.shutdown();server.server_close()


def test_v770_local_recovery_retention_never_lets_future_timestamp_outrank_generation(fresh_project):
    import os
    # 7.7.5 pruning is fail-closed, so exercise authority with *real verified*
    # packages rather than synthetic manifests that are intentionally unverifiable.
    for path in storage_context.BACKUP_DIR.glob('breadmaker_*.breadmaker'):
        path.unlink()
    project_id=fresh_project['project']['id']
    packages=[]
    for index in range(4):
        with storage_mutations.tx(operation=f'v770-retention-generation-{index}') as con:
            pid=int(con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()['id'])
            con.execute("UPDATE projects SET title=? WHERE id=?",(f'Retention generation {index}',pid))
        path=storage_context.BACKUP_DIR/f'breadmaker_generation_{index}.breadmaker'
        workspace.export_project(project_id,path,recovery_reason='v770_retention_authority')
        packages.append(path)

    # Make the oldest canonical generation look newest by both manifest clock and mtime.
    first=packages[0]
    with zipfile.ZipFile(first,'r') as src:
        members={name:src.read(name) for name in src.namelist()}
    manifest=json.loads(members['manifest.json'])
    manifest['backup_created_at']='2999-01-01T00:00:00+00:00'
    members['manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
    rewritten=first.with_suffix('.rewrite')
    with zipfile.ZipFile(rewritten,'w',compression=zipfile.ZIP_DEFLATED) as dst:
        for name,data in members.items():dst.writestr(name,data)
    rewritten.replace(first)
    os.utime(first,(4_070_908_800,4_070_908_800))

    expected_newest=max(int(storage_recovery._backup_manifest_info(path)['project_generation']) for path in packages)
    storage_recovery._retain_recovery_backups()
    remaining=[]
    for path in storage_context.BACKUP_DIR.glob('breadmaker_*.breadmaker'):
        info=storage_recovery._backup_manifest_info(path)
        if info:remaining.append(int(info['project_generation']))
    assert expected_newest in remaining
    assert max(remaining)==expected_newest

def test_v770_documented_generation_contract_remains_part_of_current_architecture():
    root=Path(__file__).resolve().parents[1]
    architecture=(root/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '## 7.7.0 Canonical generation authority' in architecture
    assert 'Canonical generation never decreases.' in architecture
