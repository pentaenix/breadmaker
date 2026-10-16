from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _map_item(detail,item_id):
    return next(item for line in detail['lines'] for item in line.get('items',[]) if item['id']==item_id)




def _map_line_client_point_for_position(page,line_id,position):
    return page.evaluate("""([lineId,position])=>{
      const svg=document.querySelector('#mapSvg'),stage=document.querySelector('#mapStage');
      const layout=svg?._mapLayout,plan=layout?.linePlans?.get(+lineId),lane=layout?.lineLane?.get(+lineId);
      if(!svg||!stage||!layout||!plan||lane===undefined)throw new Error('Map Line geometry is unavailable');
      const axis=mapLineDisplayAxisAtPosition(plan,position),coord=mapAxisCoordinate(layout,axis,lane),zoom=Math.max(.01,state.mapZoom||1);
      if(layout.orientation==='horizontal')stage.scrollLeft=Math.max(0,coord.x*zoom-stage.clientWidth*.72);
      else stage.scrollTop=Math.max(0,coord.y*zoom-stage.clientHeight*.72);
      const matrix=svg.getScreenCTM();if(!matrix)throw new Error('Map SVG has no screen transform');
      const point=svg.createSVGPoint();point.x=coord.x;point.y=coord.y;
      const client=point.matrixTransform(matrix);return{x:client.x,y:client.y,viewportWidth:innerWidth,viewportHeight:innerHeight};
    }""",[line_id,position])

