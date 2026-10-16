from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v4157_follow_up_and_selection_cuts_copy_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'data-selection-action="cuts">Cut</button>' in html
    assert 'data-selection-action="cuts">Move to Cuts</button>' not in html
    assert 'Add follow-up' in js
    assert 'Add a follow-up…' in js
    assert 'Follow-up added' in js
    assert "'follow-up':'follow-ups'" in js
    detail=js[js.index('function renderCommentsDetail'):js.index('async function reloadCommentsWorkspace')]
    assert 'add a response' not in detail.lower()
    assert 'aria-label="Response"' not in detail
