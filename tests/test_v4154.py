from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module


def _ok(response):
    assert response.status_code < 400, response.text
    return response.json()



def test_v4154_notes_ui_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'Attach notes to passages, add follow-up thoughts and resolve them when you are done.' in html
    detail=js[js.index('function renderCommentsDetail'):js.index('async function reloadCommentsWorkspace')]
    assert "thread.excerpt" not in detail and 'Anchored manuscript text' not in detail
    assert 'id="commentReplySend"' in detail and ' disabled>Add follow-up</button>' in detail
    assert "replyArea.oninput=()=>{syncCommentReplyButton();autoSizeCommentResponse(replyArea)}" in detail
    assert "button.disabled=!String(input?.value||'').trim()" in js
    assert "commentsKeyboardNavActive=false" in js
    assert "document.addEventListener('keydown'" in js
    assert "document.addEventListener('pointerdown'" in js
    assert "event.stopImmediatePropagation?.()" in js
    assert "api(`/api/comments/${thread.id}/delete`,{method:'POST'})" in js
    assert 'placeLeft=leftRoom>=minUsefulWidth' in js
    assert '.comment-reply-send:disabled' in css


def test_v4154_post_delete_action_is_undoable_and_redoable(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    detail=_ok(client.get(f"/api/chapters/{chapter['id']}"))
    block=detail['blocks'][0]
    _ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{
        'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Start writing here','text_content':'Start writing here','image_path':''
    }]}))
    block=_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    text=block['text_content']
    note=_ok(client.post('/api/comments',json={'body':'Delete through action route','items':[{
        'block_id':block['id'],'start_offset':0,'end_offset':5,'selected_text':text[:5]
    }]}))
    _ok(client.post(f"/api/comments/{note['id']}/messages",json={'body':'Keep response'}))
    token=_ok(client.post('/api/undo/begin?label=Delete%20note'))['token']
    deleted=_ok(client.post(f"/api/comments/{note['id']}/delete"))
    assert deleted['id']==note['id']
    _ok(client.post(f'/api/undo/finish/{token}'))
    assert _ok(client.get('/api/comments?status=all'))==[]
    _ok(client.post(f'/api/undo/apply/{token}?direction=undo'))
    restored=_ok(client.get('/api/comments?status=all'))
    assert len(restored)==1
    assert [m['body'] for m in restored[0]['messages']]==['Delete through action route','Keep response']
    _ok(client.post(f'/api/undo/apply/{token}?direction=redo'))
    assert _ok(client.get('/api/comments?status=all'))==[]
