from __future__ import annotations

from source_contracts import storage_source,frontend_styles_source_from_styles_path

from workspace import workspace
from backend import project_activation

from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import schema as storage_schema

import sqlite3
from pathlib import Path

import app as app_module
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def test_v850_release_contract_schema_and_participant_table():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,5,0)
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION >= 27
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    schema=ROOT.joinpath('src','storage','schema.py').read_text(encoding='utf-8')
    assert 'CREATE TABLE review_participants(' in schema
    assert 'id TEXT PRIMARY KEY' in schema
    assert 'review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE' in schema
    assert 'UNIQUE(review_id,display_name)' in schema
    for group in ('_SESSION_UNDO_TABLE_ORDER','_HISTORY_MANAGED_TABLES','_GENERATION_CANONICAL_TABLES'):
        source=storage_source(ROOT)
        assert 'review_participants' in source


def test_v850_participants_are_named_review_members_with_stable_identity(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={
        'name':'Beta Reading',
        'permission_read':True,
        'permission_comment':True,
        'permission_feedback':True,
    }))
    rebeca=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    assert rebeca['id'].startswith('review_participant_')
    assert len(rebeca['id'])==len('review_participant_')+32
    assert rebeca['review_id']==review['id']
    assert rebeca['display_name']=='Rebeca'
    assert {'id','review_id','display_name','created_at','updated_at'} <= set(rebeca)
    assert rebeca['status']=='not_started'
    assert rebeca['started_at'] is None and rebeca['completed_at'] is None

    renamed=assert_ok(client.patch(f"/api/review-participants/{rebeca['id']}",json={'display_name':'Rebecca'}))
    assert renamed['id']==rebeca['id']
    assert renamed['display_name']=='Rebecca'

    fetched=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert fetched['permission_comment'] is True
    assert fetched['permission_feedback'] is True
    assert [p['display_name'] for p in fetched['participants']]==['Rebecca']
    assert fetched['participants'][0]['id']==rebeca['id']


def test_v850_participant_names_are_unique_within_review_but_not_global(client,fresh_project):
    first=assert_ok(client.post('/api/reviews',json={'name':'First'}))
    second=assert_ok(client.post('/api/reviews',json={'name':'Second'}))
    assert_ok(client.post(f"/api/reviews/{first['id']}/participants",json={'display_name':'Carlos'}))

    duplicate=client.post(f"/api/reviews/{first['id']}/participants",json={'display_name':'cArLoS'})
    assert duplicate.status_code==409
    assert 'REVIEW_PARTICIPANT_002' in duplicate.text

    other=assert_ok(client.post(f"/api/reviews/{second['id']}/participants",json={'display_name':'Carlos'}))
    assert other['display_name']=='Carlos'


def test_v850_participants_inherit_review_policy_and_have_no_override_columns(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={
        'name':'Carlos Editor',
        'scope_type':'manuscript',
        'permission_read':True,
        'permission_suggest':True,
        'permission_feedback':False,
    }))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Carlos'}))
    assert {'id','review_id','display_name','created_at','updated_at'} <= set(participant)
    assert participant['status']=='not_started'

    with storage_mutations.tx() as con:
        columns={row['name'] for row in con.execute('PRAGMA table_info(review_participants)').fetchall()}
    assert not any(name.startswith('permission_') for name in columns)
    assert 'scope_type' not in columns and 'category_id' not in columns

    loaded=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert loaded['permission_suggest'] is True
    assert loaded['permission_feedback'] is False
    assert loaded['participants'][0]['id']==participant['id']


def test_v850_participant_structural_undo_redo_and_review_delete_cascade(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'Undo Participants'}))
    started=assert_ok(client.post('/api/undo/begin?label=Add%20Participant'))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Ana'}))
    finished=assert_ok(client.post(f"/api/undo/finish/{started['token']}"))
    assert finished['changed']>=1

    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=undo"))
    assert assert_ok(client.get(f"/api/reviews/{review['id']}/participants"))==[]
    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=redo"))
    restored=assert_ok(client.get(f"/api/reviews/{review['id']}/participants"))
    assert restored[0]['id']==participant['id'] and restored[0]['display_name']=='Ana'

    assert_ok(client.delete(f"/api/reviews/{review['id']}"))
    missing=client.patch(f"/api/review-participants/{participant['id']}",json={'display_name':'Gone'})
    assert missing.status_code==404


def test_v850_pre_alpha_rejects_schema26_without_migration(client,fresh_project):
    rec=fresh_project['project']; root=Path(rec['path'])
    storage_database.release_instance_lock()
    db=root/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute("UPDATE storage_meta SET value='26' WHERE key='schema_version'")
        con.commit()
    finally:
        con.close()
    manifest=workspace.read_manifest(root)
    manifest['schema_version']=26
    workspace._write_manifest(root,manifest)
    before_db=db.read_bytes(); before_manifest=(root/'manifest.json').read_bytes()
    try:
        project_activation.activate_project(rec['id'])
        assert False,'old schema must not activate'
    except RuntimeError as exc:
        assert 'PREALPHA_PROJECT_INCOMPATIBLE' in str(exc)
    assert db.read_bytes()==before_db
    assert (root/'manifest.json').read_bytes()==before_manifest
