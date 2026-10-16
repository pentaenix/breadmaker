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


def test_v8205_accept_applies_text_and_moves_suggestion_to_completed(scene_live_server, scene_browser):
    base = scene_live_server
    original = 'Delete this sentence.'
    _tab, chapter = _new_project(base, '8.20.5 Completed Feedback')
    _set_blocks(base, chapter['id'], [original])
    review = _ok(requests.post(base+'/api/reviews', json={
        'name':'Developmental Review','permission_read':True,'permission_comment':True,
        'permission_suggest':True,'permission_feedback':True,
    }, timeout=10))
    maria = _ok(requests.post(base+f"/api/reviews/{review['id']}/participants", json={'display_name':'Maria'}, timeout=10))
    _ok(requests.post(base+f"/api/reviews/{review['id']}/start", timeout=10))
    reader = _ok(requests.get(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/reader", timeout=10))
    block = reader['snapshot']['blocks'][0]
    _ok(requests.post(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/suggestions", json={
        'selection':{
            'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],
            'start_offset':0,'end_offset':len(original),
        },
        'operation':'delete','proposed_text':'',
    }, timeout=10))

    page, errors = _page(base, scene_browser)
    try:
        page.locator('#manuscriptReviewsBtn').click()
        page.locator('[data-manuscript-review-open]').filter(has_text='Developmental Review').first.click()
        page.wait_for_function("()=>CSS.highlights.has('manuscript-review-suggestion-delete')")
        source_text = page.evaluate("""()=>{const entry=manuscriptReviewEntries().find(row=>row.kind==='suggestion');return manuscriptReviewRangeForItem(entry.item,entry.kind)?.toString()||'';}""")
        assert source_text == original
        assert page.locator('.manuscript-review-feedback-card:not(.completed)').count() == 1

        point = page.evaluate("""()=>{const entry=manuscriptReviewEntries().find(row=>row.kind==='suggestion');const range=manuscriptReviewRangeForItem(entry.item,entry.kind);const rect=[...range.getClientRects()][0]||range.getBoundingClientRect();return {x:rect.left+Math.max(1,rect.width/2),y:rect.top+Math.max(1,rect.height/2)};}""")
        page.mouse.move(point['x'], point['y'])
        page.locator('[data-manuscript-review-hover-accept]').wait_for(timeout=1500)
        page.locator('[data-manuscript-review-hover-accept]').click()

        # Accept changes the actual manuscript immediately and removes the editorial overlay.
        page.wait_for_function("()=>!CSS.highlights.has('manuscript-review-suggestion-delete')")
        page.wait_for_function("()=>document.querySelector('.manuscript-block')?.innerText==='' ")
        assert page.locator('.manuscript-review-feedback-card:not(.completed)').count() == 0

        # Finished work is retained, but tucked under Completed rather than left in the working list.
        toggle = page.locator('#manuscriptReviewCompletedToggle')
        toggle.wait_for()
        assert '1' in toggle.inner_text()
        toggle.click()
        completed = page.locator('.manuscript-review-feedback-card.completed')
        completed.wait_for()
        assert 'Accepted' in completed.inner_text()

        feedback = _ok(requests.get(base+f"/api/reviews/{review['id']}/feedback", timeout=10))
        assert feedback['suggestions'][0]['status'] == 'accepted'
        chapter_payload = _ok(requests.get(base+f"/api/chapters/{chapter['id']}", timeout=10))
        assert chapter_payload['blocks'][0]['text_content'] == ''
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
