from source_contracts import frontend_source_from_app_path
from pathlib import Path

from conftest import assert_ok


def test_v5416_active_canvas_ui_state_is_project_scoped(client, fresh_project):
    first = assert_ok(client.post('/api/canvases', json={'name': 'First', 'canvas_type': 'blank'}))
    second = assert_ok(client.post('/api/canvases', json={'name': 'Second', 'canvas_type': 'blank'}))

    assert_ok(client.put('/api/ui-state/canvas-active', json={'value': second['id']}))
    saved = assert_ok(client.get('/api/ui-state/canvas-active'))
    assert saved['value'] == second['id']

    # A stale remembered id is harmless: the browser validates it against the
    # current project's Canvas list before trying to restore it.
    assert_ok(client.put('/api/ui-state/canvas-active', json={'value': 999999999}))
    stale = assert_ok(client.get('/api/ui-state/canvas-active'))
    assert stale['value'] == 999999999
    assert first['id'] != second['id']


def test_v5416_static_canvas_refresh_memory_contract():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root / 'static' / 'app.js')

    assert "api('/api/ui-state/canvas-active')" in js
    assert "api('/api/ui-state/canvas-active',{method:'PUT'" in js
    assert 'rememberedCanvasId=state.lastCanvasId&&state.canvases.some' in js
    assert 'if (rememberedCanvasId) await selectCanvas(rememberedCanvasId);' in js
    # Existing per-Canvas viewport memory is intentionally reused once the right
    # Canvas is restored, so zoom/pan/lane position return too.
    assert 'await restoreCanvasViewport(id);' in js
