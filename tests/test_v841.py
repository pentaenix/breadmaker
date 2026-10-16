from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_v841_release_and_permission_alignment_contract():
    version=ROOT.joinpath("VERSION").read_text(encoding="utf-8").strip()
    assert tuple(map(int,version.split("."))) >= (8,4,1)
    html=ROOT.joinpath("static","index.html").read_text(encoding="utf-8")
    css=frontend_styles_source_from_styles_path(ROOT.joinpath("static","styles.css"))
    assert ".field .review-permission-option{display:flex;align-items:center;gap:9px;margin:0" in css
    assert ".review-permission-option input{width:17px!important;height:17px;min-width:17px" in css
