from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v641_ruler_length_is_render_only_and_compression_balances_station_density(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.4.1 ruler length')
    story=requests.post(base+'/api/maps',json={'name':'Compression','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Story','planned_start':.05,'planned_end':.95,'schematic_start':.05,'schematic_end':.95},timeout=10).json()
    for index,pos in enumerate([.48,.481,.482],1):
        requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':f'Station {index}','planned_start':pos,'schematic_start':pos},timeout=10).raise_for_status()
    before=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
    stored_before=[(item['id'],item['planned_start'],item['schematic_start']) for item in before['lines'][0]['items']]

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(220)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        page.locator('#mapViewBtn').click();page.wait_for_timeout(80)
        slider=page.locator('#mapRulerScale')
        assert slider.input_value()=='100'
        baseline=page.locator('#mapSvg').evaluate("el=>({axis:el._mapLayout.baseAxisLength,width:el._mapLayout.width,r:+el.querySelector('.map-stop-outer').getAttribute('r'),label:getComputedStyle(el.querySelector('.map-item-label')).fontSize,zoom:document.querySelector('#mapZoomReset').textContent})")
        slider.evaluate("el=>{el.value='60';el.dispatchEvent(new Event('input',{bubbles:true}))}")
        page.wait_for_timeout(160)
        compressed=page.locator('#mapSvg').evaluate("el=>{const stops=[...el.querySelectorAll('.map-stop-outer')];const xs=stops.map(n=>+n.getAttribute('cx')).sort((a,b)=>a-b);return {axis:el._mapLayout.baseAxisLength,width:el._mapLayout.width,r:+stops[0].getAttribute('r'),gap:xs[1]-xs[0],label:getComputedStyle(el.querySelector('.map-item-label')).fontSize,zoom:document.querySelector('#mapZoomReset').textContent,stored:localStorage.getItem('breadmaker-map-ruler-scale')}}")
        assert 0.59 < compressed['axis']/baseline['axis'] < 0.61
        assert compressed['width'] < baseline['width']
        assert 0.79 < compressed['r']/baseline['r'] < 0.81
        assert compressed['gap'] < compressed['r']*2  # modest overlap is explicitly allowed at maximum compression
        assert compressed['gap'] > compressed['r']*1.6
        assert compressed['label']==baseline['label']
        assert compressed['zoom']==baseline['zoom']=='100%'
        assert compressed['stored']=='0.6'

        after=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        stored_after=[(item['id'],item['planned_start'],item['schematic_start']) for item in after['lines'][0]['items']]
        assert stored_after==stored_before

        # Expansion is also independent of zoom and uses the same underlying positions.
        slider.evaluate("el=>{el.value='180';el.dispatchEvent(new Event('input',{bubbles:true}))}")
        page.wait_for_timeout(100)
        expanded=page.locator('#mapSvg').evaluate("el=>({axis:el._mapLayout.baseAxisLength,r:+el.querySelector('.map-stop-outer').getAttribute('r')})")
        assert 1.79 < expanded['axis']/baseline['axis'] < 1.81
        assert expanded['r']==baseline['r']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
