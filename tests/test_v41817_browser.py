import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page,
)


def test_v41817_collapsed_scene_order_and_labels_follow_scene_identity(scene_live_server,scene_browser):
    base=scene_live_server; tab,chapter=_new_project(base,'Compact Scene labels browser')
    blocks=_set_blocks(base,chapter['id'],['first scene','gap prose','later scene','custom scene'])
    first=requests.post(base+'/api/scenes',json={'block_ids':[blocks[0]['id']]},timeout=10).json()
    second=requests.post(base+'/api/scenes',json={'block_ids':[blocks[2]['id']]},timeout=10).json()
    inserted=requests.post(base+'/api/scenes',json={'block_ids':[blocks[1]['id']]},timeout=10).json()
    custom=requests.post(base+'/api/scenes',json={'block_ids':[blocks[3]['id']]},timeout=10).json()
    assert inserted['title'].endswith('3')
    response=requests.patch(base+f"/api/scenes/{custom['id']}",json={'title':'Hello','description':''},timeout=10)
    response.raise_for_status()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('#scenesNavMode').click()
        page.wait_for_function("document.querySelectorAll('.scene-outline-row').length===4")
        page.locator('#toggleBookNavBtn').click()
        page.wait_for_function("document.querySelector('.book-nav').classList.contains('collapsed')")
        rows=page.locator('.scene-outline-row')
        ids=[int(rows.nth(i).get_attribute('data-scene-id')) for i in range(4)]
        labels=[rows.nth(i).locator('.scene-short').inner_text().strip() for i in range(4)]
        assert ids==[first['id'],inserted['id'],second['id'],custom['id']]
        assert labels==['1','3','2','H']
        assert not errors,errors
    finally:
        try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
        except Exception: pass
        _shutdown_page(page)
