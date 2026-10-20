"""Fast unit contracts: operator grants admit verified identities, never merge UIDs."""
import ast
from pathlib import Path

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
