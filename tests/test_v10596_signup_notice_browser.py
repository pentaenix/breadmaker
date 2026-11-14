"""Footer-only privacy access must never gate authentication or copy legal PII.

The real login shell/client use synthetic Firebase responses and credentials.
"""
from playwright.sync_api import expect
from tests.test_v10562_browser import auth_browser, SDK


def choose_language(page, locale):
    page.locator('#identityLanguage summary').click()
    page.locator(f'[data-locale="{locale}"]').click()
    page.wait_for_function('(language)=>document.documentElement.lang===language', arg=locale)


def assert_footer_only(page):
    expect(page.locator('.identity-footer a')).to_have_attribute('href', 'https://breadmaker.studio/privacy')
    expect(page.locator('.identity-footer a')).to_be_visible()
    assert page.locator('#identityNotice, #identityNoticeContent, #signup-privacy-title').count() == 0
    assert page.locator('[aria-describedby="identityNotice"]').count() == 0


def test_sdk_readiness_gates_provider_and_signup_without_fetching_notice(tmp_path):
    requests = []
    sdk = SDK.replace("export const setPersistence=async()=>window.authCalls.push('memory-only');",
        "export const setPersistence=async()=>{window.authCalls.push('memory-only');await new Promise(resolve=>window.finishSetup=resolve)};")
    with auth_browser(tmp_path, sdk=sdk, notice_handler=lambda route: requests.append(route.request.url)) as (page, exchanges, errors):
        page.wait_for_function('()=>typeof window.finishSetup==="function"')
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        page.locator('#identityHeaderMode').click()
        expect(page.locator('#identityEmailSignIn')).to_be_disabled()
        page.locator('#identityEmail').fill('synthetic@example.invalid')
        page.locator('#identityPassword').fill('synthetic-password')
        page.locator('#identityEmailForm').evaluate('(form)=>form.dispatchEvent(new Event("submit",{cancelable:true}))')
        assert 'signup' not in page.evaluate('window.authCalls')
        assert not exchanges
        page.evaluate('window.finishSetup()')
        expect(page.locator('#identityEmailSignIn')).to_be_enabled()
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        expect(page.locator('[data-provider=apple]')).to_be_disabled()
        assert_footer_only(page)
        assert not requests and not errors


def test_notice_outage_does_not_block_password_reset_or_email_login(tmp_path):
    requests = []
    def unavailable(route):
        requests.append(route.request.url)
        route.fulfill(status=503, json={'detail': 'SIGNUP_NOTICE_UNAVAILABLE'})
    with auth_browser(tmp_path, notice_handler=unavailable) as (page, exchanges, errors):
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        page.locator('#identityHeaderMode').click()
        expect(page.locator('#identityEmailSignIn')).to_be_enabled()
        page.locator('#identityEmailSignUp').click()
        page.locator('#identityEmail').fill('synthetic@example.invalid')
        page.locator('#identityForgotPassword').click()
        page.wait_for_function('()=>window.authCalls.includes("password-reset")')
        page.locator('#identityPassword').fill('synthetic-password')
        page.locator('#identityEmailSignIn').click()
        page.wait_for_function('location.pathname==="/accepted"')
        assert len(exchanges) == 1 and not requests and not errors


def test_locale_changes_never_fetch_or_render_legal_notice(tmp_path):
    requests = []
    with auth_browser(tmp_path, notice_handler=lambda route: requests.append(route.request.url)) as (page, exchanges, errors):
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        for locale in ('es', 'fr', 'en'):
            choose_language(page, locale)
            expect(page.locator('[data-provider=google]')).to_be_enabled()
            assert_footer_only(page)
        assert not requests and not errors and not exchanges


