"""Direct Canvas navigation in the real shell, with disposable local/hosted stores."""
import re
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser


def _board(page):
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    ids=page.evaluate("""async()=>{
      const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Direct navigation',canvas_type:'blank'})});
      const a=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'First card',body:'Keep this prose unchanged.',x:420,y:360,w:180,h:100})});
      const b=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Second card',x:720,y:360,w:180,h:100})});
      const edge=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:b.id,from_port:'right',to_port:'left'})});
      // Existing hand-mode preferences must not change the new default.
      await api(`/api/ui-state/${canvasViewportKey(cv.id)}`,{method:'PUT',body:JSON.stringify({value:{mode:'hand',left:240,top:200,zoom:1}})});
      await refresh(false);setView('canvas');await selectCanvas(cv.id);closeCanvasPanel();
      return {cv:cv.id,a:a.id,b:b.id,edge:edge.id};
    }""")
    page.wait_for_function("()=>$('#canvasStage').scrollLeft===240")
    return ids,errors


def _drag(page,x,y,dx,dy,button='left'):
    page.mouse.move(x,y);page.mouse.down(button=button)
    page.mouse.move(x+dx,y+dy,steps=5);page.mouse.up(button=button)


def _scroll(page):
    return page.locator('#canvasStage').evaluate('el=>[el.scrollLeft,el.scrollTop]')


def _mouse_navigation(home,local,engine):
    with browser(home,local=local,engine=engine,page_options={'viewport':{'width':1400,'height':950}}) as (_,page,_):
        ids,errors=_board(page)
        expect(page.locator('#canvasHandMode,#canvasPointerMode,.canvas-mode-overlay')).to_have_count(0)
        stage=page.locator('#canvasStage');box=stage.bounding_box()
        # Empty-space left drag selects both cards without moving the viewport.
        before=_scroll(page)
        _drag(page,box['x']+140,box['y']+120,550,220)
        assert page.evaluate('()=>state.selectedNodes.size')==2
        assert _scroll(page)==before
        page.locator('[data-close-canvas-panel]').click()
        node=page.locator(f'.canvas-node[data-id="{ids["a"]}"]')
        # Stationary right-click still uses the card menu, including after a pan.
        node.locator('.node-drag-strip').click(button='right')
        expect(page.locator('#contextMenu')).to_be_visible()
        assert 'Delete' in page.locator('#contextMenu').inner_text()
        page.keyboard.press('Escape')
        before=page.evaluate("()=>({scroll:[$('#canvasStage').scrollLeft,$('#canvasStage').scrollTop],nodes:state.canvasNodes.map(n=>[n.id,n.x,n.y]),selected:[...state.selectedNodes]})")
        nb=node.locator('.node-body').bounding_box()
        _drag(page,nb['x']+20,nb['y']+10,-90,-50,'right')
        assert _scroll(page)[0]>=before['scroll'][0]+85
        assert _scroll(page)[1]>=before['scroll'][1]+45
        assert page.evaluate('()=>state.canvasNodes.map(n=>[n.id,n.x,n.y])')==before['nodes']
        assert page.evaluate('()=>[...state.selectedNodes]')==before['selected']
        expect(page.locator('#contextMenu')).to_be_hidden()
        expect(stage).not_to_have_class(__import__('re').compile('panning'))
        node.locator('.node-drag-strip').click(button='right')
        expect(page.locator('#contextMenu')).to_be_visible();page.keyboard.press('Escape')
        # Space pans over a connector without moving either endpoint or selecting text.
        page.evaluate("()=>{const s=$('#canvasStage');s.scrollLeft=240;s.scrollTop=200;s.focus()}")
        point=page.locator(f'.edge-group[data-edge-id="{ids["edge"]}"] .edge-hit').evaluate("el=>{const p=el.getPointAtLength(el.getTotalLength()/2),m=el.getScreenCTM();return {x:p.x*m.a+m.e,y:p.y*m.d+m.f}}")
        page.keyboard.down('Space');_drag(page,point['x'],point['y'],-80,-40);page.keyboard.up('Space')
        assert _scroll(page)[0]>=315
        assert page.evaluate('()=>String(getSelection())')==''
        assert not page.evaluate('()=>state.canvasSpaceHeld||canvasPanGesture')
        assert not errors,errors


