import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page,
)


def test_v41816_scene_created_in_gap_renders_at_manuscript_position(scene_live_server,scene_browser):
    base=scene_live_server; tab,chapter=_new_project(base,'Scene outline placement browser')
    blocks=_set_blocks(base,chapter['id'],['first scene','gap prose','later scene'])
    first=requests.post(base+'/api/scenes',json={'block_ids':[blocks[0]['id']]},timeout=10).json()
    second=requests.post(base+'/api/scenes',json={'block_ids':[blocks[2]['id']]},timeout=10).json()
    inserted=requests.post(base+'/api/scenes',json={'block_ids':[blocks[1]['id']]},timeout=10).json()
    assert inserted['title'].endswith('3')

    page,errors=_page(base,scene_browser)
    try:
        page.locator('#scenesNavMode').click()
        page.wait_for_function("document.querySelectorAll('.scene-outline-row').length===3")
        rows=page.locator('.scene-outline-row')
        ids=[int(rows.nth(i).get_attribute('data-scene-id')) for i in range(3)]
        titles=[rows.nth(i).locator('.scene-outline-title').inner_text().strip() for i in range(3)]
        assert ids==[first['id'],inserted['id'],second['id']]
        assert titles==[first['title'],inserted['title'],second['title']]
        assert not errors,errors
    finally:
        try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
        except Exception: pass
        _shutdown_page(page)
