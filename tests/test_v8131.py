from __future__ import annotations

from workspace import workspace

from storage import schema as storage_schema
from source_contracts import backend_source,frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok
import app as app_module
from formats import breadreview
from workspace import APP_SCHEMA_VERSION
from test_v8130 import _review

ROOT=Path(__file__).resolve().parents[1]


def _export(client,review,participant):
    response=client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/breadreview")
    assert response.status_code==200,response.text
    return response.content


def test_v8131_release_contract_and_no_schema_bump():
    version=ROOT.joinpath('VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (8,13,1)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=30
    app=backend_source(ROOT)
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    html=ROOT.joinpath('static','index.html').read_text()
    for token in ('/api/portable-review/open-temporary','/api/portable-review/close-temporary','/api/portable-review/package','/api/portable-review/import-current'):
        assert token in app
    assert 'installBreadReviewDropTarget' in js and 'openDroppedBreadReview' in js
    assert 'breadReviewDropOverlay' in html


def test_v8131_temporary_breadreview_isolated_from_active_project(client,fresh_project):
    review,rebeca,_question=_review(client)
    package=_export(client,review,rebeca)
    active_before=workspace.active_id()
    try:
        opened=assert_ok(client.post('/api/portable-review/open-temporary',files={'file':('rebeca.breadreview',package,'application/octet-stream')}))
        assert opened['temporary'] is True and opened['dirty'] is False
        assert workspace.active_id()==active_before
        assert_ok(client.post('/api/portable-review/begin'))
        reader=assert_ok(client.get('/api/portable-review/reader'));block=reader['snapshot']['blocks'][0]
        selection={'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':0,'end_offset':5}
        assert_ok(client.post('/api/portable-review/comments',json={'selection':selection,'body':'Temporary comment'}))
        status=assert_ok(client.get('/api/portable-review/status'))
        assert status['temporary'] is True and status['dirty'] is True
        returned=client.get('/api/portable-review/package');assert returned.status_code==200
        path=Path(fresh_project['project']['path']).parent/'temporary-return.breadreview';path.write_bytes(returned.content)
        manifest=breadreview.read(path)
        assert any(row['body']=='Temporary comment' for row in manifest['feedback']['comments'])
        # The writer project is untouched until explicit Import BreadReview.
        writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
        assert not writer['comments']
        assert workspace.active_id()==active_before
    finally:
        client.post('/api/portable-review/close-temporary')
    assert assert_ok(client.get('/api/portable-review/status'))['active'] is False
    assert workspace.active_id()==active_before


def test_v8131_invalid_temporary_drop_does_not_replace_session(client,fresh_project):
    response=client.post('/api/portable-review/open-temporary',files={'file':('not-a-review.txt',b'nope','text/plain')})
    assert response.status_code==400
    assert assert_ok(client.get('/api/portable-review/status'))['active'] is False


def test_v8131_same_project_drop_can_import_returned_feedback_without_blocking_reviewer_mode(client,fresh_project):
    review,rebeca,_question=_review(client)
    package=_export(client,review,rebeca)
    opened=assert_ok(client.post('/api/portable-review/open-temporary',files={'file':('rebeca.breadreview',package,'application/octet-stream')}))
    assert opened['temporary'] is True
    assert opened['can_import_to_current_project'] is True
    # The package is still openable as a portable reviewer session for local testing.
    assert_ok(client.post('/api/portable-review/begin'))
    reader=assert_ok(client.get('/api/portable-review/reader'));block=reader['snapshot']['blocks'][0]
    selection={'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':0,'end_offset':5}
    assert_ok(client.post('/api/portable-review/comments',json={'selection':selection,'body':'Returned through drop flow'}))
    imported=assert_ok(client.post('/api/portable-review/import-current'))
    assert imported['review_id']==review['id']
    assert assert_ok(client.get('/api/portable-review/status'))['active'] is False
    writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert any(row['body']=='Returned through drop flow' for row in writer['comments'])
