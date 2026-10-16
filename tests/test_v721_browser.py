from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v721_free_angle_line_handles_and_prompt_free_figure_delete(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '7.2.1 line figures')
    page, errors = _page(base, scene_browser)
    try:
        setup = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'7.2.1 Canvas',canvas_type:'blank'})});
          const fig=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'figure',shape:'triangle',x:650,y:160,w:180,h:150})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');
          return {canvas:cv.id,fig:fig.id};
        }""")
        page.wait_for_selector('#canvasView.active')

        # Draw a Line diagonally through the normal Figure tool. It must retain Y delta,
        # rather than collapsing to the legacy horizontal segment.
        page.locator('#addFigureBtn').click()
        page.wait_for_selector('#canvasFigurePalette:not(.hidden)')
        page.locator('[data-canvas-figure-shape="line"]').click()
        stage = page.locator('#canvasStage')
        box = stage.bounding_box()
        assert box
        start = (box['x'] + 230, box['y'] + 230)
        end = (box['x'] + 470, box['y'] + 365)
        before = page.evaluate("()=>state.canvasNodes.length")
        page.mouse.move(*start)
        page.mouse.down()
        page.mouse.move(*end, steps=8)
        page.mouse.up()
        page.wait_for_function("count=>state.canvasNodes.length===count+1", arg=before)
        line = page.evaluate("()=>{const n=state.canvasNodes.findLast(n=>n.node_type==='figure'&&n.shape==='line');return {id:n.id,x1:n.figure_line_x1,y1:n.figure_line_y1,x2:n.figure_line_x2,y2:n.figure_line_y2,w:n.w,h:n.h}}")
        assert abs(float(line['y2']) - float(line['y1'])) > .25
        assert float(line['h']) > 100

        line_el = page.locator(f'.canvas-figure[data-id="{line["id"]}"]')
        line_el.click()
        assert line_el.locator('.figure-line-handle').count() == 2
        assert line_el.locator('[data-line-endpoint="start"]').count() == 1
        end_handle = line_el.locator('[data-line-endpoint="end"]')
        hb = end_handle.bounding_box()
        assert hb
        old_endpoint = page.evaluate("id=>{const n=state.canvasNodes.find(n=>n.id===id);const p=figureLineCanvasEndpoints(n).end;return {x:p.x,y:p.y}}", line['id'])
        page.mouse.move(hb['x'] + hb['width']/2, hb['y'] + hb['height']/2)
        page.mouse.down()
        page.mouse.move(hb['x'] + hb['width']/2 - 35, hb['y'] + hb['height']/2 - 80, steps=8)
        page.mouse.up()
        page.wait_for_function("args=>{const n=state.canvasNodes.find(n=>n.id===args.id);if(!n)return false;const p=figureLineCanvasEndpoints(n).end;return Math.hypot(p.x-args.old.x,p.y-args.old.y)>30}", arg={'id': line['id'], 'old': old_endpoint})

        # Figure deletion from its side panel is immediate and remains undoable.
        page.evaluate("id=>selectOnlyNode(id)", setup['fig'])
        page.wait_for_selector('#deleteNode')
        page.locator('#deleteNode').click()
        page.wait_for_function("id=>!state.canvasNodes.some(n=>n.id===id)", arg=setup['fig'])
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')")
        page.evaluate("()=>appUndo()")
        page.wait_for_function("id=>state.canvasNodes.some(n=>n.id===id)", arg=setup['fig'])
        assert not errors, errors
    finally:
        _shutdown_page(page)
        _release(base)


def test_v721_figure_style_conventions_language_row_and_sibling_menus(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '7.2.1 standards')
    page, errors = _page(base, scene_browser)
    try:
        fig = page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Style conventions',canvas_type:'blank'})});
          const fig=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'figure',shape:'oval',x:220,y:180,w:280,h:150})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer');selectOnlyNode(fig.id);
          return fig.id;
        }""")
        page.wait_for_selector('#figureFillChoice')

        # Default/Custom follows the same select + conditional color input convention
        # used elsewhere instead of always exposing a raw color picker.
        assert page.locator('#figureFillChoice').input_value() == 'default'
        assert page.locator('#figureStrokeChoice').input_value() == 'default'
        assert page.locator('#figureFillColor').is_hidden()
        assert page.locator('#figureStrokeColor').is_hidden()
        assert page.locator('label[for="figureBorderWidth"]').inner_text().strip() == 'Line width'

        page.select_option('#figureFillChoice', 'custom')
        page.wait_for_selector('#figureFillColor', state='visible')
        page.eval_on_selector('#figureFillColor', "el=>{el.value='#345678';el.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.figure_fill_color==='#345678'", arg=fig)
        page.wait_for_function("()=>document.querySelector('#figureFillChoice')?.value==='custom'")
        assert page.locator('#figureFillChoice').input_value() == 'custom'
        page.select_option('#figureFillChoice', 'default')
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.figure_fill_color===''", arg=fig)
        page.wait_for_selector('#figureFillChoice')
        assert page.locator('#figureFillChoice').input_value() == 'default'
        assert page.locator('#figureFillColor').is_hidden()

        page.select_option('#figureStrokeChoice', 'custom')
        page.wait_for_selector('#figureStrokeColor', state='visible')
        page.eval_on_selector('#figureStrokeColor', "el=>{el.value='#aa5500';el.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.figure_stroke_color==='#aa5500'", arg=fig)
        page.wait_for_selector('#figureStrokeChoice')
        page.select_option('#figureStrokeChoice', 'default')
        page.wait_for_function("id=>state.canvasNodes.find(n=>n.id===id)?.figure_stroke_color===''", arg=fig)

        # English uses the same two-line native/English presentation as every locale.
        page.evaluate("()=>openLanguageSettings()")
        english = page.locator('[data-language-code="en"]')
        english.wait_for(state='visible')
        assert english.locator('b').inner_text().strip() == 'English'
        assert english.locator('small').inner_text().strip() == 'English'
        page.evaluate("()=>closeModal()")
        page.wait_for_function("()=>document.querySelector('#modal')?.classList.contains('hidden')")

        # Gap crossing keeps the active submenu usable, but hovering a different sibling
        # closes the previous submenu immediately so two branches never look open at once.
        page.evaluate("""()=>openContextMenu(150,150,[
          {label:'First',children:[{label:'First child',action:()=>{}}]},
          {label:'Second',children:[{label:'Second child',action:()=>{}}]}
        ])""")
        rows = page.locator('#contextMenu > .context-menu-row')
        first = rows.nth(0)
        second = rows.nth(1)
        first.locator(':scope > button').hover()
        first_sub = first.locator(':scope > .context-submenu')
        first_sub.wait_for(state='visible')
        second.locator(':scope > button').hover()
        second_sub = second.locator(':scope > .context-submenu')
        second_sub.wait_for(state='visible')
        assert not first_sub.is_visible()
        assert second_sub.is_visible()
        assert not errors, errors
    finally:
        _shutdown_page(page)
        _release(base)
