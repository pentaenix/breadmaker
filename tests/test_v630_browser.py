from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v630-browser'},timeout=2)
    except Exception: pass


def _rects_do_not_overlap(rects,pad=1):
    for i,a in enumerate(rects):
        for b in rects[i+1:]:
            if a['x'] < b['x']+b['width']+pad and a['x']+a['width']+pad > b['x'] and a['y'] < b['y']+b['height']+pad and a['y']+a['height']+pad > b['y']:
                return False,(a,b)
    return True,None


def test_v630_map_readability_drag_span_connections_and_inspector_parity(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.0')
    boot=requests.get(base+'/api/bootstrap',timeout=10).json();tab=next(x for x in boot['tabs'] if x['is_manuscript'])
    chapter=requests.post(base+'/api/chapters',json={'title':'Linked Chapter','tab_id':tab['id']},timeout=10).json()
    story=requests.post(base+'/api/maps',json={'name':'Readable Map','ruler':'percent','label_mode':'full','layout_mode':'scaled'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Character Arc'},timeout=10).json()
    labels=[]
    for idx,p in enumerate((.10,.105,.11,.115)):
        labels.append(requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':f'Long readable station label number {idx+1}','planned_start':p,'schematic_start':p},timeout=10).json())
    linked=requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Linked movable station','planned_start':.35,'schematic_start':.35,'start_anchor_type':'chapter','start_anchor_id':str(chapter['id'])},timeout=10).json()
    span=requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'span','title':'Source Span','planned_start':.48,'planned_end':.62,'schematic_start':.48,'schematic_end':.62},timeout=10).json()
    other=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Second Line'},timeout=10).json()
    target=requests.post(base+f"/api/map-lines/{other['id']}/items",json={'item_type':'span','title':'Target Span','planned_start':.50,'planned_end':.70,'schematic_start':.50,'schematic_end':.70},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)

        # Hard readability rule: item labels do not overlap ruler labels or each other.
        item_rects=page.locator('#mapSvg .map-item-label').evaluate_all("els=>els.filter(e=>getComputedStyle(e).display!=='none').map(e=>{const r=e.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,text:e.textContent}})")
        ruler_rects=page.locator('#mapSvg .map-ruler-label').evaluate_all("els=>els.map(e=>{const r=e.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,text:e.textContent}})")
        ok,pair=_rects_do_not_overlap(item_rects);assert ok,pair
        for a in item_rects:
            for b in ruler_rects:
                assert not (a['x'] < b['x']+b['width'] and a['x']+a['width'] > b['x'] and a['y'] < b['y']+b['height'] and a['y']+a['height'] > b['y']), (a,b)

        # Unlocked, manuscript-linked Station can be dragged without losing its link.
        circle=page.locator(f'[data-map-item="{linked["id"]}"] .map-stop-outer');box=circle.bounding_box();assert box
        before=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();before_item=next(i for l in before['lines'] for i in l['items'] if i['id']==linked['id']);before_p=before_item['planned_start']
        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']/2+120,box['y']+box['height']/2,steps=8);page.mouse.up();page.wait_for_timeout(420)
        after=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();after_item=next(i for l in after['lines'] for i in l['items'] if i['id']==linked['id'])
        assert after_item['planned_start']!=before_p
        assert after_item['start_anchor_type']=='chapter' and after_item['start_source']=='manuscript'

        # Span exposes three outgoing handles, while every Span has three hidden input anchors.
        page.locator(f'[data-map-item="{span["id"]}"] .map-span-shape').click();page.wait_for_timeout(160)
        outputs=page.locator(f'[data-map-item="{span["id"]}"] .map-span-connect-output');assert outputs.count()==3
        assert sorted(outputs.evaluate_all("els=>els.map(e=>e.getAttribute('aria-label'))"))==['Connect from Span end','Connect from Span middle','Connect from Span start']
        assert page.locator(f'[data-map-item="{target["id"]}"] .map-span-connect-anchor').count()==3

        # Direct connection drag reveals target anchors instead of relying on invisible geometry.
        outputs.nth(1).scroll_into_view_if_needed();page.wait_for_timeout(80)
        out_box=outputs.nth(1).bounding_box();target_box=page.locator(f'[data-map-item="{target["id"]}"] .map-span-shape').bounding_box();assert out_box and target_box
        cx=out_box['x']+out_box['width']/2;cy=out_box['y']+out_box['height']/2
        page.mouse.move(cx,cy);page.mouse.down();page.wait_for_timeout(50);page.mouse.move(cx,cy+12,steps=2);page.wait_for_timeout(50)
        assert page.locator('#mapStage').evaluate("el=>el.classList.contains('connecting-direct')") is True
        visible_targets=page.locator(f'[data-map-item="{target["id"]}"] .map-span-connect-anchor').evaluate_all("els=>els.filter(e=>getComputedStyle(e).opacity!=='0').length");assert visible_targets==3
        page.mouse.move(target_box['x']+target_box['width']/2,target_box['y']+target_box['height']/2,steps=6);page.mouse.up();page.wait_for_timeout(420)
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();assert any(j['from_item_id']==span['id'] and j['from_edge']=='point' and j['to_item_id']==target['id'] and j['to_edge']=='point' for j in detail['junctions'])

        # The hard readability rule also holds when the same Map is vertical.
        page.locator('#mapViewBtn').click();page.locator('#mapViewOrientation').click();page.wait_for_timeout(320)
        assert page.locator('#mapSvg').get_attribute('data-orientation')=='vertical'
        vertical_items=page.locator('#mapSvg .map-item-label').evaluate_all("els=>els.filter(e=>getComputedStyle(e).display!=='none').map(e=>{const r=e.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,text:e.textContent}})")
        vertical_ruler=page.locator('#mapSvg .map-ruler-label').evaluate_all("els=>els.map(e=>{const r=e.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,text:e.textContent}})")
        ok,pair=_rects_do_not_overlap(vertical_items);assert ok,pair
        for a in vertical_items:
            for b in vertical_ruler:
                assert not (a['x'] < b['x']+b['width'] and a['x']+a['width'] > b['x'] and a['y'] < b['y']+b['height'] and a['y']+a['height'] > b['y']), (a,b)

        # 6.3.9 supersedes the old Map-specific inspector with the Canvas Card pattern.
        page.locator(f'[data-map-item="{linked["id"]}"] .map-stop-inner').click();page.wait_for_timeout(100)
        body=page.locator('#mapInspectorBody').inner_text()
        assert 'Name' in body and 'Description' in body and 'Linked items' in body
        assert 'Schematic position' not in body
        assert 'Position on timeline' not in body
        assert 'Manuscript' not in body
        assert 'Connections' not in body
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
