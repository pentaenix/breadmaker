from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v556_card_display_modes_are_visual_only_and_survive_reload(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.5.6 Card display modes')
    page, errors = _page(base, scene_browser)
    try:
        result = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Card modes',canvas_type:'blank'})});
          const node=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'free',shape:'rect',x:140,y:120,w:260,h:150,title:'Visible header',body:'Hidden body survives'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);selectOnlyNode(node.id);await new Promise(r=>setTimeout(r,50));
          return {canvasId:cv.id,nodeId:node.id,buttons:[...document.querySelectorAll('[data-card-display]')].map(b=>b.getAttribute('aria-label'))};
        }""")
        assert result['buttons'] == ['Full card', 'Header only', 'Body only']

        page.locator('[data-card-display="header"]').click()
        page.wait_for_function("()=>document.querySelector('.canvas-node.card-display-header') && !document.querySelector('.canvas-node .node-body')")
        header_state = page.evaluate("""async()=>{
          const data=await api(`/api/canvases/${state.currentCanvas.id}`),n=data.nodes.find(x=>state.selectedNodes.has(x.id));
          const el=document.querySelector(`.canvas-node[data-id="${n.id}"]`);
          return {mode:n.display_mode,title:n.title,body:n.body,hasTitle:!!el.querySelector('.node-title'),hasBody:!!el.querySelector('.node-body'),height:el.getBoundingClientRect().height};
        }""")
        assert header_state['mode'] == 'header'
        assert header_state['title'] == 'Visible header'
        assert header_state['body'] == 'Hidden body survives'
        assert header_state['hasTitle'] is True
        assert header_state['hasBody'] is False

        page.locator('[data-card-display="body"]').click()
        page.wait_for_function("()=>document.querySelector('.canvas-node.card-display-body') && !document.querySelector('.canvas-node .node-title')")
        body_state = page.evaluate("""async()=>{
          const data=await api(`/api/canvases/${state.currentCanvas.id}`),n=data.nodes.find(x=>state.selectedNodes.has(x.id));
          const el=document.querySelector(`.canvas-node[data-id="${n.id}"]`);
          return {mode:n.display_mode,title:n.title,body:n.body,hasTitle:!!el.querySelector('.node-title'),hasBody:!!el.querySelector('.node-body')};
        }""")
        assert body_state == {'mode': 'body', 'title': 'Visible header', 'body': 'Hidden body survives', 'hasTitle': False, 'hasBody': True}

        # Re-fetch the Canvas from the server to prove the mode is persisted rather
        # than living only in the current DOM.
        page.evaluate("""async()=>{const id=state.currentCanvas.id;await selectCanvas(id)}""")
        page.wait_for_function("()=>document.querySelector('.canvas-node.card-display-body')")
        reloaded = page.evaluate("""()=>{const n=state.canvasNodes.find(n=>n.node_type==='free');return {mode:n.display_mode,title:n.title,body:n.body}}""")
        assert reloaded == {'mode': 'body', 'title': 'Visible header', 'body': 'Hidden body survives'}

        page.evaluate("""()=>{const n=state.canvasNodes.find(n=>n.node_type==='free');selectOnlyNode(n.id)}""")
        page.locator('[data-card-display="full"]').click()
        page.wait_for_function("()=>document.querySelector('.canvas-node.card-display-full .node-title') && document.querySelector('.canvas-node.card-display-full .node-body')")
        full = page.evaluate("""()=>{const n=state.canvasNodes.find(n=>n.node_type==='free'),el=document.querySelector(`.canvas-node[data-id="${n.id}"]`);return {mode:n.display_mode,title:el.querySelector('.node-title')?.textContent,body:el.querySelector('.node-body')?.textContent}}""")
        assert full == {'mode': 'full', 'title': 'Visible header', 'body': 'Hidden body survives'}

        # The display switch must flush an in-progress text edit instead of hiding
        # the DOM first and accidentally restoring the previous stored body.
        page.locator('.canvas-node .node-body').fill('Edited immediately before hiding')
        page.locator('[data-card-display="header"]').click()
        page.wait_for_function("()=>document.querySelector('.canvas-node.card-display-header')")
        pending = page.evaluate("""async()=>{const data=await api(`/api/canvases/${state.currentCanvas.id}`),n=data.nodes.find(n=>n.node_type==='free');return {mode:n.display_mode,body:n.body}}""")
        assert pending == {'mode': 'header', 'body': 'Edited immediately before hiding'}

        page.locator('[data-card-display="full"]').click()
        page.wait_for_function("()=>document.querySelector('.canvas-node.card-display-full .node-body')?.textContent==='Edited immediately before hiding'")
        assert not errors, errors
    finally:
        _shutdown_page(page)
