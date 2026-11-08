"""Native frozen rounds retain the existing writer/reader HTTP payload shape."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
def routes(monkeypatch):
    from hosted import http_compat as api
    pid, rid = 'a' * 32, 'review_one'
    records = {
        'review': {'id': rid, 'status': 'open', 'reviewer_visibility': 'private', 'active_snapshot_id': 'round_two'},
        'review_participant': {'id': 'reader_one', 'status': 'completed'},
        'round_one': {'id': 'round_one', 'round_number': 1, 'status': 'closed', 'snapshot': {
            'id': 'round_one', 'round_number': 1, 'permission_read': True, 'permission_feedback': True,
            'chapters': [{'source_chapter_id': 'chapter_one', 'title': 'Old title'}],
            'blocks': [{'source_block_id': 'b_old', 'source_chapter_id': 'chapter_one', 'text_content': 'Frozen old text'}],
            'questions': [{'id': 'question_old', 'prompt': 'Old question?', 'required': True}],
        }},
        'round_two': {'id': 'round_two', 'round_number': 2, 'status': 'open', 'snapshot': {
            'id': 'round_two', 'round_number': 2, 'permission_read': True, 'permission_feedback': True,
            'chapters': [{'source_chapter_id': 'chapter_one', 'title': 'New title'}],
            'blocks': [{'source_block_id': 'b_new', 'source_chapter_id': 'chapter_one', 'text_content': 'Frozen new text'}],
            'questions': [{'id': 'question_new', 'prompt': 'New question?', 'required': True}],
        }},
    }
    calls = []
    def get(kind, ids):
        assert ids['project_id'] == pid and ids.get('review_id') == rid
        calls.append(('get', kind, dict(ids)))
        return records[ids['snapshot_id']] if kind == 'review_snapshot' else records[kind]
    def listing(kind, ids):
        assert ids['project_id'] == pid and ids['review_id'] == rid
        calls.append(('list', kind, dict(ids)))
        if kind == 'review_snapshot':
            return [records['round_two'], records['round_one']]  # Deliberately wrong lexical/order fallback.
        if kind == 'review_participant':
            return [records[kind]]
        if kind == 'review_question_answer':
            return [{'id': 'answer_new', 'snapshot_id': 'round_two', 'question_id': 'question_new', 'participant_id': 'reader_one', 'answer_text': 'New answer'},
                    {'id': 'answer_old', 'snapshot_id': 'round_one', 'question_id': 'question_old', 'participant_id': 'reader_one', 'answer_text': 'Old answer'},
                    {'id': 'other_private', 'snapshot_id': 'round_two', 'question_id': 'question_new', 'participant_id': 'reader_other', 'answer_text': 'Private answer'}]
        if kind == 'review_general_feedback':
            return [{'id': 'general', 'snapshot_id': 'round_two', 'participant_id': 'reader_one', 'body': 'Current round report'}]
        return []
    monkeypatch.setattr(api, '_pid', lambda request: pid)
    monkeypatch.setattr(api, '_get', get)
    monkeypatch.setattr(api, '_list', listing)
    return api, records, calls, pid, rid


def test_manuscript_round_honors_explicit_old_round_and_wrapper(routes):
    api, records, calls, pid, rid = routes
    result = api.hosted_review_round(rid, SimpleNamespace(query_params={'snapshot_id': 'round_one'}))
    assert result['snapshot']['blocks'][0]['text_content'] == 'Frozen old text'
    assert result['feedback']['feedback_forms'][0]['answers'][0]['answer_text'] == 'Old answer'
    assert not any(kind == 'review_snapshot' for method, kind, _ in calls if method == 'list')


def test_reader_keeps_frozen_questions_and_only_current_participant_answers(routes):
    api, records, calls, pid, rid = routes
    result = api.hosted_review_participant_reader(rid, 'reader_one', SimpleNamespace(query_params={}))
    assert result['snapshot']['blocks'][0]['text_content'] == 'Frozen new text'
    assert result['form']['questions'][0]['prompt'] == 'New question?'
    assert [row['answer_text'] for row in result['form']['answers']] == ['New answer']
    assert result['form']['general_feedback'] == 'Current round report'
    assert len(result['feedback']['feedback_forms']) == 1
    assert not any(kind == 'review_snapshot' for method, kind, _ in calls if method == 'list')


def test_round_fallback_uses_round_number_not_list_order(routes):
    api, records, calls, pid, rid = routes
    records['review']['active_snapshot_id'] = ''
    assert api._active_review_snapshot(pid, rid)['id'] == 'round_two'


def test_denied_active_snapshot_does_not_fall_through_to_other_rounds(routes, monkeypatch):
    api, records, calls, pid, rid = routes
    original = api._get
    def forbidden(kind, ids):
        if kind == 'review_snapshot':
            raise HTTPException(403, 'Forbidden')
        return original(kind, ids)
    monkeypatch.setattr(api, '_get', forbidden)
    with pytest.raises(HTTPException) as failure:
        api._active_review_snapshot(pid, rid)
    assert failure.value.status_code == 403
    assert not any(kind == 'review_snapshot' for method, kind, _ in calls if method == 'list')
