from __future__ import annotations

import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page, _select_text, _shutdown_page,
)


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v8190_manuscript_toolbar_find_bookmark_and_stub_panels(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, '8.19 Manuscript toolbar')
    _set_blocks(base, chapter['id'], ['A selected passage for the new manuscript toolbar.'])
    page, errors = _page(base, scene_browser)
    try:
        # Find & Replace is a View command and opening it closes View.
        assert not page.locator('#bookmarkBtn').is_visible()
        page.locator('#viewBtn').click()
        assert page.locator('#viewFindReplaceBtn').is_visible()
        page.locator('#viewFindReplaceBtn').click()
        assert page.locator('#findPopover').is_visible()
        assert not page.locator('#viewPopover').is_visible()
        page.locator('#findClose').click()

        # The compact selection bookmark remains and explains itself after 300 ms.
        _select_text(page, 0)
        bookmark_action = page.locator('#selectionBookmarkBtn')
        assert bookmark_action.is_visible()
        bookmark_action.hover()
        page.wait_for_timeout(250)
        assert not page.locator('#selectionBookmarkTooltip').is_visible()
        page.locator('#selectionBookmarkTooltip').wait_for(state='visible', timeout=1000)

        # Selected-text right click exposes the same bookmark action.
        page.evaluate("""() => {
          const prose=document.querySelector('.manuscript-block');
          prose.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:160,clientY:180}));
        }""")
        menu_labels = page.locator('#contextMenu button').all_inner_texts()
        assert any('Bookmark selection' in label for label in menu_labels)
        page.keyboard.press('Escape')

        # Setting the bookmark reveals the quiet viewport jump control.
        _select_text(page, 0)
        page.locator('#selectionBookmarkBtn').click()
        page.wait_for_function("()=>!document.querySelector('#bookmarkBtn').classList.contains('hidden')")
        assert page.locator('#bookmarkBtn').is_visible()
        page.locator('#bookmarkBtn').hover()
        page.wait_for_timeout(250)
        assert not page.locator('#bookmarkTooltip').is_visible()
        page.locator('#bookmarkTooltip').wait_for(state='visible', timeout=1000)

        # Image and Reviews do no more than open mutually exclusive side panels.
        page.locator('#manuscriptImageBtn').click()
        assert page.locator('#manuscriptImagePanel').is_visible()
        assert not page.locator('#manuscriptReviewsPanel').is_visible()
        page.locator('#manuscriptReviewsBtn').click()
        assert page.locator('#manuscriptReviewsPanel').is_visible()
        assert not page.locator('#manuscriptImagePanel').is_visible()
        page.locator('#closeManuscriptReviewsPanel').click()
        assert not page.locator('#manuscriptReviewsPanel').is_visible()

        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
