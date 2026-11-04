"""Targets use real local/hosted personal UI state; audio remains silent."""
import pytest
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser

def open_targets(page):
    page.locator('#brandButton').click()
    page.locator('#projectMenuTargets').click()
    expect(page.locator('#targetsForm')).to_be_visible()


def add_word_goal(page,amount=100):
    open_targets(page)
    page.get_by_role('switch',name='Enable targets',exact=True).click()
    page.locator('#targetsAdd').click()
    page.locator('[data-target-field="amount"]').fill(str(amount))
    page.locator('#targetsSave').click()
    expect(page.locator('#modal')).to_be_hidden()


def write_more(page,text):
    page.locator('.manuscript-block').first.click()
    page.keyboard.press('End')
    page.keyboard.insert_text(text)


@pytest.mark.parametrize('local',[True,False],ids=['local','hosted'])
def test_targets_optional_create_write_complete_persist_disable(tmp_path,local):
    with browser(tmp_path,local=local) as (base,page,pid):
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        if not local:
            mismatch=page.request.put(base+'/api/ui-state/targets-v1',data={'value':{'enabled':True}},headers={'Origin':base,'X-Breadmaker-Project':pid,'X-Breadmaker-Targets-Account':'usr_'+'9'*32})
            assert mismatch.status==409 and mismatch.json()['detail']=='TARGETS_ACCOUNT_CONTEXT_CHANGED'
        page.evaluate('()=>{window.targetSoundPlays=0;HTMLMediaElement.prototype.play=function(){window.targetSoundPlays++;return Promise.resolve()}}')
        open_targets(page)
        expect(page.locator('#targetsOptions')).to_be_hidden()
        expect(page.locator('#targetsTop')).to_be_hidden()
        page.get_by_role('switch',name='Enable targets',exact=True).click()
        page.locator('#targetsAdd').click()
        page.locator('[data-target-field="amount"]').fill('2')
        page.locator('#targetsSave').click()
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('#targetsTop')).to_be_visible()
        block=page.locator('.manuscript-block').first
        block.click();page.keyboard.press('End');page.keyboard.insert_text(' bright harbor')
        page.wait_for_function('()=>targetsModel.progress(targets.doc)[0]?.achieved>0')
        assert page.locator('#targetsTop').get_attribute('aria-label')=='Daily · Daily goal achieved'
        assert page.evaluate('window.targetSoundPlays')==0
        page.evaluate('async()=>{await flushSave();await flushTargets()}')
        page.reload();page.wait_for_function('()=>targets.ready')
        expect(page.locator('#targetsTop')).to_be_visible()
        assert page.evaluate('()=>targetsModel.progress(targets.doc)[0].achieved>0')
        open_targets(page)
        expect(page.locator('.targets-history-row .targets-check')).to_have_count(1)
        page.get_by_role('switch',name='Enable targets',exact=True).click()
        page.locator('#targetsSave').click()
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('#targetsTop')).to_be_hidden()
        expect(page.locator('#targetsFooter')).to_be_hidden()
        assert page.evaluate('()=>Object.keys(targets.doc.entries).length')==1
        assert not errors,errors


@pytest.mark.parametrize('width',[390,820,1360])
def test_targets_modal_follows_manuscript_mobile_and_narrow_layout(tmp_path,width):
    with browser(tmp_path,local=True,page_options={'viewport':{'width':width,'height':844}}) as (_,page,_):
        open_targets(page)
        page.get_by_role('switch',name='Enable targets',exact=True).click()
        for _ in range(4):page.locator('#targetsAdd').click()
        page.get_by_role('switch',name='Chime',exact=True).click()
        assert page.locator('#targetsSound option').all_text_contents()==['Moss','Fern','Oak','Palm','Monstera','Pothos']
        for selector in ['.modal-card','#targetsSave','[data-target-goal]']:
            for box in page.locator(selector).all():
                r=box.bounding_box();assert r['x']>=0 and r['x']+r['width']<=width
        assert page.evaluate('()=>document.documentElement.scrollWidth===innerWidth')
        page.keyboard.press('Escape')
        expect(page.locator('#modal')).to_be_hidden()
        assert page.evaluate('()=>targets.doc.enabled') is False,'dismiss must not activate a draft'


