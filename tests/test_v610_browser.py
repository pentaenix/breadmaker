from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v610_project_grid_and_tab_hover_parity(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Grid Project One')
    _new_project(base,'Grid Project Two')
    tab,_=_new_project(base,'Grid Project Three')
    r=requests.patch(base+f'/api/documents/{tab["id"]}',json={'description':'This description should fly out like a Chapter.'},timeout=10);r.raise_for_status()
    expected=len(requests.get(base+'/api/workspace',timeout=10).json()['projects'])
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate("()=>openAllProjects()")
        page.wait_for_selector('.project-explorer-grid')
        cards=page.locator('.project-explorer-card')
        assert cards.count()==expected
        names=cards.locator('.project-explorer-card-head b').all_inner_texts()
        assert {'Grid Project One','Grid Project Two','Grid Project Three'}<=set(names)
        page.click('#modalClose')
        row=page.locator(f'.document-row[data-document-id="{tab["id"]}"]')
        assert row.count()==1
        assert row.locator('.document-row-main small').count()==0
        row.hover();page.wait_for_timeout(360)
        fly=page.locator('#chapterDetailsHover')
        assert fly.count()==1 and 'This description should fly out like a Chapter.' in fly.inner_text()
        rr=row.bounding_box();fr=fly.bounding_box();assert rr and fr and (fr['x']>=rr['x']+rr['width'] or fr['x']+fr['width']<=rr['x'])
        page.click('#brandButton');page.hover('#projectMenuImport');page.wait_for_selector('#projectMenuImportJsonProject')
        assert page.locator('#projectMenuImportJsonProject').inner_text()=='Import JSON…'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
