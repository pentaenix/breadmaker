from __future__ import annotations
from source_contracts import backend_source,frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v930_runtime_contracts_are_explicit_and_local_runtime_remains_supported(client):
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (9,3,0)
    contracts=ROOT.joinpath('src','runtime','contracts.py').read_text(encoding='utf-8')
    local=ROOT.joinpath('src','runtime','local.py').read_text(encoding='utf-8')
    selector=ROOT.joinpath('src','runtime','__init__.py').read_text(encoding='utf-8')
    for name in ('IdentityProvider','AuthorizationService','ProjectRepository','AssetStore','HistoryStore','BackupStore','CapabilityProvider','RuntimeServices'):
        assert f'class {name}' in contracts
    assert 'def build_local_runtime(' in local
    assert "if mode=='local'" in selector
    assert 'BREADMAKER_RUNTIME_UNSUPPORTED' in selector

    runtime=assert_ok(client.get('/api/runtime'))
    assert runtime['mode']=='local'
    capabilities=runtime['capabilities']
    assert capabilities['offline'] is True
    assert capabilities['native_filesystem'] is True
    assert capabilities['portable_project_export'] is True
    assert capabilities['hosted_projects'] is False
    assert capabilities['account_authentication'] is False
    assert capabilities['realtime_collaboration'] is False


def test_v930_api_edges_use_runtime_services_without_changing_project_schema(client):
    app=backend_source(ROOT)
    frontend=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    schema=ROOT.joinpath('src','storage','schema.py').read_text(encoding='utf-8')

    assert 'runtime = build_runtime(workspace)' in app
    assert 'runtime.authorization.require(permission,project_id)' in app
    assert 'runtime.projects.create(payload.name,activate=False,owner_user_id=owner_user_id)' in app
    assert 'owner_user_id=_bound_user_id() or None' in app
    assert 'require_native_projects(runtime).import_package(tmp,owner_user_id=owner_user_id)' in app
    assert 'runtime.assets.read_blob(asset_path)' in app
    assert 'runtime.assets.put_blob(payload,ext)' in app
    assert 'runtime.history.retention_settings()' in app
    assert 'runtime.backups.destinations()' in app
    assert "async function loadRuntimeBootstrap()" in frontend
    assert "try{return await api('/api/runtime')}" in frontend
    assert 'state.runtime=runtimePayload' in frontend
    assert 'function runtimeSupports(capability)' in frontend
    assert 'SCHEMA_VERSION = 42' in schema

    workspace=assert_ok(client.get('/api/workspace'))
    assert workspace['runtime']=='local'
    assert workspace['capabilities']['offline'] is True
