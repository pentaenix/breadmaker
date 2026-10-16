from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_v10312_checkpoint_two_release_contract_is_documented():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    architecture = (ROOT / 'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert tuple(int(part) for part in version.split('.')) >= (10, 3, 12)
    assert '10.3.12' in readme and 'internal identity' in readme.lower()
    assert 'local Breadmaker remains authentication-free' in architecture
    assert 'hosted login remains disabled' in architecture


def test_v10312_local_runtime_has_no_account_identity_or_session_gate(tmp_path, monkeypatch):
    from runtime import build_runtime
    from auth.accounts import AccountIdentityError
    from workspace import Workspace

    monkeypatch.setenv('BREADMAKER_RUNTIME', 'local')
    ws = Workspace(tmp_path / 'local-home')
    runtime = build_runtime(ws)
    assert runtime.mode == 'local'
    assert runtime.sessions.requires_authentication is False
    assert runtime.accounts.enabled is False
    assert not (ws.root / 'account-identities').exists()
    with pytest.raises(AccountIdentityError, match='ACCOUNT_IDENTITY_DISABLED'):
        runtime.accounts.resolve_or_create_external_identity(
            'https://issuer.example', 'subject', provider='test', display_name='Writer'
        )
    assert not (ws.root / 'account-identities').exists()


def test_v10312_external_identity_is_issuer_subject_not_email(tmp_path):
    from auth.accounts import HostedAccountIdentityStore
    from workspace import Workspace

    ws = Workspace(tmp_path / 'hosted-home')
    ws.init()
    store = HostedAccountIdentityStore(ws)

    first = store.resolve_or_create_external_identity(
        'https://issuer.example/', 'subject-a', provider='oidc-test',
        display_name='Alice', email='same@example.test', email_verified=True,
    )
    again = store.resolve_or_create_external_identity(
        'https://issuer.example/', 'subject-a', provider='oidc-test',
        display_name='Renamed upstream', email='changed@example.test', email_verified=True,
    )
    same_email_other_subject = store.resolve_or_create_external_identity(
        'https://issuer.example/', 'subject-b', provider='oidc-test',
        display_name='Other Alice', email='changed@example.test', email_verified=True,
    )
    other_issuer_same_subject = store.resolve_or_create_external_identity(
        'https://other-issuer.example/', 'subject-a', provider='oidc-test',
        display_name='Third Alice', email='changed@example.test', email_verified=True,
    )

    assert again.user_id == first.user_id
    assert again.email == 'changed@example.test'
    assert same_email_other_subject.user_id != first.user_id
    assert other_issuer_same_subject.user_id != first.user_id
    assert ws.local_profile(first.user_id)['display_name'] == 'Alice'
    assert len(store.identities_for_user(first.user_id)) == 1


def test_v10312_session_replace_rotation_and_user_revocation(monkeypatch):
    from auth.sessions import OpaqueSessionService

    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE', '0')
    service = OpaqueSessionService(auth_mode='test', environment='test', test_secret='x' * 40)

    old_token, old = service.issue('usr_a', auth_method='fixture')
    new_token, new = service.replace(old_token, 'usr_a', auth_method='fixture')
    assert new_token != old_token
    assert new.session_id != old.session_id
    assert new.authenticated_at > 0
    with service.bind(old_token) as principal:
        assert principal is None
    with service.bind(new_token) as principal:
        assert principal is not None and principal.session_id == new.session_id

    second_token, second = service.issue('usr_a', auth_method='fixture')
    other_token, _other = service.issue('usr_b', auth_method='fixture')
    summaries = service.sessions_for_user('usr_a')
    assert {row['session_id'] for row in summaries} == {new.session_id, second.session_id}
    assert all('token' not in row and 'digest' not in row for row in summaries)

    assert service.revoke_user('usr_a') == 2
    with service.bind(new_token) as principal:
        assert principal is None
    with service.bind(second_token) as principal:
        assert principal is None
    with service.bind(other_token) as principal:
        assert principal is not None and principal.user_id == 'usr_b'


def test_v10312_hosted_session_runtime_has_account_registry_but_no_login_provider(tmp_path, monkeypatch):
    from runtime import build_runtime
    from workspace import Workspace
    from storage import database as storage_database

    monkeypatch.setenv('BREADMAKER_RUNTIME', 'hosted_dev')
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'test')
    monkeypatch.setenv('BREADMAKER_ENV', 'test')
    monkeypatch.setenv('BREADMAKER_TEST_AUTH_SECRET', 'z' * 40)
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE', '0')
    ws = Workspace(tmp_path / 'hosted-home')
    runtime = build_runtime(ws)
    try:
        assert runtime.sessions.requires_authentication is True
        assert runtime.accounts.enabled is True
        assert runtime.capabilities.supports('account_authentication') is False
        assert runtime.sessions.metadata()['session_rotation'] is True
        assert runtime.accounts.metadata()['identity_key'] == ['issuer', 'subject']
        assert runtime.accounts.metadata()['stores_credentials'] is False
    finally:
        storage_database.release_all_instance_locks()
