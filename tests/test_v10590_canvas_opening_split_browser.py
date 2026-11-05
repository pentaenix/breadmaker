"""Canvas startup concurrency and read-only Split camera interaction."""
import re
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10581_canvas_navigation_browser import _board


def test_cold_canvas_requests_data_and_viewport_together(tmp_path):
    with browser(tmp_path,local=False) as (_,page,_):
        ids,errors=_board(page)
        page.evaluate('(id)=>{setView("manuscript");state.currentCanvas=null;state.lastCanvasId=id}',ids['cv'])
        held=[]
        page.route(f'**/api/canvases/{ids["cv"]}',lambda route:held.append(route))
        page.route(f'**/api/ui-state/canvas-viewport-{ids["cv"]}',lambda route:held.append(route))
        try:
            with page.expect_request(f'**/api/ui-state/canvas-viewport-{ids["cv"]}'):
                page.locator('.main-tab[data-view="canvas"]').click()
            page.evaluate('()=>0')
            assert len(held)==2,'Neither read should wait for the other response'
            for route in held:route.continue_()
            held.clear()
            expect(page.locator('#canvasView')).to_have_class(re.compile(r'\bactive\b'))
            expect(page.locator('.canvas-node').first).to_be_visible()
            assert not errors,errors
        finally:
            for route in held:route.abort()


def test_late_canvas_read_cannot_replace_new_selection(tmp_path):
    with browser(tmp_path,local=False) as (_,page,_):
        ids,errors=_board(page)
        second=page.evaluate("async()=>{const row=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Newer selection',canvas_type:'blank'})});await refresh(false);return row.id}")
        held=[];page.route(f'**/api/canvases/{ids["cv"]}',lambda route:held.append(route))
        with page.expect_request(f'**/api/canvases/{ids["cv"]}'):
            page.evaluate('(id)=>{window.oldCanvasSelection=selectCanvas(id,{persist:false})}',ids['cv'])
        page.evaluate('async(id)=>await selectCanvas(id,{persist:false})',second)
        for route in held:route.continue_()
        page.evaluate('async()=>await oldCanvasSelection')
        assert page.evaluate('()=>state.currentCanvas.id')==second
        expect(page.locator('#canvasName')).to_have_value('Newer selection')
        assert not errors,errors


def split_pan(home,local,engine):
    with browser(home,local=local,engine=engine) as (_,page,_):
        ids,errors=_board(page)
        page.evaluate('''async(id)=>{await api(`/api/canvases/${id}/nodes`,{method:'POST',body:JSON.stringify({title:'Far read-only card',x:1500,y:1200,w:180,h:100})});setView("manuscript");await openCanvasSplit(id)}''',ids['cv'])
        before=page.evaluate('(id)=>api(`/api/canvases/${id}`)',ids['cv'])
        writes=[];page.on('request',lambda r:writes.append(r.url) if r.method in ('POST','PUT','PATCH','DELETE') and ('/nodes/' in r.url or '/canvases/' in r.url or '/edges/' in r.url) else None)
        body=page.locator('#splitBody');body.evaluate('el=>{el.scrollLeft=100;el.scrollTop=100}')
        origin=body.evaluate('el=>[el.scrollLeft,el.scrollTop]');box=body.bounding_box()
        card=page.locator('#splitCanvasNodes .split-canvas-node').first.bounding_box()
        x,y=card['x']+30,card['y']+30
        page.mouse.move(x,y);page.mouse.down();page.mouse.move(x-60,y-50,steps=5);page.mouse.up()
        moved=body.evaluate('el=>[el.scrollLeft,el.scrollTop]')
        assert abs(moved[0]-origin[0]-60)<2 and abs(moved[1]-origin[1]-50)<2,(origin,moved)
        assert page.locator('#splitCanvasStage').evaluate('el=>el.classList.contains("panning")') is False
        assert page.locator('#splitBody [contenteditable=true]').count()==0
        assert not writes,writes
        after=page.evaluate('(id)=>api(`/api/canvases/${id}`)',ids['cv'])
        assert before==after,'Camera movement must not edit Canvas content'
        page.mouse.move(x,y);page.mouse.down();page.mouse.move(x-20,y-20)
        page.evaluate('()=>closeSplitView()');page.mouse.up()
        expect(page.locator('#splitPane')).to_be_hidden()
        assert not errors,errors


def test_local_split_canvas_drag_is_read_only(tmp_path):split_pan(tmp_path,True,'chromium')
def test_hosted_split_canvas_drag_is_read_only_webkit(tmp_path):split_pan(tmp_path,False,'webkit')
