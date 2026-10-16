from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4156_notes_response_density_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'function autoSizeCommentResponse' in js
    assert 'line*5' in js and "area.style.height='auto'" in js
    assert "rows=\"${i?1:5}\"" in js
    assert 'id=\"commentReplyText\" rows=\"1\"' in js
    assert '.comments-list{flex-basis:48%}' in css
    assert '.comment-response-textarea,#commentReplyText' in css
    assert 'max-height:calc(5 * 1.45em + 14px)' in css
    assert 'resize:none!important' in css
