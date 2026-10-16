from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v41710_markup_button_tracks_panel_active_state_and_clear_glyph_is_centered():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'id="markupBtn"' in html and 'aria-pressed="false" aria-label="Markup"' in html
    assert '.markup-manuscript-button.active{background:var(--accent-soft);border-color:var(--accent)}' in css
    assert 'function closeMarkupPanel()' in js and 'function openMarkupPanel()' in js
    assert "button.classList.add('active');button.setAttribute('aria-pressed','true')" in js
    assert "button?.classList.remove('active');" in js and "button?.setAttribute('aria-pressed','false');" in js
    assert 'top:50%;' in css
    assert 'transform:translateY(-50%) rotate(-38deg);' in css
