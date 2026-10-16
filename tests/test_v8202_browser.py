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


def test_v8202_delete_suggestion_hover_is_quiet_and_actions_are_direct(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, '8.20.2 Inline Review Polish')
    _set_blocks(base, chapter['id'], ['A difficult choice changes everything.'])
    review = _ok(requests.post(base+'/api/reviews', json={
        'name':'Developmental Review','permission_read':True,'permission_comment':True,
        'permission_suggest':True,'permission_feedback':True,
    }, timeout=10))
    maria = _ok(requests.post(base+f"/api/reviews/{review['id']}/participants", json={'display_name':'Maria'}, timeout=10))
    _ok(requests.post(base+f"/api/reviews/{review['id']}/start", timeout=10))
    reader = _ok(requests.get(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/reader", timeout=10))
    block = reader['snapshot']['blocks'][0]
    text = block['text_content']
    start = text.index('difficult')
    end = start + len('difficult')
    _ok(requests.post(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/suggestions", json={
        'selection':{'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':start,'end_offset':end},
        'operation':'delete','proposed_text':''
    }, timeout=10))

    page, errors = _page(base, scene_browser)
    try:
        page.locator('#manuscriptReviewsBtn').click()
        page.locator('[data-manuscript-review-open]').filter(has_text='Developmental Review').first.click()
        page.wait_for_function("()=>CSS.highlights.has('manuscript-review-suggestion-delete')")

        # A single suggestion keeps its icon unobstructed: no "1" badge.
        assert page.locator('.manuscript-review-gutter-marker.kind-suggestion').count() == 1
        assert page.locator('.manuscript-review-gutter-marker.kind-suggestion .manuscript-review-gutter-count').count() == 0
        assert page.locator('.manuscript-review-gutter-change').count() == 1

        # Hover is metadata/actions, not a third copy of the already struck-through manuscript text.
        point = page.evaluate("""()=>{const entry=manuscriptReviewEntries().find(row=>row.kind==='suggestion');const range=manuscriptReviewRangeForItem(entry.item,entry.kind);const rect=[...range.getClientRects()][0]||range.getBoundingClientRect();return {x:rect.left+Math.max(1,rect.width/2),y:rect.top+Math.max(1,rect.height/2)};}""")
        page.mouse.move(point['x'], point['y'])
        hover = page.locator('.manuscript-review-hover-card')
        hover.wait_for(timeout=1500)
        hover_text = hover.inner_text()
        assert 'Maria' in hover_text
        assert 'Delete' in hover_text
        assert 'Accept' in hover_text and 'Reject' in hover_text
        assert 'difficult' not in hover_text

        # Task stays under the ellipsis, and its menu is above the hover card rather than behind it.
        hover.locator('[data-manuscript-review-hover-more]').click()
        menu = page.locator('#contextMenu:not(.hidden)')
        menu.wait_for()
        assert 'Create Task' in menu.inner_text()
        assert 'Accept' not in menu.inner_text() and 'Reject' not in menu.inner_text()
        z = page.evaluate("()=>[getComputedStyle(document.querySelector('#contextMenu')).zIndex,getComputedStyle(document.querySelector('.manuscript-review-hover-card')).zIndex]")
        assert int(z[0]) > int(z[1])
        page.keyboard.press('Escape')

        # Selecting from the gutter focuses the actual Review range, not a count bubble.
        page.locator('.manuscript-review-gutter-marker.kind-suggestion').click()
        assert 'is-selected' not in (page.locator('.manuscript-review-gutter-marker.kind-suggestion').get_attribute('class') or '')
        page.wait_for_function("()=>CSS.highlights.has('manuscript-review-focus')")
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
