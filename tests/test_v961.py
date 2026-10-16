from source_contracts import backend_source,frontend_source_from_app_path
from pathlib import Path
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_v961_release_account_session_isolation_contracts_are_explicit():
    version = ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int, version.split('.'))) >= (9, 6, 1)
    app = backend_source(ROOT)
    workspace = ROOT.joinpath('src','workspace','memberships.py').read_text(encoding='utf-8')
    hosted = ROOT.joinpath('src', 'runtime', 'hosted.py').read_text(encoding='utf-8')
    frontend = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))

    # Local Workspace recovery remains fail-closed when explicitly selected, while M5
    # hosted composition no longer instantiates Workspace at all: membership authority
    # lives in Firestore and is checked by the hosted project/context services.
    assert "workspace.set_membership_recovery_mode" not in hosted
    assert "FirestoreProjectRepository" in hosted
    assert "HostedProjectContextProvider" in hosted
    assert "if self._membership_recovery_mode=='fail_closed':" in workspace
    assert "raise WorkspaceError('PROJECT_MEMBERSHIP_INVALID')" in workspace

    # Account request routing and background work never inherit the process-global shell
    # project when there is no authenticated/request-bound project.
    assert "profile_project_target(principal.user_id)" in app
    assert "project_id='' if runtime.sessions.requires_authentication else str(runtime.projects.active_id() or '')" in app
    assert "active='' if runtime.sessions.requires_authentication else str(runtime.projects.active_id() or '')" in app

    # Session/logout are project-context free so an expired cookie can always be cleared.
    assert "'/api/auth/session','/api/auth/logout'" in app
    assert "if(error?.status!==401)throw error" in frontend

    # Browser-only drafts and server UI state are collaborator-scoped.
    assert "user_id:userId" in frontend
    assert "`${userId}:${projectId}:${chapterId}`" in frontend
    assert "return f'member:{current.actor.member_id}:{value}'" in app

    # Hosted account identity is cookie/session authority only; browser usr_ claims are
    # neither transported nor advertised as an actor header.
    assert "runtimeSupports('account_sessions')?'':currentUserIdentity()" in frontend
    assert '"actor_header":None if runtime.sessions.requires_authentication else "X-Breadmaker-User"' in app


def test_v961_fail_closed_membership_recovery_does_not_reassign_project(tmp_path):
    from workspace import Workspace

    home = tmp_path / 'home'
    ws = Workspace(home)
    ws.init()
    owner = ws.local_profile()
    rec = ws.create_project('Protected Book', activate=False, owner_user_id=owner['user_id'])
    local_state = Path(rec['path']) / 'local.json'
    payload = json.loads(local_state.read_text(encoding='utf-8'))
    payload['owner_user_id'] = 'usr_foreign'
    payload['memberships'][0]['user_id'] = 'usr_foreign'
    local_state.write_text(json.dumps(payload), encoding='utf-8')

    guarded = Workspace(home)
    guarded.set_membership_recovery_mode('fail_closed')
    guarded.init()

    assert all(str(item['id']) != str(rec['id']) for item in guarded.list_all_projects())
    assert Path(rec['path']).is_dir()
    still = json.loads(local_state.read_text(encoding='utf-8'))
    assert still['memberships'][0]['user_id'] == 'usr_foreign'
    assert any(str(item.get('id')) == str(rec['id']) for item in guarded.recovery_candidates())


def test_v961_test_fixture_registry_is_id_based_and_unowned_fixture_rotates():
    app = backend_source(ROOT)
    assert "_TEST_AUTH_FIXTURE_INDEX=workspace.root/'test-auth-fixtures.json'" in app
    assert '_TEST_AUTH_FIXTURE_LOCK=threading.Lock()' in app
    assert "workspace.local_profile(user_id)" in app
    assert "if workspace.list_projects(uid):" in app
    assert "fixtures['unowned_user']=str(profile['user_id'])" in app


def test_v961_authenticated_api_cache_policy_is_private_no_store():
    app = backend_source(ROOT)
    assert "response.headers['Cache-Control']='private, no-store'" in app
    assert "status_code=401,headers={'Cache-Control':'private, no-store'}" in app
    assert "status_code=404,headers={'Cache-Control':'private, no-store'}" in app
