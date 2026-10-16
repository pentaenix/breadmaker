from __future__ import annotations

import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page,
)


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v8192_sidebar_bookmark_navigates_and_viewport_context_menu_removes(scene_live_server, scene_browser):
    base = scene_live_server
    tab, chapter_one = _new_project(base, '8.19.2 bookmark navigation')
    _set_blocks(base, chapter_one['id'], ['Chapter one prose'])
    chapter_two = requests.post(base + '/api/chapters', json={'title': 'Bookmark destination', 'tab_id': tab['id']}, timeout=10).json()
    _set_blocks(base, chapter_two['id'], ['Chapter two bookmark destination'])
    response = requests.put(base + '/api/bookmark', json={'target_type': 'chapter', 'chapter_id': chapter_two['id']}, timeout=10)
    response.raise_for_status()

    page, errors = _page(base, scene_browser)
    try:
        page.evaluate('(id)=>selectChapter(id,false,false)', chapter_one['id'])
        page.wait_for_function("title=>document.querySelector('#chapterTitle').textContent.trim()===title", arg=chapter_one['title'])
        page.locator('#chaptersNavMode').click() if page.locator('#chaptersNavMode').is_visible() else None

        side_bookmark = page.locator(f'.chapter-row[data-id="{chapter_two["id"]}"] .sidebar-bookmark-icon')
        assert side_bookmark.count() == 1
        side_bookmark.click()
        page.wait_for_function("title=>document.querySelector('#chapterTitle').textContent.trim()===title", arg='Bookmark destination')
        assert requests.get(base + '/api/bookmark', timeout=10).json()['bookmark']['chapter_id'] == chapter_two['id']

        viewport_bookmark = page.locator('#bookmarkBtn')
        assert viewport_bookmark.is_visible()
        viewport_bookmark.click(button='right')
        remove = page.locator('#contextMenu button', has_text='Remove Bookmark')
        assert remove.is_visible()
        remove.click()
        page.wait_for_function("()=>document.querySelector('#bookmarkBtn').classList.contains('hidden')")
        assert requests.get(base + '/api/bookmark', timeout=10).json()['bookmark'] is None

        order = page.evaluate("()=>[...document.querySelector('.document-toolbar').children].map(el=>el.id).filter(Boolean)")
        assert order.index('checkpointBtn') < order.index('manuscriptReviewsBtn') < order.index('cutsBtn')
        page.locator('#markupBtn').hover(); page.wait_for_timeout(350)
        assert 'Markers' in page.locator('#markupTooltip').inner_text()
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
