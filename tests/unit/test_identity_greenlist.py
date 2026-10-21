"""Fast unit contracts: operator grants admit verified identities, never merge UIDs."""
import ast
import json
from pathlib import Path
import subprocess

import pytest
from fastapi.responses import HTMLResponse

from auth.identity_platform import IdentityPlatformAdmissionStore, IdentityPlatformError
from hosted.firestore.memory import MemoryDocumentStore
from tests.test_v10358 import _service
from tests.test_v10359 import _service as password_service, _user


def grant(docs, email='writer@example.invalid', **overrides):
    path = IdentityPlatformAdmissionStore.greenlist_path(email)
    row = {'email': email, 'status': 'active', 'allowed_providers': ['google', 'email_password']}
    row.update(overrides)
    docs.run_transaction(lambda tx: tx.create(path, row))
    return path


def test_greenlist_binds_verified_token_uid_without_invitation_and_never_merges(monkeypatch):
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE', '0')
    docs, accounts, admin, service = _service()
    path = grant(docs)
    prior = accounts.resolve_or_create_external_identity(
        service.issuer, 'legacy-uid-same-email', provider='google', display_name='Existing writer',
        email='writer@example.invalid', email_verified=True,
    )
    first = service.exchange_id_token('verified-token')
    assert first.principal.user_id != prior.user_id
    assert docs.get(path).data['bound_uid'] == 'external-uid-1'
    assert service.admission.is_admitted('external-uid-1')
    assert service.exchange_id_token('verified-token').principal.user_id == first.principal.user_id
    admin.claims['uid'] = admin.claims['sub'] = 'different-uid-same-email'
    with pytest.raises(IdentityPlatformError, match='PRIVATE_ALPHA_ADMISSION_REQUIRED'):
        service.exchange_id_token('another-verified-token')
    assert accounts.resolve_external_identity(service.issuer, 'different-uid-same-email') is None


@pytest.mark.parametrize('claims,entry', [
    ({'email_verified': False}, {}),
    ({'email': 'other@example.invalid'}, {}),
    ({'email': 'writer+other@example.invalid'}, {}),
    ({'email': ''}, {}),
    ({}, {'status': 'disabled'}),
    ({}, {'status': 'consumed'}),
    ({}, {'expires_at_epoch': 999}),
    ({}, {'expires_at_epoch': 'invalid'}),
    ({}, {'expires_at_epoch': float('nan')}),
    ({}, {'bound_uid': 'other-uid'}),
    ({}, {'bound_uid': {}}),
    ({}, {'allowed_providers': ['apple']}),
    ({}, {'allowed_providers': 'google'}),
    ({'firebase': {'sign_in_provider': 'apple.com'}}, {}),
])
def test_greenlist_denies_unverified_unapproved_expired_or_mismatched(claims, entry):
    docs, accounts, admin, service = _service()
    grant(docs, **entry)
    admin.claims.update(claims)
    with pytest.raises(IdentityPlatformError, match='PRIVATE_ALPHA_ADMISSION_REQUIRED'):
        service.exchange_id_token('token')
    assert accounts.resolve_external_identity(service.issuer, 'external-uid-1') is None
    assert not any(call[0] == 'create' for call in admin.calls)


@pytest.mark.parametrize('mutation', ['disable', 'delete', 'rebind', 'expire'])
def test_greenlist_revocation_blocks_existing_cookie_and_new_session(mutation):
    docs, _, _, service = _service()
    path = grant(docs)
    exchange = service.exchange_id_token('token')
    entry = dict(docs.get(path).data)
    if mutation == 'delete':
        docs.run_transaction(lambda tx: tx.delete(path))
    else:
        entry.update({'disable': {'status': 'disabled'}, 'rebind': {'bound_uid': 'another'},
                      'expire': {'expires_at_epoch': 999}}[mutation])
        docs.run_transaction(lambda tx: tx.set(path, entry))
    assert service.admission.is_admitted('external-uid-1') is False
    with pytest.raises(IdentityPlatformError):
        with service.bind(exchange.cookie):
            pytest.fail('revoked cookie admitted')
    with pytest.raises(IdentityPlatformError, match='PRIVATE_ALPHA_ADMISSION_DISABLED'):
        service.exchange_id_token('token')


def test_greenlist_case_normalization_does_not_fold_aliases_or_grant_unknown_user():
    docs = MemoryDocumentStore()
    admission = IdentityPlatformAdmissionStore(docs)
    assert admission.greenlist_path(' Writer@Example.Invalid ') == admission.greenlist_path('writer@example.invalid')
    assert admission.greenlist_path('writer+tag@example.invalid') != admission.greenlist_path('writer@example.invalid')
    assert admission.is_admitted('unknown') is False
    with pytest.raises(IdentityPlatformError):
        admission.require_or_consume('unknown', None, email='writer@example.invalid', email_verified=True, provider='google')


def test_greenlist_does_not_bypass_password_totp_requirement():
    docs, _, _, service = password_service(provider='password', user=_user(providers=('password',), totp=False))
    path = grant(docs)
    with pytest.raises(IdentityPlatformError, match='TOTP_ENROLLMENT_REQUIRED'):
        service.exchange_id_token('password-token')
    assert not docs.get(path).data.get('bound_uid')


