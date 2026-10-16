from source_contracts import frontend_source_from_app_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _slice(source,name):
    start=source.index(f"def {name}(")
    end=source.find("\ndef ",start+5)
    return source[start:] if end<0 else source[start:end]


def test_v81819_randomized_gates_preserve_aggregate_work_with_teardown_headroom():
    v773=(ROOT/'tests'/'test_v773.py').read_text(encoding='utf-8')
    assert "list(range(7730, 7750))" in v773
    assert "for i in range(100):" in v773
    assert "assert len(ids) == 10" in v773
    assert "2,000 canonical mutations total" in v773
    assert "200 total reconstructions" in v773

    v778=(ROOT/'tests'/'test_v778.py').read_text(encoding='utf-8')
    soak=_slice(v778,'test_v778_randomized_cross_layer_soak_preserves_all_live_invariants')
    assert "list(range(7780, 7812))" in v778
    assert "for i in range(10):" in soak
    assert "320 total" in soak
    assert "if i == 5:" in soak
    assert "shard % 4 == 0" in soak
    assert "if i == 9:" in soak


def test_v81819_durable_ui_contracts_use_deterministic_selection_and_logical_geometry_boundaries():
    source=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    selection=_slice(source,'_select_manuscript_block_text')
    assert 'document.createRange()' in selection
    assert 'showSelectionToolbarAfterPointer()' in selection
    assert "manuscript-style-chapter" in frontend_source_from_app_path(ROOT/'static'/'app.js')

    span=_slice(source,'test_browser_v45_span_endpoint_resize_persists')
    helper=_slice(source,'_map_line_client_point_for_position')
    assert 'mapLineDisplayAxisAtPosition' in helper
    assert '_map_line_client_point_for_position' in span
    assert 'planned_end>beforeEnd+.01' in span


def test_v81819_note_fixture_embeds_escaped_newlines_for_browser_evaluation():
    source=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    flyer=_slice(source,'test_browser_v4155_note_flyer_left_scrollable_and_five_line_editor')
    assert "Array.from({length:18}" in flyer
    assert ".join('\\\\n')" in flyer
