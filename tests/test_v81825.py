from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _function_slice(source: str, name: str) -> str:
    start = source.index(f"def {name}(")
    next_def = source.find("\ndef ", start + 5)
    return source[start:] if next_def < 0 else source[start:next_def]


def test_v81825_current_browser_retry_is_single_fresh_process_and_transparent():
    source = (ROOT / 'scripts' / 'verify_release.py').read_text(encoding='utf-8')
    retry = _function_slice(source, 'retry_failed_current_browser')
    assert 'result.job.category == "current browser" and not result.ok' in retry
    assert 'run_stage("Browser retry"' in retry
    assert 'original.retry_attempted = True' in retry
    assert 'original.retry_recovered = retry.ok' in retry
    # Filtering exclusively for the current-browser category is the semantic
    # exclusion rule: Unit/Core and Durable results never enter this retry path.
    assert 'PASS-RETRY' in source
    assert 'Browser retry jobs:' in source
    assert 'Initial failure:' in source
    assert 'Confirmed failed logical jobs:' in source


def test_v81825_marquee_contract_selects_canvas_without_racing_rail_render():
    source = (ROOT / 'tests' / 'test_v640_browser.py').read_text(encoding='utf-8')
    frag = _function_slice(source, 'test_v640_canvas_marquee_autoscrolls_while_pointer_is_held_at_viewport_edge')
    assert "state.canvases.some(canvas=>+canvas.id===+id)" in frag
    assert "page.evaluate('(id)=>selectCanvas(id)',canvas['id'])" in frag
    assert '.canvas-item[data-id=' not in frag
    assert 'wait_for_timeout' not in frag


def test_v81825_timeout_layers_and_release_identity_remain_synchronized():
    hooks = (ROOT / 'tests' / 'conftest.py').read_text(encoding='utf-8')
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    css = frontend_styles_source_from_styles_path((ROOT / 'static' / 'styles.css'))
    assert 'UNIT_TEST_TIMEOUT_SECONDS = 3.0' in hooks
    assert 'CORE_API_TEST_TIMEOUT_SECONDS = 10.0' in hooks
    assert 'BROWSER_TEST_TIMEOUT_SECONDS = 15.0' in hooks
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    parts = version.split('.')
    assert len(parts) == 3 and all(part.isdigit() for part in parts)
    assert '## 8.18.25 — Transparent browser retry and deterministic Canvas marquee setup' in readme
