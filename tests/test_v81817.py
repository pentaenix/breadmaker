from source_contracts import frontend_source_from_app_path
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]

def _slice(source,name):
    start=source.index(f"def {name}(")
    end=source.find("\ndef ",start+5)
    return source[start:] if end<0 else source[start:end]

def test_v81817_stable_hooks_cover_remaining_interaction_boundaries():
    html=(ROOT/'static'/'index.html').read_text(encoding='utf-8')
    soup=BeautifulSoup(html,'html.parser')
    for testid in ('manuscript-selection-style','manuscript-navigator-menu','manuscript-view-scenes'):
        assert soup.select_one(f'[data-testid="{testid}"]') is not None
    js=frontend_source_from_app_path(ROOT/'static'/'app.js')
    for token in ("button.dataset.testid=item.testId","'navigator-add-scene'","'navigator-toggle-scenes'","'data-testid':'map-line-route'"):
        assert token in js

def test_v81817_remaining_durable_contracts_are_state_synchronized():
    source=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    for name in ('test_browser_v45_span_endpoint_resize_persists','test_browser_v410_h1_creates_chapter_in_focused_and_book_views','test_v4185_per_tab_navigation_visibility_and_scene_markers'):
        frag=_slice(source,name)
        assert 'wait_for_timeout' not in frag
    assert "manuscript-selection-style" in _slice(source,'test_browser_v410_h1_creates_chapter_in_focused_and_book_views')
    assert "navigator-add-scene" in _slice(source,'test_v4185_per_tab_navigation_visibility_and_scene_markers')
    span=_slice(source,'test_browser_v45_span_endpoint_resize_persists')
    assert '_map_line_client_point_for_position' in span and 'planned_end>beforeEnd+.01' in span

def test_v81817_current_span_contract_uses_same_stable_geometry_wait():
    source=(ROOT/'tests'/'test_v639_browser.py').read_text(encoding='utf-8')
    frag=_slice(source,'test_v639_populated_span_resize_persists_and_station_renders_above_span')
    assert 'wait_for_timeout' not in frag
    assert 'map-span-handle' in frag and '_map_line_client_point_for_position' in frag
    assert 'planned_end>beforeEnd+.01' in frag
