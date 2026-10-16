from __future__ import annotations

from storage import mutations as storage_mutations
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


def _open_comment_review(client,name='Review'):
    chapter=_first_chapter(client)
    _set_text(client,chapter['id'],'Alpha frozen words for review feedback')
    marker=assert_ok(client.post('/api/review-markers',json={'name':f'{name} marker','color':'#6f93bf'}))
    review=assert_ok(client.post('/api/reviews',json={'name':name,'permission_comment':True,'marker_ids':[marker['id']]}))
    a=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    b=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Ana'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{a['id']}/reader"))
    block=next(x for x in reader['snapshot']['blocks'] if 'Alpha frozen words' in x['text_content'])
    selection={'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':6,'end_offset':18}
    return review,a,b,block,selection


def test_v890_release_contract_no_schema_bump():
    version=ROOT.joinpath('VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (8,9,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=30
    app=backend_source(ROOT)
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert '/participants/{participant_id}/highlights' in app
    assert '/participants/{participant_id}/comments' in app
    assert '/api/reviews/{rid}/feedback' in app
    assert 'showReviewSelectionToolbar' in js
    assert 'applyReviewerAnnotations' in js


def test_v890_highlight_and_comment_are_server_anchored_and_attributed(client,fresh_project):
    review,rebeca,ana,block,selection=_open_comment_review(client)
    highlight=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/highlights",json={'selection':selection,'marker_id':review['marker_ids'][0]}))
    comment=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/comments",json={'selection':selection,'body':'This line feels abrupt.'}))
    assert highlight['participant_id']==rebeca['id']
    assert comment['participant_id']==rebeca['id'] and comment['body']=='This line feels abrupt.'
    assert highlight['selected_text']=='frozen words'
    assert comment['selected_text']=='frozen words'
    with storage_mutations.tx() as con:
        events=con.execute("SELECT event_type,participant_id,entity_id FROM review_events WHERE review_id=? ORDER BY created_at,id",(review['id'],)).fetchall()
    event_pairs={(row['event_type'],row['participant_id']) for row in events}
    assert ('HIGHLIGHT_CREATED',rebeca['id']) in event_pairs
    assert ('COMMENT_CREATED',rebeca['id']) in event_pairs
    assert any(row['event_type']=='REVIEW_ROUND_STARTED' for row in events)


def test_v890_reviewer_feedback_is_private_but_writer_sees_all(client,fresh_project):
    review,rebeca,ana,block,selection=_open_comment_review(client)
    assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/comments",json={'selection':selection,'body':'Private note'}))
    rebeca_reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/reader"))
    ana_reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{ana['id']}/reader"))
    assert [row['body'] for row in rebeca_reader['feedback']['comments']]==['Private note']
    assert ana_reader['feedback']['comments']==[] and ana_reader['feedback']['highlights']==[]
    writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert writer['comments'][0]['participant_name']=='Rebeca'
    assert writer['comments'][0]['body']=='Private note'


def test_v890_feedback_requires_permission_and_valid_frozen_selection(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'Read only','permission_read':True,'permission_comment':False}))
    person=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    denied=client.post(f"/api/reviews/{review['id']}/participants/{person['id']}/highlights",json={'selection':{'start_block_id':'fake','end_block_id':'fake','start_offset':0,'end_offset':2}})
    assert denied.status_code==403 and 'REVIEW_FEEDBACK_002' in denied.text

    review2,rebeca,ana,block,selection=_open_comment_review(client,'Valid comments')
    bad=client.post(f"/api/reviews/{review2['id']}/participants/{rebeca['id']}/highlights",json={'selection':{'start_block_id':'fake','end_block_id':'fake','start_offset':0,'end_offset':2},'marker_id':review2['marker_ids'][0]})
    assert bad.status_code==400 and 'REVIEW_ANCHOR_001' in bad.text
    out=client.post(f"/api/reviews/{review2['id']}/participants/{rebeca['id']}/comments",json={'selection':selection,'body':'   '})
    assert out.status_code==400 and 'REVIEW_COMMENT_001' in out.text


def test_v890_anchor_uses_snapshot_after_live_manuscript_changes(client,fresh_project):
    review,rebeca,ana,block,selection=_open_comment_review(client)
    chapter=_first_chapter(client)
    _set_text(client,chapter['id'],'Completely different live manuscript')
    item=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/highlights",json={'selection':selection,'marker_id':review['marker_ids'][0]}))
    assert item['selected_text']=='frozen words'


def test_v890_multiblock_anchor_uses_frozen_block_order(client,fresh_project):
    chapter=_first_chapter(client)
    loaded=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))
    assert_ok(client.put(f'/api/chapters/{chapter["id"]}/blocks',json={'expected_revision':loaded['revision'],'blocks':[{'text_content':'First block','content_html':'First block'},{'text_content':'Second block','content_html':'Second block'}]}))
    marker=assert_ok(client.post('/api/review-markers',json={'name':'Multi marker','color':'#7a5cff'}))
    review=assert_ok(client.post('/api/reviews',json={'name':'Multi','permission_comment':True,'marker_ids':[marker['id']]}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))
    blocks=[x for x in reader['snapshot']['blocks'] if x['text_content'] in {'First block','Second block'}]
    assert len(blocks)==2
    item=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{participant['id']}/highlights",json={'selection':{'start_block_id':blocks[0]['source_block_id'],'end_block_id':blocks[1]['source_block_id'],'start_offset':6,'end_offset':6},'marker_id':review['marker_ids'][0]}))
    assert item['selected_text']=='block\nSecond'
