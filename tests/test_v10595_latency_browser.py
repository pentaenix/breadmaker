"""Opening hosted settings never waits for unrelated project/trash enumeration."""
from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def test_loaded_hosted_project_settings_open_without_catalog_request(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        made = page.request.post(base + '/api/projects', data={'name': 'Synthetic settings latency'}, headers={'Origin': base})
        pid = made.json()['project']['id']
        page.goto(base + '/p/' + pid, wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        requests = []
        def unavailable(route):
            requests.append(route.request.url)
            route.fulfill(status=503, json={'detail': 'Synthetic catalog outage'})
        page.route('**/api/workspace', unavailable)
        for width in (1200, 780):
            page.set_viewport_size({'width': width, 'height': 800})
            page.evaluate('()=>{void openProjectSettings()}')
            expect(page.locator('#settingsRenameProject')).to_be_visible()
            expect(page.locator('#modal')).to_contain_text('Synthetic settings latency')
            page.evaluate('()=>closeModal()')
        assert requests == []
        # If bootstrap data is absent, retain the recovery/fallback fetch rather
        # than silently presenting an invented active project.
        page.evaluate('()=>{state.workspace=null;void openProjectSettings()}')
        page.wait_for_function('()=>document.querySelector("#modal").classList.contains("hidden")')
        page.wait_for_timeout(50)
        assert len(requests) == 1
