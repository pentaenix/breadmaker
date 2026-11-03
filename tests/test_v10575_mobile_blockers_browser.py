"""Real synthetic local/hosted UI regressions; no providers or real credentials."""
import json
from pathlib import Path
from playwright.sync_api import expect

from tests.test_v10569_narration_browser import browser


def password_prompt(home, local):
    with browser(home,local=local) as (_,page,_):
        page.evaluate('''()=>{
          window.dummyCalls=[];
          accountSecurityIdentityModule=async()=>({freshReauthenticate:async(method,data)=>{
            dummyCalls.push({method,matched:data.password==='DUMMY-ONLY'});return {user:{synthetic:true}}},
            serverVerifyFresh:async()=>{},signOutClient:async()=>{}});
          window.proof=accountSecurityFreshIdentityUser({current_session_id:'synthetic',
            sessions:[{session_id:'synthetic',auth_method:'email_password'}],
            identities:[{provider:'email_password',email:'synthetic@example.invalid'}]},'synthetic').catch(()=>null);
        }''')
        field=page.get_by_label('Password',exact=True)
        expect(field).to_have_attribute('type','password')
        expect(field).to_have_attribute('autocomplete','current-password')
        field.fill('DUMMY-ONLY')
        page.evaluate('()=>window.retainedInput=document.querySelector("#dialogText")')
        page.keyboard.press('Enter')
        page.evaluate('async()=>await window.proof')
        assert page.evaluate('()=>dummyCalls')==[{'method':'password','matched':True}]
        assert page.evaluate('()=>retainedInput.value')==''
        page.evaluate('()=>{window.cancelResult="pending";passwordDialog("Cancel password").then(v=>window.cancelResult=v)}')
        page.get_by_label('Password',exact=True).fill('DUMMY-CANCEL')
        page.evaluate('()=>window.retainedInput=document.querySelector("#dialogText")')
        page.keyboard.press('Escape')
        page.wait_for_function('()=>window.cancelResult===null')
        assert page.evaluate('()=>retainedInput.value')==''
        page.evaluate('()=>{window.textResult=null;textDialog("Ordinary text",{value:"Keep me"}).then(v=>window.textResult=v)}')
        expect(page.locator('#dialogText')).to_have_value('Keep me')
        page.keyboard.press('Enter');page.wait_for_function('()=>textResult==="Keep me"')
        page.evaluate('()=>{window.textResult=null;textDialog("Multiline",{multiline:true,value:"two lines"}).then(v=>window.textResult=v)}')
        expect(page.locator('textarea#dialogText')).to_have_value('two lines')
        page.locator('#dialogConfirm').click();page.wait_for_function('()=>textResult==="two lines"')


