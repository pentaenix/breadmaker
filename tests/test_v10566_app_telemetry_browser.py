"""Preview-only consent UI on the real accountless editor and shared modal system."""
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import requests
from playwright.sync_api import expect
from scripts.browser_runtime import start_browser, close_browser

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def telemetry_preview(home):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ, BREADMAKER_HOME=str(home), PYTHONPATH=str(ROOT/'src'),
               BREADMAKER_RUNTIME='local')
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host',
                             '127.0.0.1', '--port', str(port), '--log-level', 'warning'],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    driver = browser = None
    try:
        for _ in range(100):
            assert proc.poll() is None, 'Synthetic local editor exited'
            try:
                if requests.get(base+'/api/runtime', timeout=.25).ok: break
            except requests.RequestException: pass
            time.sleep(.05)
        else: raise AssertionError('Synthetic local editor did not start')
        driver, browser, _ = start_browser()
        context = browser.new_context(viewport={'width':1280,'height':850})
        page = context.new_page()
        page.goto(base, wait_until='domcontentloaded', timeout=8000)
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        assert page.evaluate('typeof createAppTelemetryFoundation')=='undefined'
        page.add_style_tag(url=base+'/static/css/app_telemetry/consent.css')
        for name in ('foundation.js','coordinator.js','presence.js','consent.js'):
            page.add_script_tag(url=base+'/static/js/features/app_telemetry/'+name)
        page.evaluate('''()=>{
          window.telemetryClient=createAppTelemetryFoundation({storage:localStorage,appVersion:'10.5.66'});
          window.telemetryUI=mountAppTelemetryConsentPreview(telemetryClient);
        }''')
        yield page
    finally:
        if driver is not None: close_browser(driver,browser)
        proc.terminate()
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: proc.kill();proc.wait(timeout=3)


def test_banner_close_is_refusal_and_settings_can_enable_and_withdraw(tmp_path):
    with telemetry_preview(tmp_path) as page:
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        banner=page.locator('.app-telemetry-banner')
        expect(banner).to_be_visible()
        assert page.evaluate('telemetryClient.snapshot().length')==0
        page.get_by_role('button',name='Close; keep app usage off',exact=True).click()
        expect(banner).to_have_count(0)
        assert page.evaluate('telemetryClient.consent()')=='declined'
        page.evaluate('telemetryUI.openSettings()')
        switch=page.get_by_role('switch',name='Share optional app usage')
        expect(switch).to_have_attribute('aria-checked','false')
        assert switch.evaluate('(e)=>[e.offsetWidth,e.offsetHeight]')==[34,20]
        switch.click();expect(switch).to_have_attribute('aria-checked','true')
        page.evaluate('telemetryClient.startSyntheticSession()')
        assert page.evaluate('telemetryClient.snapshot().length')==1
        switch.click();expect(switch).to_have_attribute('aria-checked','false')
        assert page.evaluate('telemetryClient.snapshot().length')==0
        page.locator('#modalClose').click()
        page.evaluate('telemetryUI.dispose();telemetryUI=mountAppTelemetryConsentPreview(telemetryClient)')
        expect(banner).to_have_count(0)
        # The editing path remains independently usable after declining.
        page.locator('#editor').fill('SYNTHETIC accountless writing remains available')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        assert not errors, errors


def test_affirmative_button_and_refusal_are_explicit_keyboard_accessible_choices(tmp_path):
    with telemetry_preview(tmp_path) as page:
        allow=page.get_by_role('button',name='Allow app usage',exact=True)
        decline=page.get_by_role('button',name='Not now',exact=True)
        assert allow.evaluate('(e)=>getComputedStyle(e).fontSize')==decline.evaluate('(e)=>getComputedStyle(e).fontSize')
        decline.focus();page.keyboard.press('Enter')
        assert page.evaluate('telemetryClient.consent()')=='declined'
        page.evaluate('telemetryUI.openSettings()')
        page.get_by_role('switch').focus();page.keyboard.press('Space')
        assert page.evaluate('telemetryClient.consent()')=='granted'
        page.locator('#modalClose').click()
        # Fresh storage is a first-use preview; Allow itself provides affirmative consent.
        page.evaluate('telemetryUI.dispose();telemetryClient.dispose();localStorage.removeItem("breadmaker-optional-app-telemetry-v1");telemetryClient=createAppTelemetryFoundation({storage:localStorage});telemetryUI=mountAppTelemetryConsentPreview(telemetryClient)')
        allow.click();assert page.evaluate('telemetryClient.consent()')=='granted'
        # No observation hooks: consent alone creates no report.
        assert page.evaluate('telemetryClient.snapshot().length')==0


def test_other_tab_withdrawal_updates_switch_and_clears_unsent_reports(tmp_path):
    with telemetry_preview(tmp_path) as page:
        page.get_by_role('button',name='Allow app usage',exact=True).click()
        page.evaluate('telemetryClient.startSyntheticSession();telemetryUI.openSettings()')
        expect(page.get_by_role('switch')).to_have_attribute('aria-checked','true')
        other=page.context.new_page()
        try:
            # Same origin, no second app instance or real observations required.
            other.goto(page.url+'static/breadmaker_logo.svg',wait_until='domcontentloaded')
            other.evaluate('''()=>localStorage.setItem('breadmaker-optional-app-telemetry-v1',
              JSON.stringify({notice_version:'preview-1',choice:'declined'}))''')
            expect(page.get_by_role('switch')).to_have_attribute('aria-checked','false')
            assert page.evaluate('telemetryClient.snapshot().length')==0
        finally:
            other.close()


