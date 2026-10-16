import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _select_text, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v4194_bookmark_in_unassigned_scene_gap_marks_dash_and_updates(scene_live_server, scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'Gap bookmark 4194')
    blocks=_set_blocks(base,ch['id'],['Scene words','Outside bookmarked words','Scene two words'])
    requests.post(base+'/api/scenes',json={'block_ids':[blocks[0]['id']]},timeout=10).raise_for_status()
    requests.post(base+'/api/scenes',json={'block_ids':[blocks[2]['id']]},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#scenesNavMode').click(); page.wait_for_timeout(120)
        assert page.locator('.scene-gap-row').count()==1
        assert page.locator('.scene-gap-row .scene-gap-bookmark-icon').count()==0

        # Bookmark the prose represented by the --- gap.
        page.evaluate('(id)=>openWholeBook(false,false)',tab['id']); page.wait_for_timeout(100)
        wraps=page.locator('.manuscript-block-wrap')
        gap_index=page.evaluate("""id => [...document.querySelectorAll('.manuscript-block-wrap')].findIndex(w=>String(w.dataset.blockId)===String(id))""",blocks[1]['id'])
        _select_text(page,gap_index)
        page.locator('#selectionToolbar [data-selection-action="bookmark"]').click(); page.wait_for_timeout(320)
        page.locator('#scenesNavMode').click(); page.wait_for_timeout(100)
        gap=page.locator('.scene-gap-row')
        assert gap.locator('.scene-gap-bookmark-icon').count()==1
        assert gap.locator('.scene-gap-dashes').count()==1
        # The ribbon is appended after the dash element, not before it.
        assert gap.evaluate("el => el.querySelector('.scene-gap-dashes').nextElementSibling?.classList.contains('scene-gap-bookmark-icon')")

        # Removing the one bookmark clears the gap breadcrumb immediately.
        page.evaluate('removeBookmark()'); page.wait_for_timeout(320)
        assert page.locator('.scene-gap-row .scene-gap-bookmark-icon').count()==0
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
