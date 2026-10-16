from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v570_release_and_dusk_reading_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 7, 0)
    html=(root/'static/index.html').read_text()
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert '?v=5.7.0' in html

    # Light and Night are intentionally unchanged.
    assert ':root{--bg:#f3f3f1;--surface:#fff;--surface2:#f8f8f7;--text:#202020;' in css
    assert ':root[data-theme=dark]{--bg:#191a19;--surface:#222322;--surface2:#292a29;--text:#ececea;' in css

    # Dusk is a soft-paper, dark-text theme rather than a mid-gray/dark editor.
    assert ':root[data-theme=dusk]{--bg:#b6b1a8;--surface:#dfdbd2;--surface2:#cbc6bc;--text:#292a27;' in css
    assert ':root[data-theme=dusk] .paper{background:#e2ded5;' in css
    assert "MAP_DUSK_BG='#b6b1a8'" in js
    assert "theme==='dusk'?{background:'#4f5650',text:'#f3f0e8'}" in js

    # Editor's Eye must have a real Dusk palette; Dusk may never fall through to Light.
    assert "function editorsEyeTheme(){const theme=document.documentElement.dataset.theme||'light';return THEME_CYCLE.includes(theme)?theme:'light'}" in js
    assert js.count('dusk:{bg:') == 6
