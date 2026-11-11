"""Pre-provider disclosure on the real auth UI, with synthetic SDK/legal data."""
from pathlib import Path
from playwright.sync_api import expect
from hosted.signup_notice import parse_notice
from tests.test_v10562_browser import auth_browser, SDK

ROOT = Path(__file__).resolve().parents[1]


def payload(locale='en'):
    return parse_notice((ROOT / f'tests/fixtures/signup-notice-v9.{locale}.html').read_bytes(), locale)


def choose_language(page, locale):
    page.locator('#identityLanguage summary').click()
    page.locator(f'[data-locale="{locale}"]').click()
    page.wait_for_function('(language)=>document.documentElement.lang===language', arg=locale)


def test_notice_precedes_provider_initialization_and_every_creation_entrypoint(tmp_path):
    pending = []
    with auth_browser(tmp_path, notice_handler=lambda route: pending.append(route)) as (page, exchanges, errors):
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        assert page.evaluate('window.authCalls') == []
        page.locator('#identityHeaderMode').click()
        expect(page.locator('#identityEmailSignIn')).to_be_disabled()
        page.locator('#identityEmail').fill('synthetic@example.invalid')
        page.locator('#identityPassword').fill('synthetic-password')
        page.locator('#identityEmailForm').evaluate('(form)=>form.dispatchEvent(new Event("submit",{cancelable:true}))')
        assert not exchanges and page.evaluate('window.authCalls') == []
        page.locator('#identityHeaderMode').click()
        expect(page.locator('#identityEmailSignIn')).to_be_enabled()
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        assert len(pending) == 1
        pending.pop().fulfill(json=payload())
        expect(page.locator('#signup-privacy-title')).to_be_visible()
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        assert page.locator('#identityNoticeContent section').get_attribute('data-notice-version') == '9'
        assert page.locator('#identityNotice').evaluate('(notice)=>!!(notice.compareDocumentPosition(document.querySelector("#identityEmailForm")) & Node.DOCUMENT_POSITION_FOLLOWING)')
        assert page.locator('#identityNotice').evaluate('(notice)=>!!(notice.compareDocumentPosition(document.querySelector("#identitySocial")) & Node.DOCUMENT_POSITION_FOLLOWING)')
        assert page.locator('#identityNotice input').count() == 0
        assert not errors and not exchanges


def test_notice_outage_blocks_google_both_modes_but_password_reset_and_login_work(tmp_path):
    def unavailable(route): route.fulfill(status=503, json={'detail':'SIGNUP_NOTICE_UNAVAILABLE'})
    with auth_browser(tmp_path, notice_handler=unavailable) as (page, exchanges, errors):
        expect(page.locator('#identityNoticeRetry')).to_be_visible()
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        assert page.evaluate('window.authCalls') == []
        page.locator('#identityHeaderMode').click()
        expect(page.locator('#identityEmailSignIn')).to_be_disabled()
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        page.locator('#identityEmailSignUp').click()
        page.locator('#identityEmail').fill('synthetic@example.invalid')
        page.locator('#identityForgotPassword').click()
        page.wait_for_function('()=>window.authCalls.includes("password-reset")')
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        page.locator('#identityPassword').fill('synthetic-password')
        page.locator('#identityEmailSignIn').click()
        page.wait_for_function('location.pathname==="/accepted"')
        assert len(exchanges) == 1 and not errors


def test_locale_switch_failure_and_retry_never_enable_stale_disclosure(tmp_path):
    attempts = []
    def legal(route):
        locale='es' if route.request.url.endswith('locale=es') else 'en'
        attempts.append(locale)
        if locale=='es' and attempts.count('es')==1:
            route.fulfill(status=503,json={'detail':'SIGNUP_NOTICE_UNAVAILABLE'})
        else: route.fulfill(json=payload(locale))
    with auth_browser(tmp_path, notice_handler=legal) as (page, exchanges, errors):
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        choose_language(page,'es')
        expect(page.locator('#identityNoticeRetry')).to_be_visible()
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        assert page.locator('#identityNoticeContent section').count()==0
        page.locator('#identityNoticeRetry').click()
        expect(page.locator('#identityNoticeContent section')).to_have_attribute('lang','es')
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        expect(page.locator('#identityNoticeContent a[href="https://breadmaker.studio/privacy/es/"]')).to_be_visible()
        choose_language(page,'fr')
        expect(page.locator('#identityNoticeContent section')).to_have_attribute('lang','en')
        expect(page.locator('#identityNoticeStatus')).to_contain_text('English is shown')
        assert not errors and not exchanges


