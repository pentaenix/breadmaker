"""Targets Save commits pending progress and settings in one durable round trip."""

from playwright.sync_api import expect

from tests.test_v10569_narration_browser import browser
from tests.test_v10580_targets_browser import open_targets


def exercise(tmp_path, local):
    with browser(tmp_path, local=local) as (_, page, _):
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.evaluate('''()=>{HTMLMediaElement.prototype.play=function(){throw Error('unexpected audio')}}''')
        open_targets(page)
        page.get_by_role('switch', name='Enable targets', exact=True).click()
        page.locator('#targetsAdd').click()
        page.locator('[data-target-field="amount"]').fill('10')
        page.locator('#targetsSave').click()
        expect(page.locator('#modal')).to_be_hidden()

        open_targets(page)
        page.evaluate('()=>targetsEnqueue({words:1},Date.now())')
        requests = []
        page.on('request', lambda request: requests.append(request.method)
                if '/api/ui-state/targets-v1' in request.url else None)
        page.locator('#targetsSave').click()
        expect(page.locator('#modal')).to_be_hidden()
        assert requests.count('GET') == 1, requests
        assert requests.count('PUT') == 1, requests
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length') == 0
        page.reload()
        page.wait_for_function('()=>targets.ready')
        assert page.evaluate('()=>targetsModel.progress(targets.doc).find(p=>p.metric==="words").value') == 1
        assert not errors, errors


def test_targets_save_batches_progress_and_settings_local(tmp_path):
    exercise(tmp_path, True)


def test_targets_save_batches_progress_and_settings_hosted(tmp_path):
    exercise(tmp_path, False)


def test_targets_save_lost_response_keeps_modal_and_recovers_without_double_count(tmp_path):
    with browser(tmp_path, local=True) as (_, page, _):
        page.evaluate('''()=>{HTMLMediaElement.prototype.play=function(){throw Error('unexpected audio')}}''')
        open_targets(page)
        page.get_by_role('switch', name='Enable targets', exact=True).click()
        page.locator('#targetsAdd').click()
        page.locator('[data-target-field="amount"]').fill('10')
        page.locator('#targetsSave').click()
        expect(page.locator('#modal')).to_be_hidden()
        open_targets(page)
        page.evaluate('()=>targetsEnqueue({words:1},Date.now())')
        lost = [False]

        def lose_first_commit_response(route):
            if route.request.method == 'PUT' and not lost[0]:
                lost[0] = True
                assert route.fetch().ok
                route.abort('failed')
            else:
                route.continue_()

        page.route('**/api/ui-state/targets-v1*', lose_first_commit_response)
        page.locator('#targetsSave').click()
        page.wait_for_function("()=>document.querySelector('#targetsError')?.textContent.length>0")
        expect(page.locator('#modal')).to_be_visible()
        assert page.locator('#targetsSave').is_enabled()
        assert page.locator('#targetsCancel').is_enabled()
        page.locator('#targetsSave').click()
        expect(page.locator('#modal')).to_be_hidden()
        page.reload()
        page.wait_for_function('()=>targets.ready')
        assert page.evaluate('()=>targetsModel.progress(targets.doc).find(p=>p.metric==="words").value') == 1
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length') == 0
