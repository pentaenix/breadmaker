"""Hosted voice choices and overlapping narration interactions stay responsive."""
import re

from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser


def open_panel(page):
    page.locator('#narrationBtn').click()
    page.wait_for_function('()=>!narration.loading&&narration.project===currentProjectIdentity()')


def test_hosted_uncached_visible_voice_can_be_selected_and_used_without_server_install(tmp_path):
    with browser(tmp_path) as (base, page, pid):
        open_panel(page)
        assert page.evaluate('()=>narrationVoiceCatalog.value.voices.every(v=>v.installed===false)')
        assert page.locator('#narrationVoice option[value="am_michael"]').is_enabled()
        page.locator('#narrationVoice').select_option('am_michael')
        page.wait_for_function('()=>narrationPreferences.value.voice==="am_michael"')
        saved = page.request.get(base + '/api/ui-state/narration-preferences',
            headers={'X-Breadmaker-Project': pid}).json()['value']
        assert saved['voice'] == 'am_michael'
        # Use the real pinned public transport; inference itself is the existing
        # synthetic engine seam, never a provider/model download in this test.
        page.evaluate('''()=>{const engine=narrationEngine(),original=engine.generate.bind(engine);
          engine.generate=async(...args)=>{const snapshot=narrationSnapshot();
            await (await narrationDevice(snapshot)).downloadVoice(narrationVoiceCatalog.value.voices.find(v=>v.id===args[1]));
            return original(...args)}}''')
        page.locator('#narrationScopeOptions').click()
        page.get_by_test_id('narration-generate-only').click()
        page.wait_for_function('()=>!narration.busy&&!narration.queueLoading&&narration.records.length>0')
        assert page.evaluate('()=>narration.records.every(r=>r.voice==="am_michael")')
        assert page.evaluate('()=>document.querySelector("#narrationVoice").value') == 'am_michael'


def test_playing_retained_record_while_panel_reloads_cannot_leave_controls_locked(tmp_path):
    with browser(tmp_path) as (_, page, _):
        open_panel(page)
        page.evaluate('''async()=>await generateNarrations([narrationSource(document.querySelector('.manuscript-block'))],
            false,{generateOnly:true})''')
        expect(page.locator('.narration-recording')).to_have_count(1)
        page.locator('#closeNarrationPanel').click()
        held = []
        page.route(re.compile(r'/api/narrations$'), lambda route: held.append(route)
            if route.request.method == 'GET' else route.continue_())
        with page.expect_request(re.compile(r'/api/narrations$')):
            page.locator('#narrationBtn').click()
        page.locator('.narration-recording-main').click()
        page.wait_for_function('()=>narration.selected&&narration.url')
        assert len(held) == 1
        held.pop().continue_()
        page.wait_for_function('()=>!narration.loading', timeout=5000)
        expect(page.locator('#narrationVoice')).to_be_enabled()
        expect(page.locator('#narrationChapter')).to_be_enabled()


def test_chapter_preparation_shows_progress_and_stop_prevents_late_generation(tmp_path):
    with browser(tmp_path) as (_, page, _):
        open_panel(page)
        held = []
        page.route(re.compile(r'/api/book(?:\?|$)'), lambda route: held.append(route))
        with page.expect_request(re.compile(r'/api/book(?:\?|$)')):
            page.locator('#narrationChapter').click()
        expect(page.locator('#narrationProgress')).to_be_visible()
        expect(page.locator('#narrationProgressLabel')).to_have_text('Preparing narration…')
        assert page.locator('#narrationProgressBar').get_attribute('value') is None
        page.locator('#narrationStop').click()
        assert len(held) == 1
        held.pop().continue_()
        page.wait_for_function('()=>!narration.queueLoading', timeout=5000)
        expect(page.locator('#narrationProgress')).to_be_hidden()
        expect(page.locator('.narration-recording')).to_have_count(0)
        assert page.evaluate('()=>window.syntheticGenerations||0') == 0


