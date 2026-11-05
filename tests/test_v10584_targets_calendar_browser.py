"""Synthetic independent browser stores retain captured time across offsets."""
import json
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10580_targets_browser import open_targets

QUIET="Object.defineProperty(document,'hasFocus',{configurable:true,value:()=>false});HTMLMediaElement.prototype.play=function(){return Promise.resolve()}"


def _calendar(home,local,engine):
    with browser(home,local=local,engine=engine,page_options={'timezone_id':'Asia/Tokyo'}) as (base,page,pid):
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.evaluate('()=>{'+QUIET+'}')
        open_targets(page);page.get_by_role('switch',name='Enable targets',exact=True).click()
        for metric in ['time','active']:
            page.locator('#targetsAdd').click();row=page.locator('[data-target-goal]').last
            row.locator('[data-target-field="metric"]').select_option(metric)
            row.locator('[data-target-field="amount"]').fill('1')
        page.locator('#targetsSave').click();expect(page.locator('#modal')).to_be_hidden()
        # Captured records represent activity at controlled future instants, not
        # real-time or physical-device acceptance. Both instants have the same
        # local date but the second falls outside the first offset's UTC window.
        day=page.evaluate('()=>{const d=new Date();return Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),d.getUTCDate()+2)}')
        context=page.context.browser.new_context(timezone_id='America/Los_Angeles')
        context.add_init_script('sessionStorage.setItem("breadmaker-client-id",'+json.dumps(page.evaluate('CLIENT_ID'))+');'+QUIET)
        other=context.new_page();other.on('pageerror',lambda e:errors.append(str(e)))
        try:
            other.goto(base+'/p/'+pid);other.wait_for_function('()=>targets.ready')
            def contribute(surface,instant):
                return surface.evaluate('''async instant=>{
                  Date.now=()=>instant+10000;targets.lastTick=Date.now();
                  const goals=structuredClone(targets.doc.goals),calendar=Object.fromEntries(goals.map(g=>[g.id,targetsModel.period(g.period,instant)]));
                  await targetsJournal.enqueue(targets.context,{id:uid(),at:instant+10000,delta:{time:10,active:10},goals,calendar,intervals:{time:[instant,instant+10000],active:[instant,instant+10000]}});
                  await flushTargets();return calendar[goals[0].id].key;
                }''',instant)
            key=contribute(page,day)
            assert contribute(other,day+28*3600000)==key
            for surface in [page,other]:
                surface.evaluate('()=>flushTargets()')
                result=surface.evaluate('''async key=>{const saved=targetsModel.normalize((await targetsRequest()).value);return {values:saved.goals.map(g=>saved.entries[`${g.id}:${key}`].value),sources:Object.keys(saved.receipts).length,pending:(await targetsJournal.read(targets.context)).length}}''',key)
                assert result=={'values':[20,20],'sources':2,'pending':0},result
                expect(surface.locator('#targetsFooter')).to_contain_text('0:20/1:00')
            assert not errors,errors
        finally:context.close()


def test_targets_captured_calendar_offset_roundtrip_local(tmp_path):_calendar(tmp_path,True,'chromium')
def test_targets_captured_calendar_offset_roundtrip_hosted_webkit(tmp_path):_calendar(tmp_path,False,'webkit')
