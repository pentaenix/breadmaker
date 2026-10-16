from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from storage import undo as storage_undo

from pathlib import Path

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]




def test_v965_boot_batches_lightweight_state_and_only_hydrates_routed_heavy_workspace():
    frontend = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    boot = frontend[frontend.index('async function boot()'):frontend.index('async function openRecoveryBoot')]
    assert "api(`/api/ui-state?keys=${encodeURIComponent(BOOT_UI_STATE_KEYS.join(','))}`)" in frontend
    assert "const bootView=viewFromRoute();" in boot
    assert "loadBootUIState()" in boot
    assert "restoreCanvasUIState(bootUI,{render:false})" in boot
    assert "restoreManuscriptUIState(bootUI,{render:false})" in boot
    assert "restoreMapsUIState(bootUI,{render:false})" in boot
    assert "if(bootView==='manuscript'||bootView==='canvas')" in boot
    assert "await hydratePrimaryWorkspace(bootView)" in boot
    assert "else if(bootView==='maps'&&state.maps.length)" in boot
    assert 'await refresh(false)' not in boot
    assert "await hydratePrimaryWorkspace(view);" in frontend
    # Workspace bodies stay visually withheld until the final hydrated view is ready.
    css = frontend_styles_source_from_styles_path(ROOT.joinpath('static', 'styles.css'))
    assert 'html:not([data-breadmaker-boot-ready]) .view{visibility:hidden}' in css


def test_v965_batched_ui_state_preserves_project_scoping(client):
    assert_ok(client.put('/api/ui-state/tabs-expanded', json={'value': False}))
    assert_ok(client.put('/api/ui-state/map-active', json={'value': 42}))
    payload = assert_ok(client.get('/api/ui-state?keys=tabs-expanded,map-active,missing-key'))
    assert payload['values'] == {'tabs-expanded': False, 'map-active': 42, 'missing-key': None}


def test_v965_structural_undo_has_bounded_canvas_and_map_scopes():
    canvas = storage_undo._session_undo_scope_tables('canvas')
    maps = storage_undo._session_undo_scope_tables('maps')
    full = storage_undo._session_undo_scope_tables('full')
    assert set(canvas) == {'canvas_nodes', 'canvas_edges'}
    assert set(maps) == {'map_lines', 'map_items', 'map_junctions', 'references_map'}
    assert len(canvas) < len(full)
    assert len(maps) < len(full)


def test_v965_canvas_map_hot_paths_use_scoped_undo_and_nonblocking_workspace_switches():
    frontend = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    assert "undoScope:'canvas'" in frontend
    assert frontend.count("undoScope:'maps'") >= 2
    start=frontend.index('async function selectCanvas(id,{persist=true}={})');end=frontend.index("$('#editCanvasDetailsBtn')",start);select_canvas=frontend[start:end]
    assert 'persistCurrentCanvasViewport().catch(console.warn)' in select_canvas
    assert 'await persistCurrentCanvasViewport().catch(console.warn)' not in select_canvas
    select_map = frontend[frontend.index('async function selectMap(id'):frontend.index('async function reloadCurrentMap')]
    assert 'state.mapTreeCache.get(map.id)' in select_map
    assert 'persistMapViewportNow(previousMapId).catch(console.warn)' in select_map
    assert 'const mapTreeDetailPromises=new Map(),mapTreeDetailQueued=new Set();' in frontend
    assert "if(currentView()!=='maps'||mapTreeDetail(mapId)" in frontend


def test_v965_package_security_validation_is_streaming_but_policy_remains_strict():
    security = ROOT.joinpath('src','security','content_security.py').read_text(encoding='utf-8')
    assert 'class _StoredHtmlValidator(HTMLParser)' in security
    assert 'return parser.safe and' in security
    assert 'is_safe_project_asset_path' in security
    assert 'len(names)!=len(set(names))' in security
    # Normal editor writes continue through the canonical sanitizer; only package
    # trust validation switched away from a per-fragment DOM allocation.
    assert 'def sanitize_inline_html' in security
    assert "BeautifulSoup(fragment or '', 'html.parser')" in security


def test_v965_scale_verification_runs_without_parallel_core_contention():
    verifier = ROOT.joinpath('scripts', 'verify_release.py').read_text(encoding='utf-8')
    assert "scale_jobs=[job for job in core_jobs if Path(job.target.split('::',1)[0]).name=='test_scale.py']" in verifier
    assert 'ordinary_core_jobs=[job for job in core_jobs if job not in scale_jobs and job not in serial_core_jobs]' in verifier
    assert 'run_stage("Core / API serial", serial_core_jobs, 1)' in verifier
    assert 'run_stage("Core / API scale", scale_jobs, 1)' in verifier


def test_v965_profile_control_is_protected_from_transient_boot_chrome_state():
    frontend = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    css = frontend_styles_source_from_styles_path(ROOT.joinpath('static', 'styles.css'))
    assert "document.body.classList.remove('focus','reviewer-mode-active');" in frontend
    assert '.topbar .brand-button,.topbar .top-actions{flex:none}' in css
    assert '.user-profile-control,.user-avatar-button{visibility:visible}' in css