def test_out_of_order_locale_response_and_pagehide_cannot_reenable_signup(tmp_path):
    pending=[]
    def legal(route):
        if route.request.url.endswith('locale=es'):pending.append(route)
        else:route.fulfill(json=payload())
    with auth_browser(tmp_path, notice_handler=legal) as (page, exchanges, errors):
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        choose_language(page,'es')
        page.wait_for_function('()=>document.querySelector("[data-provider=google]").disabled')
        choose_language(page,'en')
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        assert len(pending)==1
        pending.pop().fulfill(json=payload('es'))
        expect(page.locator('#identityNoticeContent section')).to_have_attribute('lang','en')
        page.evaluate('window.dispatchEvent(new PageTransitionEvent("pagehide",{persisted:true}))')
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        assert page.locator('#identityNoticeContent section').count()==0
        page.evaluate('window.dispatchEvent(new PageTransitionEvent("pageshow",{persisted:true}))')
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        assert not errors and not exchanges


def test_malformed_browser_notice_has_no_html_execution_or_provider_calls(tmp_path):
    bad=payload();bad['paragraphs'][0].append({'tag':'img','text':'attack','src':'https://evil.invalid'})
    with auth_browser(tmp_path, notice_handler=lambda route:route.fulfill(json=bad)) as (page, exchanges, errors):
        expect(page.locator('#identityNoticeRetry')).to_be_visible()
        assert page.locator('#identityNoticeContent section').count()==0
        assert page.evaluate('window.authCalls')==[]
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        assert not exchanges and not errors


def test_popup_cancel_and_repeated_clicks_preserve_notice_and_single_request(tmp_path):
    sdk=SDK.replace("window.authCalls.push('google');return {user}","window.authCalls.push('google');if(window.authCalls.filter(x=>x==='google').length===1)throw new Error('Popup cancelled');await new Promise(resolve=>window.finishPopup=resolve);return {user}")
    with auth_browser(tmp_path,sdk=sdk) as (page, exchanges, errors):
        google=page.locator('[data-provider=google]')
        expect(google).to_be_enabled();google.click()
        expect(page.locator('#identityMessage')).to_have_text('Popup cancelled')
        expect(google).to_be_enabled()
        expect(page.locator('#signup-privacy-title')).to_be_visible()
        google.click()
        expect(google).to_be_disabled()
        google.evaluate('(button)=>{button.click();button.click()}')
        assert page.evaluate('window.authCalls.filter(x=>x==="google").length')==2
        page.evaluate('window.finishPopup()')
        page.wait_for_function('location.pathname==="/accepted"')
        assert len(exchanges)==1 and not errors


def test_notice_phone_theme_keyboard_and_navigation_remain_usable(tmp_path):
    with auth_browser(tmp_path) as (page, exchanges, errors):
        page.set_viewport_size({'width':390,'height':844})
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        for locale in ('en','es'):
            if locale=='es':choose_language(page,locale)
            expect(page.locator('#identityNoticeContent section')).to_have_attribute('lang',locale)
            for _ in range(3):
                assert page.locator('body').evaluate('(el)=>el.scrollWidth<=innerWidth')
                assert page.locator('#identityNotice').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
                page.locator('#identityTheme').click()
            page.locator('#identityLanguage summary').click()
            page.locator('#identityLanguage summary').click()
            expect(page.locator('#identityLanguage')).not_to_have_attribute('open','')
        page.locator('#identityHeaderMode').click()
        page.locator('#identityEmail').fill('synthetic@example.invalid')
        page.locator('#identityPassword').fill('synthetic-password')
        page.locator('#identityPassword').press('Enter')
        expect(page.locator('#identityVerifiedContinue')).to_be_visible()
        assert page.evaluate('window.authCalls.filter(x=>x==="signup").length')==1
        assert not exchanges and not errors


def test_provider_setup_failure_can_retry_without_reload_or_early_creation(tmp_path):
    sdk=SDK.replace("export const setPersistence=async()=>window.authCalls.push('memory-only');",
        "export const setPersistence=async()=>{window.authCalls.push('memory-only');if(window.authCalls.filter(x=>x==='memory-only').length===1)throw new Error('Synthetic setup outage')};")
    with auth_browser(tmp_path,sdk=sdk) as (page, exchanges, errors):
        expect(page.locator('#identityNoticeRetry')).to_be_visible()
        expect(page.locator('#signup-privacy-title')).to_be_visible()
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        assert not exchanges
        page.locator('#identityNoticeRetry').click()
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        expect(page.locator('#identityNoticeRetry')).to_be_hidden()
        assert page.evaluate('window.authCalls.filter(x=>x==="memory-only").length')==2
        assert not exchanges and not errors
