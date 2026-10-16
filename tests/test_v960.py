from storage import database as storage_database

from source_contracts import backend_source,frontend_source_from_app_path
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_v960_release_session_and_test_auth_contracts_are_explicit():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (9,6,0)
    sessions=ROOT.joinpath('src','auth','sessions.py').read_text(encoding='utf-8')
    hosted=ROOT.joinpath('src','runtime','hosted.py').read_text(encoding='utf-8')
    app=backend_source(ROOT)
    frontend=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert 'class OpaqueSessionService' in sessions
    assert "secrets.token_urlsafe(32)" in sessions
    assert "hashlib.sha256" in sessions
    assert "TEST_AUTH_REQUIRES_TEST_ENVIRONMENT" in sessions
    assert "TEST_AUTH_FORBIDDEN_IN_PRODUCTION" in sessions
    assert "TEST_AUTH_SECRET_TOO_SHORT" in sessions
    assert "auth_mode in {'test','session','identity_platform'}" in hosted
    assert "{'test','session'}" in sessions
    assert 'auth0' not in hosted.lower()
    assert "if runtime.sessions.test_bootstrap_enabled:" in app
    assert "@app.post('/__test__/login')" in app
    assert "@app.post('/api/auth/logout')" in app
    assert "runtime.sessions.requires_authentication" in app
    assert "runtimeSupports('account_sessions')" in frontend


def test_v960_opaque_session_issue_bind_expire_and_revoke(monkeypatch):
    from auth.sessions import OpaqueSessionService
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    service=OpaqueSessionService(auth_mode='test',environment='test',test_secret='x'*40)
    token,issued=service.issue('usr_test',auth_method='test_fixture')
    assert token and issued.user_id=='usr_test'
    with service.bind(token) as principal:
        assert principal is not None and principal.user_id=='usr_test'
        assert service.current().session_id==issued.session_id
    assert service.current() is None
    service.revoke(token)
    with service.bind(token) as principal:assert principal is None


def test_v960_test_auth_boot_guards(monkeypatch):
    from auth.sessions import build_hosted_session_service
    monkeypatch.setenv('BREADMAKER_ENV','development')
    monkeypatch.setenv('BREADMAKER_TEST_AUTH_SECRET','x'*40)
    with pytest.raises(RuntimeError,match='TEST_AUTH_REQUIRES_TEST_ENVIRONMENT'):
        build_hosted_session_service('test')
    monkeypatch.setenv('BREADMAKER_ENV','test')
    monkeypatch.setenv('BREADMAKER_TEST_AUTH_SECRET','short')
    with pytest.raises(RuntimeError,match='TEST_AUTH_SECRET_TOO_SHORT'):
        build_hosted_session_service('test')


def test_v960_hosted_session_identity_comes_from_server_session_not_browser(monkeypatch,tmp_path):
    from runtime import build_runtime
    from auth.sessions import AuthenticationError
    from workspace import Workspace

    monkeypatch.setenv('BREADMAKER_RUNTIME','hosted_dev')
    monkeypatch.setenv('BREADMAKER_AUTH_MODE','test')
    monkeypatch.setenv('BREADMAKER_ENV','test')
    monkeypatch.setenv('BREADMAKER_TEST_AUTH_SECRET','z'*40)
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    ws=Workspace(tmp_path/'hosted-auth-home')
    runtime=build_runtime(ws)
    try:
        # Milestone 5 no longer borrows profiles/projects from the local Workspace.
        # Establish the account in Firestore, bind the server session, then create the
        # project through the hosted repository itself.
        identity=runtime.accounts.resolve_or_create_external_identity(
            'https://issuer.example','session-owner',provider='test',display_name='Session Owner'
        )
        with pytest.raises(AuthenticationError):
            runtime.identity.request_user_id('usr_browser_spoof')
        token,_principal=runtime.sessions.issue(identity.user_id,auth_method='test_fixture')
        with runtime.sessions.bind(token):
            assert runtime.identity.request_user_id('usr_browser_spoof')==identity.user_id
            project=runtime.projects.create('Session Project',activate=False)
            with runtime.context.bind(project['id'],user_id=identity.user_id) as context:
                assert context.actor.user_id==identity.user_id
                assert context.actor.kind=='account'
                assert context.actor.role=='owner'
    finally:
        storage_database.release_all_instance_locks()
