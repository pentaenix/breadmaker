from source_contracts import frontend_source_from_app_path
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v8189_canvas_viewport_persistence_is_bound_to_the_origin_canvas():
    js = frontend_source_from_app_path(ROOT / 'static/app.js')
    queue = js[js.index('function cancelQueuedCanvasViewportPersist'):js.index('async function persistCurrentCanvasViewport')]
    assert 'const canvasId=+state.currentCanvas?.id||0' in queue
    assert 'if(+state.currentCanvas?.id!==canvasId)return' in queue
    start = js.index('async function selectCanvas(id,{persist=true}={})')
    end = js.index("$('#editCanvasDetailsBtn')", start)
    select = js[start:end]
    assert 'cancelQueuedCanvasViewportPersist()' in select
    assert 'persistCurrentCanvasViewport().catch(console.warn)' in select
    assert 'await persistCurrentCanvasViewport().catch(console.warn)' not in select
    assert select.index('cancelQueuedCanvasViewportPersist()') < select.index('persistCurrentCanvasViewport().catch(console.warn)')


def test_v8189_durable_contracts_follow_current_metro_canvas_and_map_interactions():
    durable = (ROOT / 'tests/test_e2e.py').read_text(encoding='utf-8')
    helper = durable[durable.index('def _enable_metro'):durable.index('def _browser_page')]
    assert 'state.features.maps_metro_view=true' in helper
    assert 'map_renderer:true' in helper
    assert "geometry['ports']==8" in durable
    assert 'Retargeting is owned by the concrete endpoint handle' in durable
    assert "breadmaker_logo.svg' in colors['brandMask']" in durable
    assert "dispatchEvent(new MouseEvent('click',{bubbles:true}))" in durable
    assert "#mapLineDetailsColorText" in durable
    assert "#focusMapLineInspector" not in durable
    assert "#mapLineInspectorColorText" not in durable


def test_v8189_verifier_keeps_source_predicates_and_accounting_context():
    hooks = (ROOT / 'tests/conftest.py').read_text(encoding='utf-8')
    verifier = (ROOT / 'scripts/verify_release.py').read_text(encoding='utf-8')
    assert 'source_candidates' in hooks
    assert 'wait_for_function(' in hooks
    assert 'cleanup_message=f"BREADMAKER_CLEANUP_TIMEOUT:' in hooks
    assert 'incomplete pytest item; recorded' in verifier
    assert 'tests = _test_functions(e2e)' in verifier
    assert '_test_functions(e2e, "test_browser_")' not in verifier
    assert 'ACCOUNTING' in verifier
    assert 'missing {missing}' in verifier