def modal_keyboard(home, local):
    with browser(home,local=local) as (_,page,_):
        page.locator('#brandButton').focus()
        page.evaluate('''()=>{
          window.dismissed=0;
          showModal('Parent','<input id="parentInput" aria-label="Parent input"><button id="childOpener">Child</button>',
            '<button id="parentDone">Done</button>',()=>window.dismissed++);
          document.querySelector('#childOpener').onclick=()=>showNestedModal('Child','<input id="childInput" aria-label="Child input">','<button id="childDone">Done</button>');
        }''')
        expect(page.locator('#modal')).to_have_attribute('role','dialog')
        expect(page.locator('#modal')).to_have_attribute('aria-modal','true')
        expect(page.locator('#parentInput')).to_be_focused()
        page.evaluate('()=>{window.backgroundKeys=0;window.auditKey=()=>window.backgroundKeys++;document.addEventListener("keydown",window.auditKey)}')
        page.locator('#parentDone').focus();page.keyboard.press('Delete')
        assert page.evaluate('()=>window.backgroundKeys')==0
        page.evaluate('()=>document.removeEventListener("keydown",window.auditKey)')
        page.locator('#modalClose').focus();page.keyboard.press('Shift+Tab')
        expect(page.locator('#parentDone')).to_be_focused()
        page.keyboard.press('Tab');expect(page.locator('#modalClose')).to_be_focused()
        page.locator('#childOpener').click()
        expect(page.locator('#childInput')).to_be_focused()
        page.keyboard.press('Escape')
        expect(page.locator('#childOpener')).to_be_focused()
        assert page.evaluate('()=>dismissed')==0
        page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_hidden()
        assert not page.locator('#app').evaluate('(el)=>el.inert')
        page.evaluate('''()=>{
          window.dismissCalls=[];
          showModal('Async parent','<button id="asyncChild">Child</button>','',()=>{
            dismissCalls.push('parent');setTimeout(()=>showModal('Async return','<input id="asyncReturn">'),10)});
          showNestedModal('Async child','<p>Child</p>','',()=>dismissCalls.push('child'));
          dismissModalTree();
        }''')
        expect(page.locator('#asyncReturn')).to_be_visible()
        assert page.evaluate('()=>dismissCalls')==['child','parent']
        assert page.locator('#app').evaluate('(el)=>el.inert')
        page.keyboard.press('Escape')
        page.evaluate('''()=>{
          showModal('Busy parent','<p>Working</p>');document.querySelector('#modalClose').disabled=true;
          showNestedModal('Dismissible child','<p>Child</p>');
        }''')
        expect(page.locator('#modalClose')).to_be_enabled()
        page.keyboard.press('Escape');expect(page.locator('#modalClose')).to_be_disabled()
        page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_visible()
        page.evaluate('()=>{document.querySelector("#modalClose").disabled=false;closeModal(false)}')
        expect(page.locator('#brandButton')).to_be_focused()
        assert page.evaluate('()=>dismissed')==1
        page.evaluate('()=>{showModal("Busy","<p>Working</p>");document.querySelector("#modalClose").disabled=true}')
        page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_visible()
        page.locator('#modal').click(position={'x':1,'y':1})
        expect(page.locator('#modal')).to_be_visible()
        page.evaluate('()=>{document.querySelector("#modalClose").disabled=false;closeModal(false)}')
        # A child callback may immediately replace the parent; it must survive.
        page.evaluate('''()=>{
          showModal('Outer','<button id="nested">Nested</button>');
          document.querySelector('#nested').onclick=()=>showNestedModal('Temporary','<p>Close me</p>','',
            ()=>showModal('Replacement','<input id="replacement" aria-label="Replacement">'));
        }''')
        page.locator('#nested').click();page.keyboard.press('Escape')
        expect(page.locator('#replacement')).to_be_focused()
        page.evaluate('()=>dismissModalTree()')
        # Detached opener uses the stable brand fallback.
        page.evaluate('()=>{const b=document.createElement("button");b.id="detached";document.body.append(b);b.focus();showModal("Detached","<p>Content</p>");b.remove()}')
        page.keyboard.press('Escape');expect(page.locator('#brandButton')).to_be_focused()
        # Only a popup with a live owner in this exact dialog joins its focus scope.
        page.evaluate('''()=>{
          showModal('Popup owner','<button id="popupOwner">Options</button>','<button id="modalDone">Done</button>');
          document.querySelector('#popupOwner').onclick=()=>openContextMenu(40,100,[{label:'First',action:()=>{}},{label:'Second',action:()=>{}}]);
        }''')
        page.locator('#popupOwner').click()
        page.locator('#contextMenu button').first.focus();page.keyboard.press('Tab')
        expect(page.locator('#contextMenu button').nth(1)).to_be_focused()
        page.keyboard.press('Escape');expect(page.locator('#contextMenu')).to_be_hidden()
        expect(page.locator('#modal')).to_be_visible()
        expect(page.locator('#popupOwner')).to_be_focused()
        page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_hidden()


def touch_menu(home, local):
    with browser(home,local=local) as (_,page,_):
        cdp=page.context.new_cdp_session(page)
        def tap(selector):
            node=page.locator(selector).first;node.wait_for(state='visible');node.scroll_into_view_if_needed()
            r=node.bounding_box()
            cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':r['x']+r['width']/2,'y':r['y']+r['height']/2}]})
            cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
        try:
            cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':True,'maxTouchPoints':1})
            # Create via the visible menu so another project is genuinely selectable.
            for width in (360,390):
                page.set_viewport_size({'width':width,'height':844})
                tap('#brandButton');tap('#projectMenuNew')
                page.locator('#newProjectName').fill(f'Synthetic touch {width}')
                with page.expect_navigation(wait_until='domcontentloaded'):tap('#createProjectConfirm')
                page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
                before=page.url;pid=page.evaluate('()=>currentProjectIdentity()')
                tap('#brandButton');tap('#projectMenuSwitch')
                expect(page.locator('#projectMenuBrowseAll')).to_be_visible()
                assert page.url==before and page.evaluate('()=>currentProjectIdentity()')==pid
                tap('#projectMenuBrowseAll')
                page.locator('#projectExplorerSearch').fill('Disposable')
                with page.expect_navigation(wait_until='domcontentloaded'):tap('[data-explorer-open]')
                page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
                assert page.url!=before
            cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':False})
            page.set_viewport_size({'width':1400,'height':950})
            page.locator('#brandButton').click();page.locator('#projectMenuSwitch').hover()
            expect(page.locator('#projectMenuBrowseAll')).to_be_visible()
            page.keyboard.press('Escape')
            page.locator('#brandButton').press('Enter')
            page.locator('#projectMenuSwitch').focus();page.keyboard.press('ArrowRight')
            assert page.locator('#projectSubmenu').evaluate('(el)=>el.contains(document.activeElement)')
            page.keyboard.press('ArrowLeft');expect(page.locator('#projectMenuSwitch')).to_be_focused()
        finally:cdp.detach()


