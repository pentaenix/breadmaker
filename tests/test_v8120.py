from __future__ import annotations

from storage import schema as storage_schema
from source_contracts import backend_source,frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION
from test_v8100 import _chapter, _set_blocks, _selection

ROOT=Path(__file__).resolve().parents[1]


def _review(client, *, shared=False, feedback=False):
    chapter=_chapter(client)
    _set_blocks(client,chapter['id'],[{'text_content':'alpha beta gamma','content_html':'alpha beta gamma'}])
    review=assert_ok(client.post('/api/reviews',json={'name':'Processing','permission_read':True,'permission_comment':True,'permission_suggest':True,'permission_feedback':feedback,'reviewer_visibility':'shared' if shared else 'private'}))
    rebeca=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    ana=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Ana'}))
    if feedback:
        questionnaire=assert_ok(client.post('/api/questionnaires',json={'name':'Processing feedback'}))
        assert_ok(client.post(f"/api/questionnaires/{questionnaire['id']}/questions",json={'prompt':'What changed for you?','required':False}))
        assert_ok(client.post(f"/api/reviews/{review['id']}/questionnaires",json={'questionnaire_id':questionnaire['id']}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/reader")); block=reader['snapshot']['blocks'][0]
    return review,rebeca,ana,block


def test_v8120_release_contract_no_schema_bump():
    version=ROOT.joinpath('VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (8,12,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=30
    app=backend_source(ROOT); js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    for token in ('/rounds/{snapshot_id}/close','/rounds/{snapshot_id}/reopen','/archive','/unarchive','/feedback/{kind}/{item_id}/status','/review-items/{kind}/{item_id}'):
        assert token in app
    assert 'reviewFeedbackFilters' in js and 'reviewParticipantStatusLabel' in js


def test_v8120_participant_progress_complete_and_revoke_block_writes(client,fresh_project):
    review,rebeca,ana,block=_review(client)
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/begin"))
    assert started['status']=='in_progress' and started['started_at']
    comment=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/comments",json={'selection':_selection(block,0,5),'body':'First note'}))
    completed=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/complete")); assert completed['status']=='completed'
    # Participant completion is no longer a write lock; returning to the same round stays safe.
    assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/comments",json={'selection':_selection(block,6,10),'body':'Works after returning'}))
    revoked=assert_ok(client.patch(f"/api/review-participants/{rebeca['id']}/status",json={'status':'revoked'})); assert revoked['status']=='revoked'
    assert client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/reader").status_code==403


def test_v8120_shared_inline_feedback_does_not_share_forms(client,fresh_project):
    review,rebeca,ana,block=_review(client,shared=True,feedback=True)
    assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/comments",json={'selection':_selection(block,0,5),'body':'Visible inline'}))
    qid=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/reader"))['form']['questions'][0]['id']
    assert_ok(client.put(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/feedback",json={'answers':[{'question_id':qid,'answer_text':'Private answer'}],'general_feedback':'Private general'}))
    ana_reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{ana['id']}/reader"))
    assert any(row['body']=='Visible inline' and row['participant_name']=='Rebeca' for row in ana_reader['feedback']['comments'])
    assert ana_reader['form']['general_feedback']==''
    assert ana_reader['form']['answers'][0]['answer_text']==''


def test_v8120_resolve_reopen_and_task_link_from_review_item(client,fresh_project):
    review,rebeca,ana,block=_review(client)
    comment=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/comments",json={'selection':_selection(block,0,5),'body':'Rewrite this later'}))
    resolved=assert_ok(client.patch(f"/api/reviews/{review['id']}/feedback/comment/{comment['id']}/status",json={'status':'resolved'})); assert resolved['status']=='resolved'
    reopened=assert_ok(client.patch(f"/api/reviews/{review['id']}/feedback/comment/{comment['id']}/status",json={'status':'open'})); assert reopened['status']=='open'
    task=assert_ok(client.post('/api/tasks',json={'title':'Rewrite and change motivation'}))
    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'review_comment','target_id':comment['id']}))
    writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    row=next(x for x in writer['comments'] if x['id']==comment['id']); assert row['task_count']==1
    refs=assert_ok(client.get(f"/api/references?source_type=task&source_id={task['id']}")); assert refs[0]['target_type']=='review_comment'
    ctx=assert_ok(client.get(f"/api/review-items/comment/{comment['id']}")); assert ctx['review_id']==review['id']


def test_v8120_review_close_archive_reopen_preserves_feedback(client,fresh_project):
    review,rebeca,ana,block=_review(client)
    comment=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/comments",json={'selection':_selection(block,0,5),'body':'Keep me'}))
    current=assert_ok(client.get(f"/api/reviews/{review['id']}"));snapshot_id=current['snapshot']['id']
    closed=assert_ok(client.post(f"/api/reviews/{review['id']}/rounds/{snapshot_id}/close")); assert closed['status']=='closed' and closed['closed_at']
    assert client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/reader").status_code==409
    assert any(x['id']==comment['id'] for x in assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))['comments'])
    reopened=assert_ok(client.post(f"/api/reviews/{review['id']}/rounds/{snapshot_id}/reopen")); assert reopened['status']=='open' and reopened['snapshot']['id']==snapshot_id
    assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/reader"))
    assert_ok(client.post(f"/api/reviews/{review['id']}/rounds/{snapshot_id}/close"))
    archived=assert_ok(client.post(f"/api/reviews/{review['id']}/archive")); assert archived['status']=='archived'
    unarchived=assert_ok(client.post(f"/api/reviews/{review['id']}/unarchive")); assert unarchived['status']=='closed'

