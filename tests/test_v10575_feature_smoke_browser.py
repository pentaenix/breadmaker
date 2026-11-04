"""Small real-UI paths shared by the fast smoke and full browser discovery."""
import re

from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser


def _reopen(page):
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"&&!!document.querySelector(".manuscript-block")')


def _bookmark_roundtrip(tmp_path, *, local):
    with browser(tmp_path, local=local) as (base, page, pid):
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.locator('.chapter-row').first.dblclick()
        page.wait_for_function('()=>state.viewMode==="chapter"&&!!state.chapter')
        chapter = page.evaluate('()=>state.chapter.id')
        text = page.locator('.manuscript-block').first.inner_text()
        page.locator('.chapter-more').first.click()
        with page.expect_response(lambda r: '/api/bookmark' in r.url and r.request.method == 'PUT') as response:
            page.get_by_role('button', name='Bookmark', exact=True).click()
        assert response.value.ok, (response.value.status, response.value.text())
        page.wait_for_function('()=>!!state.bookmark')
        _reopen(page)
        page.wait_for_function('()=>!!state.bookmark')
        assert str(page.evaluate('()=>state.bookmark.chapter_id')) == str(chapter)
        page.locator('#bookmarkBtn').click(button='right')
        with page.expect_response(lambda r: '/api/bookmark' in r.url and r.request.method == 'DELETE') as response:
            page.get_by_role('button', name='Remove Bookmark', exact=True).click()
        assert response.value.ok, (response.value.status, response.value.text())
        page.wait_for_function('()=>!state.bookmark')
        _reopen(page)
        assert page.request.get(base+'/api/bookmark', headers={'X-Breadmaker-Project': pid}).json()['bookmark'] is None
        expect(page.locator('#bookmarkBtn')).to_be_hidden()
        expect(page.locator('.manuscript-block').first).to_have_text(text)
        assert not errors, errors


def test_feature_smoke_local_bookmark_create_remove_and_reopen(tmp_path):
    _bookmark_roundtrip(tmp_path, local=True)


def test_feature_smoke_hosted_bookmark_create_remove_and_reopen(tmp_path):
    _bookmark_roundtrip(tmp_path, local=False)


def _marker_and_save_roundtrip(tmp_path, *, local):
    with browser(tmp_path, local=local) as (_, page, _):
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        text = 'Synthetic smoke manuscript. Café 😀 survives marker changes.'
        page.locator('.manuscript-block').first.fill(text)
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        page.locator('#markupBtn').click()
        page.get_by_test_id('marker-empty-new').click()
        page.locator('#createMarkerConfirm').click()
        expect(page.locator('#markerName')).to_be_visible()
        expect(page.locator('#markerName')).to_have_value('')
        page.locator('#markerName').fill('Smoke continuity')
        with page.expect_response(lambda r: '/api/labels' in r.url and r.request.method == 'POST') as response:
            page.locator('#createMarkerConfirm').click()
        assert response.value.ok, (response.value.status, response.value.text())
        page.wait_for_function('()=>state.markers.some(m=>m.name==="Smoke continuity")')
        _reopen(page)
        assert page.evaluate('()=>state.markers.some(m=>m.name==="Smoke continuity")')
        expect(page.locator('.manuscript-block').first).to_have_text(text)
        page.locator('#markupBtn').click()
        page.locator('[data-marker-edit]').first.click()
        page.get_by_role('button', name='Delete', exact=True).click()
        with page.expect_response(lambda r: '/api/labels/' in r.url and r.request.method == 'DELETE') as response:
            page.locator('#dialogConfirm').click()
        assert response.value.ok, (response.value.status, response.value.text())
        page.wait_for_function('()=>state.markers.length===0')
        _reopen(page)
        assert page.evaluate('()=>state.markers.length===0')
        expect(page.locator('.manuscript-block').first).to_have_text(text)
        assert not errors, errors


def test_feature_smoke_local_marker_validation_save_remove_and_reopen(tmp_path):
    _marker_and_save_roundtrip(tmp_path, local=True)


def test_feature_smoke_hosted_marker_validation_save_remove_and_reopen(tmp_path):
    _marker_and_save_roundtrip(tmp_path, local=False)


def test_feature_smoke_local_voice_selection_generation_stop_and_reopen(tmp_path):
    with browser(tmp_path, local=True) as (_, page, _):
        page.locator('#narrationBtn').click()
        page.wait_for_function('()=>!narration.loading&&narration.project===currentProjectIdentity()')
        page.locator('#narrationVoice').select_option('am_michael')
        page.wait_for_function('()=>narrationPreferences.value.voice==="am_michael"')
        page.locator('#narrationScopeOptions').click()
        page.get_by_test_id('narration-generate-only').click()
        page.wait_for_function('()=>!narration.busy&&!narration.queueLoading&&narration.records.length>0')
        assert page.evaluate('()=>narration.records.every(r=>r.voice==="am_michael")')
        before = page.evaluate('()=>window.syntheticGenerations')
        held = []
        page.route(re.compile(r'/api/book(?:\?|$)'), lambda route: held.append(route))
        try:
            with page.expect_request(re.compile(r'/api/book(?:\?|$)')):
                page.locator('#narrationChapter').click()
            expect(page.locator('#narrationProgress')).to_be_visible()
            page.locator('#narrationStop').click()
        finally:
            for route in held:
                route.continue_()
            page.unroute(re.compile(r'/api/book(?:\?|$)'))
        page.wait_for_function('()=>!narration.queueLoading')
        assert page.evaluate('()=>window.syntheticGenerations') == before
        _reopen(page)
        page.locator('#narrationBtn').click()
        page.wait_for_function('()=>!narration.loading')
        expect(page.locator('#narrationVoice')).to_have_value('am_michael')
        assert page.evaluate('()=>narration.records.length>0&&narration.records.every(r=>r.voice==="am_michael")')
