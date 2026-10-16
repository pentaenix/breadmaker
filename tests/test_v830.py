from __future__ import annotations

from source_contracts import frontend_styles_source_from_styles_path
from workspace import workspace
from backend import project_activation

from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import schema as storage_schema
from storage import schema_contracts as storage_schema_contracts

import sqlite3
from pathlib import Path

import app as app_module
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def test_v830_release_contract_schema_scope_and_logo_size():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,3,0)
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION >= 25
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    assert 'width:32px;height:32px' in css
    schema=ROOT.joinpath('src','storage','schema.py').read_text(encoding='utf-8')
    assert 'review_scope_chapters' in schema and 'review_scope_scenes' in schema


def test_v830_scope_is_review_level_canonical_data(client,fresh_project):
    boot=assert_ok(client.get('/api/bootstrap'))
    visible=[row for row in boot['chapters'] if not row['is_implicit']]
    assert visible
    chapter_ids=[int(row['id']) for row in visible[:2]]

    whole=assert_ok(client.post('/api/reviews',json={'name':'Whole Book'}))
    assert whole['scope_type']=='manuscript' and whole['scope_ids']==[]

    chapters=assert_ok(client.post('/api/reviews',json={
        'name':'Opening Chapters','scope_type':'chapters','scope_ids':chapter_ids,
    }))
    assert chapters['scope_type']=='chapters'
    assert chapters['scope_ids']==chapter_ids

    listed={row['id']:row for row in assert_ok(client.get('/api/reviews'))}
    assert listed[chapters['id']]['scope_ids']==chapter_ids
    bootstrap={row['id']:row for row in assert_ok(client.get('/api/bootstrap'))['reviews']}
    assert bootstrap[chapters['id']]['scope_type']=='chapters'
    assert bootstrap[chapters['id']]['scope_ids']==chapter_ids

    with storage_mutations.tx(track_generation=False) as con:
        history=storage_schema_contracts.history_schema_classification(con)
        generations=storage_schema_contracts.generation_schema_classification(con)
    for table in ('review_scope_chapters','review_scope_scenes'):
        assert table in history['managed']
        assert table in generations['canonical']
    assert history['unclassified']==[]
    assert generations['unclassified']==[]


def test_v830_scope_validation_and_scene_scope(client,fresh_project):
    boot=assert_ok(client.get('/api/bootstrap'))
    tab_id=int(next(row['id'] for row in boot['tabs'] if row['is_manuscript']))
    scene=assert_ok(client.post('/api/scenes',json={'title':'Opening Scene','tab_id':tab_id}))

    review=assert_ok(client.post('/api/reviews',json={
        'name':'Scene Pass','scope_type':'scenes','scope_ids':[scene['id']],
    }))
    assert review['scope_type']=='scenes'
    assert review['scope_ids']==[scene['id']]

    empty=client.post('/api/reviews',json={'name':'Bad','scope_type':'chapters','scope_ids':[]})
    assert empty.status_code==400
    unavailable=client.post('/api/reviews',json={'name':'Bad','scope_type':'scenes','scope_ids':[999999]})
    assert unavailable.status_code==400
    mixed=client.post('/api/reviews',json={'name':'Bad','scope_type':'manuscript','scope_ids':[1]})
    assert mixed.status_code==400

    changed=assert_ok(client.patch(f"/api/reviews/{review['id']}",json={'scope_type':'manuscript','scope_ids':[]}))
    assert changed['scope_type']=='manuscript' and changed['scope_ids']==[]


def test_v830_scope_structural_undo_restores_selection(client,fresh_project):
    boot=assert_ok(client.get('/api/bootstrap'))
    chapters=[row for row in boot['chapters'] if not row['is_implicit']]
    first=int(chapters[0]['id'])
    review=assert_ok(client.post('/api/reviews',json={'name':'Undo Scope'}))

    started=assert_ok(client.post('/api/undo/begin?label=Set%20Review%20Scope'))
    changed=assert_ok(client.patch(f"/api/reviews/{review['id']}",json={'scope_type':'chapters','scope_ids':[first]}))
    assert changed['scope_type']=='chapters' and changed['scope_ids']==[first]
    finished=assert_ok(client.post(f"/api/undo/finish/{started['token']}"))
    assert finished['changed']>=1

    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=undo"))
    restored=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert restored['scope_type']=='manuscript' and restored['scope_ids']==[]

    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=redo"))
    redone=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert redone['scope_type']=='chapters' and redone['scope_ids']==[first]


def test_v830_pre_alpha_rejects_schema24_without_migration(client,fresh_project):
    rec=fresh_project['project']; root=Path(rec['path'])
    storage_database.release_instance_lock()
    db=root/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute("UPDATE storage_meta SET value='24' WHERE key='schema_version'")
        con.commit()
    finally:
        con.close()
    manifest=workspace.read_manifest(root)
    manifest['schema_version']=24
    workspace._write_manifest(root,manifest)
    before_db=db.read_bytes(); before_manifest=(root/'manifest.json').read_bytes()
    try:
        project_activation.activate_project(rec['id'])
        assert False,'old schema must not activate'
    except RuntimeError as exc:
        assert 'PREALPHA_PROJECT_INCOMPATIBLE' in str(exc)
    assert db.read_bytes()==before_db
    assert (root/'manifest.json').read_bytes()==before_manifest
