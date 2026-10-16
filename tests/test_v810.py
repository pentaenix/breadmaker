from __future__ import annotations

from workspace import workspace

from storage import mutations as storage_mutations
from storage import schema as storage_schema
from storage import schema_contracts as storage_schema_contracts

import re
import sqlite3
import zipfile
from pathlib import Path

import app as app_module
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def test_v810_release_contract_and_review_plan_are_in_repo():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,1,0)
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION >= 23
    assert ROOT.joinpath('docs','BREADMAKER_REVIEW_IMPLEMENTATION_PLAN.md').is_file()
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')


def test_v810_review_crud_uses_stable_identity_and_draft_state(client,fresh_project):
    boot=assert_ok(client.get('/api/bootstrap'))
    assert boot['reviews']==[]
    generation=int(boot['project']['project_generation'])

    first=assert_ok(client.post('/api/reviews',json={'name':'Beta Reading'}))
    assert re.fullmatch(r'review_[0-9a-f]{32}',first['id'])
    assert first['name']=='Beta Reading'
    assert first['status']=='draft'
    assert storage_mutations.current_project_generation()==generation+1

    second=assert_ok(client.post('/api/reviews',json={'name':'Editor Pass'}))
    assert second['id']!=first['id']
    listed=assert_ok(client.get('/api/reviews'))
    assert [row['id'] for row in listed]==[first['id'],second['id']]

    renamed=assert_ok(client.patch(f"/api/reviews/{first['id']}",json={'name':'Beta Reading Round 1'}))
    assert renamed['id']==first['id']
    assert renamed['name']=='Beta Reading Round 1'
    assert renamed['status']=='draft'
    reopened=assert_ok(client.get(f"/api/reviews/{first['id']}"))
    assert reopened['id']==first['id']
    assert reopened['name']=='Beta Reading Round 1'

    assert_ok(client.delete(f"/api/reviews/{first['id']}"))
    assert client.get(f"/api/reviews/{first['id']}").status_code==404
    remaining=assert_ok(client.get('/api/reviews'))
    assert [row['id'] for row in remaining]==[second['id']]


def test_v810_reviews_are_canonical_history_managed_project_data(client,fresh_project,tmp_path):
    review=assert_ok(client.post('/api/reviews',json={'name':'Persistent Review'}))
    with storage_mutations.tx(track_generation=False) as con:
        history=storage_schema_contracts.history_schema_classification(con)
        generation=storage_schema_contracts.generation_schema_classification(con)
    assert 'reviews' in history['managed']
    assert 'reviews' in generation['canonical']
    assert history['unclassified']==[]
    assert generation['unclassified']==[]

    package=tmp_path/'review-persistence.breadmaker'
    workspace.export_project(fresh_project['project']['id'],package)
    with zipfile.ZipFile(package) as zf:
        db=tmp_path/'project.sqlite3'
        db.write_bytes(zf.read('project.sqlite3'))
    con=sqlite3.connect(db)
    try:
        row=con.execute('SELECT id,name,status FROM reviews WHERE id=?',(review['id'],)).fetchone()
    finally:
        con.close()
    assert row==(review['id'],'Persistent Review','draft')


def test_v810_review_name_validation_is_fail_closed(client,fresh_project):
    assert client.post('/api/reviews',json={'name':'   '}).status_code==400
    review=assert_ok(client.post('/api/reviews',json={'name':'Keep Me'}))
    response=client.patch(f"/api/reviews/{review['id']}",json={'name':'   '})
    assert response.status_code==400
    assert assert_ok(client.get(f"/api/reviews/{review['id']}"))['name']=='Keep Me'

def test_v810_review_actions_participate_in_structural_undo(client,fresh_project):
    started=assert_ok(client.post('/api/undo/begin?label=Create%20Review'))
    review=assert_ok(client.post('/api/reviews',json={'name':'Undoable Review'}))
    finished=assert_ok(client.post(f"/api/undo/finish/{started['token']}"))
    assert finished['changed']>=1
    assert assert_ok(client.get(f"/api/reviews/{review['id']}"))['name']=='Undoable Review'

    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=undo"))
    assert client.get(f"/api/reviews/{review['id']}").status_code==404

    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=redo"))
    restored=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert restored['id']==review['id']
    assert restored['name']=='Undoable Review'

