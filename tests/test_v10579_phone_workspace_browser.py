"""Silent phone layout/menus/panels; never opens or generates narration."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser


def exercise(page,base,pid,size,screens=None):
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base+'/p/'+pid,wait_until='domcontentloaded')
    page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
    for width,height in (size,):
        page.set_viewport_size({'width':width,'height':height})
        page.wait_for_function('(height)=>Math.abs(document.querySelector("#app").getBoundingClientRect().height-height)<2',arg=height)
        expect(page.locator('.main-tabs')).to_be_hidden()
        for selector in ('#brandButton','#phoneWorkspaceBtn','#userAvatarButton','#phoneManuscriptToolsBtn','#viewBtn'):
            rect=page.locator(selector).bounding_box();assert rect and rect['x']>=0 and rect['x']+rect['width']<=width,(selector,rect,page.locator('.document-toolbar > *').evaluate_all('els=>els.map(e=>({id:e.id,display:getComputedStyle(e).display,width:e.getBoundingClientRect().width}))'))
        page.locator('#phoneWorkspaceBtn').tap()
        expect(page.get_by_role('menu',name='Workspaces',exact=True)).to_be_visible()
        expect(page.get_by_role('menuitem',name='Manuscript',exact=True)).to_have_attribute('aria-current','page')
        page.keyboard.press('Escape');expect(page.locator('#phoneWorkspaceBtn')).to_be_focused()
        page.locator('#phoneManuscriptToolsBtn').tap()
        menu=page.get_by_role('menu',name='Manuscript tools',exact=True)
        box=menu.bounding_box();assert box['y']>=0 and box['y']+box['height']<=height
        page.keyboard.press('End')
        last=menu.get_by_role('menuitem').last.bounding_box();assert last['y']>=0 and last['y']+last['height']<=height
        page.keyboard.press('Home')
        page.get_by_role('menuitem',name='Notes',exact=True).tap()
        expect(page.locator('#commentsPanel')).to_be_visible()
        sheet=page.locator('#commentsPanel').bounding_box();view=page.locator('#manuscriptView').bounding_box()
        assert sheet['width']==width and abs(sheet['height']-view['height']/2)<=1
        assert abs(sheet['y']+sheet['height']-view['y']-view['height'])<=1
        assert page.locator('#manuscriptPrimaryPane').bounding_box()['height']<=view['height']*.51
        expect(page.get_by_role('button',name='Close Notes',exact=True)).to_be_visible()
        if screens and height==844:page.screenshot(path=str(screens))
        page.get_by_role('button',name='Close Notes',exact=True).tap()
        expect(page.locator('#commentsPanel')).to_be_hidden()
        page.locator('.manuscript-block').first.fill(f'Silent phone writing at {width} by {height}.')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        # Long content scrolls inside its scroller; root and command bars stay put.
        page.locator('.manuscript-block').first.fill(('A quiet harbor.\n'*100).strip())
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        page.locator('.document-scroller').evaluate('el=>el.scrollTop=el.scrollHeight')
        assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth&&scrollX===0&&scrollY===0')
        assert page.locator('#phoneManuscriptToolsBtn').bounding_box()['y']<100
    page.set_viewport_size({'width':1200,'height':900})
    expect(page.locator('#phoneWorkspaceBtn')).to_be_hidden();expect(page.locator('.main-tabs')).to_be_visible()
    expect(page.locator('#phoneManuscriptToolsBtn')).to_be_hidden();expect(page.locator('#commentsBtn')).to_be_visible()
    assert not errors,errors


def phone(home,local,size,engine='chromium'):
    options={'viewport':{'width':size[0],'height':size[1]},'is_mobile':True,'has_touch':True,'device_scale_factor':3}
    with browser(home,local=local,page_options=options,engine=engine) as (base,page,pid):
        exercise(page,base,pid,size)


def _exercise_local_phone_viewport_menus_and_bottom_sheets(tmp_path,size):phone(tmp_path,True,size)
def _exercise_hosted_phone_viewport_menus_and_bottom_sheets(tmp_path,size):phone(tmp_path,False,size)
def _exercise_webkit_phone_viewport_menus_and_bottom_sheets(tmp_path,size):phone(tmp_path,False,size,'webkit')


def _exercise_phone_workspace_menu_routes_every_view_without_root_scroll(tmp_path,local):
    with browser(tmp_path,local=local,page_options={'viewport':{'width':390,'height':844},'is_mobile':True,'has_touch':True}) as (_,page,_):
        page.set_viewport_size({'width':390,'height':844})
        for name,view in (('Canvas','canvas'),('Maps','maps'),('Reviews','reviews'),('Tasks','tasks'),('Manuscript','manuscript')):
            page.locator('#phoneWorkspaceBtn').tap();page.get_by_role('menuitem',name=name,exact=True).tap()
            page.wait_for_function('(view)=>currentView()===view',arg=view)
            expect(page.locator('#phoneWorkspaceName')).to_have_text(name)
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth&&scrollY===0')


def _exercise_phone_find_and_keyboard_height_dialog_keep_controls_inside_viewport(tmp_path,local):
    options={'viewport':{'width':390,'height':420},'is_mobile':True,'has_touch':True}
    with browser(tmp_path,local=local,page_options=options) as (_,page,_):
        page.locator('#viewBtn').tap();page.locator('#viewFindReplaceBtn').tap()
        expect(page.locator('#findPopover')).to_be_visible()
        box=page.locator('#findPopover').bounding_box();assert box['x']>=0 and box['x']+box['width']<=390
        assert page.locator('#findInput').evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')>=16
        page.keyboard.press('Escape')
        page.evaluate("()=>{window.syntheticPhoneDialog=textDialog('Synthetic phone dialog',{value:'Disposable text'})}")
        expect(page.locator('#dialogText')).to_be_focused()
        box=page.locator('.modal-card').bounding_box();assert box['y']>=0 and box['y']+box['height']<=420
        confirm=page.locator('#dialogConfirm').bounding_box();assert confirm['y']+confirm['height']<=420
        page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_hidden()


# Explicit cases match the release verifier's one-item browser jobs.

def test_local_phone_viewport_menus_and_bottom_sheets_390x844(tmp_path):
    _exercise_local_phone_viewport_menus_and_bottom_sheets(tmp_path,(390,844))

def test_local_phone_viewport_menus_and_bottom_sheets_360x740(tmp_path):
    _exercise_local_phone_viewport_menus_and_bottom_sheets(tmp_path,(360,740))

def test_local_phone_viewport_menus_and_bottom_sheets_390x420(tmp_path):
    _exercise_local_phone_viewport_menus_and_bottom_sheets(tmp_path,(390,420))

def test_hosted_phone_viewport_menus_and_bottom_sheets_390x844(tmp_path):
    _exercise_hosted_phone_viewport_menus_and_bottom_sheets(tmp_path,(390,844))

def test_hosted_phone_viewport_menus_and_bottom_sheets_360x740(tmp_path):
    _exercise_hosted_phone_viewport_menus_and_bottom_sheets(tmp_path,(360,740))

def test_hosted_phone_viewport_menus_and_bottom_sheets_390x420(tmp_path):
    _exercise_hosted_phone_viewport_menus_and_bottom_sheets(tmp_path,(390,420))

def test_webkit_phone_viewport_menus_and_bottom_sheets_390x844(tmp_path):
    _exercise_webkit_phone_viewport_menus_and_bottom_sheets(tmp_path,(390,844))

def test_webkit_phone_viewport_menus_and_bottom_sheets_360x740(tmp_path):
    _exercise_webkit_phone_viewport_menus_and_bottom_sheets(tmp_path,(360,740))

def test_webkit_phone_viewport_menus_and_bottom_sheets_390x420(tmp_path):
    _exercise_webkit_phone_viewport_menus_and_bottom_sheets(tmp_path,(390,420))

def test_phone_workspace_menu_routes_every_view_without_root_scroll_local(tmp_path):
    _exercise_phone_workspace_menu_routes_every_view_without_root_scroll(tmp_path,True)

def test_phone_workspace_menu_routes_every_view_without_root_scroll_hosted(tmp_path):
    _exercise_phone_workspace_menu_routes_every_view_without_root_scroll(tmp_path,False)

def test_phone_find_and_keyboard_height_dialog_keep_controls_inside_viewport_local(tmp_path):
    _exercise_phone_find_and_keyboard_height_dialog_keep_controls_inside_viewport(tmp_path,True)

def test_phone_find_and_keyboard_height_dialog_keep_controls_inside_viewport_hosted(tmp_path):
    _exercise_phone_find_and_keyboard_height_dialog_keep_controls_inside_viewport(tmp_path,False)


def _exercise_phone_writing_taps_and_tool_icons(tmp_path,local,engine):
    options={'viewport':{'width':390,'height':844},'is_mobile':True,'has_touch':True,'device_scale_factor':3}
    with browser(tmp_path,local=local,page_options=options,engine=engine) as (_,page,_):
        block=page.locator('.manuscript-block').first
        block.fill('Harbor morning light.')
        assert block.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')>=16
        assert page.locator('#manuscriptPaper').evaluate('e=>getComputedStyle(e).touchAction')=='manipulation'
        viewport=page.locator('meta[name=viewport]').get_attribute('content')
        assert 'user-scalable=no' not in viewport and 'maximum-scale' not in viewport
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        block.tap();page.keyboard.press('End');page.keyboard.type(' Quiet writing.')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        expect(block).to_contain_text('Quiet writing.')
        block.dblclick()
        assert page.evaluate('()=>getSelection().toString().length')>0
        assert page.evaluate('()=>visualViewport.scale')==1
        page.locator('#phoneManuscriptToolsBtn').tap()
        menu=page.get_by_role('menu',name='Manuscript tools',exact=True)
        items=menu.get_by_role('menuitem')
        assert items.count()>=7
        for item in items.all():
            icon=item.locator('svg')
            expect(icon).to_be_visible();expect(icon).to_have_attribute('aria-hidden','true')
            assert icon.bounding_box()['width']==18
        page.screenshot(path=str(tmp_path/'phone-tools.png'))
        menu.get_by_role('menuitem',name='Notes',exact=True).tap()
        expect(page.locator('#commentsPanel')).to_be_visible()
        page.get_by_role('button',name='Close Notes',exact=True).tap()
        page.locator('#phoneManuscriptToolsBtn').tap()
        page.get_by_role('menuitem',name='Editor’s Eye',exact=True).tap()
        page.wait_for_function('()=>document.querySelector("#documentScroller").classList.contains("editors-eye-active")')
        assert block.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')>=16
        page.locator('#phoneManuscriptToolsBtn').tap()
        page.get_by_role('menuitem',name='Editor’s Eye on — click to restore normal manuscript view',exact=True).tap()
        page.wait_for_function('()=>!document.querySelector("#documentScroller").classList.contains("editors-eye-active")')
        # Larger writer preferences survive the phone minimum and desktop remains unchanged.
        page.evaluate("()=>document.documentElement.style.setProperty('--manuscript-font-size','22px')")
        assert block.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')==22
        page.evaluate("()=>document.documentElement.style.removeProperty('--manuscript-font-size')")
        page.set_viewport_size({'width':1200,'height':900})
        assert block.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')==14


def test_phone_writing_taps_and_tool_icons_local(tmp_path):
    _exercise_phone_writing_taps_and_tool_icons(tmp_path,True,'chromium')


def test_phone_writing_taps_and_tool_icons_hosted(tmp_path):
    _exercise_phone_writing_taps_and_tool_icons(tmp_path,False,'chromium')


def test_webkit_phone_writing_taps_and_tool_icons_local(tmp_path):
    _exercise_phone_writing_taps_and_tool_icons(tmp_path,True,'webkit')


def test_webkit_phone_writing_taps_and_tool_icons_hosted(tmp_path):
    _exercise_phone_writing_taps_and_tool_icons(tmp_path,False,'webkit')