def test_google_provider_requests_email_for_verified_greenlist_admission():
    root = Path(__file__).resolve().parents[2]
    source = (root / 'static/js/features/account/identity-platform-client.js').read_text()
    google = source.split("if(name==='google'){", 1)[1].split("if(name==='apple'){", 1)[0]
    assert "provider.addScope('email');" in google
    assert google.index("provider.addScope('email');") < google.index('return provider;')
    assert "prompt:'select_account'" in google
    assert google.count('addScope(') == 1  # No access to Gmail, Drive, or other user data.


def run_client_function(relative, name, next_name, setup, invocation):
    """Execute the real JS function with a fake SDK; no network/browser needed."""
    source = (Path(__file__).resolve().parents[2] / relative).read_text()
    body = source.split(f'async function {name}(', 1)[1].split(f'async function {next_name}(', 1)[0]
    body = body.removesuffix('export ')
    code = setup + f'\nasync function {name}(' + body + '\n' + invocation
    result = subprocess.run(['node', '--input-type=module', '-e', code],
                            capture_output=True, text=True, timeout=2, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('user,expected', [
    ({'email': 'writer@example.invalid'}, ['verify', 'writer@example.invalid']),
    ({'email': None, 'providerData': [{'providerId': 'google.com', 'email': 'writer@example.invalid'}]},
     ['verifyBeforeUpdate', 'writer@example.invalid']),
    ({'providerData': [{'providerId': 'apple.com', 'email': 'writer@example.invalid'}]}, ['error']),
    ({'providerData': [{'providerId': 'google.com', 'email': ''}]}, ['error']),
    ({}, ['error']),
])
def test_client_verification_uses_firebase_email_action_not_profile_authority(user, expected):
    actual = run_client_function(
        'static/js/features/account/identity-platform-client.js', 'sendVerification', 'reloadUser',
        'const calls=[]; async function identityClient(){return {authMod:{'
        'async sendEmailVerification(user){calls.push("verify",user.email)},'
        'async verifyBeforeUpdateEmail(user,email){calls.push("verifyBeforeUpdate",email)}'
        '}}}',
        f'try{{await sendVerification({json.dumps(user)})}}catch{{calls.push("error")}};'
        'console.log(JSON.stringify(calls));',
    )
    assert actual == expected


@pytest.mark.parametrize('verified,password', [(False, False), (True, False), (False, True)])
def test_login_waits_for_verified_social_email_before_exchange_without_weakening_password_totp(verified, password):
    actual = run_client_function(
        'static/js/features/account/identity-platform.js', 'finish', 'withBusy',
        'const calls=[]; const sendVerification=async()=>calls.push("email-action");'
        'const requireVerifiedEmail=async()=>calls.push("password-email");'
        'const requirePasswordTotp=async()=>calls.push("totp");'
        'const exchangeServerSession=async()=>calls.push("exchange");'
        'const signOutClient=async()=>calls.push("signout");'
        'const safeReturn=()=>"/"; const location={assign:()=>calls.push("navigate")};',
        f'try{{await finish({{emailVerified:{json.dumps(verified)}}},'
        f'{{passwordAccount:{json.dumps(password)}}})}}catch{{calls.push("retry-signin")}};'
        'console.log(JSON.stringify(calls));',
    )
    if password:
        assert actual == ['password-email', 'totp', 'exchange', 'signout', 'navigate']
    elif verified:
        assert actual == ['exchange', 'signout', 'navigate']
    else:
        assert actual == ['email-action', 'retry-signin']


def test_missing_primary_email_cannot_consume_grant_until_fresh_provider_verified_claim():
    docs, accounts, admin, service = _service()
    path = grant(docs)
    admin.claims.pop('email')
    admin.claims.pop('email_verified')
    with pytest.raises(IdentityPlatformError, match='PRIVATE_ALPHA_ADMISSION_REQUIRED'):
        service.exchange_id_token('before-email-action')
    assert not docs.get(path).data.get('bound_uid')
    assert accounts.resolve_external_identity(service.issuer, 'external-uid-1') is None
    admin.claims.update(email='writer@example.invalid', email_verified=True)
    service.exchange_id_token('fresh-token-after-firebase-verification')
    assert docs.get(path).data['bound_uid'] == 'external-uid-1'


def test_login_shell_removes_requested_copy_and_preserves_auth_controls():
    root = Path(__file__).resolve().parents[2]
    source = (root / 'src/hosted/http_auth.py').read_text()
    node = next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == 'identity_platform_login_shell')
    node.decorator_list = []
    namespace = {'HTMLResponse': HTMLResponse, '_ip_no_store_headers': lambda: {'Cache-Control': 'no-store'}}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<login-shell>', 'exec'), namespace)
    response = namespace['identity_platform_login_shell']()
    html = response.body.decode()
    for absent in ('Private Alpha', 'Private-alpha', 'identityInvite', 'identity-divider',
                   'Your provider proves', 'Provider credentials and authenticator secrets'):
        assert absent not in html
    for present in ('identitySocial', 'identityEmail', 'identityPassword', 'identityTotp', 'identityForgotPassword'):
        assert present in html
    assert response.headers['cache-control'] == 'no-store'
    js = (root / 'static/js/features/account/identity-platform.js').read_text()
    assert 'Continue with Google' in js
    assert 'Social sign-in is not enabled in this environment yet.' not in js
    assert 'identityInvite' not in js
