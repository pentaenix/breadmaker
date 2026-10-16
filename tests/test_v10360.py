from __future__ import annotations
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def read(path):return (ROOT/path).read_text(encoding='utf-8')
def test_v10360_identity_platform_is_only_real_hosted_auth_mode():
    hosted=read('src/runtime/hosted.py').lower();http=read('src/hosted/http_auth.py').lower();sessions=read('src/auth/sessions.py').lower()
    assert "else 'identity_platform'" in hosted and 'hosted_runtime_identity_platform_required' in hosted
    assert 'auth0' not in hosted and 'auth0' not in http
    assert "mode not in {'test','session'}" in sessions

def test_v10360_retired_auth0_code_is_archived_not_importable():
    for name in ['auth0.py','hosted_login.py','google.py','apple.py','database.py','linking.py','security.py','secrets.py']:
        assert not (ROOT/'src/auth'/name).exists(),name
        assert (ROOT/'docs/archive/auth0_runtime'/(name+'.txt')).exists(),name
    assert not (ROOT/'run-hosted-auth-dev').exists() and (ROOT/'run-hosted-identity-dev').exists()

def test_v10360_identity_platform_browser_and_server_authority_are_explicit():
    client=read('static/js/features/account/identity-platform-client.js');ui=read('static/js/features/account/security.js');auth=read('src/hosted/http_auth.py')
    assert 'inMemoryPersistence' in client and 'localStorage' not in client and 'sessionStorage' not in client
    assert "accountSecurityMode()==='identity_platform'" in ui and 'authorization_url' not in ui
    assert '/api/auth/session/exchange' in auth and 'check_revoked' not in auth  # Admin verification is encapsulated in the session service.
    assert 'exchange_id_token' in auth and 'revoke_user' in auth

def test_v10360_local_runtime_cannot_import_cloud_identity():
    local=read('src/runtime/local.py').lower();requirements=read('requirements.txt').lower();assert 'identity_platform' not in local and 'firebase-admin' not in requirements

def test_v10360_hosted_requirements_and_docs_have_retired_runtime_secret_loader():
    hosted_req=read('requirements-hosted.txt').lower();assert 'firebase-admin' in hosted_req and 'google-cloud-secret-manager' not in hosted_req
    assert 'identity platform' in read('deploy/HOSTED_STAGING.md').lower() and 'auth0 runtime variables' in read('deploy/HOSTED_STAGING.md').lower()
