from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

from conftest import assert_ok
from test_v8100 import _chapter, _set_blocks

ROOT = Path(__file__).resolve().parents[1]


def test_v8206_review_working_ui_is_clean_and_popup_is_local():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8,20,6)
    js = frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    css = frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    assert 'manuscriptReviewParticipantChips' not in js
    assert 'data-manuscript-review-person' not in js
    assert 'id="manuscriptReviewDetailMore"' in js
    assert 'manuscriptReviewDetailMenuItems' in js
    assert "const left=Math.max(8,Math.min(window.innerWidth-box.width-8,rect.left));let top=rect.bottom+gap" in js
    assert '.manuscript-review-detail-heading-row' in css


def test_v8206_accepts_unchanged_multi_block_replace_automatically(client, fresh_project):
    chapter = _chapter(client)
    _set_blocks(client, chapter['id'], [
        {'text_content':'First block','content_html':'First block'},
        {'text_content':'Middle paragraph','content_html':'Middle paragraph'},
        {'text_content':'Second block tail','content_html':'Second block tail'},
    ])
    review = assert_ok(client.post('/api/reviews', json={'name':'Multi block','permission_suggest':True}))
    person = assert_ok(client.post(f"/api/reviews/{review['id']}/participants", json={'display_name':'Editor'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader = assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{person['id']}/reader"))
    blocks = reader['snapshot']['blocks'][:3]
    suggestion = assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{person['id']}/suggestions", json={
        'selection':{
            'start_block_id':blocks[0]['source_block_id'],
            'end_block_id':blocks[2]['source_block_id'],
            'start_offset':6,
            'end_offset':7,
        },
        'operation':'replace',
        'proposed_text':'rewritten',
    }))
    accepted = assert_ok(client.post(f"/api/reviews/{review['id']}/suggestions/{suggestion['id']}/accept"))
    assert accepted['suggestion']['status'] == 'accepted'
    loaded = assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert [row['text_content'] for row in loaded['blocks']] == ['First rewritten', 'block tail']
