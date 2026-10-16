from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from oidc_fake import FakeOidcIssuer

ROOT = Path(__file__).resolve().parents[1]


def _client(issuer: FakeOidcIssuer):
    from auth.oidc import OidcClient, OidcProviderConfig

    config = OidcProviderConfig(
        issuer=issuer.issuer,
        client_id=issuer.client_id,
        client_secret=issuer.client_secret,
        redirect_uri=issuer.redirect_uri,
        allow_insecure_loopback=True,
        http_timeout_seconds=2,
        clock_skew_seconds=5,
    )
    return OidcClient(config)


def test_v10313_checkpoint_three_release_contract_is_documented():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    architecture = (ROOT / 'ARCHITECTURE.md').read_text(encoding='utf-8')
    oidc = (ROOT / 'src/auth/oidc.py').read_text(encoding='utf-8')
    routes = (ROOT / 'src/backend/runtime_routes.py').read_text(encoding='utf-8')
    assert tuple(map(int,version.split('.'))) >= (10,3,13)
    assert '10.3.13' in readme and 'generic OIDC' in readme
    assert 'hosted login remains disabled' in architecture
    requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    assert 'class OidcClient' in oidc and "'code_challenge_method': 'S256'" in oidc
    assert 'Authlib==1.8.0' in requirements
    assert 'from authlib.oidc.core import CodeIDToken' in oidc
    assert 'from joserfc import jwt' in oidc
    assert 'cryptography.hazmat' not in oidc
    # Checkpoint 3/4 documented hosted login as disabled at that time. Later
    # identity checkpoints legitimately add development-only routes, so preserve the
    # historical architecture statement rather than forbidding future implementation.
    assert 'hosted login remains disabled' in architecture


def test_v10313_oidc_authorization_request_uses_state_nonce_and_pkce_s256():
    from auth.oidc import OidcError

    with FakeOidcIssuer() as issuer:
        client = _client(issuer)
        request = client.begin_authorization(return_path='/reviews?tab=mine')
        query = parse_qs(urlsplit(request.authorization_url).query)
        assert query['response_type'] == ['code']
        assert query['client_id'] == [issuer.client_id]
        assert query['state'] == [request.state]
        assert query['nonce'] == [request.nonce]
        assert query['code_challenge'] == [request.code_challenge]
        assert query['code_challenge_method'] == ['S256']
        assert 'openid' in query['scope'][0].split()
        assert request.return_path == '/reviews?tab=mine'
        assert client.metadata()['browser_token_exposure'] is False
        with pytest.raises(OidcError, match='OIDC_RETURN_PATH_INVALID'):
            client.begin_authorization(return_path='https://evil.example/steal')


def test_v10313_fake_issuer_valid_flow_returns_verified_identity_and_single_use_state():
    from auth.oidc import OidcError

    with FakeOidcIssuer() as issuer:
        client = _client(issuer)
        request = client.begin_authorization(return_path='/manuscript')
        code = issuer.issue_code(request)
        identity, return_path = client.complete_authorization(code=code, state=request.state)
        assert identity.issuer == issuer.issuer
        assert identity.subject == 'subject-123'
        assert identity.email == 'writer@example.test'
        assert identity.email_verified is True
        assert identity.display_name == 'OIDC Test Writer'
        assert identity.authenticated_at is not None
        assert return_path == '/manuscript'
        with pytest.raises(OidcError, match='OIDC_STATE_INVALID_OR_REPLAYED'):
            client.complete_authorization(code=code, state=request.state)
        assert issuer.token_requests == 1


