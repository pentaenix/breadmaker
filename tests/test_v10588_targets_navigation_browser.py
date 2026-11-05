"""Navigation journals Targets locally instead of beginning revision reads on unload."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10580_targets_browser import add_word_goal, open_targets


def test_targets_pagehide_keeps_journal_and_recovers_after_reopen(tmp_path):
    with browser(tmp_path,local=False,engine='webkit') as (_,page,_):
        add_word_goal(page,10)
        open_targets(page);page.locator('#targetsAdd').click()
        page.locator('[data-target-field="metric"]:visible').select_option('time')
        page.locator('[data-target-field="amount"]:visible').fill('1')
        page.locator('#targetsSave').click();expect(page.locator('#modal')).to_be_hidden()
        page.evaluate('async()=>await flushTargets()')
        requests=[]
        page.on('request',lambda r:requests.append(r.method) if '/api/ui-state/targets-v1' in r.url else None)
        pending=page.evaluate('''async()=>{
          targetsEnqueue({words:3},Date.now());targets.lastTick=Date.now()-1000;window.dispatchEvent(new Event('pagehide'));
          if(targets.saving)await targets.saving;
          const records=await targetsJournal.read(targets.context);return {count:records.length,finalTick:records.some(row=>row.delta?.time>0)};
        }''')
        assert requests==[],requests
        assert pending["count"]>0
        assert pending["finalTick"]
        page.reload();page.wait_for_function("()=>targets.ready&&targetsModel.progress(targets.doc).find(p=>p.metric==='words')?.value===3")
        page.evaluate('async()=>await flushTargets()')
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length')==0
