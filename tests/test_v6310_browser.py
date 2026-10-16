from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v6310_populated_chapter_station_exposes_structural_link_and_description_has_no_placeholder(scene_live_server,scene_browser):
    base=scene_live_server
    _,chapter=_new_project(base,'Maps 6.3.10 chapter links')
    story=requests.post(base+'/api/maps',json={'name':'Chapter Links','layout_mode':'scaled','label_mode':'minimal'},timeout=10).json()
    populated=requests.post(base+f"/api/maps/{story['id']}/populate",json={'template_id':'chapters'},timeout=10).json()
    detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();line=next(x for x in detail['lines'] if x['id']==populated['line_id']);station=line['items'][0]
    assert station['start_anchor_type']=='chapter' and int(station['start_anchor_id'])==chapter['id']

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        page.locator(f'#mapSvg [data-map-item="{station["id"]}"] .map-stop-inner').click();page.wait_for_timeout(220)
        panel=page.locator('#mapInspector');assert panel.is_visible()
        assert page.locator('#mapItemDescription').get_attribute('placeholder') in (None,'')
        rows=panel.locator('.link-row')
        assert rows.count()>=1
        text=rows.first.inner_text()
        assert chapter['title'] in text
        assert 'Chapter' in text
        assert 'Structural link' in text
        assert rows.first.locator('[data-delete-ref]').count()==0
        rows.first.locator('.reference-target').click();page.wait_for_timeout(320)
        assert page.locator('#manuscriptView').get_attribute('class') and 'active' in page.locator('#manuscriptView').get_attribute('class').split()
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v6310_span_at_line_end_does_not_render_broken_line_endpoint_ring(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.10 span endpoint')
    story=requests.post(base+'/api/maps',json={'name':'Span Endpoint','layout_mode':'scaled','label_mode':'minimal'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Line','planned_start':.1,'planned_end':.9,'schematic_start':.1,'schematic_end':.9},timeout=10).json()
    span=requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'span','title':'Ending Span','planned_start':.65,'planned_end':.9,'schematic_start':.65,'schematic_end':.9},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        page.locator(f'#mapSvg [data-map-line-route="{line["id"]}"]').first.dispatch_event('click');page.wait_for_timeout(180)
        assert page.locator(f'#mapSvg [data-map-line-handle="{line["id"]}"][data-line-edge="end"]').count()==0
        # The Span remains the endpoint interaction owner.
        page.locator(f'#mapSvg [data-map-item="{span["id"]}"] .map-span-shape').click();page.wait_for_timeout(160)
        assert page.locator(f'#mapSvg [data-map-item="{span["id"]}"] [data-span-edge="end"]').count()==1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