def test_local_direct_canvas_navigation_chromium(tmp_path):_mouse_navigation(tmp_path,True,'chromium')
def test_hosted_direct_canvas_navigation_chromium(tmp_path):_mouse_navigation(tmp_path,False,'chromium')
def test_hosted_direct_canvas_navigation_webkit(tmp_path):_mouse_navigation(tmp_path,False,'webkit')


def _keyboard(home,engine):
    with browser(home,local=True,engine=engine,page_options={'viewport':{'width':1400,'height':950}}) as (_,page,_):
        ids,errors=_board(page)
        # Space keeps its normal activation behavior on toolbar/menu controls.
        page.locator('#canvasViewBtn').focus();page.keyboard.press('Space')
        expect(page.locator('#canvasViewPopover')).to_be_visible()
        assert not page.evaluate('()=>state.canvasSpaceHeld||state.canvasSpaceKeyDown')
        page.keyboard.press('Escape')
        body=page.locator(f'.canvas-node[data-id="{ids["a"]}"] .node-body')
        body.click();body.evaluate("el=>{const r=document.createRange();r.selectNodeContents(el);r.collapse(false);getSelection().removeAllRanges();getSelection().addRange(r)}");page.keyboard.press('Space');page.keyboard.insert_text('more')
        expect(body).to_have_text('Keep this prose unchanged. more')
        # A held Space can still convert focused card editing into panning.
        page.keyboard.down('Space')
        box=page.locator('#canvasStage').bounding_box();before=_scroll(page)
        _drag(page,box['x']+600,box['y']+350,-80,-40);page.keyboard.up('Space')
        expect(body).to_have_text('Keep this prose unchanged. more')
        assert _scroll(page)[0]>=before[0]+75
        # Blur clears a held gesture even when its pointer-up is lost.
        page.locator('#canvasStage').focus();page.keyboard.down('Space')
        page.mouse.move(box['x']+600,box['y']+350);page.mouse.down()
        page.evaluate("()=>window.dispatchEvent(new Event('blur'))")
        assert not page.evaluate('()=>canvasPanGesture||state.canvasSpaceHeld||state.canvasSpaceKeyDown')
        page.mouse.up();page.keyboard.up('Space')
        expect(page.locator('#canvasStage')).not_to_have_class(__import__('re').compile('panning'))
        # Manuscript retains its native Space input and existing View menu grammar.
        page.evaluate("()=>setView('manuscript')")
        prose=page.locator('.manuscript-block').first;prose.fill('A quiet')
        page.keyboard.press('Space');page.keyboard.insert_text('harbor.')
        expect(prose).to_have_text('A quiet harbor.')
        page.locator('#viewBtn').focus();page.keyboard.press('Space')
        expect(page.locator('#viewPopover')).to_be_visible()
        assert not errors,errors


def test_canvas_keyboard_editing_and_manuscript_chromium(tmp_path):_keyboard(tmp_path,'chromium')
def test_canvas_keyboard_editing_and_manuscript_webkit(tmp_path):_keyboard(tmp_path,'webkit')


def _touch(home,engine):
    options={'viewport':{'width':390,'height':844},'is_mobile':True,'has_touch':True}
    with browser(home,local=True,engine=engine,page_options=options) as (_,page,_):
        ids,errors=_board(page)
        stage=page.locator('#canvasStage');box=stage.bounding_box();before=_scroll(page)
        x,y=box['x']+280,box['y']+350
        if engine=='chromium':
            cdp=page.context.new_cdp_session(page)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
            cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x-65,'y':y-45}]})
            cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
            cdp.detach()
        else:
            # WebKit automation has no portable touch-drag command. Exercise the
            # same PointerEvent path, followed by an actual touch tap below.
            page.evaluate("""({x,y})=>{const s=$('#canvasStage'),event=(type,x,y)=>new PointerEvent(type,{bubbles:true,cancelable:true,pointerType:'touch',pointerId:71,button:0,clientX:x,clientY:y});s.dispatchEvent(event('pointerdown',x,y));s.dispatchEvent(event('pointermove',x-65,y-45));s.dispatchEvent(event('pointerup',x-65,y-45))}""",{'x':x,'y':y})
        assert _scroll(page)[0]>=before[0]+60 and _scroll(page)[1]>=before[1]+40
        expect(page.locator('#selectionRect')).to_be_hidden()
        assert not page.evaluate('()=>canvasPanGesture')
        expect(page.locator('#canvasHandMode,#canvasPointerMode')).to_have_count(0)
        # A normal touch tap can still select and edit a card after panning.
        page.evaluate("()=>{const s=$('#canvasStage');s.scrollLeft=240;s.scrollTop=200}")
        card=page.locator(f'.canvas-node[data-id="{ids["a"]}"]')
        title=card.locator('.node-title');title.tap()
        # Native touch focus follows pointerup in a later task. Observe the
        # completed selection/focus before checking the model; never sleep.
        expect(card).to_have_class(re.compile(r'\bselected\b'),timeout=1500)
        expect(title).to_be_focused(timeout=1500)
        assert page.evaluate('(id)=>state.selectedNodes.has(id)',ids['a'])
        assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors


