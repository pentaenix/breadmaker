from source_contracts import frontend_source_from_app_path
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]


def test_v4192_toolbar_clarity_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    soup=BeautifulSoup(html,'html.parser')
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html

    checkpoint=soup.select_one('#checkpointBtn svg')
    assert checkpoint is not None
    paths=[p.get('d','') for p in checkpoint.select('path')]
    assert 'M6 21V4' in paths
    assert 'M6 5h11l-2.5 3L17 11H6' in paths
    assert 'M6.5 3.5h11v17l-5.5-3.6-5.5 3.6z' not in paths

    expected='Mark one place in your text you want to come back to while writing or editing. It can be a passage, Scene, or Chapter. Click here to go there.'
    assert soup.select_one('#bookmarkTooltipText').get_text(' ',strip=True)==expected
    assert f"text.textContent='{expected}'" in js

    view=soup.select_one('#viewTooltip')
    assert view is not None
    text=view.get_text(' ',strip=True)
    assert 'Show Chapters' in text
    assert 'Chapter headers and separators' in text
    assert 'Show Scenes' in text
