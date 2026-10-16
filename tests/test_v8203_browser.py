from __future__ import annotations

import time
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


def test_v8203_review_panel_survives_reload_and_replace_reads_as_delete_plus_add(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, '8.20.3 Review Continuity')
    _set_blocks(base, chapter['id'], ['She walked slowly toward the door.'])
    review = _ok(requests.post(base+'/api/reviews', json={
        'name':'Developmental Review','permission_read':True,'permission_comment':True,
        'permission_suggest':True,'permission_feedback':True,
    }, timeout=10))
    maria = _ok(requests.post(base+f"/api/reviews/{review['id']}/participants", json={'display_name':'Maria'}, timeout=10))
    started = _ok(requests.post(base+f"/api/reviews/{review['id']}/start", timeout=10))
    reader = _ok(requests.get(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/reader", timeout=10))
    block = reader['snapshot']['blocks'][0]
    text = block['text_content']
    start = text.index('walked slowly')
    end = start + len('walked slowly')
    _ok(requests.post(base+f"/api/reviews/{review['id']}/participants/{maria['id']}/suggestions", json={
        'selection':{'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':start,'end_offset':end},
        'operation':'replace','proposed_text':'hurried'
    }, timeout=10))

    page, errors = _page(base, scene_browser)
    replacement_errors = errors
    try:
        page.locator('#manuscriptReviewsBtn').click()
        page.locator('[data-manuscript-review-open]').filter(has_text='Developmental Review').first.click()
        page.wait_for_function("()=>CSS.highlights.has('manuscript-review-suggestion-replace')")
        page.wait_for_selector('.manuscript-review-proposal-decoration.operation-replace')
        assert page.locator('[data-manuscript-review-person]').count() == 0

        source_text = page.evaluate("""()=>{const entry=manuscriptReviewEntries().find(row=>row.kind==='suggestion');return manuscriptReviewRangeForItem(entry.item,entry.kind)?.toString()||'';}""")
        proposed = page.locator('.manuscript-review-proposal-decoration.operation-replace').first
        assert source_text == 'walked slowly'
        assert proposed.inner_text().replace('↳ ', '') == 'hurried'
        assert page.evaluate("()=>getComputedStyle(document.querySelector('.manuscript-review-proposal-decoration.operation-replace')).boxShadow!='none'") is True

        # UI state is persisted immediately; wait only for the server mirror used by
        # the isolated browser harness, then simulate a fresh page load.
        deadline=time.time()+3
        saved={}
        while time.time()<deadline:
            saved=_ok(requests.get(base+'/api/ui-state/manuscript-reviews', timeout=10)).get('value') or {}
            if saved.get('panel_open') is True and saved.get('review_id')==review['id'] and str(saved.get('snapshot_id'))==str(started['snapshot']['id']):
                break
            time.sleep(.05)
        assert saved.get('panel_open') is True
        assert saved.get('review_id') == review['id']
        assert 'participant_id' not in saved
        assert not replacement_errors, replacement_errors
    finally:
        _shutdown_page(page)

    page2, errors2 = _page(base, scene_browser)
    try:
        page2.wait_for_selector('#manuscriptReviewsPanel:not(.hidden)')
        page2.wait_for_selector('.manuscript-review-detail-title')
        assert 'Developmental Review' in page2.locator('.manuscript-review-detail-title').inner_text()
        assert page2.locator('[data-manuscript-review-person]').count() == 0
        page2.wait_for_function("()=>CSS.highlights.has('manuscript-review-suggestion-replace')")
        assert page2.locator('.manuscript-review-proposal-decoration.operation-replace').count() == 1
        assert not errors2, errors2
    finally:
        _release(base)
        _shutdown_page(page2)
