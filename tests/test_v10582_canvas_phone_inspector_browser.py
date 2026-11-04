"""Canvas inspector shares the phone Manuscript sheet; no narration is exercised."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10581_canvas_navigation_browser import _board


def _inspector(home, local, engine):
    with browser(home,local=local,engine=engine,page_options={
        'viewport':{'width':390,'height':844},'has_touch':True,'is_mobile':True,
    }) as (_,page,_):
        ids,errors=_board(page)
        body=page.locator(f'.canvas-node[data-id="{ids["a"]}"] .node-body')
        panel=page.locator('#canvasToolPanel')
        shell=page.locator('#canvasView>.canvas-shell')
        for width,height in ((390,844),(320,568),(430,932),(390,420),(1024,768),(1440,900)):
            page.set_viewport_size({'width':width,'height':height})
            body.tap() if width<=640 else body.click()
            expect(body).to_be_focused()
            expect(panel).to_be_visible()
            view=page.locator('#canvasView').bounding_box()
            sheet=panel.bounding_box();board=shell.bounding_box()
            if width<=640:
                assert abs(sheet['width']-view['width'])<=1,(view,sheet)
                assert abs(sheet['height']-view['height']/2)<=1,(view,sheet)
                assert abs(sheet['y']-board['y']-board['height'])<=1,(sheet,board)
                assert abs(sheet['y']+sheet['height']-height)<=1,(sheet,height)
                # The card remains editable in the separate top half, including
                # when available height shrinks as a phone keyboard opens.
                body.scroll_into_view_if_needed()
                box=body.bounding_box()
                assert box['y']>=board['y'] and box['y']+box['height']<=sheet['y']+1,(box,sheet)
            else:
                assert abs(sheet['height']-view['height'])<=1,(sheet,view)
                assert abs(sheet['width']-290)<=1,sheet
                assert board['x']+board['width']<=sheet['x']+1,(board,sheet)
            body.fill(f'Phone sheet prose {width}x{height}.')
            page.evaluate('()=>flushPendingCanvasNodeSaves()')
            expect(body).to_have_text(f'Phone sheet prose {width}x{height}.')
            # Scroll the inspector without moving its close control offscreen.
            page.locator('#canvasPanelBody').evaluate('el=>el.scrollTop=el.scrollHeight')
            close=panel.locator('[data-close-canvas-panel]')
            rect=close.bounding_box()
            assert 0<=rect['y'] and rect['y']+rect['height']<=height,rect
            close.click();expect(panel).to_be_hidden()
            assert abs(shell.bounding_box()['height']-view['height'])<=1
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth&&scrollY===0')
        # Reopen from the actual store, rather than trusting rendered edits.
        assert not errors,errors
        page.wait_for_function('()=>!state.canvasViewportSaveTimer&&state.canvasNodeTimers.size===0&&state.canvasNodeSaveInFlight.size===0')
        page.wait_for_load_state('networkidle')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.evaluate('async id=>{setView("canvas");await selectCanvas(id)}',ids['cv'])
        expect(body).to_have_text('Phone sheet prose 1440x900.')
        # Resize with an already-open inspector, then close/reopen via selection.
        body.click();page.set_viewport_size({'width':390,'height':844})
        expect(panel).to_be_visible()
        assert panel.bounding_box()['width']==390
        page.evaluate("()=>setView('manuscript')")
        expect(panel).not_to_be_visible()
        page.evaluate("()=>setView('canvas')")
        body.click();expect(panel).to_be_visible()
        assert panel.bounding_box()['width']==390
        assert not errors,errors


def test_local_phone_canvas_inspector_chromium(tmp_path):_inspector(tmp_path,True,'chromium')
def test_hosted_phone_canvas_inspector_chromium(tmp_path):_inspector(tmp_path,False,'chromium')
def test_local_phone_canvas_inspector_webkit(tmp_path):_inspector(tmp_path,True,'webkit')
def test_hosted_phone_canvas_inspector_webkit(tmp_path):_inspector(tmp_path,False,'webkit')
