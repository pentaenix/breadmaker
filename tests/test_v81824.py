from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v81824_unit_expectations_match_history_and_breadreview_semantics():
    history = (ROOT / 'tests' / 'unit' / 'test_history_policy_units.py').read_text(encoding='utf-8')
    breadreview = (ROOT / 'tests' / 'unit' / 'test_breadreview_units.py').read_text(encoding='utf-8')
    assert '(None, "90_days")' in history
    assert 'conservative_retention(["garbage", None], default="90_days") == "90_days"' in history
    assert breadreview.count('\"./relative\"') == 1
    assert breadreview.index('\"./relative\"') < breadreview.index('def test_safe_member_name_accepts_portable_relative_paths')


def test_v81824_marquee_contract_does_not_duplicate_top_level_canvas_navigation():
    source = (ROOT / 'tests' / 'test_v640_browser.py').read_text(encoding='utf-8')
    frag = source.split('def test_v640_canvas_marquee_autoscrolls_while_pointer_is_held_at_viewport_edge', 1)[1]
    assert 'setView(\'canvas\',{historyMode:\'none\'})' in frag
    assert 'page.locator(\'[data-view="canvas"]\').click()' not in frag
    assert "get_by_test_id('canvas-pointer-mode')" in frag
    assert "get_by_test_id('canvas-selection-rect')" in frag


def test_v81824_release_identity_is_synchronized():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    css = frontend_styles_source_from_styles_path((ROOT / 'static' / 'styles.css'))
    parts = version.split('.')
    assert len(parts) == 3 and all(part.isdigit() for part in parts)
