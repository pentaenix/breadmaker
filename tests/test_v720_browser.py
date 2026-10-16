from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v720_canvas_alignment_connector_delete_and_figures(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'7.2 Canvas feature pass')
    page,errors=_page(base,scene_browser)
    try:
        setup=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'7.2 Canvas',canvas_type:'blank'})});
          const a=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:180,y:160,w:250,h:150,title:'Aligned card',body:'Body copy',icon:'★'})});
          const b=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:560,y:170,w:220,h:130,title:'Target',body:''})});
          const edge=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:b.id,from_port:'right',to_port:'left'})});
          const fig=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'figure',shape:'triangle',x:120,y:110,w:380,h:280,figure_layer:'back'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');
          return {canvas:cv.id,a:a.id,b:b.id,edge:edge.id,fig:fig.id};
        }""")
        page.wait_for_selector('#canvasView.active')

        # Card text alignment is three direct buttons, and it does not move the icon.
        page.evaluate("id=>selectOnlyNode(id)",setup['a'])
        page.wait_for_selector('[data-card-text-align="left"]')
        assert page.locator('[data-card-text-align]').count()==3
        assert page.locator('.card-text-align-field select').count()==0
        before=page.evaluate("""id=>{const el=document.querySelector(`.canvas-node[data-id='${id}']`),icon=el.querySelector('.node-icon');return {iconX:icon.getBoundingClientRect().x}}""",setup['a'])
        page.locator('[data-card-text-align="center"]').click()
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.text_align==='center'",arg=setup['a'])
        aligned=page.evaluate("""id=>{const el=document.querySelector(`.canvas-node[data-id='${id}']`),icon=el.querySelector('.node-icon');return {title:getComputedStyle(el.querySelector('.node-title')).textAlign,body:getComputedStyle(el.querySelector('.node-body')).textAlign,iconX:icon.getBoundingClientRect().x}}""",setup['a'])
        assert aligned['title']=='center' and aligned['body']=='center'
        assert abs(aligned['iconX']-before['iconX'])<1

        # Delete/Backspace removes a connector immediately, and structural Undo restores it.
        page.evaluate("id=>selectEdge(id,true)",setup['edge'])
        page.wait_for_selector('#deleteEdge')
        page.keyboard.press('Delete')
        page.wait_for_function("id=>!state.canvasEdges.some(e=>e.id===id)",arg=setup['edge'])
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')")
        page.evaluate("()=>appUndo()")
        page.wait_for_function("id=>state.canvasEdges.some(e=>e.id===id)",arg=setup['edge'])
        # The inspector Delete button follows the same prompt-free path.
        restored_edge=page.evaluate("()=>state.canvasEdges[0].id")
        page.evaluate("id=>selectEdge(id,true)",restored_edge)
        page.locator('#deleteEdge').click()
        page.wait_for_function("id=>!state.canvasEdges.some(e=>e.id===id)",arg=restored_edge)
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')")
        page.evaluate("()=>appUndo()")
        page.wait_for_function("()=>state.canvasEdges.length===1")

        # Current Figure maker exposes Rectangle/Triangle/Oval/Line; Circle is now Shift+Oval.
        page.locator('#addFigureBtn').click()
        palette=page.locator('#canvasFigurePalette')
        palette.wait_for(state='visible')
        assert palette.locator('[data-canvas-figure-shape]').count()==4
        assert palette.locator('[data-canvas-figure-shape="circle"]').count()==0
        for shape in ('rect','triangle','oval','line'):
            assert palette.locator(f'[data-canvas-figure-shape="{shape}"]').count()==1
        page.keyboard.press('Escape')

        # Default Figure edge follows theme text color instead of remaining black in Dark mode.
        page.evaluate("id=>{document.documentElement.dataset.theme='dark';selectOnlyNode(id)}",setup['fig'])
        page.wait_for_selector('#figureBorderWidth')
        default_style=page.evaluate("""id=>{const el=document.querySelector(`.canvas-figure[data-id='${id}']`),shape=el.querySelector('.figure-shape'),root=getComputedStyle(document.documentElement);return {stroke:getComputedStyle(shape).stroke,text:root.getPropertyValue('--text').trim(),parent:el.parentElement.id,fill:getComputedStyle(shape).fill}}""",setup['fig'])
        assert default_style['parent']=='figures'
        assert default_style['fill']=='rgba(0, 0, 0, 0)'
        assert default_style['stroke'] not in ('rgb(0, 0, 0)','#000000')

        # Right-click layer controls move the same Figure from behind Cards to over Cards.
        fig=page.locator(f'.canvas-figure[data-id="{setup["fig"]}"]')
        fig.click(button='right',position={'x':10,'y':10})
        page.wait_for_selector('#contextMenu:not(.hidden)')
        move_forward=page.locator('#contextMenu button',has_text='Move forward')
        assert move_forward.is_enabled()
        move_forward.click()
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.figure_layer==='front'",arg=setup['fig'])
        assert page.locator(f'#nodes .canvas-figure[data-id="{setup["fig"]}"]').count()==1

        # Appearance controls persist custom background, edge and weight.
        page.evaluate("id=>selectOnlyNode(id)",setup['fig'])
        page.wait_for_selector('#figureFillChoice')
        page.select_option('#figureFillChoice','custom')
        page.wait_for_selector('#figureFillColor',state='visible')
        page.eval_on_selector('#figureFillColor',"el=>{el.value='#336699';el.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.figure_fill_color==='#336699'",arg=setup['fig'])
        page.wait_for_function("()=>document.querySelector('#figureFillChoice')?.value==='custom'")
        page.select_option('#figureStrokeChoice','custom')
        page.wait_for_selector('#figureStrokeColor',state='visible')
        page.eval_on_selector('#figureStrokeColor',"el=>{el.value='#ff8800';el.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.figure_stroke_color==='#ff8800'",arg=setup['fig'])
        page.eval_on_selector('#figureBorderWidth',"el=>{el.value='4';el.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.wait_for_function("id=>Number(state.canvasNodes.find(n=>n.id===id)?.figure_border_width)===4",arg=setup['fig'])
        assert not errors,errors
    finally:
        _shutdown_page(page)
        _release(base)


def test_v720_task_and_milestone_search_and_nested_menu_grace(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'7.2 Task search and menu grace')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate("""async()=>{
          const first=await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Prepare release',description:'General work'})});
          await api(`/api/tasks/${first.id}/milestones`,{method:'POST',body:JSON.stringify({title:'Needle milestone',description:'Unique milestone phrase'})});
          await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Unrelated task',description:'Other work'})});
          setView('tasks');await loadTasks();
        }""")
        page.wait_for_selector('#tasksView.active')
        search=page.locator('#taskSearch')
        search.fill('Needle milestone')
        page.wait_for_function("()=>document.querySelectorAll('#taskList .task-row').length===1")
        assert 'Prepare release' in page.locator('#taskList .task-row').inner_text()
        search.fill('does not exist')
        page.wait_for_function("()=>document.querySelectorAll('#taskList .task-row').length===0")
        assert page.locator('#taskList').inner_text().splitlines()[0].strip()=='No tasks or milestones match your search.'
        assert 'Create “does not exist”' in page.locator('#taskList').inner_text()

        # A submenu remains open while the pointer crosses the small gap into it.
        page.evaluate("""()=>openContextMenu(120,120,[{label:'Outer',children:[{label:'Inner',children:[{label:'Leaf',action:()=>{}}]}]}])""")
        outer=page.locator('#contextMenu > .context-menu-row').first
        outer_btn=outer.locator(':scope > button')
        outer_btn.hover()
        submenu=outer.locator(':scope > .context-submenu')
        submenu.wait_for(state='visible')
        b1=outer_btn.bounding_box();b2=submenu.bounding_box();assert b1 and b2
        # Spend a moment in the gap after leaving the parent, but less than the grace period.
        page.mouse.move(b1['x']+b1['width']-1,b1['y']+b1['height']/2)
        page.mouse.move((b1['x']+b1['width']+b2['x'])/2,b1['y']+b1['height']/2)
        page.wait_for_timeout(100)
        assert submenu.is_visible()
        page.mouse.move(b2['x']+10,b2['y']+10)
        assert submenu.is_visible()
        assert not errors,errors
    finally:
        _shutdown_page(page)
        _release(base)