def test_canvas_touch_panning_chromium(tmp_path):_touch(tmp_path,'chromium')
def test_canvas_touch_panning_webkit(tmp_path):_touch(tmp_path,'webkit')


def test_canvas_context_menu_timing_and_pointer_cancellation(tmp_path):
    with browser(tmp_path,local=True,page_options={'viewport':{'width':1400,'height':950}}) as (_,page,_):
        ids,errors=_board(page)
        result=page.evaluate("""id=>{
          state.selectedNodes=new Set(state.canvasNodes.map(n=>n.id));renderNodeSelection();
          const target=$(`.canvas-node[data-id="${id}"] .node-body`),s=$('#canvasStage'),r=target.getBoundingClientRect(),x=r.x+20,y=r.y+10;
          const pointer=(type,dx=0,pid=91)=>new PointerEvent(type,{bubbles:true,cancelable:true,pointerId:pid,pointerType:'mouse',button:2,clientX:x+dx,clientY:y});
          const context=()=>new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2,clientX:x,clientY:y});
          target.dispatchEvent(pointer('pointerdown'));target.dispatchEvent(context());
          const deferred=$('#contextMenu').classList.contains('hidden');
          target.dispatchEvent(pointer('pointerup'));
          const stationary=!$('#contextMenu').classList.contains('hidden');$('#contextMenu').classList.add('hidden');
          target.dispatchEvent(pointer('pointerdown'));target.dispatchEvent(context());target.dispatchEvent(pointer('pointermove',-60));
          const moved=s.scrollLeft;
          document.dispatchEvent(pointer('pointerup',-60,92));
          const foreignIgnored=!!canvasPanGesture;
          document.dispatchEvent(pointer('pointercancel',-60));target.dispatchEvent(context());
          const cancelled=!canvasPanGesture&&!s.classList.contains('panning')&&$('#contextMenu').classList.contains('hidden');
          document.dispatchEvent(pointer('pointermove',-120));
          return {deferred,stationary,foreignIgnored,cancelled,noGhostMove:s.scrollLeft===moved};
        }""",ids['a'])
        assert result==dict.fromkeys(('deferred','stationary','foreignIgnored','cancelled','noGhostMove'),True),result
        # A slow background-menu catalog must not reopen a menu after a pan.
        page.evaluate("""()=>{
          window.originalCanvasCatalog=canvasPopulateCatalog;
          canvasPopulateCatalog=()=>new Promise(resolve=>window.finishCanvasCatalog=resolve);
          const s=$('#canvasStage');s.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:900,clientY:600}));
        }""")
        page.wait_for_function('()=>typeof finishCanvasCatalog==="function"')
        box=page.locator('#canvasStage').bounding_box()
        _drag(page,box['x']+600,box['y']+400,-50,-30,'right')
        page.evaluate('async()=>{finishCanvasCatalog({categories:[]});await Promise.resolve();canvasPopulateCatalog=originalCanvasCatalog}')
        expect(page.locator('#contextMenu')).to_be_hidden()
        # Keyboard context-menu events remain available after the right drag.
        page.locator('#canvasStage').dispatch_event('contextmenu',{'button':0,'clientX':900,'clientY':600})
        expect(page.locator('#contextMenu')).to_be_visible()
        page.keyboard.press('Escape')
        assert not errors,errors
