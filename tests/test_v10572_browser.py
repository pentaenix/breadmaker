"""Hosted rename saves pending writing, then obtains a fresh authorized baseline."""
import re

from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def project(base, page):
    page.goto(base, wait_until='domcontentloaded', timeout=8000)
    page.wait_for_url(re.compile(r'/p/[0-9a-f]{32}$'), timeout=5000)
    page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
    return page.url.rsplit('/', 1)[-1]


def test_hosted_menu_rename_flushes_pending_draft_and_reopens_current_write_revision(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        pid = project(base, page)
        before = 'Synthetic writing before rename'
        page.locator('.manuscript-block').first.fill(before)
        page.evaluate('()=>clearTimeout(state.saveTimer)')  # Leave a deliberate unsaved draft.
        held = []
        page.route('**/api/projects/*', lambda route: held.append(route)
                   if route.request.method == 'PATCH' else route.continue_())
        page.locator('#brandButton').click()
        page.locator('#projectMenuRename').click()
        page.locator('#projectMenuRenameInput').fill('Synthetic safely renamed project')
        page.locator('#projectMenuRenameInput').press('Enter')
        page.wait_for_function('()=>document.body.classList.contains("manuscript-mutating")&&!state.dirty')
        assert len(held) == 1
        cid = page.locator('.manuscript-block-wrap').first.get_attribute('data-chapter-id')
        saved = page.request.get(base + '/api/chapters/' + cid, headers={'X-Breadmaker-Project': pid})
        assert saved.ok and saved.json()['blocks'][0]['text_content'] == before
        page.locator('#manuscriptPaper').evaluate('(node)=>node.focus()')
        page.keyboard.insert_text('must not enter the old snapshot')
        expect(page.locator('.manuscript-block').first).to_have_text(before)
        with page.expect_navigation(wait_until='domcontentloaded', timeout=6000):
            held.pop().continue_()
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page).to_have_title('Synthetic safely renamed project')
        expect(page.locator('.manuscript-block').first).to_have_text(before)
        page.locator('.manuscript-block').first.fill(before + ' and after rename')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight', timeout=5000)
        assert page.locator('#reloadLease').count() == 0


def test_hosted_settings_rename_also_reopens_before_more_writing(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        project(base, page)
        page.locator('#brandButton').click()
        page.locator('#projectMenuSettings').click()
        page.locator('#settingsRenameProject').click()
        page.locator('#renameProjectName').fill('Synthetic settings rename')
        with page.expect_navigation(wait_until='domcontentloaded', timeout=6000):
            page.locator('#renameProjectConfirm').click()
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page).to_have_title('Synthetic settings rename')
        page.locator('.manuscript-block').first.fill('Synthetic after settings rename')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight', timeout=5000)


def test_hosted_generation_conflict_preserves_draft_and_does_not_claim_lease_loss(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        project(base, page)
        page.route('**/api/chapters/*/blocks', lambda route: route.fulfill(
            status=409, json={'detail': 'HOSTED_GENERATION_CONFLICT'}))
        page.locator('.manuscript-block').first.fill('Synthetic conflict must stay visible')
        expect(page.locator('#conflictKeep')).to_be_visible()
        expect(page.locator('#conflictReload')).to_be_visible()
        expect(page.locator('.manuscript-block').first).to_have_text('Synthetic conflict must stay visible')
        assert page.locator('#reloadLease').count() == 0
        assert page.evaluate('()=>state.dirty')


def test_denied_fresh_start_after_rename_cannot_reinstall_old_editable_text(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        project(base, page)
        page.route(re.compile(r'/api/bootstrap(?:\?|$)'), lambda route: route.fulfill(
            status=403, json={'detail': 'PROJECT_PERMISSION_DENIED'}))
        page.locator('#brandButton').click()
        page.locator('#projectMenuRename').click()
        page.locator('#projectMenuRenameInput').fill('Synthetic denied after rename')
        with page.expect_navigation(wait_until='domcontentloaded', timeout=6000):
            page.locator('#projectMenuRenameInput').press('Enter')
        expect(page.locator('#startupRetry')).to_be_visible()
        expect(page.locator('#app')).to_be_hidden()
