from __future__ import annotations

from source_contracts import frontend_styles_source_from_styles_path
from workspace import workspace
from backend import project_activation

from storage import database as storage_database
from storage import schema as storage_schema

import sqlite3
from pathlib import Path

import app as app_module
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def test_v840_release_contract_schema_and_permission_columns():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,4,0)
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION >= 26
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    schema=ROOT.joinpath('src','storage','schema.py').read_text(encoding='utf-8')
    for column in ('permission_read','permission_comment','permission_suggest','permission_feedback'):
        assert column in schema
    assert 'CHECK(permission_read=1 OR (permission_comment=0 AND permission_suggest=0))' in schema


def test_v840_permissions_are_review_level_and_feedback_is_independent(client,fresh_project):
    default=assert_ok(client.post('/api/reviews',json={'name':'Default'}))
    assert default['permission_read'] is True
    assert default['permission_comment'] is False
    assert default['permission_suggest'] is False
    assert default['permission_feedback'] is False

    feedback_only=assert_ok(client.post('/api/reviews',json={
        'name':'Feedback Only',
        'permission_read':False,
        'permission_comment':False,
        'permission_suggest':False,
        'permission_feedback':True,
    }))
    assert feedback_only['permission_read'] is False
    assert feedback_only['permission_feedback'] is True
    assert feedback_only['permission_comment'] is False
    assert feedback_only['permission_suggest'] is False

    suggestion=assert_ok(client.post('/api/reviews',json={
        'name':'Carlos',
        'permission_read':False,
        'permission_suggest':True,
        'permission_comment':False,
        'permission_feedback':False,
    }))
    # Suggest needs visible prose, but does not imply Comment or Feedback.
    assert suggestion['permission_read'] is True
    assert suggestion['permission_suggest'] is True
    assert suggestion['permission_comment'] is False
    assert suggestion['permission_feedback'] is False

    bootstrap={row['id']:row for row in assert_ok(client.get('/api/bootstrap'))['reviews']}
    assert bootstrap[feedback_only['id']]['permission_feedback'] is True
    assert bootstrap[suggestion['id']]['permission_suggest'] is True


def test_v840_permission_updates_preserve_dependencies_without_progressive_roles(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'Permission Pass'}))
    changed=assert_ok(client.patch(f"/api/reviews/{review['id']}",json={
        'permission_comment':True,
        'permission_feedback':True,
    }))
    assert changed['permission_read'] is True
    assert changed['permission_comment'] is True
    assert changed['permission_suggest'] is False
    assert changed['permission_feedback'] is True

    # Read cannot be removed while a text capability still needs it.
    still_readable=assert_ok(client.patch(f"/api/reviews/{review['id']}",json={'permission_read':False}))
    assert still_readable['permission_read'] is True
    assert still_readable['permission_comment'] is True

    # Turning off Comment and Read can produce an independent Feedback-only Review.
    feedback_only=assert_ok(client.patch(f"/api/reviews/{review['id']}",json={
        'permission_read':False,
        'permission_comment':False,
        'permission_feedback':True,
    }))
    assert feedback_only['permission_read'] is False
    assert feedback_only['permission_comment'] is False
    assert feedback_only['permission_feedback'] is True


def test_v840_permission_structural_undo_redo(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'Undo Permissions'}))
    started=assert_ok(client.post('/api/undo/begin?label=Set%20Review%20Permissions'))
    changed=assert_ok(client.patch(f"/api/reviews/{review['id']}",json={
        'permission_suggest':True,
        'permission_feedback':True,
    }))
    assert changed['permission_read'] is True
    assert changed['permission_suggest'] is True
    assert changed['permission_feedback'] is True
    finished=assert_ok(client.post(f"/api/undo/finish/{started['token']}"))
    assert finished['changed']>=1

    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=undo"))
    restored=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert restored['permission_read'] is True
    assert restored['permission_suggest'] is False
    assert restored['permission_feedback'] is False

    assert_ok(client.post(f"/api/undo/apply/{started['token']}?direction=redo"))
    redone=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert redone['permission_suggest'] is True
    assert redone['permission_feedback'] is True


def test_v840_pre_alpha_rejects_schema25_without_migration(client,fresh_project):
    rec=fresh_project['project']; root=Path(rec['path'])
    storage_database.release_instance_lock()
    db=root/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute("UPDATE storage_meta SET value='25' WHERE key='schema_version'")
        con.commit()
    finally:
        con.close()
    manifest=workspace.read_manifest(root)
    manifest['schema_version']=25
    workspace._write_manifest(root,manifest)
    before_db=db.read_bytes(); before_manifest=(root/'manifest.json').read_bytes()
    try:
        project_activation.activate_project(rec['id'])
        assert False,'old schema must not activate'
    except RuntimeError as exc:
        assert 'PREALPHA_PROJECT_INCOMPATIBLE' in str(exc)
    assert db.read_bytes()==before_db
    assert (root/'manifest.json').read_bytes()==before_manifest
