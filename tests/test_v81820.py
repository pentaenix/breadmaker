from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _slice(source,name):
    start=source.index(f"def {name}(")
    end=source.find("\ndef ",start+5)
    return source[start:] if end<0 else source[start:end]


def test_v81820_remote_soak_repairs_the_revision_it_actually_damaged():
    source=(ROOT/'tests'/'test_v778.py').read_text(encoding='utf-8')
    soak=_slice(source,'test_v778_randomized_cross_layer_soak_preserves_all_live_invariants')
    assert "repair_revision_id = str(newest['revision_id'])" in soak
    assert "repaired = repo.publish(store, repair_revision_id)" in soak
    assert "assert repaired.repaired is True" in soak
    assert "repo.publish(store, last_revision['revision_id'])\n            assert repaired.repaired" not in soak


def test_v81820_h1_selection_is_deterministic_but_uses_real_application_boundary():
    source=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    helper=_slice(source,'_select_manuscript_block_text')
    assert 'document.createRange()' in helper
    assert 'range.selectNodeContents(prose)' in helper
    assert 'showSelectionToolbarAfterPointer()' in helper
    assert "result['items']==1" in helper
    assert '.dblclick()' not in helper


def test_v81820_span_resize_targets_semantic_line_position_not_render_segment():
    source=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    helper=_slice(source,'_map_line_client_point_for_position')
    span=_slice(source,'test_browser_v45_span_endpoint_resize_persists')
    assert 'mapLineDisplayAxisAtPosition' in helper
    assert 'mapAxisCoordinate' in helper
    assert 'stage.scrollLeft' in helper and 'stage.scrollTop' in helper
    assert 'target_p=' in span
    assert '_map_line_client_point_for_position' in span
    assert 'routes.last' not in span
    assert 'planned_end>beforeEnd+.01' in span
    current=(ROOT/'tests'/'test_v639_browser.py').read_text(encoding='utf-8')
    current_helper=_slice(current,'_map_line_client_point_for_position')
    current_span=_slice(current,'test_v639_populated_span_resize_persists_and_station_renders_above_span')
    assert 'mapLineDisplayAxisAtPosition' in current_helper
    assert '_map_line_client_point_for_position' in current_span
