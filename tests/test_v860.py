from __future__ import annotations

from source_contracts import storage_source

from workspace import workspace
from backend import project_activation

from storage import database as storage_database
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import schema as storage_schema
import sqlite3
from pathlib import Path
import app as app_module
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION
ROOT=Path(__file__).resolve().parents[1]

def _first_chapter(client): return assert_ok(client.get('/api/bootstrap'))['chapters'][0]

def _set_text(client, chapter_id, text):
    loaded=assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'expected_revision':loaded['revision'],'blocks':[{'text_content':text,'content_html':text}]}))

def test_v860_release_contract_snapshot_schema():
    version=ROOT.joinpath('VERSION').read_text().strip(); assert tuple(map(int,version.split('.'))) >= (8,6,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=28
    schema=ROOT.joinpath('src','storage','schema.py').read_text()
    for table in ('review_snapshots','review_snapshot_chapters','review_snapshot_scenes','review_snapshot_blocks'): assert f'CREATE TABLE {table}(' in schema
    source=storage_source(ROOT)
    for table in ('review_snapshots','review_snapshot_chapters','review_snapshot_scenes','review_snapshot_blocks'):
        assert table in source

def test_v860_start_review_freezes_exact_manuscript(client,fresh_project):
    chapter=_first_chapter(client); _set_text(client,chapter['id'],'frozen alpha')
    before=assert_ok(client.get('/api/bootstrap'))['project']['project_generation']
    review=assert_ok(client.post('/api/reviews',json={'name':'Freeze me','permission_read':True,'permission_feedback':True}))
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    assert started['status']=='open'; snap=started['snapshot']; assert snap['source_generation']>=before and len(snap['content_hash'])==64
    full=assert_ok(client.get(f"/api/reviews/{review['id']}/snapshot")); assert any(b['text_content']=='frozen alpha' for b in full['blocks'])
    with storage_mutations.tx() as con:
        pid=int(con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]); assert storage_invariants.project_invariant_errors(con,pid)==[]
    old_hash=full['content_hash']; _set_text(client,chapter['id'],'live beta')
    again=assert_ok(client.get(f"/api/reviews/{review['id']}/snapshot"))
    assert again['content_hash']==old_hash and any(b['text_content']=='frozen alpha' for b in again['blocks']) and all(b['text_content']!='live beta' for b in again['blocks'])

def test_v860_open_round_freezes_its_policy_while_review_configuration_remains_editable(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'Contract','permission_read':True}))
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/start")); frozen_id=started['snapshot']['id']
    changed=assert_ok(client.patch(f"/api/reviews/{review['id']}",json={'permission_feedback':True,'name':'Renamed'}))
    assert changed['name']=='Renamed' and changed['permission_feedback'] is True
    frozen=assert_ok(client.get(f"/api/reviews/{review['id']}/snapshot"))
    assert frozen['id']==frozen_id and frozen['permission_feedback'] is False
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'})); assert participant['display_name']=='Rebeca'
    # Starting again intentionally begins the next editorial round. The first frozen
    # round stays historical while the new round freezes the current Review policy.
    second=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    assert second['snapshot']['id']!=frozen_id
    assert second['snapshot']['round_number']==2
    assert second['snapshot']['permission_feedback'] is True
    rounds=assert_ok(client.get(f"/api/reviews/{review['id']}"))['rounds']
    first=next(row for row in rounds if row['id']==frozen_id)
    assert first['status']=='closed'

def test_v860_start_review_structural_undo_redo(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'Undo start'}))
    token=assert_ok(client.post('/api/undo/begin?label=Start%20Review'))['token']
    assert_ok(client.post(f"/api/reviews/{review['id']}/start")); assert_ok(client.post(f'/api/undo/finish/{token}'))
    assert assert_ok(client.get(f"/api/reviews/{review['id']}"))['status']=='open'
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=undo')); reverted=assert_ok(client.get(f"/api/reviews/{review['id']}")); assert reverted['status']=='draft' and reverted['snapshot'] is None
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=redo')); restored=assert_ok(client.get(f"/api/reviews/{review['id']}")); assert restored['status']=='open' and restored['snapshot']

def test_v860_pre_alpha_rejects_schema27_without_migration(client,fresh_project):
    rec=fresh_project['project']; root=Path(rec['path']); storage_database.release_instance_lock(); db=root/'project.sqlite3'
    con=sqlite3.connect(db); con.execute("UPDATE storage_meta SET value='27' WHERE key='schema_version'"); con.commit(); con.close()
    manifest=workspace.read_manifest(root); manifest['schema_version']=27; workspace._write_manifest(root,manifest)
    before_db=db.read_bytes(); before_manifest=(root/'manifest.json').read_bytes()
    try: project_activation.activate_project(rec['id']); assert False
    except RuntimeError as exc: assert 'PREALPHA_PROJECT_INCOMPATIBLE' in str(exc)
    assert db.read_bytes()==before_db and (root/'manifest.json').read_bytes()==before_manifest
