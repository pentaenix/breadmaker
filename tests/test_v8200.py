from __future__ import annotations

from storage import schema as storage_schema
from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION
from test_v8100 import _chapter, _set_blocks, _selection

ROOT = Path(__file__).resolve().parents[1]


def _review_with_feedback(client):
    chapter = _chapter(client)
    _set_blocks(client, chapter['id'], [{'text_content':'A difficult choice changes everything.','content_html':'A difficult choice changes everything.'}])
    review = assert_ok(client.post('/api/reviews', json={
        'name':'Developmental Review',
        'permission_read':True,
        'permission_comment':True,
        'permission_suggest':True,
        'permission_feedback':True,
    }))
    maria = assert_ok(client.post(f"/api/reviews/{review['id']}/participants", json={'display_name':'Maria'}))
    david = assert_ok(client.post(f"/api/reviews/{review['id']}/participants", json={'display_name':'David'}))
    questionnaire = assert_ok(client.post('/api/questionnaires', json={'name':'Development questions'}))
    question = assert_ok(client.post(f"/api/questionnaires/{questionnaire['id']}/questions", json={'prompt':'Does the motivation feel earned?','required':False}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/questionnaires", json={'questionnaire_id':questionnaire['id']}))
    started = assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader = assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{maria['id']}/reader"))
    block = reader['snapshot']['blocks'][0]
    snapshot_question_id = reader['form']['questions'][0]['id']
    comment = assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{maria['id']}/comments", json={
        'selection':_selection(block, 2, 11), 'body':'Make the decision cost more.'
    }))
    suggestion = assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{david['id']}/suggestions", json={
        'selection':_selection(block, 12, 18), 'operation':'replace', 'proposed_text':'decision'
    }))
    assert_ok(client.put(f"/api/reviews/{review['id']}/participants/{maria['id']}/feedback", json={
        'answers':[{'question_id':snapshot_question_id,'answer_text':'Almost, but the previous scene needs more pressure.'}],
        'general_feedback':'The middle is working much better now.'
    }))
    return chapter, review, maria, david, started['snapshot'], comment, suggestion


def test_v8200_manuscript_review_contract_is_working_layer_not_admin_surface():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8,20,0)
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION == 42
    html = ROOT.joinpath('static','index.html').read_text()
    js = frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    css = frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    app = backend_source(ROOT)

    assert 'id="manuscriptReviewsPanelBody"' in html
    for token in (
        'renderManuscriptReviewsList', 'openManuscriptReviewRound',
        'navigateManuscriptReviewFeedback',
        'applyManuscriptReviewAnnotations', 'openManuscriptReviewQuestionsModal',
        'manuscriptReviewTransitionRound', 'openManuscriptReviewWorkspace',
    ):
        assert token in js
    assert "const filters=['open','all','closed']" in js
    assert "pieces.join('\\n')!==String(item.selected_text||'')" in js
    assert "openReviewFeedbackTask(manuscriptReviewsWorkspace.reviewId,kind,item" in js
    assert 'manuscriptReviewCachedRange' in js
    assert 'manuscriptReviewRecordHitZones' in js
    assert 'CSS.highlights' in js
    assert 'showManuscriptReviewHover' in js
    assert '::highlight(manuscript-review-comments)' in css
    assert '.manuscript-review-proposal-decoration' in css
    assert 'manuscript-review-gutter-marker' in css
    assert 'highlight_count' in app and 'comment_count' in app and 'suggestion_count' in app
    # Review administration remains in the Reviews workspace; Manuscript only links to it.
    assert 'reviewParticipantsPanelAdd' not in html[html.index('id="manuscriptReviewsPanel"'):html.index('id="findPopover"')]


def test_v8200_round_counts_and_existing_lifecycle_are_available_to_manuscript_panel(client, fresh_project):
    _chapter_row, review, maria, david, snapshot, comment, suggestion = _review_with_feedback(client)
    boot = assert_ok(client.get('/api/bootstrap'))
    listed = next(row for row in boot['reviews'] if row['id'] == review['id'])
    round_row = next(row for row in listed['rounds'] if row['id'] == snapshot['id'])
    assert round_row['comment_count'] == 1
    assert round_row['suggestion_count'] == 1
    assert round_row['highlight_count'] == 0
    assert round_row['answer_count'] == 1
    assert round_row['general_feedback_count'] == 1

    feedback = assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    form = next(row for row in feedback['feedback_forms'] if row['participant_id'] == maria['id'])
    assert form['answers'][0]['answer_text'].startswith('Almost')
    assert form['general_feedback'].startswith('The middle')

    # Manuscript close/reopen deliberately delegates to the already-tested immutable round lifecycle.
    closed = assert_ok(client.post(f"/api/reviews/{review['id']}/rounds/{snapshot['id']}/close"))
    assert closed['status'] == 'closed'
    reopened = assert_ok(client.post(f"/api/reviews/{review['id']}/rounds/{snapshot['id']}/reopen"))
    assert reopened['status'] == 'open' and reopened['snapshot']['id'] == snapshot['id']


def test_v8200_resolve_and_task_paths_used_by_manuscript_panel_preserve_review_link(client, fresh_project):
    _chapter_row, review, maria, david, snapshot, comment, suggestion = _review_with_feedback(client)
    resolved = assert_ok(client.patch(f"/api/reviews/{review['id']}/feedback/comment/{comment['id']}/status", json={'status':'resolved'}))
    assert resolved['status'] == 'resolved'
    reopened = assert_ok(client.patch(f"/api/reviews/{review['id']}/feedback/comment/{comment['id']}/status", json={'status':'open'}))
    assert reopened['status'] == 'open'
    task = assert_ok(client.post(f"/api/reviews/{review['id']}/feedback/comment/{comment['id']}/task", json={'title':'Address developmental note','description':'Make the decision cost more.'}))
    assert task['reference']['target_type'] == 'review_comment'
    assert task['reference']['target_id'] == comment['id']
    assert task['manuscript_reference'] and task['manuscript_reference']['start_block_id']
