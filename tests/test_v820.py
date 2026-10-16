from __future__ import annotations

from source_contracts import frontend_styles_source_from_styles_path
from workspace import workspace
from backend import project_activation

from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import schema as storage_schema
from storage import schema_contracts as storage_schema_contracts

import re
import sqlite3
from pathlib import Path

import app as app_module
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def test_v820_release_contract_schema_and_logo_size():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,2,0)
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION >= 33
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    sizes=[(float(a),float(b)) for a,b in re.findall(r'\.brand-mark\{\s*width:([0-9.]+)px;height:([0-9.]+)px',css)]
    assert sizes and max(a for a,_ in sizes)>=29.7 and max(b for _,b in sizes)>=29.7


def test_v820_categories_are_intentionally_absent_from_current_review_model(client,fresh_project):
    boot=assert_ok(client.get('/api/bootstrap'))
    assert 'review_categories' not in boot
    assert client.get('/api/review-categories').status_code==404
    assert client.post('/api/review-categories',json={'name':'Beta Reading'}).status_code==404
    review=assert_ok(client.post('/api/reviews',json={'name':'Round 1'}))
    assert 'category_id' not in review
    with storage_mutations.tx(track_generation=False) as con:
        history=storage_schema_contracts.history_schema_classification(con)
        generations=storage_schema_contracts.generation_schema_classification(con)
    assert 'review_categories' not in history['managed']
    assert 'review_categories' not in generations['canonical']
    assert history['unclassified']==[]
    assert generations['unclassified']==[]


def test_v820_populate_presets_are_repeatable_and_number_repeated_reviews(client,fresh_project):
    options=assert_ok(client.get('/api/reviews/populate-options'))
    assert [row['id'] for row in options]==['reader','editorial','full','specialist']
    assert [item['name'] for item in next(row for row in options if row['id']=='editorial')['items']]==['Developmental Review','Line Review','Copy Edit','Proofreading']
    first=assert_ok(client.post('/api/reviews/populate',json={'preset_id':'editorial'}))
    assert first['count']==4
    assert [row['name'] for row in first['created']]==['Developmental Review','Line Review','Copy Edit','Proofreading']
    second=assert_ok(client.post('/api/reviews/populate',json={'preset_id':'editorial'}))
    assert second['count']==4
    assert [row['name'] for row in second['created']]==['Developmental Review 2','Line Review 2','Copy Edit 2','Proofreading 2']
    assert all(row['description'] and row['scope_type']=='manuscript' and not row['participants'] for row in first['created']+second['created'])



def test_v820_review_order_is_domain_data_and_persists(client,fresh_project):
    first=assert_ok(client.post('/api/reviews',json={'name':'First'}))
    second=assert_ok(client.post('/api/reviews',json={'name':'Second'}))
    third=assert_ok(client.post('/api/reviews',json={'name':'Third'}))
    assert [row['name'] for row in assert_ok(client.get('/api/reviews'))][-3:]==['First','Second','Third']
    assert_ok(client.post('/api/reviews/reorder',json={'ids':[third['id'],first['id'],second['id']]}))
    ordered=assert_ok(client.get('/api/reviews'))
    assert [row['name'] for row in ordered][-3:]==['Third','First','Second']
    assert [row['position'] for row in ordered][-3:]==sorted(row['position'] for row in ordered[-3:])

def test_v820_pre_alpha_rejects_previous_schema_without_migration(client,fresh_project):
    rec=fresh_project['project']; root=Path(rec['path'])
    storage_database.release_instance_lock()
    db=root/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute("UPDATE storage_meta SET value='23' WHERE key='schema_version'")
        con.commit()
    finally:
        con.close()
    manifest=workspace.read_manifest(root)
    manifest['schema_version']=23
    workspace._write_manifest(root,manifest)
    before_db=db.read_bytes(); before_manifest=(root/'manifest.json').read_bytes()
    try:
        project_activation.activate_project(rec['id'])
        assert False,'old schema must not activate'
    except RuntimeError as exc:
        assert 'PREALPHA_PROJECT_INCOMPATIBLE' in str(exc)
    assert db.read_bytes()==before_db
    assert (root/'manifest.json').read_bytes()==before_manifest


def test_v820_review_delete_structural_undo_restores_review(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'Developmental Review','description':'Keep identity through undo.'}))
    started=assert_ok(client.post('/api/undo/begin?label=Delete%20Review'))
    assert_ok(client.delete(f"/api/reviews/{review['id']}"))
    finished=assert_ok(client.post(f"/api/undo/finish/{started['token']}"))
    assert finished['changed']>=1
    assert client.get(f"/api/reviews/{review['id']}").status_code==404
    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=undo"))
    restored=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert restored['name']=='Developmental Review'
    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=redo"))
    assert client.get(f"/api/reviews/{review['id']}").status_code==404
