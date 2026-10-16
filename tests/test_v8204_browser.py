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


def test_v8204_whole_paragraph_replace_is_not_stale_or_misplaced(scene_live_server, scene_browser):
    base = scene_live_server
    original = 'A difficult choice changes everything.'
    proposed = 'A harder decision changes the entire ending.'
    _tab, chapter = _new_project(base, '8.20.4 Replace Anchor')
    _set_blocks(base, chapter['id'], [original])
    review = _ok(requests.post(base+'/api/reviews', json={
        'name':'Developmental Review','permission_read':True,'permission_comment':True,
        'permission_suggest':True,'permission_feedback':True,
    }, timeout=10))
    maria = _ok(requests.post(base+f"/api/reviews/{review['id']}/participants", json={'display_name':'Maria'}, timeout=10))
    _ok(requests.post(base+f"/api/reviews/{review['id']}/start", timeout=10))
    reader = _ok(requests.get(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/reader", timeout=10))
    block = reader['snapshot']['blocks'][0]
    suggestion = _ok(requests.post(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/suggestions", json={
        'selection':{
            'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],
            'start_offset':0,'end_offset':len(original),
        },
        'operation':'replace','proposed_text':proposed,
    }, timeout=10))

    page, errors = _page(base, scene_browser)
    try:
        page.locator('#manuscriptReviewsBtn').click()
        page.locator('[data-manuscript-review-open]').filter(has_text='Developmental Review').first.click()
        page.wait_for_function("()=>CSS.highlights.has('manuscript-review-suggestion-replace')")
        page.wait_for_selector('.manuscript-review-proposal-decoration.operation-replace')

        source_text = page.evaluate("""()=>{const entry=manuscriptReviewEntries().find(row=>row.kind==='suggestion');return manuscriptReviewRangeForItem(entry.item,entry.kind)?.toString()||'';}""")
        addition = page.locator('.manuscript-review-proposal-decoration.operation-replace').first
        assert source_text == original
        assert addition.inner_text().replace('↳ ', '') == proposed
        assert page.evaluate("""()=>{const add=document.querySelector('.manuscript-review-proposal-decoration.operation-replace');const prose=add?.closest('.manuscript-block');return !!add&&!prose&&add.contentEditable==='false';}""") is True

        # Once the visual proposal exists, the frozen anchor must still resolve against
        # underlying manuscript text rather than counting the proposal as manuscript content.
        assert page.evaluate("()=>!!manuscriptReviewRangeForItem(manuscriptReviewEntries()[0].item)") is True

        # Navigating the suggestion must not report that the unchanged paragraph is stale.
        page.locator('[data-manuscript-review-navigate]').first.click()
        page.wait_for_timeout(250)
        assert 'This passage has changed or no longer exists' not in page.locator('body').inner_text()

        # Review DOM is presentational only. A manuscript save must never serialize the
        # proposal or temporary Review wrappers before the writer accepts it.
        serialized = page.evaluate("()=>serializeWrap(document.querySelector('.manuscript-block-wrap'))")
        assert serialized['text_content'] == original
        assert proposed not in serialized['text_content']
        assert proposed not in serialized['content_html']
        assert 'data-manuscript-review' not in serialized['content_html']
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
