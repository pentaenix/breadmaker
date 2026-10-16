from __future__ import annotations

from storage import schema as storage_schema
from source_contracts import backend_source,frontend_source_from_app_path
from pathlib import Path
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def _first_chapter(client):
    return assert_ok(client.get('/api/bootstrap'))['chapters'][0]


def _set_text(client, chapter_id, text):
    loaded=assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'expected_revision':loaded['revision'],'blocks':[{'text_content':text,'content_html':text}]}))


def test_v870_release_contract_reader_mode_no_schema_bump():
    version=ROOT.joinpath('VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (8,7,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=28
    app=backend_source(ROOT)
    html=ROOT.joinpath('static/index.html').read_text()
    js=frontend_source_from_app_path(ROOT.joinpath('static/app.js'))
    assert '/participants/{participant_id}/reader' in app
    assert 'id="reviewerMode"' in html
    assert 'openReviewerMode' in js and 'exitReviewerMode' in js


def test_v870_reader_returns_frozen_snapshot_for_exact_participant(client,fresh_project):
    chapter=_first_chapter(client); _set_text(client,chapter['id'],'frozen reviewer copy')
    review=assert_ok(client.post('/api/reviews',json={'name':'Reader','permission_read':True}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    before=client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader")
    assert before.status_code==409 and 'REVIEW_READER_001' in before.text
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    _set_text(client,chapter['id'],'live manuscript changed')
    payload=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))
    assert payload['review']['name']=='Reader'
    assert payload['participant']['id']==participant['id'] and payload['participant']['display_name']=='Rebeca'
    assert any(block['text_content']=='frozen reviewer copy' for block in payload['snapshot']['blocks'])
    assert all(block['text_content']!='live manuscript changed' for block in payload['snapshot']['blocks'])


def test_v870_reader_rejects_wrong_participant(client,fresh_project):
    a=assert_ok(client.post('/api/reviews',json={'name':'A','permission_read':True}))
    b=assert_ok(client.post('/api/reviews',json={'name':'B','permission_read':True}))
    pa=assert_ok(client.post(f"/api/reviews/{a['id']}/participants",json={'display_name':'Ana'}))
    pb=assert_ok(client.post(f"/api/reviews/{b['id']}/participants",json={'display_name':'Beto'}))
    assert_ok(client.post(f"/api/reviews/{a['id']}/start")); assert_ok(client.post(f"/api/reviews/{b['id']}/start"))
    wrong=client.get(f"/api/reviews/{a['id']}/participants/{pb['id']}/reader")
    assert wrong.status_code==404 and 'REVIEW_PARTICIPANT_004' in wrong.text
    assert_ok(client.get(f"/api/reviews/{a['id']}/participants/{pa['id']}/reader"))


def test_v870_feedback_only_review_does_not_expose_snapshot_content(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'Questions only','permission_read':False,'permission_feedback':True}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    payload=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))
    assert payload['snapshot']['permission_read'] is False
    assert payload['snapshot']['permission_feedback'] is True
    assert 'blocks' not in payload['snapshot']
