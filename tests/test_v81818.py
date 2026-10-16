from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _slice(source,name):
    start=source.index(f"def {name}(")
    end=source.find("\ndef ",start+5)
    return source[start:] if end<0 else source[start:end]


def test_v81818_svg_route_geometry_avoids_visibility_heuristics():
    durable=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    helper=_slice(durable,'_map_line_client_point_for_position')
    span=_slice(durable,'test_browser_v45_span_endpoint_resize_persists')
    assert 'mapLineDisplayAxisAtPosition' in helper
    assert 'getScreenCTM()' in helper
    assert 'stage.scrollLeft' in helper or 'stage.scrollTop' in helper
    assert '_map_line_client_point_for_position' in span
    assert "route.wait_for(state='visible')" not in span

    current=(ROOT/'tests'/'test_v639_browser.py').read_text(encoding='utf-8')
    current_span=_slice(current,'test_v639_populated_span_resize_persists_and_station_renders_above_span')
    current_helper=_slice(current,'_map_line_client_point_for_position')
    assert 'wait_for_timeout' not in current_span
    assert '_map_line_client_point_for_position' in current_span
    assert 'mapLineDisplayAxisAtPosition' in current_helper
    assert 'getScreenCTM()' in current_helper


def test_v81818_durable_h1_selection_crosses_selection_lifecycle_boundary():
    source=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    helper=_slice(source,'_select_manuscript_block_text')
    h1=_slice(source,'test_browser_v410_h1_creates_chapter_in_focused_and_book_views')
    assert 'document.createRange()' in helper
    assert 'showSelectionToolbarAfterPointer()' in helper
    assert "manuscript-selection-toolbar" in helper
    assert h1.count('_select_manuscript_block_text(page,')==2


def test_v81818_note_scroll_fixture_is_deterministically_multiline():
    source=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    flyer=_slice(source,'test_browser_v4155_note_flyer_left_scrollable_and_five_line_editor')
    assert 'Array.from({length:18}' in flyer
    assert ".join('\\\\n')" in flyer
    assert "scrollHeight'] > pos['clientHeight']" in flyer
