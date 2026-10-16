from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _set_text(base,cid,text):
    block=requests.get(base+f'/api/chapters/{cid}',timeout=10).json()['blocks'][0]
    requests.put(base+f'/api/chapters/{cid}/blocks',json={'blocks':[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None}]},timeout=10).raise_for_status()
    return block


def test_v533_developmental_creation_scope_lane_and_compact_apply_modal(scene_live_server,scene_browser):
    base=scene_live_server
    tab,ch1=_new_project(base,'5.3.3 Developmental Scope')
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="canvas"]').click(); page.locator('#canvasView').wait_for(state='visible')
        page.locator('#emptyCreateCanvas').click();page.locator('[data-canvas-type="developmental"]').click()
        assert page.locator('#developmentalScopeField').is_visible()
        scenes=page.locator('[data-developmental-scope="scenes"]')
        assert scenes.get_attribute('aria-disabled')=='true'
        tooltip=page.locator('.canvas-scope-tooltip').get_attribute('title'); assert 'Scene' in tooltip and 'manuscript' in tooltip.lower()
        page.locator('#cancelNewCanvas').click()

        # Give the manuscript two written Scenes, then refresh the UI state.
        page.evaluate(f"""async()=>{{
          const ch2=await api('/api/chapters',{{method:'POST',body:JSON.stringify({{title:'Chapter 2',tab_id:{tab['id']}}})}});
          const c1=await api('/api/chapters/{ch1['id']}'),c2=await api(`/api/chapters/${{ch2.id}}`);
          const b1=c1.blocks[0],b2=c2.blocks[0];
          await api('/api/chapters/{ch1['id']}/blocks',{{method:'PUT',body:JSON.stringify({{blocks:[{{id:b1.id,block_type:'paragraph',semantic_style:'body',content_html:'First scene',text_content:'First scene',image_path:'',scene_id:null}}]}})}});
          await api(`/api/chapters/${{ch2.id}}/blocks`,{{method:'PUT',body:JSON.stringify({{blocks:[{{id:b2.id,block_type:'paragraph',semantic_style:'body',content_html:'Second scene',text_content:'Second scene',image_path:'',scene_id:null}}]}})}});
          await api('/api/scenes',{{method:'POST',body:JSON.stringify({{title:'First',block_ids:[b1.id]}})}});
          await api('/api/scenes',{{method:'POST',body:JSON.stringify({{title:'Second',block_ids:[b2.id]}})}});
          await refresh(false);
        }}""")
        page.wait_for_timeout(100)

        page.locator('#addCanvas').click();page.locator('[data-canvas-type="developmental"]').click()
        scenes=page.locator('[data-developmental-scope="scenes"]')
        assert scenes.get_attribute('aria-disabled')=='false'
        scenes.click();page.locator('#newCanvasName').fill('Scene Order');page.locator('#createCanvasConfirm').click()
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Scene Order'")
        current=requests.get(base+'/api/bootstrap',timeout=10).json()['canvases']
        canvas=next(c for c in current if c['name']=='Scene Order')
        assert canvas['developmental_scope']=='scenes'
        detail=requests.get(base+f"/api/canvases/{canvas['id']}",timeout=10).json()
        nodes=sorted(detail['nodes'],key=lambda n:n['lane_position'])
        assert len(nodes)==2 and [n['lane_position'] for n in nodes]==[0,1]
        # 5.4.1 renders the order as compact header-only tiles in a fixed dock.
        # Sequence arrows remain absent; connector data is only drawable on desk objects.
        assert detail['edges']==[]
        page.wait_for_selector('.developmental-structural-tile')
        assert page.locator('.developmental-structural-tile').count()==2
        assert page.locator('.developmental-structural-tile .node-port').count()==0

        # Many Chapters make the comparison long. The modal shell must stay fixed;
        # the two compact order lists are the only scroll containers.
        page.evaluate(f"""async()=>{{for(let i=3;i<22;i++)await api('/api/chapters',{{method:'POST',body:JSON.stringify({{title:`Chapter ${{i}}`,tab_id:{tab['id']}}})}});await api('/api/canvases',{{method:'POST',body:JSON.stringify({{name:'Chapter Order',canvas_type:'developmental',developmental_scope:'chapters'}})}});await refresh(false)}}""")
        page.wait_for_timeout(100)
        page.locator('.canvas-item').filter(has_text='Chapter Order').click();page.wait_for_function("document.querySelector('#canvasName')?.value==='Chapter Order'")
        page.locator('#canvasStructureBtn').click();page.locator('#reviewStructureChanges').click();page.locator('#modal').wait_for(state='visible')
        overflow=page.locator('.modal-card').evaluate("el=>getComputedStyle(el).overflowY")
        assert overflow=='hidden'
        actions=page.locator('#modalActions').bounding_box(); assert actions and actions['y']+actions['height']<=page.viewport_size['height']+1
        lists=page.locator('.developmental-order-list')
        assert lists.count()==2
        assert any(lists.nth(i).evaluate('el=>el.scrollHeight>el.clientHeight') for i in range(2))
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
