from __future__ import annotations

from workspace import workspace
from backend import project_activation

from storage import database as storage_database
from storage import mutations as storage_mutations
from storage import schema as storage_schema
from storage import schema_contracts as storage_schema_contracts
from storage import undo as storage_undo
from storage import values as storage_values

import sqlite3
from pathlib import Path

import pytest

import app as app_module
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]

CURRENT_REVIEW_TABLES={
    'review_anchors','review_highlights','review_comments','review_comment_replies',
    'review_suggestions','questionnaires','questionnaire_questions','review_questionnaires',
    'review_snapshot_questions','review_question_answers','review_general_feedback',
    'review_events','review_imports',
}


def test_v880_release_contract_complete_local_review_storage_foundation():
    version=ROOT.joinpath('VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (8,8,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=30
    schema=ROOT.joinpath('src','storage','schema.py').read_text()
    for table in CURRENT_REVIEW_TABLES:
        assert f'CREATE TABLE {table}(' in schema
    assert "reviewer_visibility TEXT NOT NULL DEFAULT 'private'" in schema
    assert "status TEXT NOT NULL DEFAULT 'not_started'" in schema
    assert 'permission_read INTEGER NOT NULL DEFAULT 1' in schema
    assert 'permission_feedback INTEGER NOT NULL DEFAULT 0' in schema
    assert 'start_block_id TEXT NOT NULL' in schema and 'end_block_id TEXT NOT NULL' in schema


def test_v880_future_review_tables_are_canonical_history_and_undo_managed(client,fresh_project):
    with storage_database.connect() as con:
        tables={str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert CURRENT_REVIEW_TABLES <= tables
    assert CURRENT_REVIEW_TABLES <= storage_schema_contracts._HISTORY_MANAGED_TABLES
    assert CURRENT_REVIEW_TABLES <= storage_schema_contracts._GENERATION_CANONICAL_TABLES
    assert CURRENT_REVIEW_TABLES <= set(storage_undo._SESSION_UNDO_TABLE_ORDER)


def test_v880_future_review_relations_fail_closed_across_reviews(client,fresh_project):
    a=assert_ok(client.post('/api/reviews',json={'name':'A','permission_read':True,'permission_comment':True,'permission_feedback':True}))
    b=assert_ok(client.post('/api/reviews',json={'name':'B','permission_read':True,'permission_comment':True,'permission_feedback':True}))
    pa=assert_ok(client.post(f"/api/reviews/{a['id']}/participants",json={'display_name':'Ana'}))
    pb=assert_ok(client.post(f"/api/reviews/{b['id']}/participants",json={'display_name':'Beto'}))
    started=assert_ok(client.post(f"/api/reviews/{a['id']}/start"))
    sid=started['snapshot']['id']
    block=assert_ok(client.get(f"/api/reviews/{a['id']}/snapshot"))['blocks'][0]
    now=storage_values.now(); anchor=storage_values.uid('review_anchor_')
    with storage_mutations.tx(operation='v880-foundation') as con:
        con.execute("INSERT INTO review_anchors(id,review_id,snapshot_id,start_block_id,end_block_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,context_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (anchor,a['id'],sid,block['source_block_id'],block['source_block_id'],0,0,'','','','',now))
        con.execute("INSERT INTO review_highlights(id,review_id,participant_id,anchor_id,tag,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (storage_values.uid('review_highlight_'),a['id'],pa['id'],anchor,'','open',now,now))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO review_highlights(id,review_id,participant_id,anchor_id,tag,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (storage_values.uid('review_highlight_'),a['id'],pb['id'],anchor,'','open',now,now))
        questionnaire=storage_values.uid('questionnaire_')
        question=storage_values.uid('questionnaire_question_')
        frozen_question=storage_values.uid('review_snapshot_question_')
        con.execute("INSERT INTO questionnaires(id,project_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (questionnaire,fresh_project['bootstrap']['project']['id'],'Reader form','',now,now))
        con.execute("INSERT INTO questionnaire_questions(id,questionnaire_id,position,prompt,required,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (question,questionnaire,0,'What did you think?',0,now,now))
        con.execute("INSERT INTO review_snapshot_questions(id,snapshot_id,source_questionnaire_id,source_question_id,questionnaire_name,position,prompt,required,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (frozen_question,sid,questionnaire,question,'Reader form',0,'What did you think?',0,now))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO review_question_answers(id,review_id,snapshot_id,question_id,participant_id,answer_text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (storage_values.uid('review_answer_'),a['id'],sid,frozen_question,pb['id'],'wrong review',now,now))


def test_v880_lifecycle_and_visibility_defaults_are_reserved_without_changing_current_behavior(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'Lifecycle'}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    with storage_database.connect() as con:
        r=dict(con.execute("SELECT status,reviewer_visibility,started_at,closed_at FROM reviews WHERE id=?",(review['id'],)).fetchone())
        p=dict(con.execute("SELECT status,started_at,completed_at FROM review_participants WHERE id=?",(participant['id'],)).fetchone())
    assert r=={'status':'draft','reviewer_visibility':'private','started_at':None,'closed_at':None}
    assert p=={'status':'not_started','started_at':None,'completed_at':None}


def test_v880_pre_alpha_rejects_schema28_without_migration(client,fresh_project):
    rec=fresh_project['project']; root=Path(rec['path']); storage_database.release_instance_lock(); db=root/'project.sqlite3'
    con=sqlite3.connect(db); con.execute("UPDATE storage_meta SET value='28' WHERE key='schema_version'"); con.commit(); con.close()
    manifest=workspace.read_manifest(root); manifest['schema_version']=28; workspace._write_manifest(root,manifest)
    before_db=db.read_bytes(); before_manifest=(root/'manifest.json').read_bytes()
    with pytest.raises(RuntimeError,match='PREALPHA_PROJECT_INCOMPATIBLE'):
        project_activation.activate_project(rec['id'])
    assert db.read_bytes()==before_db and (root/'manifest.json').read_bytes()==before_manifest
