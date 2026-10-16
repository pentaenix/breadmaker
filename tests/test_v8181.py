from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_v8181_empty_workspace_titles_match_canvas_placeholder_and_hide_rename_affordance():
    version=ROOT.joinpath("VERSION").read_text().strip()
    assert tuple(map(int,version.split("."))) >= (8,18,1)
    js=frontend_source_from_app_path(ROOT.joinpath("static","app.js"))
    css=frontend_styles_source_from_styles_path(ROOT.joinpath("static","styles.css"))
    assert "canvasDetails.classList.add('hidden')" in js
    assert "canvasDetails.classList.remove('hidden')" in js
    assert ".map-current-name.map-title-placeholder{color:var(--muted)!important;opacity:1}" in css
    assert ".review-current-name.review-title-placeholder{color:var(--muted)!important;font-weight:700}" in css