def test_targets_response_loss_retry_preserves_subsequent_writing(tmp_path):
    with browser(tmp_path,local=True) as (_,page,_):
        add_word_goal(page)
        write_more(page,' one two')
        lost=[False]
        def lose_response(route):
            if route.request.method=='PUT' and not lost[0]:
                lost[0]=True
                response=route.fetch()
                assert response.ok
                route.abort('failed')
            else:route.continue_()
        page.route('**/api/ui-state/targets-v1*',lose_response)
        assert page.evaluate('async()=>{try{await flushTargets();return false}catch{return true}}')
        assert page.evaluate('()=>targets.pending[0].sealed')
        write_more(page,' three four')
        assert page.evaluate('()=>targets.pending.length')==2
        page.evaluate('async()=>await flushTargets()')
        assert page.evaluate('()=>targetsModel.progress(targets.doc)[0].value')==4
        assert page.evaluate('()=>targets.pending.length')==0


def test_targets_optional_banner_and_chime_fire_once_per_achievement(tmp_path):
    with browser(tmp_path,local=True) as (_,page,_):
        page.evaluate('()=>{window.targetSoundPlays=0;HTMLMediaElement.prototype.play=function(){window.targetSoundPlays++;return Promise.resolve()}}')
        open_targets(page)
        page.get_by_role('switch',name='Enable targets',exact=True).click()
        page.locator('#targetsAdd').click()
        page.locator('[data-target-field="amount"]').fill('2')
        page.get_by_role('switch',name='In-app banner',exact=True).click()
        page.get_by_role('switch',name='Chime',exact=True).click()
        page.locator('#targetsSave').click();expect(page.locator('#modal')).to_be_hidden()
        write_more(page,' one two')
        expect(page.locator('#toast')).to_have_text('Daily goal achieved')
        assert page.evaluate('window.targetSoundPlays')==1
        write_more(page,' three four')
        assert page.evaluate('window.targetSoundPlays')==1
        page.locator('#targetsTop').hover()
        assert page.locator('#targetsTopTip').is_hidden()
        expect(page.locator('#targetsTopTip')).to_be_visible()
        box=page.locator('#targetsTopTip').bounding_box()
        assert box['x']>=0 and box['y']>=0


def test_targets_manuscript_undo_redo_and_custom_paragraph_changes(tmp_path):
    with browser(tmp_path,local=True) as (_,page,_):
        add_word_goal(page)
        write_more(page,' one two')
        assert page.evaluate('()=>targetsModel.progress(targets.doc)[0].value')==2
        page.evaluate('async()=>await appUndo()')
        assert page.evaluate('()=>targetsModel.progress(targets.doc)[0].value')==0
        page.evaluate('async()=>await appRedo()')
        assert page.evaluate('()=>targetsModel.progress(targets.doc)[0].value')==2
        page.locator('.manuscript-block').first.click()
        page.keyboard.press('End');page.keyboard.press('Enter');page.keyboard.insert_text('new paragraph')
        assert page.evaluate('()=>targetsModel.progress(targets.doc)[0].value')==4


def test_targets_activity_uses_workspaces_thinking_grace_and_window_focus(tmp_path):
    with browser(tmp_path,local=True) as (_,page,_):
        open_targets(page)
        page.get_by_role('switch',name='Enable targets',exact=True).click()
        for metric in ['time','active']:
            page.locator('#targetsAdd').click()
            row=page.locator('[data-target-goal]').last
            row.locator('[data-target-field="metric"]').select_option(metric)
            row.locator('[data-target-field="amount"]').fill('1')
        page.locator('#targetsSave').click();expect(page.locator('#modal')).to_be_hidden()
        page.locator('.main-tab[data-view="canvas"]').click()
        assert page.evaluate('()=>Date.now()-targets.lastActivity<1000')
        def tick(activity):
            return page.evaluate('''activity=>{const values=()=>targetsModel.progress(targets.doc).map(p=>p.value),before=values(),now=Date.now();targets.lastTick=now-1000;targets.lastActivity=now-activity;targetsClock(now);return values().map((v,i)=>v-before[i])}''',activity)
        assert tick(5000)==pytest.approx([1,1]),'short thinking pause counts as active work'
        assert tick(60000)==pytest.approx([1,0]),'long pause counts only foreground time'
        # Headless Chromium can report focus in multiple offscreen windows.
        # Drive the real blur cleanup, then control that browser API boundary.
        page.evaluate("()=>{window.dispatchEvent(new Event('blur'));Object.defineProperty(document,'hasFocus',{configurable:true,value:()=>false})}")
        assert page.evaluate('()=>targets.lastActivity')==0
        assert tick(5000)==pytest.approx([0,0]),'an unfocused app cannot accrue either kind of time'
