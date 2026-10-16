from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page


def _ok(response):
    response.raise_for_status()
    return response.json()


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client':'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v8206_multiblock_accept_clean_header_and_local_hover(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, '8.20.6 Multi-block Review')
    _set_blocks(base, chapter['id'], ['First block', 'Middle paragraph', 'Second block tail'])
    review = _ok(requests.post(base+'/api/reviews', json={
        'name':'Developmental Review','permission_read':True,'permission_suggest':True,
    }, timeout=10))
    editor = _ok(requests.post(base+f"/api/reviews/{review['id']}/participants", json={'display_name':'Maria'}, timeout=10))
    started = _ok(requests.post(base+f"/api/reviews/{review['id']}/start", timeout=10))
    reader = _ok(requests.get(base+f"/api/reviews/{review['id']}/participants/{editor['id']}/reader", timeout=10))
    blocks = reader['snapshot']['blocks'][:3]
    _ok(requests.post(base+f"/api/reviews/{review['id']}/participants/{editor['id']}/suggestions", json={
        'selection':{
            'start_block_id':blocks[0]['source_block_id'],
            'end_block_id':blocks[2]['source_block_id'],
            'start_offset':6,
            'end_offset':7,
        },
        'operation':'replace','proposed_text':'rewritten',
    }, timeout=10))

    page, errors = _page(base, scene_browser)
    try:
        page.locator('#manuscriptReviewsBtn').click()
        page.locator('[data-manuscript-review-open]').filter(has_text='Developmental Review').first.click()
        page.wait_for_function("()=>CSS.highlights.has('manuscript-review-suggestion-replace')")
        page.wait_for_selector('.manuscript-review-proposal-decoration.operation-replace')

        # Writer view is always the whole round; reviewer identity is attribution only.
        assert page.locator('[data-manuscript-review-person]').count() == 0
        assert page.locator('#manuscriptReviewLifecycle').count() == 0
        assert page.locator('#manuscriptReviewOpenWorkspace').count() == 0
        page.locator('#manuscriptReviewDetailMore').click()
        menu = page.locator('#contextMenu:not(.hidden)')
        menu.wait_for()
        menu_text = menu.inner_text()
        assert 'Close' in menu_text and 'Open in Reviews' in menu_text
        page.keyboard.press('Escape')

        # Hover actions use the calculated multi-block range without wrapping editable text.
        point = page.evaluate("""()=>{const entry=manuscriptReviewEntries().find(row=>row.kind==='suggestion');const range=manuscriptReviewRangeForItem(entry.item,entry.kind);const rect=[...range.getClientRects()][0]||range.getBoundingClientRect();return {x:rect.left+Math.max(1,rect.width/2),y:rect.top+Math.max(1,rect.height/2)};}""")
        page.mouse.move(point['x'], point['y'])
        card = page.locator('.manuscript-review-hover-card')
        card.wait_for(timeout=1500)
        geometry = page.evaluate("""()=>{const entry=manuscriptReviewEntries().find(row=>row.kind==='suggestion');const range=manuscriptReviewRangeForItem(entry.item,entry.kind).getBoundingClientRect();const card=document.querySelector('.manuscript-review-hover-card').getBoundingClientRect();return {sourceBottom:range.bottom,sourceTop:range.top,cardTop:card.top,cardBottom:card.bottom,viewport:innerHeight};}""")
        assert geometry['cardTop'] >= geometry['sourceBottom'] - 1 or geometry['cardBottom'] <= geometry['sourceTop'] + 1
        assert card.locator('[data-manuscript-review-hover-accept]').count() == 1

        card.locator('[data-manuscript-review-hover-accept]').click()
        page.wait_for_function("()=>!CSS.highlights.has('manuscript-review-suggestion-replace')")
        page.wait_for_selector('#manuscriptReviewCompletedToggle')
        chapter_now = _ok(requests.get(base+f"/api/chapters/{chapter['id']}", timeout=10))
        assert [row['text_content'] for row in chapter_now['blocks']] == ['First rewritten', 'block tail']
        feedback = _ok(requests.get(base+f"/api/reviews/{review['id']}/feedback", timeout=10))
        assert feedback['suggestions'][0]['status'] == 'accepted'
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
