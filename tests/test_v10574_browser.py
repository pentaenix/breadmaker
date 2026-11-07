"""Hosted project Trash uses reversible owner operations, never global purge."""
import re

from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser
from tests.test_v10572_browser import project


def open_delete(page):
    page.locator('#brandButton').click()
    page.locator('#projectMenuSettings').click()
    page.locator('#settingsDeleteProject').click()
    expect(page.locator('#dialogConfirm')).to_be_visible()


def test_hosted_trash_cancel_then_save_delete_and_restore_through_actual_ui(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        pid = project(base, page)
        calls = []
        page.on('request', lambda req: calls.append((req.method, req.url)))
        text = 'Disposable pending writing must return from Trash.'
        page.locator('.manuscript-block').first.fill(text)
        page.evaluate('()=>clearTimeout(state.saveTimer)')
        open_delete(page)
        page.locator('#dialogCancel').click()
        expect(page.locator('#settingsDeleteProject')).to_be_visible()
        assert page.url.endswith('/p/' + pid)
        assert not any(method == 'DELETE' for method, _ in calls)
        page.locator('#settingsDeleteProject').click()
        page.locator('#dialogConfirm').click()
        code = page.locator('.danger-confirm-code').inner_text()
        assert len(code) == 5
        expect(page.locator('#dangerCodeConfirm')).to_be_disabled()
        page.locator('#dangerConfirmInput').fill(code[:4])
        expect(page.locator('#dangerCodeConfirm')).to_be_disabled()
        assert not any(method == 'DELETE' for method, _ in calls)
        page.locator('#dangerConfirmInput').fill(code)
        with page.expect_navigation(wait_until='domcontentloaded', timeout=6000):
            page.locator('#dangerCodeConfirm').click()
        page.wait_for_url(re.compile(r'/p/[0-9a-f]{32}$'), timeout=5000)
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        assert not page.url.endswith('/p/' + pid)
        workspace = page.request.get(base + '/api/workspace').json()
        assert pid not in {row['id'] for row in workspace['projects']}
        trashed = next(row for row in workspace['trash'] if row['id'] == pid)
        assert trashed['trash_id'] == pid
        assert page.request.get(base + '/api/chapters/chapter_1',
            headers={'X-Breadmaker-Project': pid}).status == 404
        page.locator('#brandButton').click()
        page.locator('#projectMenuSettings').click()
        page.locator('#settingsTrash').click()
        with page.expect_navigation(wait_until='domcontentloaded', timeout=6000):
            page.locator(f'[data-trash-restore="{pid}"]').click()
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        assert page.url.endswith('/p/' + pid)
        expect(page.locator('.manuscript-block').first).to_have_text(text)
        page.locator('.manuscript-block').first.fill(text + ' Restored safely.')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight', timeout=5000)
        saved = page.request.get(base + '/api/chapters/chapter_1', headers={'X-Breadmaker-Project': pid})
        assert saved.ok and saved.json()['blocks'][0]['text_content'] == text + ' Restored safely.'
        assert len([url for method, url in calls if method == 'DELETE']) == 1
        assert not any('/api/danger/' in url for _, url in calls)


def test_failed_save_before_hosted_trash_preserves_conflict_draft_and_does_not_delete(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        pid = project(base, page)
        deletes = []
        page.on('request', lambda req: deletes.append(req.url) if req.method == 'DELETE' else None)
        page.route('**/api/chapters/*/blocks', lambda route: route.fulfill(
            status=409, json={'detail': 'HOSTED_GENERATION_CONFLICT'}))
        text = 'Disposable conflicted writing must stay in this window.'
        page.locator('.manuscript-block').first.fill(text)
        page.evaluate('()=>clearTimeout(state.saveTimer)')
        open_delete(page)
        page.locator('#dialogConfirm').click()
        page.locator('#dangerConfirmInput').fill(page.locator('.danger-confirm-code').inner_text())
        page.locator('#dangerCodeConfirm').click()
        expect(page.locator('#conflictKeep')).to_be_visible()
        expect(page.locator('.manuscript-block').first).to_have_text(text)
        assert page.evaluate('()=>state.dirty')
        assert not deletes and page.url.endswith('/p/' + pid)
        assert pid in {row['id'] for row in page.request.get(base + '/api/workspace').json()['projects']}


def test_denied_hosted_trash_keeps_the_current_project_and_saved_text(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        pid = project(base, page)
        text = 'Disposable saved writing remains after a rejected deletion.'
        page.locator('.manuscript-block').first.fill(text)
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight', timeout=5000)
        page.route('**/api/projects/*', lambda route: route.fulfill(
            status=404, json={'detail': 'PROJECT_NOT_FOUND'})
            if route.request.method == 'DELETE' else route.continue_())
        open_delete(page)
        page.locator('#dialogConfirm').click()
        page.locator('#dangerConfirmInput').fill(page.locator('.danger-confirm-code').inner_text())
        page.locator('#dangerCodeConfirm').click()
        expect(page.get_by_text('PROJECT_NOT_FOUND', exact=True).first).to_be_visible()
        assert page.url.endswith('/p/' + pid)
        expect(page.locator('.manuscript-block').first).to_have_text(text)
        assert not page.locator('#manuscriptPrimaryPane').evaluate('(node)=>node.inert')
        assert pid in {row['id'] for row in page.request.get(base + '/api/workspace').json()['projects']}


def test_hosted_delete_all_still_rejects_global_challenge_and_purge(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        pid = project(base, page)
        deletes = []
        page.on('request', lambda req: deletes.append(req.url) if req.method == 'DELETE' else None)
        page.locator('#brandButton').click()
        page.locator('#projectMenuSettings').click()
        page.locator('#settingsDeleteAll').click()
        with page.expect_response('**/api/danger/challenge') as response:
            page.locator('#dialogConfirm').click()
        assert response.value.status == 409
        assert response.value.json()['detail'] == 'HOSTED_ACCOUNT_DELETION_UNAVAILABLE_UNTIL_M9'
        denied = page.request.post(base + '/api/danger/delete-all-data', data={})
        assert denied.status == 409 and denied.json()['detail'] == 'HOSTED_ACCOUNT_DELETION_UNAVAILABLE_UNTIL_M9'
        assert not deletes
        assert pid in {row['id'] for row in page.request.get(base + '/api/workspace').json()['projects']}
