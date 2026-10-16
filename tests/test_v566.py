from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v566_release_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 6, 6)
    html=(root/'static/index.html').read_text()
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert '?v=5.6.6' in html
    assert ':root[data-theme=dusk]{--bg:#92938d;--surface:#d8d4cb;--surface2:#c6c4bc;' in css
    assert '--text:#282923;--muted:#65665f;--line:#aaa79e;--line2:#8a8981;' in css
    assert '--accent:#59695b;--accent-soft:#b3bcb2;' in css
    assert '--index-bg:#515850;--index-text:#f5f3ec' in css
    assert "MAP_DUSK_BG='#92938d'" in js
    assert "theme==='dusk'?{background:'#515850',text:'#f5f3ec'}" in js
    # The established Light and Night root palettes remain unchanged.
    assert ':root{--bg:#f3f3f1;--surface:#fff;--surface2:#f8f8f7;--text:#202020;' in css
    assert ':root[data-theme=dark]{--bg:#191a19;--surface:#222322;--surface2:#292a29;--text:#ececea;' in css
