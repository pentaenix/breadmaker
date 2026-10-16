from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v546_structure_panel_and_versioned_workspace(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.7 Structure workspace')
    page, errors = _page(base, scene_browser)
    try:
        ids = page.evaluate("""async d=>{
          await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:d.tab})});
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental',developmental_scope:'chapters'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
          const v1=state.canvasVersions[0],chapter=state.canvasNodes.find(isDevelopmentalStructureCard);
          const idea=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:280,y:360,w:260,h:140,title:'Shared idea',body:'Live body',color:'#e8e8e8'})});idea.developmental_hidden=0;state.canvasNodes.push(idea);
          await api(`/api/nodes/${chapter.id}`,{method:'PATCH',body:JSON.stringify({color:'#1f2937'})});chapter.color='#1f2937';renderNodes();
          await api(`/api/canvases/${cv.id}/versions/${v1.id}/update`,{method:'POST'});await loadActiveCanvasVersion(v1.id);
          await api(`/api/nodes/${idea.id}`,{method:'PATCH',body:JSON.stringify({x:720,y:590,w:330,h:180,shape:'text',color:'#f2e9b8',title:'Version 2 idea',body:'Version 2 body'})});Object.assign(idea,{x:720,y:590,w:330,h:180,shape:'text',color:'#f2e9b8',title:'Version 2 idea',body:'Version 2 body'});
          await api(`/api/nodes/${chapter.id}`,{method:'PATCH',body:JSON.stringify({color:'#d7eef9'})});chapter.color='#d7eef9';
          const extra=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:1040,y:690,title:'Version 2 only'})});extra.developmental_hidden=0;state.canvasNodes.push(extra);renderNodes();
          const v2=await api(`/api/canvases/${cv.id}/versions`,{method:'POST',body:JSON.stringify({name:'Version 2',from_version_id:v1.id})});
          await api(`/api/canvases/${cv.id}/versions/${v2.id}/activate`,{method:'POST'});let data=await api(`/api/canvases/${cv.id}`);state.canvasNodes=data.nodes;state.canvasEdges=data.edges;await loadActiveCanvasVersion(v2.id);renderNodes();drawEdges();
          return{cv:cv.id,v1:v1.id,v2:v2.id,idea:idea.id,extra:extra.id,chapter:chapter.id};
        }""", {'tab': tab['id']})

        page.wait_for_function("()=>!document.querySelector('#canvasStructureBtn').classList.contains('hidden')")
        assert page.locator('#developmentalStructureToggle').count() == 0
        assert page.locator('#canvasVersionsBtn').count() == 0
        assert page.locator('#applyStructure').count() == 0

        page.locator('#canvasStructureBtn').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Structure'")
        assert page.locator('#structureOrderToggle').get_attribute('aria-checked') == 'true'
        assert page.locator('.canvas-version-list-row').count() == 2
        assert page.locator('#reviewStructureChanges').inner_text().strip() == 'Review changes'
        assert page.locator('#canvasStructureBtn').get_attribute('aria-pressed') == 'true'

        # Version 1 restores its own appearance/position and hides Version-2-only material.
        page.locator(f'[data-canvas-version-use="{ids["v1"]}"]').click()
        page.wait_for_function("id=>state.activeCanvasVersionId===id", arg=ids['v1'])
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.developmental_hidden===1", arg=ids['extra'])
        state1 = page.evaluate("""ids=>{const idea=state.canvasNodes.find(n=>n.id===ids.idea),extra=state.canvasNodes.find(n=>n.id===ids.extra),chapter=state.canvasNodes.find(n=>n.id===ids.chapter);return{idea:{x:idea.x,y:idea.y,w:idea.w,h:idea.h,shape:idea.shape,color:idea.color,title:idea.title,body:idea.body,hidden:idea.developmental_hidden},extraHidden:extra.developmental_hidden,chapterColor:chapter.color}}""", ids)
        assert state1['idea']['x'] == 280
        assert state1['idea']['y'] == 360
        assert state1['idea']['w'] == 260
        assert state1['idea']['shape'] == 'rect'
        assert state1['idea']['color'] == '#e8e8e8'
        assert state1['idea']['title'] == 'Shared idea'
        assert state1['idea']['body'] == 'Live body'
        assert state1['extraHidden'] == 1
        assert state1['chapterColor'] == '#1f2937'
        assert page.locator(f'.canvas-node[data-id="{ids["extra"]}"]').count() == 0

        # Version 2 restores its own complete lower workspace, including Card prose.
        page.locator(f'[data-canvas-version-use="{ids["v2"]}"]').click()
        page.wait_for_function("id=>state.activeCanvasVersionId===id", arg=ids['v2'])
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.developmental_hidden===0", arg=ids['extra'])
        state2 = page.evaluate("""ids=>{const idea=state.canvasNodes.find(n=>n.id===ids.idea),extra=state.canvasNodes.find(n=>n.id===ids.extra),chapter=state.canvasNodes.find(n=>n.id===ids.chapter);return{idea:{x:idea.x,y:idea.y,w:idea.w,h:idea.h,shape:idea.shape,color:idea.color,title:idea.title},extraHidden:extra.developmental_hidden,chapterColor:chapter.color}}""", ids)
        assert state2['idea']['x'] == 720
        assert state2['idea']['y'] == 590
        assert state2['idea']['w'] == 330
        assert state2['idea']['shape'] == 'text'
        assert state2['idea']['color'] == '#f2e9b8'
        assert state2['idea']['title'] == 'Version 2 idea'
        assert state2['extraHidden'] == 0
        assert state2['chapterColor'] == '#d7eef9'
        assert page.locator(f'.canvas-node[data-id="{ids["extra"]}"]').count() == 1

        # Structure Order visibility now belongs to the Structure panel itself.
        page.locator('#structureOrderToggle').click()
        page.wait_for_function("()=>document.querySelector('#developmentalStructureDock').classList.contains('hidden')")
        assert page.evaluate("()=>state.developmentalStructureOpen") is False
        page.locator('#structureOrderToggle').click()
        page.wait_for_function("()=>!document.querySelector('#developmentalStructureDock').classList.contains('hidden')")

        # Review changes is the only entry point; final Apply remains in the preview modal.
        page.locator('#reviewStructureChanges').click()
        page.wait_for_selector('.developmental-apply-preview')
        assert page.locator('#confirmDevelopmentalApply').count() == 1
        assert 'Apply to Manuscript' in page.locator('#modal').inner_text()
        page.locator('#cancelDevelopmentalApply').click()
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
