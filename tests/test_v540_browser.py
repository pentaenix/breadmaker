from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _open_structure(page):
    if page.locator('#canvasPanelTitle').inner_text() != 'Structure' or page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')"):
        page.locator('#canvasStructureBtn').click()
    page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Structure'&&!document.querySelector('#canvasToolPanel').classList.contains('hidden')")


def test_v540_canvas_modes_default_names_and_developmental_versions(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.4 Canvas Modes'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="canvas"]').click();page.locator('#canvasView').wait_for(state='visible')
        page.locator('#emptyCreateCanvas').click();assert page.locator('#newCanvasName').input_value()=='Canvas 1';page.locator('#createCanvasConfirm').click()
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Canvas 1'")
        # Normal Canvas is freeform; Cards are created explicitly through Add Card.
        stage=page.locator('#canvasStage');box=stage.bounding_box();assert box
        page.locator('#addNodePanelBtn').click();page.locator('#canvasCardPalette').wait_for(state='visible')
        page.locator('#canvasCardPalette [data-canvas-card-shape="rect"]').click()
        x1,y1=box['x']+360,box['y']+240;x2,y2=x1+220,y1+125
        page.mouse.move(x1,y1);page.mouse.down();page.mouse.move(x2,y2,steps=6);page.mouse.up();page.wait_for_selector('.canvas-node')
        assert page.locator('.canvas-developmental-lane').count()==0 and page.locator('.canvas-timeline-axis').count()==0
        # Timeline is retired; new Canvas creation offers only Blank and Developmental.
        page.locator('#addCanvas').click();assert page.locator('#newCanvasName').input_value()=='Canvas 2';assert page.locator('[data-canvas-type]').count()==2;assert page.locator('[data-canvas-type="timeline"]').count()==0;page.locator('#cancelNewCanvas').click()
        # Create manuscript structure through the app's own client/lease.
        page.evaluate(f"""async()=>{{await api('/api/chapters',{{method:'POST',body:JSON.stringify({{title:'Chapter 2',tab_id:{tab['id']}}})}});await api('/api/chapters',{{method:'POST',body:JSON.stringify({{title:'Chapter 3',tab_id:{tab['id']}}})}});await refresh(false)}}""")
        page.locator('#addCanvas').click();assert page.locator('#newCanvasName').input_value()=='Canvas 2';page.locator('[data-canvas-type="developmental"]').click();page.locator('#createCanvasConfirm').click()
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Canvas 2'");page.wait_for_selector('#developmentalStructureDock')
        assert page.locator('#canvasStructureBtn').is_visible();assert page.locator('#canvasVersionsBtn').count()==0 and page.locator('#applyStructure').count()==0
        detail=requests.get(base+'/api/bootstrap',timeout=10).json();cv=next(c for c in detail['canvases'] if c['name']=='Canvas 2')
        canvas=requests.get(base+f"/api/canvases/{cv['id']}",timeout=10).json();assert len(canvas['nodes'])==3 and canvas['edges']==[]
        assert [n['lane_position'] for n in sorted(canvas['nodes'],key=lambda n:n['lane_position'])]==[0,1,2]
        versions=requests.get(base+f"/api/canvases/{cv['id']}/versions",timeout=10).json()['versions'];assert [v['name'] for v in versions]==['Version 1']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v540_lane_drag_undo_versions_and_external_structure(scene_live_server,scene_browser):
    base=scene_live_server;tab,ch1=_new_project(base,'5.4 Developmental Lane');page,errors=_page(base,scene_browser)
    try:
        setup=page.evaluate("""async d=>{const ch2=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:d.tab})});const ch3=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 3',tab_id:d.tab})});const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental',developmental_scope:'chapters'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);return{canvas:cv.id,ch2:ch2.id,ch3:ch3.id}}""",{'tab':tab['id']})
        page.wait_for_selector('#developmentalStructureDock');original=[ch1['id'],setup['ch2'],setup['ch3']]
        # Drag Chapter 2 down onto the free desk.
        moving=page.locator('.developmental-structural-tile').filter(has_text='Chapter 2').first;mb=moving.bounding_box();stage_box=page.locator('#canvasStage').bounding_box();assert mb and stage_box
        page.mouse.move(mb['x']+mb['width']/2,mb['y']+mb['height']/2);page.mouse.down();page.mouse.move(stage_box['x']+420,stage_box['y']+260,steps=8);page.mouse.up();page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_selector('.canvas-node.developmental-unplaced');_open_structure(page);assert page.locator('#reviewStructureChanges').is_disabled();page.locator('#canvasStructureBtn').click()
        order=requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'];assert [c['id'] for c in order if c['tab_id']==tab['id'] and not c.get('is_implicit')]==original
        # Canvas Undo/Redo owns lane experimentation.
        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)");page.wait_for_function("()=>document.querySelector('.canvas-node.developmental-unplaced')===null")
        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)");page.wait_for_function("()=>document.querySelector('.canvas-node.developmental-unplaced')!==null")
        # Save the working arrangement as a named version.
        _open_structure(page);page.locator('#newCanvasVersion').click();page.locator('#dialogText').fill('Set Chapter 2 Aside');page.locator('#dialogConfirm').click();page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Set Chapter 2 Aside'")
        assert page.evaluate("()=>state.activeCanvasVersion?.version?.name")=='Set Chapter 2 Aside'
        # New manuscript structure appears Unplaced in this older outline.
        new_id=page.evaluate("""async d=>{const ch=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 4',tab_id:d.tab})});await refresh(false);await selectCanvas(d.canvas);return ch.id}""",{'tab':tab['id'],'canvas':setup['canvas']})
        page.wait_for_function("()=>[...document.querySelectorAll('.canvas-node.developmental-unplaced')].some(el=>el.textContent.includes('Chapter 4'))")
        # Delete Chapter 3 in Manuscript through the same in-app lease, then reopen Canvas.
        page.evaluate("""async d=>{await api(`/api/chapters/${d.chapter}`,{method:'DELETE'});await refresh(false);await selectCanvas(d.canvas)}""",{'chapter':setup['ch3'],'canvas':setup['canvas']})
        page.wait_for_selector('.developmental-missing-tile');assert 'Chapter 3' in page.locator('.developmental-missing-tile').inner_text()
        page.locator('.developmental-missing-tile .developmental-missing-remove').click();page.wait_for_function("()=>document.querySelector('.developmental-missing-tile')===null");assert page.locator('.developmental-missing-tile').count()==0
        # None of that navigational/version work rearranged surviving manuscript Chapters.
        surviving=[c['id'] for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit')]
        assert surviving==[ch1['id'],setup['ch2'],new_id]
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v540_dirty_version_navigation_prompts_and_apply_saves_proposal(scene_live_server,scene_browser):
    base=scene_live_server;tab,ch1=_new_project(base,'5.4 Version navigation');page,errors=_page(base,scene_browser)
    try:
        setup=page.evaluate("""async d=>{const ch2=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:d.tab})});const ch3=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 3',tab_id:d.tab})});const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);return{canvas:cv.id,ch2:ch2.id,ch3:ch3.id}}""",{'tab':tab['id']})
        page.wait_for_selector('#developmentalStructureDock');original=[ch1['id'],setup['ch2'],setup['ch3']]
        def drag_before(title,target):
            moving=page.locator('.developmental-structural-tile').filter(has_text=title).first;t=page.locator('.developmental-structural-tile').filter(has_text=target).first
            moving.wait_for(state='visible');t.wait_for(state='visible');mb=moving.bounding_box();tb=t.bounding_box();assert mb and tb
            page.mouse.move(mb['x']+mb['width']/2,mb['y']+mb['height']/2);page.mouse.down();page.mouse.move(tb['x']+6,tb['y']+tb['height']/2,steps=7);page.mouse.up();page.evaluate("()=>state.structuralChain.then(()=>true)")
        drag_before('Chapter 3','Chapter 1');assert page.evaluate("()=>state.activeCanvasVersion?.working_dirty") is True
        _open_structure(page);page.locator('#newCanvasVersion').click();page.locator('#dialogText').fill('Alternate');page.locator('#dialogConfirm').click();page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Alternate'")
        drag_before('Chapter 2','Chapter 3');page.wait_for_selector('.canvas-version-list-row')
        version1=page.locator('.canvas-version-use').filter(has_text='Version 1').first
        version1.click();page.wait_for_selector('#modal:not(.hidden)');assert page.locator('#modalTitle').inner_text()=='This version has changes'
        page.locator('#cancelVersionSwitch').click();assert page.evaluate("()=>state.activeCanvasVersion?.version?.name")=='Alternate'
        version1.click();page.wait_for_selector('#modal:not(.hidden)');page.locator('#discardVersionSwitch').click();page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Version 1'&&state.activeCanvasVersion?.working_dirty===false");page.wait_for_function("()=>developmentalPlacedCards().map(n=>n.chapter_id).join(',')===state.chapters.filter(c=>!c.is_implicit).map(c=>c.id).join(',')")
        assert page.evaluate("()=>state.activeCanvasVersion?.version?.name")=='Version 1'
        manuscript=[c['id'] for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit')];assert manuscript==original
        # Dirty Version 1, then Apply. Breadmaker snapshots that proposal as a new version first.
        drag_before('Chapter 3','Chapter 1');expected=page.evaluate("()=>developmentalPlacedCards().map(n=>n.chapter_id)");before=requests.get(base+f"/api/canvases/{setup['canvas']}/versions",timeout=10).json()['versions']
        _open_structure(page);page.locator('#reviewStructureChanges').click();page.wait_for_selector('#modal:not(.hidden)');page.locator('#confirmDevelopmentalApply').click();page.wait_for_function("()=>document.querySelector('#toast')?.textContent?.includes('applied to manuscript')",timeout=7000)
        after=[c['id'] for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit')];assert after==expected
        versions=requests.get(base+f"/api/canvases/{setup['canvas']}/versions",timeout=10).json()['versions'];assert len(versions)==len(before)+1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v540_version_panel_update_rename_and_delete(scene_live_server,scene_browser):
    base=scene_live_server;tab,ch1=_new_project(base,'5.4 Version panel');page,errors=_page(base,scene_browser)
    try:
        setup=page.evaluate("""async d=>{const ch2=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:d.tab})});const ch3=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 3',tab_id:d.tab})});const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Development',canvas_type:'developmental'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);return{canvas:cv.id,ch2:ch2.id,ch3:ch3.id}}""",{'tab':tab['id']})
        page.wait_for_selector('#developmentalStructureDock')
        _open_structure(page);page.locator('#newCanvasVersion').click();page.locator('#dialogText').fill('Alternate');page.locator('#dialogConfirm').click();page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Alternate'")
        alt_id=page.evaluate("()=>state.activeCanvasVersionId")
        # Change Alternate, then choose Update when navigating back. The saved named
        # version must adopt the working geometry before the switch occurs.
        moving=page.locator('.developmental-structural-tile').filter(has_text='Chapter 3').first;target=page.locator('.developmental-structural-tile').filter(has_text='Chapter 1').first
        mb=moving.bounding_box();tb=target.bounding_box();assert mb and tb
        page.mouse.move(mb['x']+mb['width']/2,mb['y']+mb['height']/2);page.mouse.down();page.mouse.move(tb['x']+6,tb['y']+tb['height']/2,steps=7);page.mouse.up();page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.locator('.canvas-version-use').filter(has_text='Version 1').first.click();page.wait_for_selector('#updateVersionSwitch');page.locator('#updateVersionSwitch').click();page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Version 1'")
        assert page.evaluate("()=>state.activeCanvasVersion?.version?.name")=='Version 1'
        saved=requests.get(base+f"/api/canvases/{setup['canvas']}/versions/{alt_id}",timeout=10).json()
        placed=sorted([i for i in saved['items'] if i['placed']],key=lambda i:i['lane_position'])
        assert [i['entity_id'] for i in placed]==[setup['ch3'],ch1['id'],setup['ch2']]
        # Rename and delete use the same side panel; deleting a version never touches
        # the manuscript and returns to the remaining version.
        page.locator('.canvas-version-use').filter(has_text='Alternate').first.click();page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Alternate'")
        page.locator('.canvas-version-list-row.active .canvas-version-more').click();page.locator('#contextMenu button').filter(has_text='Rename…').click();page.locator('#dialogText').fill('Revised');page.locator('#dialogConfirm').click();page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Revised'")
        assert page.evaluate("()=>state.activeCanvasVersion?.version?.name")=='Revised'
        page.locator('.canvas-version-list-row.active .canvas-version-more').click();page.locator('#contextMenu button').filter(has_text='Delete').click();page.wait_for_selector('#dialogConfirm');page.locator('#dialogConfirm').click();page.wait_for_function("()=>state.activeCanvasVersion?.version?.name==='Version 1'")
        assert page.evaluate("()=>state.activeCanvasVersion?.version?.name")=='Version 1'
        versions=requests.get(base+f"/api/canvases/{setup['canvas']}/versions",timeout=10).json()['versions'];assert [v['name'] for v in versions]==['Version 1']
        manuscript=[c['id'] for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit')]
        assert manuscript==[ch1['id'],setup['ch2'],setup['ch3']]
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
