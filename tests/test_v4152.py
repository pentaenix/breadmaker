from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4152_comments_presentation_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    start=js.index('function renderCommentsList()')
    end=js.index('function commentAnchorLabel', start)
    body=js[start:end]
    assert 'comments-row-message' in body and 'comments-row-replies' in body
    assert 'comments-row-excerpt' not in body and 'comments-row-meta' not in body
    assert 'chapter_title' not in body
    assert 'overflow-y:auto;overflow-x:hidden' in css
    assert '.comments-list' in css and 'gap:7px' in css
    assert '-webkit-line-clamp:2' in css
    assert "marker.innerHTML=`<span class=\"note-gutter-pencil\"" in js
    assert 'note-gutter-count' in js and '.note-gutter-marker' in css
    assert 'function commentMarkerHoverKey(items,target)' in js
    assert 'function queueCommentMarkerHover(items,target)' in js
    assert 'commentHoverSourceKey' in js
    assert 'showCommentMarkerHover(items,target,key)' in js
    assert 'function handleCommentRowArrow(event)' in js
    assert 'event.preventDefault();event.stopPropagation();event.stopImmediatePropagation?.();navigateComments' in js
    assert 'row.onfocus=activateCommentsKeyboardNavigation' in js
    assert "document.addEventListener('keydown'" in js and 'commentsKeyboardNavActive' in js
    assert 'id="commentDelete"' in js and 'Delete note' in js
    assert '<b>Notes</b>' in html and 'Search notes' in html


def test_v4152_hover_preview_prefers_left_margin():
    js=frontend_source_from_app_path(Path(__file__).resolve().parents[1]/'static/app.js')
    start=js.index('function positionCommentHoverCard')
    end=js.index('function commentMarkerHoverKey', start)
    body=js[start:end]
    assert 'const markerRect=' in body
    assert 'leftRoom=Math.max' in body and 'placeLeft=leftRoom>=minUsefulWidth' in body
    assert "card.style.width=`${Math.floor(Math.min(preferredWidth,leftRoom))}px`" in body
    assert 'markerRect.left-box.width-gap' in body and 'markerRect.right+gap' in body
