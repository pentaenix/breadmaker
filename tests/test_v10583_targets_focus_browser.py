"""Quiet Targets remain usable in the established Manuscript focus workspace."""
import re
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10580_targets_browser import add_word_goal,write_more


def _focus(home,local,engine,width):
    with browser(home,local=local,engine=engine,page_options={'viewport':{'width':width,'height':844}}) as (_,page,_):
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.evaluate('()=>{window.targetSoundPlays=0;HTMLMediaElement.prototype.play=function(){targetSoundPlays++;return Promise.resolve()}}')
        add_word_goal(page,2)
        expect(page.locator('#targetsTop')).to_be_visible()
        expect(page.locator('#targetsFooter')).to_be_visible()
        expect(page.locator('#targetsFocus')).to_be_hidden()
        page.locator('#viewBtn').click();page.locator('#viewFocusBtn').click()
        expect(page.locator('body')).to_have_class(re.compile(r'\bfocus\b'))
        focus=page.locator('#targetsFocus')
        expect(focus).to_be_visible()
        expect(page.locator('#targetsTop')).to_be_hidden()
        expect(page.locator('#targetsFooter')).to_be_hidden()
        expect(focus.locator('.targets-ring')).to_have_count(0)
        box=focus.bounding_box()
        assert box['x']>=0 and box['x']+box['width']<=width and box['y']+box['height']<=844,box
        write_more(page,' one two')
        expect(focus.locator('.targets-check')).to_be_visible()
        assert page.evaluate('()=>targetSoundPlays')==0
        focus.click();expect(page.locator('#targetsForm')).to_be_visible()
        expect(page.locator('[data-target-field="amount"]')).to_have_value('2')
        page.locator('#targetsCancel').click();expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('body')).to_have_class(re.compile(r'\bfocus\b'))
        page.keyboard.press('Escape')
        expect(focus).to_be_hidden()
        expect(page.locator('#targetsTop')).to_be_visible()
        expect(page.locator('#targetsFooter')).to_be_visible()
        assert not errors,errors


def test_targets_quiet_focus_workspace_local_desktop(tmp_path):_focus(tmp_path,True,'chromium',1360)
def test_targets_quiet_focus_workspace_hosted_phone_webkit(tmp_path):_focus(tmp_path,False,'webkit',390)
