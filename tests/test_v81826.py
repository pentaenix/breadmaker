from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _function_slice(source: str, name: str) -> str:
    start = source.index(f"def {name}(")
    next_def = source.find("\ndef ", start + 5)
    return source[start:] if next_def < 0 else source[start:next_def]


def test_v81826_current_browser_helper_waits_for_complete_boot_boundary():
    source = (ROOT / 'tests' / 'test_v41812_browser.py').read_text(encoding='utf-8')
    frag = _function_slice(source, '_page')
    blocks = frag.index("document.querySelectorAll('.manuscript-block').length >= 1")
    ready = frag.index("document.documentElement.dataset.breadmakerBootReady==='true'")
    assert ready > blocks
    assert 'window.__breadmakerBootPromise' in frag


def test_v81826_structural_soak_preserves_200_mutations_with_more_headroom():
    source = (ROOT / 'tests' / 'test_reliability.py').read_text(encoding='utf-8')
    frag = _function_slice(source, 'test_randomized_structural_operations_preserve_database_invariants')
    decorator_area = source[max(0, source.index('def test_randomized_structural_operations_preserve_database_invariants') - 180):source.index('def test_randomized_structural_operations_preserve_database_invariants')]
    assert "8126,8127,8128,8129,8130,8131,8132,8133,8134,8135" in decorator_area
    assert 'for step in range(20):' in frag
    assert 'Ten independent 20-operation shards preserve 200 total' in frag


def test_v81826_release_identity_and_retry_contract_are_semantic():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    css = frontend_styles_source_from_styles_path((ROOT / 'static' / 'styles.css'))
    retry_test = (ROOT / 'tests' / 'test_v81825.py').read_text(encoding='utf-8')
    assert 'result.job.category == \"current browser\" and not result.ok' in retry_test
