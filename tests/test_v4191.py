from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_v4191_bookmark_navigation_and_native_selection_contract():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert "button.setAttribute('aria-label','Go to bookmark')" in js
    assert "Mark one place in your text you want to come back to while writing or editing. It can be a passage, Scene, or Chapter. Click here to go there." in js
    assert "attachManuscriptToolTooltip('bookmarkBtn','bookmarkTooltip',{hoverOnly:true})" in js
    assert "if(startingMode==='book'" in js and "if(startingMode==='chapter'" in js
    assert "Never consume the browser's native double-click gesture" in js
    assert '.manuscript-block{user-select:text;-webkit-user-select:text}' in css
    assert '.text-bookmark-marker svg{width:15px;height:15px}' in css
    assert 'top:.86em;transform:translateY(-50%)' in css
    assert '### 4.19.1 Bookmark navigation and native selection polish' in readme
    assert '### 4.19.1 Bookmark navigation context + native text gestures' in architecture
