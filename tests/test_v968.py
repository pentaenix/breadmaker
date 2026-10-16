from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v968_release_identity_launcher_and_assets_are_synchronized():
    version=(ROOT/'VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (9,6,8)
    index=(ROOT/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    run=(ROOT/'run').read_text(encoding='utf-8')
    assert 'uvicorn src.server:app' in run


def test_v968_outer_asgi_runtime_bootstrap_is_project_free():
    server=(ROOT/'src/server.py').read_text(encoding='utf-8')
    assert "scope.get('path')=='/api/runtime'" in server
    assert "await core.app(scope,receive,send)" in server
    assert "x-breadmaker-bootstrap" in server
    assert server.index("scope.get('path')=='/api/runtime'") < server.index("await core.app(scope,receive,send)")


def test_v968_frontend_local_fallback_is_health_attested_and_hosted_fail_closed():
    frontend=frontend_source_from_app_path(ROOT/'static/app.js')
    assert "async function loadRuntimeBootstrap()" in frontend
    assert "const health=await api('/api/health')" in frontend
    assert "health?.app!=='Breadmaker Editor'||health?.runtime!=='local'" in frontend
    assert "if(![404,500].includes(Number(error?.status))||!localHost)throw error" in frontend
    assert "account_sessions:false" in frontend
    assert "loadRuntimeBootstrap()" in frontend and "state.runtime=runtimePayload" in frontend
