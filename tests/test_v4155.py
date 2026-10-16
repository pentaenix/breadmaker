from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4155_notes_flyer_and_editor_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert "commentHoverTimer=setTimeout" in js and "},300)" in js
    fn=js[js.index('function positionCommentHoverCard'):js.index('function commentMarkerHoverKey')]
    assert 'markerRect.left' in fn and 'markerRect.right' in fn
    assert 'proseRect.left' not in fn and 'proseRect.right' not in fn
    assert 'max-height:calc(10 * 1.4em + 18px)' in css
    assert '.comment-hover-card.comment-hover-preview,.comment-hover-card.comment-hover-picker{pointer-events:auto}' in css
    assert '-webkit-line-clamp:unset' in css
    assert 'rows="${i?1:5}"' in js
    assert 'height:calc(5 * 1.45em + 12px)' in css and 'resize:vertical' in css
