import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page,
    _select_text, _select_block_range, _shutdown_page,
)


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v41814_mixed_selection_adds_primary_and_remove_in_overflow(scene_live_server,scene_browser):
    base=scene_live_server; tab,chapter=_new_project(base,'Scene dual action browser')
    blocks=_set_blocks(base,chapter['id'],['scene start','scene edge','outside'])
    scene=requests.post(base+'/api/scenes',json={'title':'Dual Scene','block_ids':[blocks[0]['id'],blocks[1]['id']]},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#bookViewBtn').click(); page.wait_for_timeout(200)
        # Crossing the end boundary keeps Add to Scene primary but also exposes Remove in •••.
        _select_block_range(page,1,2)
        primary=page.locator('#selectionToolbar [data-selection-action="scene"]')
        assert primary.inner_text().strip()=='Add to Scene' and not primary.is_disabled()
        page.locator('#selectionToolbar [data-selection-action="more"]').click()
        remove=page.locator('#contextMenu button').filter(has_text='Remove from Scene')
        assert remove.count()==1
        remove.click(); page.wait_for_timeout(500)
        current=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()['blocks']
        assert current[0]['scene_id']==scene['id']
        assert current[1]['scene_id'] is None and current[2]['scene_id'] is None

        # Scene-only removable prose uses Remove from Scene in the normal primary position.
        _select_text(page,0)
        primary=page.locator('#selectionToolbar [data-selection-action="scene"]')
        assert primary.inner_text().strip()=='Remove from Scene' and not primary.is_disabled()
        primary.click(); page.wait_for_timeout(500)
        current=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()['blocks']
        assert all(block['scene_id'] is None for block in current)
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v41814_collapsed_scene_rows_have_full_chapter_like_hit_area(scene_live_server,scene_browser):
    base=scene_live_server; tab,chapter=_new_project(base,'Scene compact hit area browser')
    blocks=_set_blocks(base,chapter['id'],['scene one','outside','scene two'])
    first=requests.post(base+'/api/scenes',json={'title':'One','block_ids':[blocks[0]['id']]},timeout=10).json()
    second=requests.post(base+'/api/scenes',json={'title':'Two','block_ids':[blocks[2]['id']]},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#scenesNavMode').click(); page.wait_for_function("document.querySelectorAll('.scene-outline-row').length===2")
        page.locator('#toggleBookNavBtn').click(); page.wait_for_timeout(100)
        assert 'collapsed' in (page.locator('.book-nav').get_attribute('class') or '')
        rows=page.locator('.scene-outline-row')
        box=rows.nth(0).bounding_box(); assert box and box['width']>45
        # Double-click near the far left of the compact row, well outside the centered number.
        rows.nth(0).dblclick(position={'x':3,'y':box['height']/2}); page.wait_for_timeout(350)
        active=page.locator('.scene-outline-row.active')
        assert active.count()==1 and active.get_attribute('data-scene-id')==str(first['id'])
        # While focused, a click near the far right of the next row must switch Scene focus too.
        box2=rows.nth(1).bounding_box(); assert box2
        rows.nth(1).click(position={'x':box2['width']-3,'y':box2['height']/2}); page.wait_for_timeout(400)
        active=page.locator('.scene-outline-row.active')
        assert active.count()==1 and active.get_attribute('data-scene-id')==str(second['id'])
        style=active.evaluate("e=>({bg:getComputedStyle(e).backgroundColor,shadow:getComputedStyle(e).boxShadow,cursor:getComputedStyle(e).cursor})")
        assert style['cursor']=='pointer' and style['shadow']!='none'
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