def test_stop_during_source_preparation_cannot_start_cached_playback_later(tmp_path):
    with browser(tmp_path) as (_, page, _):
        open_panel(page)
        page.locator('#narrationScopeOptions').click()
        page.get_by_test_id('narration-generate-only').click()
        page.wait_for_function('()=>!narration.busy&&!narration.queueLoading&&narration.records.length>0')
        before = page.evaluate('()=>window.syntheticGenerations')
        page.evaluate('''()=>{const original=narrationStore.sources.bind(narrationStore);
          narrationStore.sources=async(...args)=>{await new Promise(resolve=>window.releaseNarrationSources=resolve);return original(...args)}}''')
        page.locator('#narrationChapter').click()
        page.wait_for_function('()=>typeof window.releaseNarrationSources==="function"')
        page.locator('#narrationStop').click()
        page.evaluate('()=>window.releaseNarrationSources()')
        page.wait_for_function('()=>!narration.queueLoading')
        assert page.evaluate('()=>narration.selected') is None
        assert page.evaluate('()=>window.syntheticGenerations') == before


def test_late_panel_load_cannot_unlock_a_more_recent_open(tmp_path):
    with browser(tmp_path) as (_, page, _):
        open_panel(page)
        page.locator('#closeNarrationPanel').click()
        # Delay completion after the real authorized list, not its HTTP request:
        # the hosted store intentionally coalesces concurrent equivalent reads.
        page.evaluate('''()=>{const list=narrationStore.list.bind(narrationStore);window.panelLoads=[];window.completedPanelLoads=0;
          narrationStore.list=async(...args)=>{const result=await list(...args);
            await new Promise(resolve=>window.panelLoads.push(resolve));window.completedPanelLoads++;return result}}''')
        page.locator('#narrationBtn').click()
        page.wait_for_function('()=>window.panelLoads.length===1')
        page.locator('#closeNarrationPanel').click()
        page.locator('#narrationBtn').click()
        page.wait_for_function('()=>window.panelLoads.length===2')
        page.evaluate('()=>window.panelLoads[0]()')
        page.wait_for_function('()=>window.completedPanelLoads===1')
        expect(page.locator('#narrationVoice')).to_be_disabled()
        page.evaluate('()=>window.panelLoads[1]()')
        page.wait_for_function('()=>!narration.loading')
        expect(page.locator('#narrationVoice')).to_be_enabled()


def test_voice_change_preserves_saved_audio_until_explicit_regeneration(tmp_path):
    with browser(tmp_path) as (_, page, _):
        open_panel(page)
        page.locator('#narrationScopeOptions').click()
        page.get_by_test_id('narration-generate-only').click()
        page.wait_for_function('()=>!narration.busy&&!narration.queueLoading&&narration.records.length>0')
        original = page.evaluate('()=>Object.fromEntries(narration.records.map(r=>[r.id,r.voice]))')
        assert all(voice == 'af_heart' for voice in original.values())
        before = page.evaluate('()=>window.syntheticGenerations')
        page.locator('#narrationVoice').select_option('am_michael')
        page.wait_for_function('()=>narrationPreferences.value.voice==="am_michael"')
        page.locator('#narrationChapter').click()
        page.wait_for_function('()=>!narration.queueLoading&&narration.selected?.voice==="af_heart"')
        assert page.evaluate('()=>window.syntheticGenerations') == before
        assert page.evaluate('()=>Object.fromEntries(narration.records.map(r=>[r.id,r.voice]))') == original
        page.locator('#narrationScopeOptions').click()
        page.get_by_test_id('narration-regenerate').click()
        page.wait_for_function('()=>!narration.busy&&!narration.queueLoading&&narration.records.every(r=>r.voice==="am_michael")')
        assert page.evaluate('()=>narration.records.length') == len(original)
        assert page.evaluate('()=>window.syntheticGenerations') == before + len(original)
