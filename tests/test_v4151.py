from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4151_comments_ui_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'id="commentComposer"' in html and 'id="commentCreateText"' in html
    assert 'id="commentSelectionBtn"' not in html
    assert 'comments-chevron-button' in html
    assert 'function renderNoteGutterMarker' in js
    assert 'function queueCommentMarkerHover(items,target)' in js
    assert 'setTimeout(()=>{if(commentHoverSourceKey===key)showCommentMarkerHover(items,target,key)},300)' in js
    assert '.note-gutter-marker' in css and '.note-gutter-count' in css
    assert '.comment-rail-layer,.comment-rail-segment' in css and 'display:none!important' in css
    assert "document.addEventListener('keydown'" in js and 'commentsKeyboardNavActive' in js
    assert "event.key==='ArrowUp'||event.key==='ArrowDown'" in js
    assert '<b>Notes</b>' in html


def test_v4151_comment_composer_is_inline_not_sidebar():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    start=js.index('async function beginCommentComposer')
    end=js.index('async function createPendingComment', start)
    body=js[start:end]
    assert 'openCommentsPanel' not in body
    assert 'positionCommentComposer(range)' in body
    assert 'paintCommentComposeFocus(items)' in body
    assert 'hideSelectionToolbar()' in body
    assert "event.metaKey||event.ctrlKey" in js
