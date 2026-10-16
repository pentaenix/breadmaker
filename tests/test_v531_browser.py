from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v531_apply_button_visibility_and_before_after_preview(scene_live_server,scene_browser):
    base=scene_live_server
    tab,ch1=_new_project(base,'5.3.1 Apply Preview')
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    first=requests.get(base+f"/api/chapters/{ch1['id']}",timeout=10).json()['blocks'][0]
    second=requests.get(base+f"/api/chapters/{ch2['id']}",timeout=10).json()['blocks'][0]
    requests.put(base+f"/api/chapters/{ch1['id']}/blocks",json={'blocks':[{'id':first['id'],'block_type':'paragraph','semantic_style':'body','content_html':'First scene','text_content':'First scene','image_path':'','scene_id':None}]},timeout=10).raise_for_status()
    requests.put(base+f"/api/chapters/{ch2['id']}/blocks",json={'blocks':[{'id':second['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Second scene','text_content':'Second scene','image_path':'','scene_id':None}]},timeout=10).raise_for_status()
    scene1=requests.post(base+'/api/scenes',json={'title':'First','block_ids':[first['id']]},timeout=10).json()
    scene2=requests.post(base+'/api/scenes',json={'title':'Second','block_ids':[second['id']]},timeout=10).json()
    blank=requests.post(base+'/api/canvases',json={'name':'Blank','canvas_type':'blank'},timeout=10).json()
    development=requests.post(base+'/api/canvases',json={'name':'Development','canvas_type':'developmental','developmental_scope':'scenes'},timeout=10).json()
    detail=requests.get(base+f"/api/canvases/{development['id']}",timeout=10).json()
    nodes=sorted(detail['nodes'],key=lambda n:n['lane_position'])
    # 5.4.1 stores proposal order explicitly in the fixed Structure Lane.
    requests.post(base+f"/api/canvases/{development['id']}/developmental-layout",json={'items':[{'node_id':nodes[1]['id'],'lane_position':0},{'node_id':nodes[0]['id'],'lane_position':1}]},timeout=10).raise_for_status()

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="canvas"]').click();page.locator('#canvasView').wait_for(state='visible')
        page.locator('.canvas-item').filter(has_text='Blank').click()
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Blank'")
        assert not page.locator('#canvasStructureBtn').is_visible()

        page.locator('.canvas-item').filter(has_text='Development').click()
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Development'")
        page.locator('#canvasStructureBtn').wait_for(state='visible')
        assert page.locator('#canvasStructureBtn').is_visible()
        assert page.locator('#canvasStructureBtn').get_attribute('aria-label')=='Structure'

        page.locator('#canvasStructureBtn').click();page.locator('#reviewStructureChanges').click();page.locator('#modal').wait_for(state='visible')
        assert page.locator('#modalTitle').inner_text()=='Apply to Manuscript'
        assert page.locator('.developmental-order-column').count()==2
        current=page.locator('.developmental-order-column').nth(0); proposed=page.locator('.developmental-order-column').nth(1)
        assert 'First' in current.inner_text() and 'Second' in current.inner_text()
        assert 'First' in proposed.inner_text() and 'Second' in proposed.inner_text()
        assert page.locator('#confirmDevelopmentalApply').is_enabled()
        page.locator('#cancelDevelopmentalApply').click()
        chapter1=requests.get(base+f"/api/chapters/{ch1['id']}",timeout=10).json()['blocks']
        assert any(b.get('scene_id')==scene1['id'] for b in chapter1)

        if page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')"):
            page.locator('#canvasStructureBtn').click()
        page.locator('#reviewStructureChanges').click();page.locator('#confirmDevelopmentalApply').click();page.locator('#modal').wait_for(state='hidden')
        chapter1=requests.get(base+f"/api/chapters/{ch1['id']}",timeout=10).json()['blocks']
        assert any(b.get('scene_id')==scene2['id'] for b in chapter1)
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)

