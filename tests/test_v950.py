from storage import database as storage_database

from source_contracts import backend_source,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v950_release_and_hosted_runtime_contract():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (9,5,0)
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    selector=ROOT.joinpath('src','runtime','__init__.py').read_text(encoding='utf-8')
    hosted=ROOT.joinpath('src','runtime','hosted.py').read_text(encoding='utf-8')
    app=backend_source(ROOT)
    assert "mode=='hosted_dev'" in selector
    assert 'HOSTED_DEV_UNAUTHENTICATED_DISABLED' in hosted
    assert "'hosted_projects':True" in hosted
    assert "'account_authentication':False" in hosted
    assert "'native_filesystem':False" in hosted
    assert 'request_user_id' in hosted
    assert "runtime.mode=='local'" in app
    assert 'Breadmaker hosted requests must come from the same origin.' in app
    assert "out.pop('path',None)" in app
    assert "'hosted_persistence':True" in hosted
    assert "'single_worker_required':False" in hosted


def test_v950_hosted_dev_packaging_is_explicit_and_local_runtime_remains_default():
    selector=ROOT.joinpath('src','runtime','__init__.py').read_text(encoding='utf-8')
    launcher=ROOT.joinpath('run-hosted-dev').read_text(encoding='utf-8')
    dockerfile=ROOT.joinpath('deploy','Dockerfile.hosted-dev').read_text(encoding='utf-8')
    assert "or 'local'" in selector
    assert 'BREADMAKER_RUNTIME=hosted_dev' in launcher
    assert 'BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED=1' in launcher
    assert 'BREADMAKER_HOME=/data' in dockerfile
    assert 'BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED' in dockerfile


def test_v950_hosted_dev_identity_is_server_selected_and_opt_in_only(monkeypatch,tmp_path):
    import pytest
    from runtime import build_runtime
    from workspace import Workspace

    ws=Workspace(tmp_path/'hosted-home')
    monkeypatch.setenv('BREADMAKER_RUNTIME','hosted_dev')
    monkeypatch.delenv('BREADMAKER_AUTH_MODE',raising=False)
    monkeypatch.delenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED',raising=False)
    with pytest.raises(RuntimeError,match='HOSTED_DEV_UNAUTHENTICATED_DISABLED'):
        build_runtime(ws)

    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED','1')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_NAME','Hosted Tester')
    runtime=build_runtime(ws)
    try:
        runtime.projects.init()
        uid=runtime.identity.active_profile_id()
        assert runtime.identity.request_user_id('usr_browser_spoof')==uid
        assert runtime.identity.current_profile()['kind']=='hosted_dev'
        assert runtime.capabilities.supports('hosted_projects') is True
        assert runtime.capabilities.supports('native_filesystem') is False
        rec=runtime.projects.create('Hosted Project',activate=False)
        with runtime.context.bind(rec['id'],user_id=uid) as context:
            assert context.actor.user_id==uid
            assert context.actor.kind=='hosted_dev'
            assert context.actor.role=='owner'
    finally:
        storage_database.release_all_instance_locks()
