from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v532_canvas_creation_uses_modal_and_opens_new_canvas(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'5.3.2 Canvas Creation Modal')
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="canvas"]').click()
        page.locator('#canvasView').wait_for(state='visible')
        page.locator('#emptyCreateCanvas').click()
        page.locator('#modal').wait_for(state='visible')
        assert page.locator('#modalTitle').inner_text()=='New Canvas'
        assert not page.locator('#canvasToolPanel').is_visible()
        assert page.locator('[data-canvas-type]').count()==2
        assert page.locator('[data-canvas-type="timeline"]').count()==0
        assert page.locator('#newCanvasParent option').count()==1

        page.locator('#newCanvasName').fill('Story Shape')
        page.locator('#newCanvasDescription').fill('Developmental planning board')
        page.locator('[data-canvas-type="developmental"]').click()
        assert page.locator('#newCanvasType').input_value()=='developmental'
        page.locator('#createCanvasConfirm').click()
        page.wait_for_function("document.querySelector('#modal').classList.contains('hidden')")
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Story Shape'")
        page.locator('.canvas-item.active').filter(has_text='Story Shape').wait_for(state='visible')
        assert page.locator('.canvas-item.active').filter(has_text='Story Shape').count()==1

        canvases=requests.get(base+'/api/bootstrap',timeout=10).json()['canvases']
        parent=next(c for c in canvases if c['name']=='Story Shape')
        assert parent['canvas_type']=='developmental'
        assert parent['description']=='Developmental planning board'

        # Creating from a Canvas context menu uses the same modal and preserves nesting.
        page.locator('.canvas-item').filter(has_text='Story Shape').click(button='right')
        page.get_by_text('New canvas inside…',exact=True).click()
        page.locator('#modal').wait_for(state='visible')
        assert page.locator('#newCanvasParent').input_value()==str(parent['id'])
        page.locator('#newCanvasName').fill('Character Arc')
        page.locator('#createCanvasConfirm').click()
        page.wait_for_function("document.querySelector('#canvasName')?.value==='Character Arc'")
        canvases=requests.get(base+'/api/bootstrap',timeout=10).json()['canvases']
        child=next(c for c in canvases if c['name']=='Character Arc')
        assert child['parent_id']==parent['id']
        assert child['canvas_type']=='blank'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
