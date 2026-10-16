from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _open_map_line_actions(page,story_id,line_id):
    page.locator('[data-view="maps"]').click()
    page.wait_for_function("()=>document.querySelector('#mapsView')?.classList.contains('active')")
    page.locator(f'.map-tree-map-row[data-map-id="{story_id}"]').click()
    page.wait_for_function('(id)=>+state.currentMap?.id===+id',arg=story_id)
    actions=page.locator(f'[data-testid="map-line-actions"][data-map-line-actions="{line_id}"]')
    actions.wait_for(state='visible')
    actions.click()


def test_v640_station_creation_modal_names_description_links_and_enter(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.4 station creation modal')
    story=requests.post(base+'/api/maps',json={'name':'Creation','layout_mode':'scaled','label_mode':'minimal'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Story','planned_start':.05,'planned_end':.95,'schematic_start':.05,'schematic_end':.95},timeout=10).json()
    requests.post(base+f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Station 49','planned_start':.2,'schematic_start':.2},timeout=10).raise_for_status()
    task=requests.post(base+'/api/tasks',json={'title':'Check pacing','status':'open'},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        _open_map_line_actions(page,story['id'],line['id'])
        page.get_by_test_id('map-add-station').click()
        page.locator('#modal:not(.hidden)').wait_for(state='visible')
        page.wait_for_function("()=>document.querySelector('#modalTitle')?.textContent==='New Station'")
        name=page.locator('#newMapItemName')
        name.wait_for(state='visible')
        assert name.input_value()=='Station 50'
        page.locator('#newMapItemDescription').fill('Created with its metadata up front.')
        page.locator('#newMapItemAddLink').click()
        page.locator('#pendingMapItemLinkType').wait_for(state='visible')
        page.locator('#pendingMapItemLinkType').select_option('task')
        page.locator('#simpleLinkTarget').wait_for(state='visible')
        page.locator('#simpleLinkTarget').select_option(str(task['id']))
        page.locator('#savePendingMapItemLink').click()
        page.wait_for_function("()=>document.querySelector('#newMapItemLinks')?.textContent.includes('Check pacing')")
        name.fill('Opening Beat')
        name.press('Enter')
        page.locator('#modal').wait_for(state='hidden')
        page.wait_for_function("()=>document.querySelector('#mapItemName')?.value==='Opening Beat'")
        assert page.locator('#mapInspector').is_visible()
        assert page.locator('#mapItemDescription').input_value()=='Created with its metadata up front.'
        assert 'Check pacing' in page.locator('#mapInspector').inner_text()
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        created=next(i for ln in detail['lines'] for i in ln.get('items',[]) if i['title']=='Opening Beat')
        refs=requests.get(base+'/api/references',params={'source_type':'map_item','source_id':str(created['id'])},timeout=10).json()
        assert any(r['target_type']=='task' and str(r['target_id'])==str(task['id']) for r in refs)
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v640_span_creation_modal_name_and_enter(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Maps 6.4 span creation modal')
    story=requests.post(base+'/api/maps',json={'name':'Span Creation','layout_mode':'scaled','label_mode':'minimal'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story['id']}/lines",json={'name':'Story','planned_start':.05,'planned_end':.95,'schematic_start':.05,'schematic_end':.95},timeout=10).json()

    page,errors=_page(base,scene_browser)
    try:
        _open_map_line_actions(page,story['id'],line['id'])
        page.get_by_test_id('map-add-span').click()
        page.locator('#modal:not(.hidden)').wait_for(state='visible')
        name=page.locator('#newMapItemName')
        name.wait_for(state='visible')
        assert name.input_value()=='Span 1'
        name.press('Enter')
        page.locator('#modal').wait_for(state='hidden')
        page.wait_for_function("()=>document.querySelector('#mapItemName')?.value==='Span 1'")
        detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        spans=[i for ln in detail['lines'] for i in ln.get('items',[]) if i['item_type']=='span']
        assert len(spans)==1 and spans[0]['title']=='Span 1'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)

def test_v640_canvas_marquee_autoscrolls_while_pointer_is_held_at_viewport_edge(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Canvas 6.4 marquee autoscroll')
    canvas=requests.post(base+'/api/canvases',json={'name':'Selection Canvas','canvas_type':'blank'},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        # This contract owns marquee/autoscroll behavior, not main-tab routing.
        # Activate the Canvas workspace deterministically, then exercise the real
        # pointer/marquee interactions below. Navigation and rail selection are
        # covered independently by focused browser contracts.
        activated=page.evaluate("()=>{setView('canvas',{historyMode:'none'});return {view:currentView(),active:document.querySelector('#canvasView')?.classList.contains('active')}}")
        assert activated=={'view':'canvas','active':True},activated
        # Marquee/autoscroll is the contract here. Canvas-rail rendering and click
        # selection have dedicated coverage, so enter the already-created canvas
        # through Breadmaker's actual selection boundary instead of racing its rail.
        page.wait_for_function('(id)=>state.canvases.some(canvas=>+canvas.id===+id)',arg=canvas['id'])
        page.evaluate('(id)=>selectCanvas(id)',canvas['id'])
        page.wait_for_function('(id)=>+state.currentCanvas?.id===+id',arg=canvas['id'])

        pointer=page.get_by_test_id('canvas-pointer-mode')
        pointer.wait_for(state='visible')
        pointer.click()
        page.wait_for_function("()=>state.canvasMode==='pointer'&&document.querySelector('[data-testid=\"canvas-stage\"]')?.classList.contains('pointer-mode')")

        stage=page.get_by_test_id('canvas-stage')
        stage.wait_for(state='visible')
        stage.evaluate('el=>{el.scrollLeft=0;el.scrollTop=0}')
        box=stage.bounding_box();assert box
        start_x=box['x']+80;start_y=box['y']+80;edge_x=box['x']+box['width']-3;edge_y=box['y']+box['height']-3
        page.mouse.move(start_x,start_y);page.mouse.down();page.mouse.move(edge_x,edge_y,steps=6)

        marquee=page.get_by_test_id('canvas-selection-rect')
        page.wait_for_function("()=>!document.querySelector('[data-testid=\"canvas-selection-rect\"]')?.classList.contains('hidden')")
        first_box=marquee.bounding_box();assert first_box
        width_before=first_box['width']
        scroll_before=stage.evaluate('el=>[el.scrollLeft,el.scrollTop]')
        page.wait_for_function("([left,top])=>{const el=document.querySelector('[data-testid=\"canvas-stage\"]');return el.scrollLeft>left||el.scrollTop>top}",arg=scroll_before,timeout=2500)
        scroll_after=stage.evaluate('el=>[el.scrollLeft,el.scrollTop]')
        second_box=marquee.bounding_box();assert second_box
        width_after=second_box['width']
        assert scroll_after[0]>scroll_before[0] or scroll_after[1]>scroll_before[1]
        assert width_after>width_before

        page.mouse.up()
        page.wait_for_function("()=>document.querySelector('[data-testid=\"canvas-selection-rect\"]')?.classList.contains('hidden')")
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
