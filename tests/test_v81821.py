from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _slice(source,name):
    start=source.index(f"def {name}(")
    end=source.find("\ndef ",start+5)
    return source[start:] if end<0 else source[start:end]


def test_v81821_h1_conversion_separates_selection_semantics_from_playwright_actionability():
    source=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    test=_slice(source,'test_browser_v410_h1_creates_chapter_in_focused_and_book_views')
    assert test.count("_select_manuscript_block_text(page,") == 2
    assert test.count("document.querySelector('[data-testid=\\\"manuscript-selection-style\\\"]')") == 2
    assert test.count("document.querySelector('[data-testid=\\\"manuscript-style-chapter\\\"]')?.click()") == 2
    assert "get_by_test_id('manuscript-selection-style').click()" not in test
    assert "get_by_test_id('manuscript-selection-style').wait_for" not in test


def test_v81821_h1_selection_helper_still_proves_real_breadmaker_selection_state():
    source=(ROOT/'tests'/'test_e2e.py').read_text(encoding='utf-8')
    helper=_slice(source,'_select_manuscript_block_text')
    assert 'showSelectionToolbarAfterPointer()' in helper
    assert "assert result['items']==1" in helper
    assert "assert result['visible']" in helper
    assert "manuscript-selection-toolbar" in helper
