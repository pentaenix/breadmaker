from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
import storage as storage_package

from pathlib import Path
from conftest import assert_ok


def _two_blocks(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    data=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    first=data['blocks'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[
        {'id':first['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Alpha first paragraph','text_content':'Alpha first paragraph','image_path':''},
        {'block_type':'paragraph','semantic_style':'body','content_html':'Beta second paragraph','text_content':'Beta second paragraph','image_path':''},
    ]}))
    data=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    return chapter,data['blocks']


def test_v415_static_comments_workspace_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    assert html.index('id="findBtn"') < html.index('id="commentsBtn"') < html.index('id="markupBtn"')
    assert 'id="commentsPanel"' in html and 'id="commentSelectionBtn"' not in html
    assert 'id="commentsSearch"' in html and 'id="commentsPrev"' in html and 'id="commentsNext"' in html
    assert '<option value="resolved">Resolved</option>' in html
    assert 'commentHoverTimer=setTimeout' in js and ',300)' in js
    assert '.note-gutter-marker' in css and '.note-gutter-count' in css
    assert 'user-select:none' in css
    assert "async function commentSelection(items){await beginCommentComposer(items)}" in js
    assert "textDialog('Add Comment'" not in js
    assert '<b>Notes</b>' in html and 'Search notes' in html and 'Write a note…' in html


def test_v415_comment_selection_is_one_thread_with_multiple_anchors(client,fresh_project):
    chapter,blocks=_two_blocks(client,fresh_project)
    created=assert_ok(client.post('/api/comments',json={'body':'This transition needs work.','items':[
        {'block_id':blocks[0]['id'],'start_offset':6,'end_offset':11,'selected_text':'first','prefix_context':'Alpha ','suffix_context':' paragraph'},
        {'block_id':blocks[1]['id'],'start_offset':0,'end_offset':4,'selected_text':'Beta','prefix_context':'','suffix_context':' second'},
    ]}))
    assert len(created['anchors'])==2
    assert len(created['messages'])==1
    assert created['first_message']=='This transition needs work.'
    listed=assert_ok(client.get('/api/comments?status=open'))
    assert [x['id'] for x in listed]==[created['id']]
    assert [a['block_id'] for a in listed[0]['anchors']]==[blocks[0]['id'],blocks[1]['id']]
    annotations=assert_ok(client.get('/api/annotations?status=all'))
    linked=[a for a in annotations if a['annotation_type']=='comment']
    assert len(linked)==2 and {a['thread_id'] for a in linked}=={created['id']}


