from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _function_slice(source: str, name: str) -> str:
    start = source.index(f"def {name}(")
    next_def = source.find("\ndef ", start + 5)
    return source[start:] if next_def < 0 else source[start:next_def]


def test_v81827_app_exposes_explicit_boot_completion_boundary():
    app = frontend_source_from_app_path(ROOT / 'static' / 'app.js')
    start = app.index('window.__breadmakerBootPromise=boot()')
    ready = app.index("document.documentElement.dataset.breadmakerBootReady='true'", start)
    assert ready > start
    assert '.then(()=>' in app[start:ready + 120]


def test_v81827_browser_helper_waits_for_real_boot_promise_not_initial_view_marker():
    source = (ROOT / 'tests' / 'test_v41812_browser.py').read_text(encoding='utf-8')
    frag = _function_slice(source, '_page')
    assert 'window.__breadmakerBootPromise' in frag
    assert "breadmakerBootReady==='true'" in frag
    assert "!document.documentElement.hasAttribute('data-initial-view')" not in frag


def test_v81827_marquee_activation_is_synchronous_after_boot_boundary():
    source = (ROOT / 'tests' / 'test_v640_browser.py').read_text(encoding='utf-8')
    frag = _function_slice(source, 'test_v640_canvas_marquee_autoscrolls_while_pointer_is_held_at_viewport_edge')
    assert "setView('canvas',{historyMode:'none'})" in frag
    assert "assert activated=={'view':'canvas','active':True}" in frag


