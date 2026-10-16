from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v545_structural_contrast_versions_panel_and_apply_jump(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.7 Developmental UI')
    page, errors = _page(base, scene_browser)
    try:
        page.evaluate("""async d=>{
          for(let i=2;i<=34;i++) await api('/api/chapters',{method:'POST',body:JSON.stringify({title:`Chapter ${i}`,tab_id:d.tab})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental',developmental_scope:'chapters'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return cv.id;
        }""", {'tab': tab['id']})
        page.wait_for_function("()=>document.querySelectorAll('.developmental-structural-tile').length>=34")

        # The redundant Canvas desk label is gone, so the hand/pointer overlay owns
        # the top-left Canvas space without colliding with text.
        assert page.locator('.canvas-developmental-desk-label').count() == 0

        # Structural foreground color is derived automatically from the background.
        page.evaluate("""async()=>{
          const nodes=state.canvasNodes.filter(isDevelopmentalStructureCard).sort((a,b)=>a.lane_position-b.lane_position);
          for(const [node,color] of [[nodes[0],'#161616'],[nodes[1],'#f2e9b8']]){
            await api(`/api/nodes/${node.id}`,{method:'PATCH',body:JSON.stringify({color,text_color:'#ff00ff'})});
            node.color=color;node.text_color='#ff00ff';
          }
          renderNodes();
        }""")
        tiles = page.locator('.developmental-structural-tile')
        dark = tiles.nth(0).evaluate("el=>getComputedStyle(el).color")
        light = tiles.nth(1).evaluate("el=>getComputedStyle(el).color")
        assert dark == 'rgb(236, 236, 234)'
        assert light == 'rgb(32, 32, 32)'

        # Move a structural tile to the Canvas and confirm the same automatic
        # foreground rule is used there too.
        page.evaluate("""async()=>{
          const node=state.canvasNodes.filter(isDevelopmentalStructureCard).sort((a,b)=>a.lane_position-b.lane_position)[0];
          const remaining=developmentalLaneCards().filter(n=>n.id!==node.id);node.lane_position=null;node.x=260;node.y=180;assignDevelopmentalLanePositions(remaining);
          await persistDevelopmentalLayout('Test unplaced contrast',[node,...remaining]);renderNodes();
        }""")
        page.wait_for_selector('.canvas-node.developmental-unplaced')
        assert page.locator('.canvas-node.developmental-unplaced').first.evaluate("el=>getComputedStyle(el).color") == 'rgb(236, 236, 234)'
        # Put it back before previewing Apply.
        page.evaluate("""async()=>{
          const node=state.canvasNodes.find(n=>isDevelopmentalStructureCard(n)&&n.lane_position==null),order=developmentalLaneCards();order.unshift(node);assignDevelopmentalLanePositions(order);await persistDevelopmentalLayout('Test replace contrast',order);renderNodes();
        }""")

        # Versions follows the Checkpoints panel pattern: rows + trailing menu,
        # not previous/next lateral navigation.
        page.evaluate("""async()=>{
          await api(`/api/canvases/${state.currentCanvas.id}/versions`,{method:'POST',body:JSON.stringify({name:'Alternate',from_version_id:state.activeCanvasVersionId})});
        }""")
        page.locator('#canvasStructureBtn').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Structure'")
        assert page.locator('.canvas-version-list-row').count() >= 2
        assert page.locator('.canvas-version-list-row.active').count() == 1
        assert page.locator('.canvas-version-more').count() == page.locator('.canvas-version-list-row').count()
        assert page.locator('#canvasVersionPrev').count() == 0
        assert page.locator('#canvasVersionNext').count() == 0
        assert page.locator('#newCanvasVersion').inner_text().strip() == '+'
        assert page.locator('#canvasStructureBtn').get_attribute('aria-pressed') == 'true'
        page.locator('#canvasStructureBtn').click()
        assert page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')")

        # Put the first changed region well below the initial viewport, then opening
        # Apply automatically centers the first changed row in both columns.
        page.evaluate("""async()=>{
          const order=developmentalLaneCards();const moved=order.splice(22,1)[0];order.splice(27,0,moved);assignDevelopmentalLanePositions(order);await persistDevelopmentalLayout('Test apply jump',order);renderNodes();
        }""")
        page.locator('#canvasStructureBtn').click() if page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')") else None
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Structure'")
        page.locator('#reviewStructureChanges').click()
        page.wait_for_selector('.developmental-apply-preview')
        page.wait_for_function("()=>[...document.querySelectorAll('.developmental-order-list')].every(el=>el.scrollTop>0)")
        positions = page.locator('.developmental-order-list').evaluate_all("""lists=>lists.map(list=>{const row=list.querySelector('.changed'),lr=list.getBoundingClientRect(),rr=row.getBoundingClientRect();return{scroll:list.scrollTop,delta:Math.abs((rr.top+rr.height/2)-(lr.top+lr.height/2)),height:lr.height}})""")
        assert all(item['scroll'] > 0 for item in positions)
        assert all(item['delta'] < item['height'] * .35 for item in positions)
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
