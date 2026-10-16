from __future__ import annotations

import math
import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def _draw_figure(page, shape, start, end, shift=False):
    page.locator('#addFigureBtn').click()
    page.wait_for_selector('#canvasFigurePalette:not(.hidden)')
    page.locator(f'[data-canvas-figure-shape="{shape}"]').click()
    before = page.evaluate("()=>state.canvasNodes.length")
    page.mouse.move(*start)
    if shift:
        page.keyboard.down('Shift')
    page.mouse.down()
    page.mouse.move(*end, steps=8)
    page.mouse.up()
    if shift:
        page.keyboard.up('Shift')
    page.wait_for_function("count=>state.canvasNodes.length===count+1", arg=before)
    return page.evaluate("()=>{const n=state.canvasNodes.at(-1);return {id:n.id,shape:n.shape,x:n.x,y:n.y,w:n.w,h:n.h,x1:n.figure_line_x1,y1:n.figure_line_y1,x2:n.figure_line_x2,y2:n.figure_line_y2}}")


def _line_angle(page, node_id):
    pts = page.evaluate("id=>{const n=state.canvasNodes.find(n=>n.id===id),p=figureLineCanvasEndpoints(n);return p}", node_id)
    dx = float(pts['end']['x']) - float(pts['start']['x'])
    dy = float(pts['end']['y']) - float(pts['start']['y'])
    angle = abs(math.degrees(math.atan2(dy, dx))) % 180
    if angle > 90:
        angle = 180 - angle
    return angle


def test_v722_shift_constrains_rectangle_and_oval_and_removes_circle_tool(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '7.2.2 shift shapes')
    page, errors = _page(base, scene_browser)
    try:
        page.evaluate("""async()=>{const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Shift Shapes',canvas_type:'blank'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer')}""")
        page.wait_for_selector('#canvasView.active')
        page.locator('#addFigureBtn').click()
        page.wait_for_selector('#canvasFigurePalette:not(.hidden)')
        assert page.locator('[data-canvas-figure-shape="circle"]').count() == 0
        for shape in ('rect', 'triangle', 'oval', 'line'):
            assert page.locator(f'[data-canvas-figure-shape="{shape}"]').count() == 1
        page.locator('#addFigureBtn').click()  # close palette

        stage = page.locator('#canvasStage')
        box = stage.bounding_box()
        assert box
        rect = _draw_figure(page, 'rect', (box['x'] + 180, box['y'] + 180), (box['x'] + 390, box['y'] + 285), shift=True)
        assert abs(float(rect['w']) - float(rect['h'])) <= 1.0
        oval = _draw_figure(page, 'oval', (box['x'] + 520, box['y'] + 190), (box['x'] + 680, box['y'] + 300), shift=True)
        assert abs(float(oval['w']) - float(oval['h'])) <= 1.0
        assert not errors, errors
    finally:
        _shutdown_page(page)
        _release(base)


def test_v722_line_draw_keeps_30_degrees_and_shift_snaps_to_45(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '7.2.2 line angles')
    page, errors = _page(base, scene_browser)
    try:
        page.evaluate("""async()=>{const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Line Angles',canvas_type:'blank'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);await setCanvasMode('pointer')}""")
        page.wait_for_selector('#canvasView.active')
        stage = page.locator('#canvasStage')
        box = stage.bounding_box()
        assert box

        dx = 240
        dy = dx * math.tan(math.radians(30))
        start = (box['x'] + 220, box['y'] + 220)
        end = (start[0] + dx, start[1] + dy)
        free_line = _draw_figure(page, 'line', start, end, shift=False)
        free_angle = _line_angle(page, free_line['id'])
        assert 27 <= free_angle <= 33, free_angle

        start2 = (box['x'] + 230, box['y'] + 470)
        end2 = (start2[0] + dx, start2[1] + dy)
        snapped = _draw_figure(page, 'line', start2, end2, shift=True)
        snapped_angle = _line_angle(page, snapped['id'])
        assert 43 <= snapped_angle <= 47, snapped_angle

        # Shift also applies when editing either endpoint, relative to the fixed endpoint.
        page.evaluate("id=>selectOnlyNode(id)", free_line['id'])
        handle = page.locator(f'.canvas-figure[data-id="{free_line["id"]}"] [data-line-endpoint="end"]')
        hb = handle.bounding_box()
        assert hb
        page.keyboard.down('Shift')
        page.mouse.move(hb['x'] + hb['width']/2, hb['y'] + hb['height']/2)
        page.mouse.down()
        page.mouse.move(hb['x'] + hb['width']/2 + 95, hb['y'] + hb['height']/2 + 18, steps=6)
        page.mouse.up()
        page.keyboard.up('Shift')
        page.wait_for_timeout(150)
        edited_angle = _line_angle(page, free_line['id'])
        assert min(abs(edited_angle - candidate) for candidate in (0, 45, 90)) <= 2.0, edited_angle
        assert not errors, errors
    finally:
        _shutdown_page(page)
        _release(base)