def test_back_forward_page_restore_keeps_footer_and_authentication_ready(tmp_path):
    requests = []
    with auth_browser(tmp_path, notice_handler=lambda route: requests.append(route.request.url)) as (page, exchanges, errors):
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        page.locator('#identityHeaderMode').click()
        page.evaluate('window.dispatchEvent(new PageTransitionEvent("pagehide",{persisted:true}))')
        page.evaluate('window.dispatchEvent(new PageTransitionEvent("pageshow",{persisted:true}))')
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        expect(page.locator('#identityEmailSignIn')).to_be_enabled()
        assert_footer_only(page)
        assert not requests and not errors and not exchanges


def test_untrusted_notice_content_is_not_requested_or_rendered_and_google_works(tmp_path):
    requests = []
    def malicious(route):
        requests.append(route.request.url)
        route.fulfill(json={'title':'<img src=x onerror="window.legalExecuted=true">', 'paragraphs':['SYNTHETIC PRIVATE ADDRESS']})
    with auth_browser(tmp_path, notice_handler=malicious) as (page, exchanges, errors):
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        assert_footer_only(page)
        assert 'SYNTHETIC PRIVATE ADDRESS' not in page.locator('body').inner_text()
        assert page.evaluate('window.legalExecuted===undefined')
        page.locator('[data-provider=google]').click()
        page.wait_for_function('location.pathname==="/accepted"')
        assert len(exchanges) == 1 and not requests and not errors


def test_popup_cancel_and_repeated_clicks_preserve_footer_and_single_request(tmp_path):
    sdk=SDK.replace("window.authCalls.push('google');return {user}","window.authCalls.push('google');if(window.authCalls.filter(x=>x==='google').length===1)throw new Error('Popup cancelled');await new Promise(resolve=>window.finishPopup=resolve);return {user}")
    with auth_browser(tmp_path,sdk=sdk) as (page, exchanges, errors):
        google=page.locator('[data-provider=google]')
        expect(google).to_be_enabled();google.click()
        expect(page.locator('#identityMessage')).to_have_text('Popup cancelled')
        expect(google).to_be_enabled()
        assert_footer_only(page)
        google.click()
        expect(google).to_be_disabled()
        google.evaluate('(button)=>{button.click();button.click()}')
        assert page.evaluate('window.authCalls.filter(x=>x==="google").length')==2
        page.evaluate('window.finishPopup()')
        page.wait_for_function('location.pathname==="/accepted"')
        assert len(exchanges)==1 and not errors


def test_footer_phone_theme_keyboard_and_signup_remain_usable(tmp_path):
    with auth_browser(tmp_path) as (page, exchanges, errors):
        page.set_viewport_size({'width':390,'height':844})
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        for locale in ('en','es'):
            if locale=='es':choose_language(page,locale)
            assert_footer_only(page)
            for _ in range(3):
                assert page.locator('body').evaluate('(el)=>el.scrollWidth<=innerWidth')
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


def test_provider_setup_failure_blocks_creation_and_reload_recovers(tmp_path):
    sdk=SDK.replace("export const setPersistence=async()=>window.authCalls.push('memory-only');",
        "export const setPersistence=async()=>{window.authCalls.push('memory-only');if(!sessionStorage.getItem('synthetic-setup-failed')){sessionStorage.setItem('synthetic-setup-failed','1');throw new Error('Synthetic setup outage')}};")
    with auth_browser(tmp_path,sdk=sdk) as (page, exchanges, errors):
        expect(page.locator('#identityMessage')).to_contain_text('Please reload to retry')
        expect(page.locator('[data-provider=google]')).to_be_disabled()
        page.locator('#identityHeaderMode').click()
        expect(page.locator('#identityEmailSignIn')).to_be_disabled()
        page.locator('#identityEmail').fill('synthetic@example.invalid')
        page.locator('#identityPassword').fill('synthetic-password')
        page.locator('#identityEmailForm').evaluate('(form)=>form.dispatchEvent(new Event("submit",{cancelable:true}))')
        assert 'signup' not in page.evaluate('window.authCalls') and not exchanges
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        assert_footer_only(page)
        page.locator('[data-provider=google]').click()
        page.wait_for_function('location.pathname==="/accepted"')
        assert len(exchanges)==1 and not errors
