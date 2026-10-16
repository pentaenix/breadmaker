from __future__ import annotations

import base64
import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page
from test_v540_browser import _open_structure, _release


def test_v5419_developmental_version_switch_restores_lower_canvas_content_and_media(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.4.19 Full Developmental workspace versions')
    page, errors = _page(base, scene_browser)
    try:
        tiny = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n7sAAAAASUVORK5CYII=')
        a1 = requests.post(base + '/api/assets', headers={'X-Breadmaker-Client': 'v41812-browser'}, files={'file': ('one.png', tiny, 'image/png')}, timeout=5).json()['path']
        a2 = requests.post(base + '/api/assets', headers={'X-Breadmaker-Client': 'v41812-browser'}, files={'file': ('two.png', tiny + b'2', 'image/png')}, timeout=5).json()['path']
        ids = page.evaluate("""async assets=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');
          const v1=state.canvasVersions[0];
          const card=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'free',shape:'rect',x:260,y:430,w:260,h:150,title:'Version one',body:'First body'})});card.developmental_hidden=0;state.canvasNodes.push(card);
          const image=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'image',shape:'rect',x:650,y:430,w:280,h:200,image_path:assets.a1})});image.developmental_hidden=0;state.canvasNodes.push(image);renderNodes();
          await api(`/api/canvases/${cv.id}/versions/${v1.id}/update`,{method:'POST'});await loadActiveCanvasVersion(v1.id);
          await api(`/api/nodes/${card.id}`,{method:'PATCH',body:JSON.stringify({x:880,y:690,title:'Version two',body:'Second body'})});Object.assign(card,{x:880,y:690,title:'Version two',body:'Second body'});
          await api(`/api/nodes/${image.id}`,{method:'PATCH',body:JSON.stringify({x:1190,y:700,w:360,h:240,image_path:assets.a2})});Object.assign(image,{x:1190,y:700,w:360,h:240,image_path:assets.a2});
          const v2=await api(`/api/canvases/${cv.id}/versions`,{method:'POST',body:JSON.stringify({name:'Version 2',from_version_id:v1.id})});
          await api(`/api/canvases/${cv.id}/versions/${v2.id}/activate`,{method:'POST'});const data=await api(`/api/canvases/${cv.id}`);state.canvasNodes=data.nodes;state.canvasEdges=data.edges;await loadActiveCanvasVersion(v2.id);renderNodes();drawEdges();
          return{cv:cv.id,v1:v1.id,v2:v2.id,card:card.id,image:image.id}
        }""", {'a1': a1, 'a2': a2})

        _open_structure(page)
        page.locator(f'[data-canvas-version-use="{ids["v1"]}"]').click()
        page.wait_for_function("id=>state.activeCanvasVersionId===id", arg=ids['v1'])
        one = page.evaluate("""ids=>{const c=state.canvasNodes.find(n=>n.id===ids.card),i=state.canvasNodes.find(n=>n.id===ids.image);return{title:c.title,body:c.body,x:c.x,image:i.image_path,imageX:i.x,imageW:i.w}}""", ids)
        assert one == {'title': 'Version one', 'body': 'First body', 'x': 260, 'image': a1, 'imageX': 650, 'imageW': 280}

        _open_structure(page)
        page.locator(f'[data-canvas-version-use="{ids["v2"]}"]').click()
        page.wait_for_function("id=>state.activeCanvasVersionId===id", arg=ids['v2'])
        two = page.evaluate("""ids=>{const c=state.canvasNodes.find(n=>n.id===ids.card),i=state.canvasNodes.find(n=>n.id===ids.image);return{title:c.title,body:c.body,x:c.x,image:i.image_path,imageX:i.x,imageW:i.w}}""", ids)
        assert two == {'title': 'Version two', 'body': 'Second body', 'x': 880, 'image': a2, 'imageX': 1190, 'imageW': 360}

        # Editing lower-Canvas prose is now itself a version change, not shared prose.
        title = page.locator(f'.canvas-node[data-id="{ids["card"]}"] .node-title')
        title.fill('Version two edited')
        page.wait_for_timeout(550)
        page.wait_for_function("()=>state.activeCanvasVersion?.working_dirty===true")

        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)


def test_v5420_card_created_in_one_version_is_absent_from_other_version(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '5.4.20 Version-local new Card')
    page, errors = _page(base, scene_browser)
    try:
        ids = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');
          const v1=state.canvasVersions[0];
          const v2=await api(`/api/canvases/${cv.id}/versions`,{method:'POST',body:JSON.stringify({name:'Version 2',from_version_id:v1.id})});
          await api(`/api/canvases/${cv.id}/versions/${v2.id}/activate`,{method:'POST'});
          let data=await api(`/api/canvases/${cv.id}`);state.canvasNodes=data.nodes;state.canvasEdges=data.edges;await loadActiveCanvasVersion(v2.id);renderNodes();drawEdges();
          const card=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'free',shape:'rect',x:320,y:430,w:240,h:140,title:'Only Version 2'})});
          card.developmental_hidden=0;state.canvasNodes.push(card);renderNodes();
          await api(`/api/canvases/${cv.id}/versions/${v2.id}/update`,{method:'POST'});await loadActiveCanvasVersion(v2.id);renderNodes();
          return{cv:cv.id,v1:v1.id,v2:v2.id,card:card.id}
        }""")
        _open_structure(page)
        page.locator(f'[data-canvas-version-use="{ids["v1"]}"]').click()
        page.wait_for_function("id=>state.activeCanvasVersionId===id", arg=ids['v1'])
        page.wait_for_function("id=>Number(state.canvasNodes.find(n=>n.id===id)?.developmental_hidden||0)===1", arg=ids['card'])
        assert page.locator(f'.canvas-node[data-id="{ids["card"]}"]').count() == 0

        _open_structure(page)
        page.locator(f'[data-canvas-version-use="{ids["v2"]}"]').click()
        page.wait_for_function("id=>state.activeCanvasVersionId===id", arg=ids['v2'])
        page.wait_for_function("id=>Number(state.canvasNodes.find(n=>n.id===id)?.developmental_hidden||0)===0", arg=ids['card'])
        assert page.locator(f'.canvas-node[data-id="{ids["card"]}"]').count() == 1
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
