"""Startup never paints translation keys or partially hydrated editor chrome."""
import re

from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def test_startup_hides_untranslated_chrome_until_real_project_is_ready(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        created=page.request.post(base+'/api/projects',data={'name':'Synthetic startup project'},headers={'Origin':base})
        assert created.ok
        project_id=created.json()['project']['id']
        pending=[]
        page.route('**/api/bootstrap', lambda route: pending.append(route))
        with page.expect_request('**/api/bootstrap'):
            page.goto(base+'/p/'+project_id,wait_until='domcontentloaded',timeout=8000)
        expect(page.locator('#startupScreen')).to_be_visible()
        expect(page).to_have_title('Breadmaker Studio')
        expect(page.locator('#app')).to_be_hidden()
        expect(page.locator('#startupScreen')).to_contain_text('Breadmaker Studio')
        assert '{{i18n:' not in page.locator('#startupScreen').inner_text()
        page.wait_for_function('()=>document.querySelector("#startupProgress").value>=45')
        assert pending
        pending.pop().continue_()
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page.locator('#startupScreen')).to_be_hidden()
        expect(page.locator('#app')).to_be_visible()
        assert page.title()==page.evaluate('()=>state.project.name||state.project.title')


def test_failed_startup_keeps_editor_hidden_and_offers_retry(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        created=page.request.post(base+'/api/projects',data={'name':'Synthetic failed startup'},headers={'Origin':base})
        assert created.ok
        project_id=created.json()['project']['id']
        page.route('**/api/bootstrap',lambda route:route.fulfill(status=503,json={'detail':'Synthetic unavailable'}))
        page.goto(base+'/p/'+project_id,wait_until='domcontentloaded',timeout=8000)
        expect(page.locator('#startupRetry')).to_be_visible()
        expect(page.locator('#app')).to_be_hidden()
        expect(page.locator('#startupMessage')).to_contain_text('Please try again')
        expect(page).to_have_title('Breadmaker Studio')


def test_startup_modal_is_interactive_without_revealing_editor(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        pending=[]
        page.route('**/api/runtime',lambda route:pending.append(route))
        with page.expect_request('**/api/runtime'):
            page.goto(base,wait_until='domcontentloaded',timeout=8000)
        page.evaluate('()=>{showModal("Synthetic startup decision","<p>Continue opening?</p>","<button id=syntheticStartupContinue>Continue</button>");document.getElementById("syntheticStartupContinue").onclick=()=>{window.syntheticStartupContinued=true;closeModal(false)}}')
        expect(page.locator('#startupScreen')).to_be_visible()
        expect(page.locator('#app')).to_be_hidden()
        assert page.locator('#app').evaluate('(el)=>el.inert')
        page.locator('#syntheticStartupContinue').click()
        assert page.evaluate('()=>window.syntheticStartupContinued')
        pending.pop().continue_()
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