def test_v10313_oidc_rejects_wrong_state_nonce_issuer_audience_and_expiry():
    from auth.oidc import OidcError

    cases = [
        ({'nonce': 'attacker-nonce'}, 'OIDC_ID_TOKEN_NONCE_MISMATCH'),
        ({'iss': 'https://wrong-issuer.example'}, 'OIDC_ID_TOKEN_ISSUER_MISMATCH'),
        ({'aud': 'some-other-client'}, 'OIDC_ID_TOKEN_AUDIENCE_MISMATCH'),
        ({'exp': int(time.time()) - 30}, 'OIDC_ID_TOKEN_EXPIRED'),
    ]
    with FakeOidcIssuer() as issuer:
        client = _client(issuer)
        request = client.begin_authorization()
        code = issuer.issue_code(request)
        with pytest.raises(OidcError, match='OIDC_STATE_INVALID_OR_REPLAYED'):
            client.complete_authorization(code=code, state='wrong-state')
        # Wrong state must not consume the legitimate transaction.
        identity, _ = client.complete_authorization(code=code, state=request.state)
        assert identity.subject == 'subject-123'

        for claims, code_name in cases:
            request = client.begin_authorization()
            code = issuer.issue_code(request, claims=claims)
            with pytest.raises(OidcError, match=code_name):
                client.complete_authorization(code=code, state=request.state)



def test_v10313_oidc_rejects_malformed_bad_signature_unknown_key_and_code_replay():
    from auth.oidc import OidcError

    with FakeOidcIssuer() as issuer:
        client = _client(issuer)

        malformed = client.begin_authorization()
        malformed_code = issuer.issue_code(malformed, token_override='not-a-jwt')
        with pytest.raises(OidcError, match='OIDC_ID_TOKEN_SIGNATURE_INVALID'):
            client.complete_authorization(code=malformed_code, state=malformed.state)

        bad_signature = client.begin_authorization()
        bad_signature_code = issuer.issue_code(bad_signature, kid='key-a', signing_kid='key-b')
        with pytest.raises(OidcError, match='OIDC_ID_TOKEN_SIGNATURE_INVALID'):
            client.complete_authorization(code=bad_signature_code, state=bad_signature.state)

        unknown_key = client.begin_authorization()
        unknown_key_code = issuer.issue_code(unknown_key, kid='unknown-kid', signing_kid='key-b')
        with pytest.raises(OidcError, match='OIDC_SIGNING_KEY_NOT_FOUND'):
            client.complete_authorization(code=unknown_key_code, state=unknown_key.state)

        original = client.begin_authorization()
        code = issuer.issue_code(original)
        identity, _ = client.complete_authorization(code=code, state=original.state)
        assert identity.subject == 'subject-123'
        replay_transaction = client.begin_authorization()
        with pytest.raises(OidcError):
            client.complete_authorization(code=code, state=replay_transaction.state)


def test_v10313_oidc_transaction_expiry_is_fail_closed_and_single_use():
    from auth.oidc import OidcError, OidcTransactionStore

    clock = [1000.0]
    store = OidcTransactionStore(ttl_seconds=60, now=lambda: clock[0])
    transaction = store.begin(return_path='/manuscript')
    clock[0] = transaction.expires_at
    with pytest.raises(OidcError, match='OIDC_STATE_EXPIRED'):
        store.consume(transaction.state)
    with pytest.raises(OidcError, match='OIDC_STATE_INVALID_OR_REPLAYED'):
        store.consume(transaction.state)

def test_v10313_oidc_refreshes_jwks_on_signing_key_rotation():
    with FakeOidcIssuer() as issuer:
        client = _client(issuer)
        first = client.begin_authorization()
        first_identity, _ = client.complete_authorization(code=issuer.issue_code(first), state=first.state)
        assert first_identity.subject == 'subject-123'
        first_jwks_requests = issuer.jwks_requests

        issuer.rotate_to_second_key()
        second = client.begin_authorization()
        second_identity, _ = client.complete_authorization(code=issuer.issue_code(second, kid='key-b'), state=second.state)
        assert second_identity.subject == 'subject-123'
        assert issuer.jwks_requests > first_jwks_requests


def test_v10313_oidc_engine_does_not_change_local_runtime_or_enable_hosted_login(tmp_path, monkeypatch):
    from runtime import build_runtime
    from workspace import Workspace

    monkeypatch.setenv('BREADMAKER_RUNTIME', 'local')
    ws = Workspace(tmp_path / 'local-home')
    runtime = build_runtime(ws)
    assert runtime.sessions.requires_authentication is False
    assert runtime.accounts.enabled is False
    assert runtime.capabilities.supports('account_authentication') is False
    assert not (ws.root / 'account-identities').exists()