def voice_library_portal(home,local):
    with browser(home,local=local) as (_,page,_):
        page.set_default_timeout(2500)
        if local:
            catalog=json.loads((Path(__file__).resolve().parents[1]/'static/narration-voices.json').read_text())
            page.route('**/api/narration-voices',lambda route:route.fulfill(json={**catalog,'voices':[{**v,'installed':False} for v in catalog['voices']]}))
        # Hosted uses its real fixture catalog and pinned public-manifest shape.
        try:
            page.locator('#narrationBtn').click();page.wait_for_function('()=>!narration.loading')
            page.evaluate('async()=>await openNarrationSettings()')
            page.locator('#narrationSettingsMoreVoices').click()
            page.locator('#narrationVoicePack').press('Enter')
            first=page.locator('#contextMenu button:not(:disabled)').first
            expect(first).to_be_visible();first.focus()
            page.keyboard.press('Shift+Tab');page.keyboard.press('Tab')
            expect(first).to_be_focused()
            page.keyboard.press('Escape');expect(page.locator('#contextMenu')).to_be_hidden()
            expect(page.locator('#narrationVoicePack')).to_be_focused()
            expect(page.locator('#narrationVoiceSearch')).to_be_visible()
            page.keyboard.press('Escape');expect(page.locator('#narrationSettingsMoreVoices')).to_be_focused()
            page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_hidden()
        finally:
            # Drain intercepted responses before synchronous driver shutdown.
            page.unroute_all(behavior='ignoreErrors');page.close()


def test_local_masked_password_and_text_dialogs(tmp_path):
    password_prompt(tmp_path,True)


def test_hosted_masked_password_and_text_dialogs(tmp_path):
    password_prompt(tmp_path,False)


def test_local_modal_keyboard_nested_busy_and_portal(tmp_path):
    modal_keyboard(tmp_path,True)


def test_hosted_modal_keyboard_nested_busy_and_portal(tmp_path):
    modal_keyboard(tmp_path,False)


def test_local_project_menu_touch_hover_and_keyboard(tmp_path):
    touch_menu(tmp_path,True)


def test_hosted_project_menu_touch_hover_and_keyboard(tmp_path):
    touch_menu(tmp_path,False)


def test_local_voice_pack_popup_keyboard_scope(tmp_path):
    voice_library_portal(tmp_path,True)


def test_hosted_voice_pack_popup_keyboard_scope(tmp_path):
    voice_library_portal(tmp_path,False)


def submenu_hover_preserves_keyboard_focus(home,local):
    with browser(home,local=local) as (_,page,_):
        page.locator('#brandButton').press('Enter')
        page.locator('#projectMenuSwitch').focus();page.keyboard.press('ArrowRight')
        expect(page.locator('#projectSubmenu button:not(:disabled)').first).to_be_focused()
        page.evaluate('()=>{window.focusedSubmenuItem=document.activeElement;document.querySelector("#projectMenuSwitch").dispatchEvent(new PointerEvent("pointerenter",{pointerType:"mouse",buttons:0}))}')
        assert page.evaluate('()=>focusedSubmenuItem.isConnected&&document.activeElement===focusedSubmenuItem')
        expect(page.locator('#projectSubmenu button:not(:disabled)').first).to_be_focused()
        page.keyboard.press('ArrowLeft');expect(page.locator('#projectMenuSwitch')).to_be_focused()
        expect(page.locator('#projectMenuSwitch')).to_have_attribute('aria-expanded','false')


def test_local_submenu_hover_preserves_live_keyboard_focus(tmp_path):
    submenu_hover_preserves_keyboard_focus(tmp_path,True)


def test_hosted_submenu_hover_preserves_live_keyboard_focus(tmp_path):
    submenu_hover_preserves_keyboard_focus(tmp_path,False)
