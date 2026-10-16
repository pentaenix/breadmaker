from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from bs4 import BeautifulSoup


def test_v4129_shell_contract_and_version():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    soup=BeautifulSoup(html,'html.parser')
    top=soup.select_one('.top-actions')
    children=[node.get('id') or 'group' for node in top.find_all(recursive=False) if getattr(node,'name',None)]
    assert children[:3]==['globalSaveState','group','quickOpenBtn']
    history=top.select_one('.global-history-controls')
    assert history.select_one('#undoBtn') and history.select_one('#redoBtn')
    assert not soup.select_one('.document-toolbar #undoBtn')
    assert not soup.select_one('.document-toolbar #redoBtn')
    assert not soup.select_one('#splitViewBtn')
    assert soup.select_one('#viewBtn')
    assert soup.select_one('#viewFocusBtn')
    assert soup.select_one('#viewSplitBtn')
    slider=soup.select_one('#manuscriptTextSize')
    assert slider['min']=='11' and slider['max']=='28' and slider['step']=='0.5' and slider['value']=='14'


def test_v4129_text_size_is_presentation_only_and_persistent_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert "MANUSCRIPT_TEXT_SIZE_KEY='breadmaker-manuscript-text-size'" in js
    assert "localStorage.setItem(MANUSCRIPT_TEXT_SIZE_KEY" in js
    assert "document.documentElement.style.setProperty('--manuscript-font-size'" in js
    assert "restoreManuscriptTextSize();" in js
    assert "font-size:var(--manuscript-font-size,14px)" in css
    assert "#viewSplitBtn" in js and "openSplitViewChooser()" in js