def test_banner_and_settings_fit_with_manuscript_panel_at_desktop_and_mobile_widths(tmp_path):
    with telemetry_preview(tmp_path) as page:
        page.evaluate('openManuscriptImagePanel()')
        expect(page.locator('#manuscriptImagePanel')).to_be_visible()
        for width in (1280,760,390):
            page.set_viewport_size({'width':width,'height':850})
            for theme in ('light','dark'):
                page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
                banner=page.locator('.app-telemetry-banner')
                box=banner.bounding_box();assert box['x']>=0 and box['x']+box['width']<=width
                assert banner.evaluate('(e)=>e.scrollWidth<=e.clientWidth')
                for name in ('Allow app usage','Not now','Close; keep app usage off'):
                    button=page.get_by_role('button',name=name,exact=True)
                    rect=button.bounding_box();assert rect['x']>=0 and rect['x']+rect['width']<=width
                page.evaluate('telemetryUI.openSettings()')
                card=page.locator('.modal-card').bounding_box()
                assert card['x']>=0 and card['x']+card['width']<=width
                page.locator('#modalClose').click()
        # Match the actual Manuscript panel, keeping its existing control size.
        page.set_viewport_size({'width':760,'height':850})
        page.evaluate('openExperimentalFeatures()')
        reference=page.locator('.structure-switch').bounding_box()
        page.locator('#modalClose').click();page.evaluate('telemetryUI.openSettings()')
        actual=page.locator('.structure-switch').bounding_box()
        assert actual['width']==reference['width'] and actual['height']==reference['height']


def test_preview_settings_row_returns_to_overview_after_withdrawal(tmp_path):
    with telemetry_preview(tmp_path) as page:
        page.get_by_role('button',name='Allow app usage',exact=True).click()
        page.evaluate('telemetryUI.openSettingsOverview()')
        page.locator('#appTelemetrySettingsRow').click()
        expect(page.get_by_role('switch')).to_have_attribute('aria-checked','true')
        page.get_by_role('switch').click()
        assert page.evaluate('telemetryClient.consent()')=='declined'
        page.locator('#modalClose').click()
        expect(page.locator('#appTelemetrySettingsRow')).to_be_visible()
        page.locator('#appTelemetrySettingsRow').click()
        expect(page.get_by_role('switch')).to_have_attribute('aria-checked','false')


def test_real_browser_lock_excludes_second_tab_then_hands_over_and_revokes(tmp_path):
    with telemetry_preview(tmp_path) as page:
        page.get_by_role('button',name='Allow app usage',exact=True).click()
        page.evaluate('window.owner=createAppTelemetryCoordinatorPreview(telemetryClient,{locks:navigator.locks,events:window})')
        assert page.evaluate('owner.acquire()') is True
        assert page.evaluate('owner.startSyntheticSession()') is True
        other=page.context.new_page()
        try:
            other.goto(page.url,wait_until='domcontentloaded')
            other.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
            for name in ('foundation.js','coordinator.js'):
                other.add_script_tag(url=page.url+'static/js/features/app_telemetry/'+name)
            other.evaluate('window.client=createAppTelemetryFoundation({storage:localStorage});window.owner=createAppTelemetryCoordinatorPreview(client,{locks:navigator.locks,events:window})')
            assert other.evaluate('owner.acquire()') is False
            assert other.evaluate('owner.recordSyntheticMinute()') is False
            assert other.evaluate('client.snapshot().length')==0
            page.evaluate('owner.dispose()')
            assert page.evaluate('telemetryClient.snapshot().length')==0
            other.wait_for_function('()=>owner.acquire()')
            assert other.evaluate('owner.recordSyntheticMinute()') is True
            # Withdraw from the original tab; real storage events stop the new owner.
            page.evaluate('telemetryClient.setConsent(false)')
            other.wait_for_function('()=>!owner.isOwner()')
            assert other.evaluate('client.snapshot().length')==0
            assert other.evaluate('owner.acquire()') is False
        finally:
            other.close()


def test_presence_preview_samples_real_document_and_expires_without_collecting(tmp_path):
    with telemetry_preview(tmp_path) as page:
        page.bring_to_front()
        page.wait_for_function('()=>document.hasFocus()',timeout=1500)
        page.evaluate('window.elapsed=0;window.presence=createAppTelemetryPresencePreview({client:telemetryClient,document,monotonic:()=>elapsed})')
        assert page.evaluate('presence.noteSyntheticInteraction()') is False
        page.get_by_role('button',name='Allow app usage',exact=True).click()
        assert page.evaluate('presence.eligible()') is False
        assert page.evaluate('presence.noteSyntheticInteraction();presence.eligible()') is True
        assert page.evaluate('elapsed=60000;presence.eligible()') is False
        assert page.evaluate('presence.noteSyntheticInteraction();presence.eligible()') is True
        page.evaluate('telemetryClient.setConsent(false)')
        assert page.evaluate('presence.eligible()') is False
        assert page.evaluate('telemetryClient.snapshot().length')==0
