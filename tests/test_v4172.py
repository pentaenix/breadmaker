from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_find_expander_uses_standard_chevron_and_tracks_state():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    js=frontend_source_from_app_path(root/'static/app.js')
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'class="bare-button find-expand-button"' in html
    assert 'class="find-menu-chevron"' in html
    assert 'viewBox="0 0 10 6"' in html
    assert 'aria-expanded="false"' in html
    assert '.find-expand-button[aria-expanded="true"] .find-menu-chevron{transform:rotate(180deg)}' in css
    assert 'function setFindReplaceExpanded(expanded)' in js
    assert "setAttribute('aria-expanded',expanded?'true':'false')" in js
    assert "setFindReplaceExpanded(false);$('#findPopover').classList.add('hidden')" in js
