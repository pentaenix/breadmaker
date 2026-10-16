from __future__ import annotations
from source_contracts import backend_source,frontend_source_from_app_path

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v931_asset_uploads_are_project_bound_and_runtime_discovery_fails_closed():
    version=tuple(map(int,ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.')))
    assert version >= (9,3,1)
    local=ROOT.joinpath('src','runtime','local.py').read_text(encoding='utf-8')
    app=backend_source(ROOT)
    frontend=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert "if current_root!=bound_root:raise RuntimeError('PROJECT_ASSET_CONTEXT_CHANGED')" in local
    assert "if str(exc)=='PROJECT_ASSET_CONTEXT_CHANGED'" in app
    assert "try{return await api('/api/runtime')}" in frontend
    assert 'state.runtime=runtimePayload' in frontend
    assert "catch(()=>({mode:'local',capabilities:{}}))" not in frontend
    assert "state.workspace.runtime!==state.runtime?.mode" in frontend


def test_v931_runtime_capabilities_gate_environment_specific_backup_edges():
    app=backend_source(ROOT)
    frontend=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert "_require_runtime_capability('native_filesystem'" in app
    assert "_require_runtime_capability('direct_remote_backups'" in app
    assert "runtimeSupports('native_filesystem')" in frontend
    assert "runtimeSupports('direct_remote_backups')" in frontend
