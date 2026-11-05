"""Nested history is read-only and truthful; no physical audio output."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10580_targets_browser import open_targets


def test_empty_history_and_always_banner_preserve_parent(tmp_path):
    with browser(tmp_path, local=True) as (_, page, _):
        page.evaluate('''async()=>{
          await loadTargets(); const doc=targetsModel.defaults();doc.enabled=true;doc.banner=false;doc.chime=false;
          const response=await targetsRequest();await targetsRequest({method:'PUT',body:JSON.stringify({value:doc,expected_revision:response.revision})});
          targets.ready=false;await loadTargets();HTMLMediaElement.prototype.play=()=>{throw Error('unexpected audio')};
        }''')
        open_targets(page)
        assert page.locator('[data-target-toggle=banner]').count()==0
        page.locator('#targetsHistoryToggle').click()
        expect(page.locator('#modalTitle')).to_have_text('History')
        for period in ('daily','weekly','monthly'):
            page.locator('#targetsHistoryPeriod').select_option(period)
            assert page.locator('.targets-history-cell').count()==0
            expect(page.locator('#targetsHistoryList')).to_contain_text('Your progress will appear here.')
        page.locator('#targetsHistoryClose').click()
        expect(page.locator('#targetsHistoryToggle')).to_be_focused()
        page.locator('#targetsSave').click();expect(page.locator('#modal')).to_be_hidden()
        assert page.evaluate('()=>targets.doc.banner') is True
        page.evaluate('''()=>{window.bannerCalls=[];toast=(message)=>bannerCalls.push(message);targets.doc.banner=false;targetsCelebrate([{period:'daily',metric:'words'}])}''')
        assert page.evaluate('bannerCalls.length')==1
