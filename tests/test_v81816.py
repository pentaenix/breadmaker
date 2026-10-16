from source_contracts import frontend_source_from_app_path
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def _function_slice(source: str, name: str) -> str:
    start = source.index(f"def {name}(")
    next_def = source.find("\ndef ", start + 5)
    return source[start:] if next_def < 0 else source[start:next_def]


def test_v81816_stable_browser_testids_cover_fragile_interaction_surfaces():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    expected = {
        "canvas-stage": "canvasStage",
        "canvas-pointer-mode": "canvasPointerMode",
        "canvas-selection-rect": "selectionRect",
        "manuscript-selection-toolbar": "selectionToolbar",
        "manuscript-selection-highlight": None,
        "manuscript-highlight-picker": "selectionHighlightPicker",
        "markup-panel": "markupPanel",
        "marker-new": "newLabelBtn",
    }
    for testid, element_id in expected.items():
        node = soup.select_one(f'[data-testid="{testid}"]')
        assert node is not None, testid
        if element_id is not None:
            assert node.get("id") == element_id

    js = frontend_source_from_app_path(ROOT / "static" / "app.js")
    assert "wrap.dataset.testid='manuscript-block-wrap'" in js
    assert "prose.dataset.testid='manuscript-block'" in js
    assert 'data-testid="marker-empty-new"' in js
    assert "'data-testid':'map-span-handle'" in js
    assert "'data-map-item-id':item.id" in js


def test_v81816_flaky_browser_contracts_wait_on_state_not_arbitrary_sleep():
    canvas = (ROOT / "tests" / "test_v640_browser.py").read_text(encoding="utf-8")
    marquee = _function_slice(canvas, "test_v640_canvas_marquee_autoscrolls_while_pointer_is_held_at_viewport_edge")
    assert "wait_for_timeout" not in marquee
    assert "get_by_test_id('canvas-stage')" in marquee
    assert "canvas-selection-rect" in marquee

    durable = (ROOT / "tests" / "test_e2e.py").read_text(encoding="utf-8")
    span = _function_slice(durable, "test_browser_v45_span_endpoint_resize_persists")
    assert "wait_for_timeout" not in span
    assert "map-span-handle" in span
    assert "api(`/api/maps/${mapId}`)" in span


def test_v81816_direct_map_manipulation_is_split_into_focused_contracts():
    source = (ROOT / "tests" / "test_e2e.py").read_text(encoding="utf-8")
    for name in (
        "test_browser_v45_line_endpoint_drag_creates_junction",
        "test_browser_v45_station_drag_persists_position",
        "test_browser_v45_span_endpoint_resize_persists",
    ):
        assert f"def {name}(" in source
    assert "def test_browser_v45_direct_manipulation_edits_real_geometry(" not in source


def test_v81816_property_gate_keeps_total_work_while_using_smaller_shards():
    source = (ROOT / "tests" / "test_v773.py").read_text(encoding="utf-8")
    assert "list(range(7730, 7750))" in source
    assert "for i in range(100):" in source
    assert "assert len(ids) == 10" in source
    assert "2,000 canonical mutations total" in source
    assert "200 total reconstructions" in source
