from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def _assert_delayed_only_on_control(page, label_selector: str, control_selector: str):
    label = page.locator(label_selector)
    control = page.locator(control_selector)
    label.hover()
    page.wait_for_timeout(380)
    assert page.locator('#structurePanelHoverCard:not(.hidden)').count() == 0

    control.hover()
    page.wait_for_timeout(180)
    assert page.locator('#structurePanelHoverCard:not(.hidden)').count() == 0
    page.wait_for_timeout(180)
    tip = page.locator('#structurePanelHoverCard:not(.hidden)')
    assert tip.count() == 1
    cb = control.bounding_box(); hb = tip.bounding_box()
    assert cb and hb and hb['x'] + hb['width'] <= cb['x'] - 4
    page.mouse.move(30, 30)
    page.wait_for_function("()=>document.querySelector('#structurePanelHoverCard')?.classList.contains('hidden')")


def test_v549_structure_help_is_control_only_and_add_link_owns_tasks(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.9 Structure inspector cleanup')
    page, errors = _page(base, scene_browser)
    try:
        setup = page.evaluate("""async tab=>{
          const chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Linked Chapter',tab_id:tab})});
          const task=await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Existing task',status:'open'})});
          const canvas=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Structure Cleanup',canvas_type:'developmental',developmental_scope:'chapters'})});
          const card=await api(`/api/canvases/${canvas.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',x:330,y:320,title:'Planning Card',body:'Think here'})});
          await refresh(false);setView('canvas');await selectCanvas(canvas.id);
          return {chapter:chapter.id,task:task.id,canvas:canvas.id,card:card.id};
        }""", tab['id'])

        # Structural inspector has no permanent explanatory prose and no parallel Tasks section.
        page.wait_for_selector('.developmental-structural-tile')
        page.locator('.developmental-structural-tile').filter(has_text='Linked Chapter').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Chapter Structure'")
        panel_text = page.locator('#canvasPanelBody').inner_text()
        assert 'This tile is linked to a real Chapter' not in panel_text
        assert 'Text switches automatically between light and dark for contrast' not in panel_text
        assert page.locator('#canvasPanelBody .section-label').filter(has_text='Tasks').count() == 0
        assert page.locator('#nodeTaskSelect').count() == 0

        # Existing Task is managed through Add link.
        page.locator('#nodeAddLink').click()
        page.wait_for_selector('#entityLinkType')
        values = page.locator('#entityLinkType option').evaluate_all("els=>els.map(e=>({value:e.value,text:e.textContent.trim()}))")
        assert any(row['value'] == 'task' and row['text'] == 'Task' for row in values)
        assert not any(row['value'] == 'new_task' or row['text'] == 'New Task' for row in values)
        page.locator('#entityLinkType').select_option('task')
        page.wait_for_selector('#simpleLinkTarget')
        page.locator('#simpleLinkTarget').select_option(str(setup['task']))
        page.locator('#saveEntityLink').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelBody')?.textContent.includes('Existing task')")

        # New Task is an action inside Task and is immediately linked.
        page.locator('#nodeAddLink').click()
        page.wait_for_selector('#entityLinkType')
        page.locator('#entityLinkType').select_option('task')
        page.wait_for_selector('#createLinkedTask')
        page.locator('#createLinkedTask').click()
        page.wait_for_selector('#newLinkedTaskTitle')
        page.locator('#newLinkedTaskTitle').fill('Created from Add link')
        page.locator('#saveLinkedTask').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelBody')?.textContent.includes('Created from Add link')")
        created = page.evaluate("""async nodeId=>{
          const tasks=await api('/api/tasks');
          const refs=await api(`/api/references?source_type=node&source_id=${nodeId}`);
          const task=tasks.find(t=>t.title==='Created from Add link');
          return {task:!!task,linked:!!task&&refs.some(r=>r.target_type==='task'&&+r.target_id===+task.id)};
        }""", page.evaluate("()=>+state.canvasNodes.find(n=>n.title==='Linked Chapter').id"))
        assert created == {'task': True, 'linked': True}

        # Ordinary Card inspector uses the same single relationship path.
        page.evaluate("id=>{state.selectedNodes=new Set([id]);renderNodeSelection();openNodeInspector(id)}", setup['card'])
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Card'")
        assert page.locator('#nodeAddLink').count() == 1
        assert page.locator('#nodeTaskSelect').count() == 0
        assert page.locator('#canvasPanelBody .section-label').filter(has_text='Tasks').count() == 0

        # Structure descriptions now trigger only from the actual control, never the row/label.
        page.locator('#canvasStructureBtn').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Structure'")
        _assert_delayed_only_on_control(page, '.structure-toggle-row .structure-section-title', '#structureOrderToggle')
        _assert_delayed_only_on_control(page, '.structure-panel-versions .structure-section-title', '#newCanvasVersion')
        _assert_delayed_only_on_control(page, '.structure-panel-manuscript .structure-section-title', '#reviewStructureChanges')

        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
