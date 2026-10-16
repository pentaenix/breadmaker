from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v632-browser'},timeout=2)
    except Exception: pass


def test_v632_line_details_no_inspector_visibility_and_keyboard_copy_paste(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.2 line cleanup')
    first=requests.post(base+'/api/maps',json={'name':'First Map'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{first['id']}/lines",json={'name':'Arc','description':'Old details','color':'#336699'},timeout=10).json()
    requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Beat','planned_start':.3,'schematic_start':.3},timeout=10)
    second=requests.post(base+'/api/maps',json={'name':'Second Map'},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{first["id"]}"]').click();page.wait_for_timeout(180)
        row=page.locator(f'[data-map-tree-line="{line["id"]}"]')
        row.click();page.wait_for_timeout(80)
        assert page.locator('#mapInspector').evaluate("el=>el.classList.contains('hidden')") is True

        # The menu is intentionally small and Line-specific.
        row.locator('.map-tree-line-more').click();page.wait_for_timeout(80)
        menu=page.locator('#contextMenu').inner_text()
        assert 'Details' in menu and 'Hide' in menu and 'Add Station' in menu and 'Add Span' in menu and 'Delete' in menu
        for retired in ('Focus','Color','Populate','New Line','Import JSON','Connect to Another','Duplicate','Lock Line'):
            assert retired not in menu
        page.keyboard.press('Escape')

        # Details owns name, description, and color.
        row.dblclick();page.wait_for_timeout(80)
        assert page.locator('#modalTitle').inner_text()=='Line Details'
        page.locator('#mapLineDetailsName').fill('Arc Renamed')
        page.locator('#mapLineDetailsDescription').fill('New details')
        page.locator('#mapLineDetailsColorText').fill('#8844aa')
        page.locator('#saveMapLineDetails').click();page.wait_for_timeout(220)
        detail=requests.get(base+f"/api/maps/{first['id']}",timeout=10).json();saved=next(x for x in detail['lines'] if x['id']==line['id'])
        assert saved['name']=='Arc Renamed' and saved['description']=='New details' and saved['color'].lower()=='#8844aa'

        # Visibility lives in the menu and hidden Lines remain present/greyed in the rail.
        page.locator(f'[data-map-tree-line="{line["id"]}"] .map-tree-line-more').click();page.locator('#contextMenu button').filter(has_text='Hide').click();page.wait_for_timeout(220)
        hidden=page.locator(f'[data-map-tree-line="{line["id"]}"]');assert 'hidden-line' in (hidden.get_attribute('class') or '')
        hidden.locator('.map-tree-line-more').click();assert 'Show' in page.locator('#contextMenu').inner_text();page.keyboard.press('Escape')

        # Ctrl/Cmd-style copy/paste replaces the old Duplicate menu action, including across Maps.
        hidden.click();page.keyboard.press('Control+c');page.wait_for_timeout(50)
        page.locator(f'.map-tree-map-row[data-map-id="{second["id"]}"]').click();page.wait_for_timeout(160)
        page.keyboard.press('Control+v');page.wait_for_timeout(300)
        second_detail=requests.get(base+f"/api/maps/{second['id']}",timeout=10).json()
        copies=[x for x in second_detail['lines'] if x['name'].startswith('Arc Renamed Copy')]
        assert len(copies)==1 and [i['title'] for i in copies[0]['items']]==['Beat']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
