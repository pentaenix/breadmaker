"""Original auth presentation on the real current client; Firebase is a fake SDK.

No provider credentials, mail, cloud writes or production manuscripts are used.
Each non-parametrized test is discovered/accounted by the normal browser lane.
"""
import ast
from contextlib import contextmanager
from pathlib import Path

from fastapi.responses import HTMLResponse
from playwright.sync_api import expect

from tests.test_v10548_browser import hosted_browser

ROOT = Path(__file__).resolve().parents[1]
SDK = """
const auth={authStateReady:async()=>{}};
const user={email:null,emailVerified:false,getIdToken:async()=>{window.authCalls.push('token');return 'synthetic-token'}};
export const inMemoryPersistence={};
export const getAuth=()=>auth;
export const setPersistence=async()=>window.authCalls.push('memory-only');
export class GoogleAuthProvider{addScope(scope){window.authCalls.push('scope:'+scope)}setCustomParameters(){}}
export const signInWithPopup=async()=>{window.authCalls.push('google');return {user}};
export const createUserWithEmailAndPassword=async(auth,email)=>{window.authCalls.push('signup');user.email=email;return {user}};
export const signInWithEmailAndPassword=async(auth,email)=>{window.authCalls.push('password');user.email=email;user.emailVerified=true;return {user}};
export const sendEmailVerification=async()=>window.authCalls.push('verification-mail');
export const reload=async()=>{window.authCalls.push('reload');user.emailVerified=true};
export const signOut=async()=>window.authCalls.push('signout');
export const TotpMultiFactorGenerator={FACTOR_ID:'totp'};
export const multiFactor=()=>({enrolledFactors:[{factorId:'totp'}]});
export const sendPasswordResetEmail=async()=>window.authCalls.push('password-reset');
"""


@contextmanager
def auth_browser(home, *, admitted=True):
    source = (ROOT / 'src/hosted/http_auth.py').read_text()
    node = next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == 'identity_platform_login_shell')
    node.decorator_list = []
    namespace = {'HTMLResponse': HTMLResponse, 'APP_VERSION': (ROOT / 'VERSION').read_text().strip(),
                 '_ip_no_store_headers': lambda: {'Cache-Control': 'no-store'}}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<login-shell>', 'exec'), namespace)
    shell = namespace['identity_platform_login_shell']()
    with hosted_browser(home) as (base, page):
        page.set_default_timeout(2500)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script('window.authCalls=[]')
        page.route(base + '/auth/login?**', lambda route: route.fulfill(
            status=200, content_type='text/html', headers=dict(shell.headers), body=shell.body))
        page.route('https://www.gstatic.com/firebasejs/*/firebase-app.js', lambda route: route.fulfill(
            content_type='application/javascript', headers={'Access-Control-Allow-Origin': '*'},
            body='export const getApps=()=>[];export const initializeApp=()=>({});'))
        page.route('https://www.gstatic.com/firebasejs/*/firebase-auth.js', lambda route: route.fulfill(
            content_type='application/javascript', headers={'Access-Control-Allow-Origin': '*'}, body=SDK))
        page.route(base + '/api/auth/client-config', lambda route: route.fulfill(json={
            'firebase': {'projectId': 'synthetic'}, 'providers': {'google': True, 'apple': False}}))
        page.route(base + '/api/auth/csrf', lambda route: route.fulfill(json={'csrf_token': 'synthetic-csrf'}))
        exchanges = []
        def exchange(route):
            exchanges.append(route.request.post_data_json)
            assert route.request.headers['x-csrf-token'] == 'synthetic-csrf'
            route.fulfill(status=200 if admitted else 403, json={
                'ok': admitted, 'detail': 'PRIVATE_ALPHA_ADMISSION_REQUIRED' if not admitted else ''})
        page.route(base + '/api/auth/session/exchange', exchange)
        page.route(base + '/accepted', lambda route: route.fulfill(content_type='text/html', body='<h1>Accepted</h1>'))
        page.goto(base + '/auth/login?return_to=/accepted', wait_until='domcontentloaded')
        try:
            expect(page.locator('[data-provider=google]')).to_be_visible(timeout=2500)
        except AssertionError as exc:
            raise AssertionError({'url': page.url, 'errors': errors, 'body': page.locator('body').inner_text()}) from exc
        page.wait_for_function('()=>document.documentElement.lang==="en" && i18nReady')
        try:
            yield page, exchanges, errors
        finally:
            # Finish intercepted responses before shutting down the driver;
            # pending route callbacks can otherwise mask an assertion at close.
            page.unroute_all(behavior='ignoreErrors')
            page.close()