def test_v639_station_inspector_matches_card_links_autosaves_and_delete_has_no_modal(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.9 inspector')
    story=requests.post(base+'/api/maps',json={'name':'Inspector','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Line','planned_start':.1,'planned_end':.9,'schematic_start':.1,'schematic_end':.9},timeout=10).json()
    station=requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Old Name','description':'','planned_start':.4,'schematic_start':.4},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_function("()=>state.view==='maps'||document.querySelector('#mapsView')?.classList.contains('active')")
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        page.locator(f'#mapSvg [data-map-item="{station["id"]}"] .map-stop-inner').click();page.wait_for_timeout(220)
        panel=page.locator('#mapInspector')
        assert panel.is_visible()
        text=panel.inner_text()
        assert 'Linked items' in text
        for dead in ('Map label','Manuscript','Position','Break Line','Connections','Save Station'):
            assert dead not in text
        assert page.locator('#mapItemName').count()==1
        assert page.locator('#mapItemDescription').count()==1
        assert page.locator('#mapItemAddLink').count()==1

        # Map items use the same Task-creation affordance as Canvas Cards.
        page.locator('#mapItemAddLink').click();page.wait_for_timeout(120)
        page.locator('#entityLinkType').select_option('task');page.wait_for_timeout(120)
        assert page.locator('#createLinkedTask').count()==1
        page.locator('#modalClose').click();page.wait_for_timeout(100)

        page.locator('#mapItemName').fill('Renamed Station')
        page.locator('#mapItemDescription').fill('This is the hover description.')
        page.wait_for_timeout(520)
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();saved=_map_item(detail,station['id'])
        assert saved['title']=='Renamed Station'
        assert saved['description']=='This is the hover description.'

        stop=page.locator(f'#mapSvg [data-map-item="{station["id"]}"] .map-stop-inner')
        stop.hover();page.wait_for_timeout(120)
        tip=page.locator('#mapTooltip')
        assert tip.is_visible()
        assert 'This is the hover description.' in tip.inner_text()
        assert 'Renamed Station' not in tip.inner_text()

        page.locator('#deleteMapItemInspector').click();page.wait_for_timeout(420)
        assert 'hidden' in (page.locator('#modal').get_attribute('class') or '').split()
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        assert not any(item['id']==station['id'] for ln in detail['lines'] for item in ln.get('items',[]))
        page.keyboard.press('ControlOrMeta+z');page.wait_for_timeout(520)
        restored=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        assert any(item['id']==station['id'] for ln in restored['lines'] for item in ln.get('items',[]))
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v639_populated_span_resize_persists_and_station_renders_above_span(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.9 span resize')
    story=requests.post(base+'/api/maps',json={'name':'Span Resize','layout_mode':'scaled','label_mode':'minimal','ruler':'percent'},timeout=10).json()
    populated=requests.post(base+f"/api/maps/{story['id']}/populate",json={'template_id':'save_the_cat'},timeout=10).json()
    detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();line=next(x for x in detail['lines'] if x['id']==populated['line_id'])
    span=next(item for item in line['items'] if item['item_type']=='span')
    requests.patch(base+f"/api/map-lines/{line['id']}",json={'layout_locked':False},timeout=10).raise_for_status()
    # A Station overlapping a Span is valid and must paint above it.
    station=requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Over Span','planned_start':(span['planned_start']+span['planned_end'])/2,'schematic_start':(span['schematic_start']+span['schematic_end'])/2},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_function("()=>document.querySelector('#mapsView')?.classList.contains('active')")
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_function('(id)=>state.currentMap?.id===id',arg=story['id'])
        page.locator(f'#mapSvg [data-map-item="{span["id"]}"] .map-span-shape').evaluate("el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true}))");page.locator(f'[data-testid="map-span-handle"][data-map-item-id="{span["id"]}"][data-span-edge="end"]').wait_for(state='visible')
        end=page.locator(f'[data-testid="map-span-handle"][data-map-item-id="{span["id"]}"][data-span-edge="end"]')
        assert end.count()==1
        before=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();before_span=_map_item(before,span['id']);before_end=before_span['planned_end']
        before_line=next(row for row in before['lines'] if row['id']==line['id'])
        target_p=min(float(before_line['planned_end'])-.04,float(before_end)+.10);assert target_p>float(before_end)+.01
        target=_map_line_client_point_for_position(page,line['id'],target_p)
        eb=end.bounding_box();assert eb
        ex=eb['x']+eb['width']/2;ey=eb['y']+eb['height']/2
        assert 0<=target['x']<target['viewportWidth'] and 0<=target['y']<target['viewportHeight'],target
        assert target['x']>ex+8,(target,eb)
        page.mouse.move(ex,ey);page.mouse.down();page.mouse.move(target['x'],target['y'],steps=10);page.mouse.up()
        page.wait_for_function("""async ([mapId,spanId,beforeEnd])=>{const detail=await api(`/api/maps/${mapId}`);const span=detail.lines.flatMap(line=>line.items||[]).find(item=>+item.id===+spanId);return !!span&&span.planned_end>beforeEnd+.01}""",arg=[story['id'],span['id'],before_end],timeout=5000)
        after=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json();after_span=_map_item(after,span['id'])
        assert after_span['planned_end'] > before_end+.01

        station_paints_after_span=page.evaluate("([spanId,stationId])=>{const span=document.querySelector(`[data-map-item=\"${spanId}\"]`),station=document.querySelector(`[data-map-item=\"${stationId}\"]`);return !!(span&&station&&(span.compareDocumentPosition(station)&Node.DOCUMENT_POSITION_FOLLOWING))}",[str(span['id']),str(station['id'])])
        assert station_paints_after_span
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v639_connection_selection_keeps_side_panel_closed(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.3.9 no connection inspector')
    story=requests.post(base+'/api/maps',json={'name':'Connections','layout_mode':'scaled'},timeout=10).json()
    a=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'A'},timeout=10).json();b=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'B'},timeout=10).json()
    junction=requests.post(base+f"/api/maps/{story['id']}/junctions",json={'kind':'merge','from_line_id':a['id'],'from_edge':'point','from_planned_position':.4,'from_schematic_position':.4,'to_line_id':b['id'],'to_edge':'point','to_planned_position':.6,'to_schematic_position':.6},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250);page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        page.locator(f'#mapSvg [data-map-junction="{junction["id"]}"]').click();page.wait_for_timeout(140)
        assert not page.locator('#mapInspector').is_visible()
        assert page.locator(f'#mapSvg [data-map-junction-handle="{junction["id"]}"]').count()==2
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
