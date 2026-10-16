from source_contracts import backend_source,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]




def test_v9614_runtime_bootstrap_source_contract_targets_actual_guard_body():
    app=backend_source(ROOT)
    test=(ROOT/'tests/test_v967.py').read_text(encoding='utf-8')
    assert 'async def local_origin_guard(' in app
    assert "start=APP.index('async def local_origin_guard(')" in test
    assert "path=='/api/runtime'" in test


def test_v9614_project_import_contract_matches_explicit_owner_binding():
    app=backend_source(ROOT)
    assert 'owner_user_id=_bound_user_id() or None' in app
    assert 'require_native_projects(runtime).import_package(tmp,owner_user_id=owner_user_id)' in app