def test_auth_original_presentation_theme_language_and_signup_are_functional(tmp_path):
    with auth_browser(tmp_path) as (page, exchanges, errors):
        expect(page.locator('#identityHeading')).to_have_text('Login')
        expect(page.locator('.identity-brand')).to_have_text('Breadmaker')
        apple = page.locator('[data-provider=apple]')
        expect(apple).to_be_visible()
        expect(apple).to_be_disabled()
        expect(apple).to_have_attribute('aria-disabled', 'true')
        assert apple.evaluate('(el)=>getComputedStyle(el).opacity') == '0.5'
        apple.evaluate('(el)=>el.click()')
        assert not exchanges and 'google' not in page.evaluate('window.authCalls')
        assert page.locator('.identity-card').evaluate('(el)=>getComputedStyle(el).boxShadow') == 'none'
        assert page.locator('body').evaluate('(el)=>getComputedStyle(el).backgroundImage') == 'none'
        page.locator('#identityHeaderMode').click()
        expect(page.locator('#identityHeading')).to_have_text('Create your account')
        expect(page.locator('#identityEmailSignIn')).to_have_text('Create account')
        expect(page.locator('#identityForgotPassword')).to_be_hidden()
        expect(page.locator('#identityPassword')).to_have_attribute('autocomplete', 'new-password')
        assert not exchanges  # Signup link changes view, never creates an account.
        page.locator('#identityEmailSignUp').click()
        expect(page.locator('#identityForgotPassword')).to_be_visible()
        page.locator('#identityShowPassword').click()
        expect(page.locator('#identityPassword')).to_have_attribute('type', 'text')
        page.locator('#identityShowPassword').click()
        expect(page.locator('#identityPassword')).to_have_attribute('type', 'password')
        for theme in ('dusk', 'dark', 'light'):
            page.locator('#identityTheme').click()
            expect(page.locator('html')).to_have_attribute('data-theme', theme)
        page.locator('#identityLanguage summary').click()
        page.locator('[data-locale=es]').click()
        expect(page.locator('html')).to_have_attribute('lang', 'es')
        assert page.locator('#identityHeading').inner_text() != 'Login'
        assert '{{i18n:' not in page.locator('body').inner_text()
        assert page.evaluate('localStorage.getItem("breadmaker-language")') == 'es'
        page.set_viewport_size({'width': 360, 'height': 740})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        expect(page.locator('[data-provider=google]')).to_be_visible()
        expect(apple).to_be_visible()
        expect(apple).to_be_disabled()
        assert not errors


def test_google_missing_primary_email_exchanges_without_sending_mail(tmp_path):
    with auth_browser(tmp_path) as (page, exchanges, errors):
        page.locator('[data-provider=google]').click()
        page.wait_for_function('location.pathname==="/accepted"')
        assert exchanges == [{'idToken': 'synthetic-token', 'invitationToken': None}]
        assert not errors
        # A mail/retry branch would leave this missing-primary user on login,
        # rather than reach the accepted page. Denial case checks the call log.


def test_google_denied_admission_stays_denied_without_automatic_email(tmp_path):
    with auth_browser(tmp_path, admitted=False) as (page, exchanges, errors):
        page.locator('[data-provider=google]').click()
        expect(page.locator('#identityMessage')).to_have_text('This account is not approved for Breadmaker access.')
        assert len(exchanges) == 1
        calls = page.evaluate('window.authCalls')
        assert 'google' in calls and 'memory-only' in calls and 'scope:email' in calls
        assert 'verification-mail' not in calls and 'signout' in calls
        expect(page.locator('[data-provider=google]')).to_be_enabled()
        expect(page.locator('[data-provider=apple]')).to_be_visible()
        expect(page.locator('[data-provider=apple]')).to_be_disabled()
        assert page.locator('[data-provider=apple]').evaluate('(el)=>el.onclick') is None
        assert not errors


def test_password_signup_still_waits_for_email_verification_before_session(tmp_path):
    with auth_browser(tmp_path) as (page, exchanges, errors):
        page.locator('#identityHeaderMode').click()
        page.locator('#identityEmail').fill('synthetic@example.invalid')
        page.locator('#identityPassword').fill('synthetic-password-only')
        page.locator('#identityEmailSignIn').click()
        expect(page.locator('#identityVerifiedContinue')).to_be_visible()
        assert not exchanges
        assert page.evaluate('window.authCalls').count('verification-mail') == 1
        page.locator('#identityVerifiedContinue').click()
        page.wait_for_function('location.pathname==="/accepted"')
        assert len(exchanges) == 1
        assert not errors
