from __future__ import annotations
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];SRC=ROOT/'src'
if str(SRC) not in sys.path:sys.path.insert(0,str(SRC))
def read(path):return (ROOT/path).read_text(encoding='utf-8')

def test_v10338_release_and_schema_stay_explicit():
    assert tuple(map(int, read('VERSION').strip().split("."))) >= (10, 3, 38) and 'APP_SCHEMA_VERSION = 42' in read('src/workspace/constants.py')

def test_v10338_local_runtime_remains_zero_cloud_identity(monkeypatch):
    local=read('src/runtime/local.py');requirements=read('requirements.txt').lower();hosted=read('requirements-hosted.txt').lower()
    assert 'identity_platform' not in local and 'firebase' not in requirements and 'google-cloud-secret-manager' not in requirements and 'google-cloud-secret-manager' not in hosted
    import runtime as runtime_module
    marker=object();monkeypatch.delenv('BREADMAKER_RUNTIME',raising=False);monkeypatch.setattr(runtime_module,'build_local_runtime',lambda workspace:marker);assert runtime_module.build_runtime(object()) is marker

def test_v10338_hosted_runtime_is_deployed_identity_platform_https_with_external_authority():
    hosted=read('src/runtime/hosted.py')
    for marker in ['HOSTED_RUNTIME_ENVIRONMENT_REQUIRED','HOSTED_RUNTIME_IDENTITY_PLATFORM_REQUIRED','HOSTED_RUNTIME_HTTPS_ORIGIN_REQUIRED','HOSTED_RUNTIME_DEV_BYPASS_FORBIDDEN','HOSTED_RUNTIME_PUBLIC_HOST_PIN_REQUIRED','HOSTED_RUNTIME_SECURE_COOKIE_REQUIRED']:
        assert marker in hosted
    assert "environment not in {'staging','production'}" in hosted
    assert "'production_ready':environment=='production'" in hosted
    assert 'HOSTED_SINGLE_WORKER_REQUIRED' not in hosted and "'single_worker_required':False" in hosted
    assert "else 'identity_platform'" in hosted and "auth_mode!='identity_platform'" in hosted and 'auth0' not in hosted.lower()


def test_v10338_hosted_runtime_accepts_only_staging_or_production(monkeypatch):
    from runtime.hosted import _validate_mode

    monkeypatch.setenv('BREADMAKER_PUBLIC_ORIGIN', 'https://app.breadmaker.studio')
    monkeypatch.setenv('BREADMAKER_ENFORCE_PUBLIC_HOST', '1')
    monkeypatch.delenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', raising=False)
    _validate_mode('hosted', 'identity_platform', 'staging')
    _validate_mode('hosted', 'identity_platform', 'production')
    with pytest.raises(RuntimeError, match='HOSTED_RUNTIME_ENVIRONMENT_REQUIRED'):
        _validate_mode('hosted', 'identity_platform', 'development')

def test_v10338_current_hosted_auth_surface_is_identity_platform_only():
    auth=read('src/hosted/http_auth.py').lower();routes=read('src/backend/runtime_routes.py').lower()
    for route in ['/auth/login','/api/auth/session/exchange','/api/auth/csrf','/api/auth/security','/api/auth/security/proof','/api/auth/security/sync','/api/auth/security/sign-out-everywhere']:
        assert route in auth
    assert 'identity_platform' in auth and 'auth0' not in auth
    assert 'auth0' not in routes

def test_v10338_hosted_image_is_nonroot_ephemeral_staging_image():
    d=read('deploy/Dockerfile.hosted');assert 'requirements-hosted.txt' in d and 'BREADMAKER_RUNTIME=hosted' in d and 'BREADMAKER_ENV=staging' in d and 'BREADMAKER_AUTH_MODE=identity_platform' in d and 'BREADMAKER_WORKERS=1' in d and 'USER 10001:10001' in d and 'VOLUME' not in d and 'BREADMAKER_HOME=/data' not in d

def test_v10338_load_balancer_health_and_hsts_boundaries_remain():
    server=read('src/server.py');boundary=read('src/backend/http_boundary.py');pipeline=read('src/backend/request_pipeline.py')
    assert '_send_load_balancer_health' in server and 'strict-transport-security' in server and "!= 'identity_platform'" in server
    assert "configured_hosted_https=runtime.mode=='hosted'" in boundary and 'Strict-Transport-Security' in boundary
    assert "=='identity_platform'" in pipeline and '_rate_limit_client_identity(request)' in pipeline


@pytest.mark.parametrize('method',['GET','HEAD'])
@pytest.mark.parametrize('path',['/health','/healthz'])
def test_cloud_run_compatible_health_reaches_data_free_handler(method,path):
    import ast,asyncio
    tree=ast.parse(read('src/server.py'))
    node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='app')
    calls=[]
    async def health(send,*,head):calls.append(head)
    namespace={'_send_load_balancer_health':health}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<health-boundary>','exec'),namespace)
    asyncio.run(namespace['app']({'type':'http','path':path,'method':method},None,None))
    assert calls==[method=='HEAD']
