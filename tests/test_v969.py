from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v969_release_identity_and_package_qualified_launcher_are_synchronized():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (9,6,9)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    run=(ROOT/'run').read_text(encoding='utf-8')
    assert 'uvicorn src.server:app' in run
    assert (ROOT/'src/__init__.py').is_file()


def test_v969_outer_server_import_is_unambiguous_and_never_assumes_core_runtime_global():
    server=(ROOT/'src/server.py').read_text(encoding='utf-8')
    assert 'from . import app as core' in server
    assert '\nimport app as core' not in server
    assert 'from backend import environment, request_pipeline' in server
    assert 'from security import hosted_guardrails' in server
    assert "request_pipeline._runtime_bootstrap_payload()" in server
    assert "environment._HOSTED_LIMITS" in server
    assert "core._HOSTED_LIMITS" not in server
    assert "core.hosted_guardrails" not in server
    assert 'core.runtime' not in server
    assert "scope.get('path')=='/api/runtime'" in server


def test_v969_local_bootstrap_fallback_recovers_wrapper_404_or_500_only_after_health_attestation():
    frontend=frontend_source_from_app_path(ROOT/'static/app.js')
    assert "if(![404,500].includes(Number(error?.status))||!localHost)throw error" in frontend
    assert "const health=await api('/api/health')" in frontend
    assert "health?.app!=='Breadmaker Editor'||health?.runtime!=='local'" in frontend
    assert "account_sessions:false" in frontend
