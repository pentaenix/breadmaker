from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v5410_task_is_family_and_new_task_is_inside_task_chooser(scene_live_server, scene_browser):
    base = scene_live_server
    tab, _ = _new_project(base, '5.4.15 Task linker hierarchy')
    page, errors = _page(base, scene_browser)
    try:
        setup = page.evaluate("""async tab=>{
          await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Linked Chapter',tab_id:tab})});
          const canvas=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Task Linker',canvas_type:'developmental',developmental_scope:'chapters'})});
          await refresh(false);setView('canvas');await selectCanvas(canvas.id);
          return {canvas:canvas.id};
        }""", tab['id'])

        page.wait_for_selector('.developmental-structural-tile')
        page.locator('.developmental-structural-tile').filter(has_text='Linked Chapter').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelTitle')?.textContent==='Chapter Structure'")

        # First level exposes Task exactly like the other relationship families.
        page.locator('#nodeAddLink').click()
        page.wait_for_selector('#entityLinkType')
        options = page.locator('#entityLinkType option').evaluate_all("els=>els.map(e=>({value:e.value,text:e.textContent.trim()}))")
        assert any(row['value'] == 'task' and row['text'] == 'Task' for row in options)
        assert not any(row['text'] == 'New Task' or row['value'] == 'new_task' for row in options)

        # Task chooser owns both the existing-task list and New Task action.
        page.locator('#entityLinkType').select_option('task')
        page.wait_for_selector('#createLinkedTask')
        assert page.locator('#entityLinkChooser').inner_text().startswith('Task')
        assert 'No open Tasks yet.' in page.locator('#entityLinkChooser').inner_text()
        assert page.locator('#simpleLinkTarget').count() == 0
        assert page.locator('#createLinkedTask').inner_text() == 'New Task'

        page.locator('#createLinkedTask').click()
        page.wait_for_function("()=>document.querySelector('#modalTitle')?.textContent==='New Task'")
        page.locator('#newLinkedTaskTitle').fill('Created inside Task chooser')
        page.locator('#saveLinkedTask').click()
        page.wait_for_function("()=>document.querySelector('#canvasPanelBody')?.textContent.includes('Created inside Task chooser')")

        # Reopening Task now shows the created Task in the Task list, with New Task still below it.
        page.locator('#nodeAddLink').click()
        page.locator('#entityLinkType').select_option('task')
        page.wait_for_function("()=>document.querySelector('#simpleLinkTarget')?.options.length===1")
        assert page.locator('#simpleLinkTarget option').all_inner_texts() == ['Created inside Task chooser']
        assert page.locator('#createLinkedTask').inner_text() == 'New Task'

        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
