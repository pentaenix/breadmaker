import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page, _select_text, _shutdown_page,
)


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v419_bookmark_ui_text_owner_and_safe_removal(scene_live_server, scene_browser):
    base=scene_live_server; tab,chapter=_new_project(base,'Bookmark browser')
    blocks=_set_blocks(base,chapter['id'],['Bookmark this paragraph','Second paragraph'])
    scene=requests.post(base+'/api/scenes',json={'block_ids':[blocks[0]['id']]},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        # Toolbar is navigation-only and gives useful feedback when empty.
        page.locator('#bookmarkBtn').click()
        page.wait_for_function("document.querySelector('#toast').textContent.includes('No bookmark set')")

        # Exact text selection creates the one bookmark from the dedicated toolbar icon.
        _select_text(page,0)
        button=page.locator('#selectionToolbar [data-selection-action="bookmark"]')
        assert button.is_visible()
        button.click(); page.wait_for_timeout(500)
        saved=requests.get(base+'/api/bookmark',timeout=10).json()['bookmark']
        assert saved['target_type']=='range' and saved['start_block_id']==blocks[0]['id']
        assert page.locator(f'.manuscript-block-wrap[data-block-id="{blocks[0]["id"]}"] .text-bookmark-marker').count()==1
        page.locator('#chaptersNavMode').click(); page.wait_for_timeout(150)
        assert page.locator(f'.chapter-row[data-id="{chapter["id"]}"] .sidebar-bookmark-icon').count()==1

        # The starting Scene gets the structural breadcrumb too.
        page.locator('#scenesNavMode').click(); page.wait_for_timeout(150)
        assert page.locator(f'.scene-outline-row[data-scene-id="{scene["id"]}"] .sidebar-bookmark-icon').count()==1

        # Single click only arms removal; the second click removes it.
        icon=page.locator(f'.scene-outline-row[data-scene-id="{scene["id"]}"] .sidebar-bookmark-icon')
        icon.click(); page.wait_for_timeout(230)
        assert requests.get(base+'/api/bookmark',timeout=10).json()['bookmark'] is not None
        assert 'Click again to remove bookmark' in page.locator('#toast').inner_text()
        icon.click(); page.wait_for_timeout(500)
        assert requests.get(base+'/api/bookmark',timeout=10).json()['bookmark'] is None
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v419_reload_prefers_last_position_over_bookmark(scene_live_server, scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'Restore position browser')
    _set_blocks(base,ch1['id'],['Chapter one prose'])
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    _set_blocks(base,ch2['id'],['Chapter two prose'])
    # Bookmark Chapter 1, but work last in Chapter 2.
    response=requests.put(base+'/api/bookmark',json={'target_type':'chapter','chapter_id':ch1['id']},timeout=10); response.raise_for_status()

    page,errors=_page(base,scene_browser)
    try:
        page.evaluate("id => selectChapter(id,false,false)",ch2['id'])
        page.wait_for_function("title => document.querySelector('#chapterTitle').textContent.trim()===title",arg='Chapter 2')
        page.wait_for_timeout(450)  # allow debounced last-location persistence
        saved=requests.get(base+'/api/ui-state/manuscript-last-location',timeout=10).json()['value']
        assert saved['view']=='chapter' and saved['chapter_id']==ch2['id']
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)

    # A fresh UI boot must restore the last working position, not the bookmark.
    page2,errors2=_page(base,scene_browser)
    try:
        page2.wait_for_function("title => document.querySelector('#chapterTitle').textContent.trim()===title",arg='Chapter 2')
        assert page2.locator('#chapterTitle').inner_text().strip()=='Chapter 2'
        assert requests.get(base+'/api/bookmark',timeout=10).json()['bookmark']['chapter_id']==ch1['id']
        # The toolbar bookmark remains an explicit jump back to Chapter 1.
        page2.locator('#bookmarkBtn').click(); page2.wait_for_timeout(300)
        assert page2.locator('#chapterTitle').inner_text().strip()==ch1['title']
        assert not errors2,errors2
    finally:
        _release(base); _shutdown_page(page2)


def test_v419_reload_restores_focused_scene(scene_live_server, scene_browser):
    base=scene_live_server; tab,chapter=_new_project(base,'Restore scene browser')
    blocks=_set_blocks(base,chapter['id'],['Scene focus prose','Outside prose'])
    scene=requests.post(base+'/api/scenes',json={'block_ids':[blocks[0]['id']]},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate("id => selectScene(id,false,false)",scene['id'])
        page.wait_for_function("title => document.querySelector('.scene-view-boundary')?.textContent.includes(title)",arg=scene['title'])
        page.wait_for_timeout(450)
        saved=requests.get(base+'/api/ui-state/manuscript-last-location',timeout=10).json()['value']
        assert saved['view']=='scene' and saved['scene_id']==scene['id']
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
    page2,errors2=_page(base,scene_browser)
    try:
        page2.wait_for_function("title => document.querySelector('.scene-view-boundary')?.textContent.includes(title)",arg=scene['title'])
        assert page2.locator('.scene-view-boundary').count()==2
        assert not errors2,errors2
    finally:
        _release(base); _shutdown_page(page2)
