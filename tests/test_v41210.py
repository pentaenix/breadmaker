from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from bs4 import BeautifulSoup


def test_v41210_view_focus_and_chevron_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    soup=BeautifulSoup(html,'html.parser')
    assert soup.select_one('#focusBtn') is None
    focus=soup.select_one('#viewFocusBtn')
    assert focus and focus.get('role')=='menuitemcheckbox' and focus.get('aria-checked')=='false'
    assert soup.select_one('#viewBtn svg.view-menu-chevron path') is not None
    assert '.view-menu-chevron path' in css
    assert "$('#viewFocusBtn')?.addEventListener('click'" in js
    assert 'syncFocusModeControl' in js


def test_v41210_undo_stack_is_commit_on_success_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    # Risky restore happens while the target is only peeked. The stack is consumed
    # only after the restore API/operation succeeds, allowing a failed Undo to retry.
    undo=js[js.index('async function performUndo()'):js.index('async function performRedo()')]
    redo=js[js.index('async function performRedo()'):js.index('function requestHistory')]
    assert "const target=historyPeek('undo')" in undo
    structural_undo=undo[undo.index("else if(target.kind==='structural')"):]
    assert structural_undo.index("await api(`/api/undo/apply/${target.token}?direction=undo`") < structural_undo.index("historyPop('undo')")
    assert '/api/undo/restore/' not in undo
    assert "const target=historyPeek('redo')" in redo
    structural_redo=redo[redo.index("else if(target.kind==='structural')"):]
    assert structural_redo.index("await api(`/api/undo/apply/${target.token}?direction=redo`") < structural_redo.index("historyPop('redo')")
    assert '/api/undo/restore/' not in redo
    assert 'await state.structuralChain;' in js
    assert "The action is still available—please try again." in js