def test_v415_comment_replies_edit_and_resolution(client,fresh_project):
    chapter,blocks=_two_blocks(client,fresh_project)
    thread=assert_ok(client.post('/api/comments',json={'body':'Original comment','items':[{'block_id':blocks[0]['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))
    replied=assert_ok(client.post(f"/api/comments/{thread['id']}/messages",json={'body':'A follow-up reply'}))
    assert replied['reply_count']==1 and [m['body'] for m in replied['messages']]==['Original comment','A follow-up reply']
    first_id=replied['messages'][0]['id']
    edited=assert_ok(client.patch(f'/api/comment-messages/{first_id}',json={'body':'Edited original'}))
    assert edited['first_message']=='Edited original'
    anns=assert_ok(client.get('/api/annotations?status=all'))
    assert next(a for a in anns if a.get('thread_id')==thread['id'])['body']=='Edited original'
    resolved=assert_ok(client.patch(f"/api/comments/{thread['id']}",json={'status':'resolved'}))
    assert resolved['status']=='resolved'
    assert not assert_ok(client.get('/api/comments?status=open'))
    assert [x['id'] for x in assert_ok(client.get('/api/comments?status=resolved'))]==[thread['id']]
    reopened=assert_ok(client.patch(f"/api/comments/{thread['id']}",json={'status':'open'}))
    assert reopened['status']=='open'


def test_v415_comments_participate_in_session_undo(client,fresh_project):
    chapter,blocks=_two_blocks(client,fresh_project)
    token=assert_ok(client.post('/api/undo/begin?label=Add%20comment'))['token']
    thread=assert_ok(client.post('/api/comments',json={'body':'Undo me','items':[{'block_id':blocks[0]['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))
    assert assert_ok(client.post(f'/api/undo/finish/{token}'))['changed']
    assert [x['id'] for x in assert_ok(client.get('/api/comments?status=open'))]==[thread['id']]
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=undo'))
    assert assert_ok(client.get('/api/comments?status=all'))==[]
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=redo'))
    redone=assert_ok(client.get('/api/comments?status=open'))
    assert len(redone)==1 and redone[0]['first_message']=='Undo me'


def test_v415_legacy_batch_comment_becomes_thread(client,fresh_project):
    chapter,blocks=_two_blocks(client,fresh_project)
    result=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'comment','body':'Legacy API comment','items':[
        {'block_id':blocks[0]['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'},
        {'block_id':blocks[1]['id'],'start_offset':0,'end_offset':4,'selected_text':'Beta'},
    ]}))
    assert result['thread_id']
    comments=assert_ok(client.get('/api/comments?status=open'))
    assert len(comments)==1 and len(comments[0]['anchors'])==2

def test_v415_history_restore_preserves_threads_and_replies(client,fresh_project):
    chapter,blocks=_two_blocks(client,fresh_project)
    thread=assert_ok(client.post('/api/comments',json={'body':'History original','items':[{'block_id':blocks[0]['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))
    assert_ok(client.post(f"/api/comments/{thread['id']}/messages",json={'body':'History reply'}))
    pid=fresh_project['bootstrap']['project']['id']
    rid=storage_package.commit_revision(pid,'Comment history state','edit',dedupe=False)
    first=thread['messages'][0]['id'] if thread.get('messages') else assert_ok(client.get(f"/api/comments/{thread['id']}"))['messages'][0]['id']
    assert_ok(client.patch(f'/api/comment-messages/{first}',json={'body':'Changed later'}))
    assert_ok(client.patch(f"/api/comments/{thread['id']}",json={'status':'resolved'}))
    storage_package.restore_revision(rid,physical_backup=False,preserve_current=False)
    restored=assert_ok(client.get('/api/comments?status=open'))
    assert len(restored)==1
    assert [m['body'] for m in restored[0]['messages']]==['History original','History reply']


def test_v415_checkpoint_restore_preserves_comment_thread_messages_and_multiline_body(client,fresh_project):
    chapter,blocks=_two_blocks(client,fresh_project)
    thread=assert_ok(client.post('/api/comments',json={'body':'Line one\nLine two','items':[{'block_id':blocks[0]['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))
    thread=assert_ok(client.post(f"/api/comments/{thread['id']}/messages",json={'body':'Reply A\nReply B'}))
    assert [m['body'] for m in thread['messages']]==['Line one\nLine two','Reply A\nReply B']
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Comments checkpoint'}))
    snapshot=assert_ok(client.get(f"/api/checkpoints/{cp['id']}"))['snapshot']
    assert len(snapshot.get('comment_threads',[]))==1
    assert [m['body'] for m in snapshot.get('comment_messages',[])]==['Line one\nLine two','Reply A\nReply B']

    first_id=thread['messages'][0]['id']
    assert_ok(client.patch(f'/api/comment-messages/{first_id}',json={'body':'Changed after checkpoint'}))
    assert_ok(client.post(f"/api/comments/{thread['id']}/messages",json={'body':'Third reply'}))
    assert_ok(client.patch(f"/api/comments/{thread['id']}",json={'status':'resolved'}))

    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':chapter['id']}))
    restored=assert_ok(client.get('/api/comments?status=all'))
    assert len(restored)==1
    assert [m['body'] for m in restored[0]['messages']]==['Changed after checkpoint','Reply A\nReply B','Third reply']
    assert restored[0]['status']=='resolved'


def test_v4153_delete_note_participates_in_session_undo_redo(client,fresh_project):
    chapter,blocks=_two_blocks(client,fresh_project)
    thread=assert_ok(client.post('/api/comments',json={'body':'Delete and restore me','items':[{'block_id':blocks[0]['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))
    assert_ok(client.post(f"/api/comments/{thread['id']}/messages",json={'body':'Keep this response too'}))
    token=assert_ok(client.post('/api/undo/begin?label=Delete%20note'))['token']
    assert_ok(client.delete(f"/api/comments/{thread['id']}"))
    assert assert_ok(client.post(f'/api/undo/finish/{token}'))['changed']
    assert assert_ok(client.get('/api/comments?status=all'))==[]
    assert assert_ok(client.get('/api/annotations?status=all'))==[]
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=undo'))
    restored=assert_ok(client.get('/api/comments?status=open'))
    assert len(restored)==1 and restored[0]['first_message']=='Delete and restore me'
    assert [m['body'] for m in restored[0]['messages']]==['Delete and restore me','Keep this response too']
    assert len(restored[0]['anchors'])==1
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=redo'))
    assert assert_ok(client.get('/api/comments?status=all'))==[]
    assert assert_ok(client.get('/api/annotations?status=all'))==[]
