from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from bs4 import BeautifulSoup


def test_v413_manuscript_clean_toolbar_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    soup=BeautifulSoup(html,'html.parser')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html

    toolbar=soup.select_one('.document-toolbar')
    direct=[x.get('id') for x in toolbar.find_all(recursive=False) if x.get('id')]
    assert direct==['activeDocumentTitle','findBtn','bookmarkBtn','commentsBtn','markupBtn','checkpointBtn','cutsBtn','editorsEyeBtn']
    assert soup.select_one('#viewBtn') is not None
    view_wrap=soup.select_one('.manuscript-view-control')
    assert view_wrap and view_wrap.find('button',id='viewBtn')
    # View is before the two icon-only manuscript tools in DOM order.
    children=[x for x in toolbar.find_all(recursive=False)]
    assert children.index(view_wrap) < children.index(soup.select_one('#findBtn')) < children.index(soup.select_one('#commentsBtn')) < children.index(soup.select_one('#markupBtn'))

    assert soup.select_one('#blockStyleBtn') is None
    assert soup.select_one('#boldBtn') is None and soup.select_one('#italicBtn') is None and soup.select_one('#underlineBtn') is None
    selection=soup.select_one('#selectionToolbar')
    buttons=selection.select('button')
    assert buttons[0].get('data-selection-action')=='style' and buttons[0].get_text(strip=True)=='Aa'
    assert [b.get('data-selection-command') for b in buttons[1:4]]==['bold','italic','underline']
    assert "if(action==='style')" in js and 'manuscriptStyleMenuItems()' in js

    find=soup.select_one('#findBtn'); markup=soup.select_one('#markupBtn')
    assert find and find.find('svg') and find.get('aria-describedby')=='findTooltip' and find.get('title') is None
    assert markup and markup.find('svg') and markup.get('aria-describedby')=='markupTooltip' and markup.get('title') is None
    assert soup.select_one('#findTooltip b').get_text(strip=True)=='Find & Replace'
    assert soup.select_one('#markupTooltip b').get_text(strip=True)=='Markup'
    assert '.tool-icon-button' in css and '.markup-manuscript-button svg' in css

    book=soup.select_one('#bookViewBtn')
    assert book.get_text(strip=True)=='Full Text' and 'full-document-row' in book.get('class',[])
    assert '.full-document-row{' in css


def test_v413_selection_menus_are_viewport_aware_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert "function openContextMenu(x,y,items,{anchorRect=null}={})" in js
    assert "if(anchorRect&&top+h>innerHeight-margin)top=anchorRect.top-h-6" in js
    assert "top=Math.max(margin,Math.min(top,innerHeight-h-margin))" in js
    assert 'max-width:calc(100vw - 16px)' in css
