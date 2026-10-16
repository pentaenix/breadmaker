from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client':'v41812-browser'}, timeout=2)
    except Exception:
        pass


def _ok(response):
    response.raise_for_status()
    return response.json()


def _review_fixture(base, chapter_id):
    _set_blocks(base, chapter_id, ['A difficult choice changes everything.'])
    review = _ok(requests.post(base+'/api/reviews', json={
        'name':'Developmental Review','permission_read':True,'permission_comment':True,
        'permission_suggest':True,'permission_feedback':True,
    }, timeout=10))
    maria = _ok(requests.post(base+f"/api/reviews/{review['id']}/participants", json={'display_name':'Maria'}, timeout=10))
    david = _ok(requests.post(base+f"/api/reviews/{review['id']}/participants", json={'display_name':'David'}, timeout=10))
    questionnaire = _ok(requests.post(base+'/api/questionnaires', json={'name':'Development questions'}, timeout=10))
    question = _ok(requests.post(base+f"/api/questionnaires/{questionnaire['id']}/questions", json={'prompt':'Does the motivation feel earned?','required':False}, timeout=10))
    _ok(requests.post(base+f"/api/reviews/{review['id']}/questionnaires", json={'questionnaire_id':questionnaire['id']}, timeout=10))
    _ok(requests.post(base+f"/api/reviews/{review['id']}/start", timeout=10))
    reader = _ok(requests.get(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/reader", timeout=10))
    block = reader['snapshot']['blocks'][0]
    snapshot_question_id = reader['form']['questions'][0]['id']
    selection = lambda a,b:{'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':a,'end_offset':b}
    _ok(requests.post(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/comments", json={'selection':selection(2,11),'body':'Make the decision cost more.'}, timeout=10))
    _ok(requests.post(base+f"/api/reviews/{review['id']}/participants/{david['id']}/comments", json={'selection':selection(12,18),'body':'I need one more beat here.'}, timeout=10))
    _ok(requests.put(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/feedback", json={
        'answers':[{'question_id':snapshot_question_id,'answer_text':'Almost, but the previous scene needs more pressure.'}],
        'general_feedback':'The middle is working much better now.',
    }, timeout=10))
    return review, maria, david


def test_v8200_manuscript_review_list_detail_people_annotations_and_feedback_modal(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, '8.20 Manuscript Reviews')
    review, maria, david = _review_fixture(base, chapter['id'])
    page, errors = _page(base, scene_browser)
    try:
        page.locator('#manuscriptReviewsBtn').click()
        page.locator('#manuscriptReviewsPanel:not(.hidden)').wait_for()
        # Open is the clean default list filter and the active round is immediately visible.
        assert page.locator('[data-manuscript-review-filter="open"]').get_attribute('class').find('active') >= 0
        row = page.locator('[data-manuscript-review-open]').filter(has_text='Developmental Review').first
        row.wait_for()
        assert '2 comments' in row.inner_text()
        row.click()

        page.wait_for_selector('.manuscript-review-detail-title')
        detail = page.locator('#manuscriptReviewsPanelBody')
        assert 'Developmental Review' in detail.inner_text()
        assert page.locator('[data-manuscript-review-person]').count() == 0
        assert page.locator('.manuscript-review-feedback-card:not(.completed)').count() == 2
        page.wait_for_function("()=>CSS.highlights.has('manuscript-review-comments')")
        assert page.locator('.manuscript-review-gutter-marker').count() >= 1

        # Manuscript Reviews is deliberately always the complete round; people remain attribution, not a filter.
        names = page.locator('.manuscript-review-feedback-card:not(.completed) .manuscript-review-feedback-head small').all_inner_texts()
        assert any('Maria' in name for name in names) and any('David' in name for name in names)
        point = page.evaluate("""()=>{const entry=manuscriptReviewEntries().find(row=>row.kind==='comment');const range=manuscriptReviewRangeForItem(entry.item,entry.kind);const rect=[...range.getClientRects()][0]||range.getBoundingClientRect();return {x:rect.left+Math.max(1,rect.width/2),y:rect.top+Math.max(1,rect.height/2)};}""")
        page.mouse.move(point['x'], point['y'])
        page.locator('.manuscript-review-hover-card').wait_for(timeout=1500)
        assert 'Make the decision cost more.' in page.locator('.manuscript-review-hover-card').inner_text()

        # Questionnaire/general feedback stays one clean modal and reuses Create Task.
        page.locator('#manuscriptReviewQuestions').click()
        page.locator('.manuscript-review-feedback-modal').wait_for()
        modal_text = page.locator('.manuscript-review-feedback-modal').inner_text()
        assert 'Does the motivation feel earned?' in modal_text
        assert 'Almost, but the previous scene needs more pressure.' in modal_text
        assert 'The middle is working much better now.' in modal_text
        page.locator('.manuscript-review-form-answer button').first.click()
        page.locator('#taskName').wait_for()
        assert page.locator('#taskName').input_value().startswith('[Feedback]:')
        page.locator('#modalClose').click()

        # Round lifecycle and the Reviews-workspace escape hatch stay out of the main chrome under one menu.
        assert page.locator('#manuscriptReviewLifecycle').count() == 0
        assert page.locator('#manuscriptReviewOpenWorkspace').count() == 0
        page.locator('#manuscriptReviewDetailMore').click()
        page.locator('#contextMenu button').filter(has_text='Close').first.click()
        page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.reviews.find(r=>r.name==='Developmental Review')?.status==='closed'")
        page.locator('#manuscriptReviewDetailMore').click()
        page.locator('#contextMenu button').filter(has_text='Reopen').first.click()
        page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.reviews.find(r=>r.name==='Developmental Review')?.status==='open'")

        # Closing the panel is not a reset: the working round stays selected.
        page.locator('#manuscriptReviewsBtn').click()
        page.wait_for_function("()=>document.querySelector('#manuscriptReviewsPanel').classList.contains('hidden')")
        page.locator('#manuscriptReviewsBtn').click()
        page.wait_for_selector('#manuscriptReviewsPanel:not(.hidden)')
        page.wait_for_selector('.manuscript-review-detail-title')
        assert 'Developmental Review' in page.locator('.manuscript-review-detail-title').inner_text()

        # Comments are live display anchors: ordinary edits re-anchor the visual range
        # without mutating the immutable Review snapshot.
        prose = page.locator('.manuscript-block').first
        prose.fill('A changed choice changes everything.')
        page.wait_for_timeout(220)
        assert page.evaluate("()=>CSS.highlights.has('manuscript-review-comments')") is True
        live_text = page.evaluate("""()=>{const entry=manuscriptReviewEntries().find(row=>row.kind==='comment');const range=manuscriptReviewRangeForItem(entry.item,entry.kind);return range?.toString()||'';}""")
        assert 'changed' in live_text
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
