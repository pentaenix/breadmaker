from __future__ import annotations

from storage import schema as storage_schema

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION


def _first_chapter(client):
    return assert_ok(client.get('/api/bootstrap'))['chapters'][0]


def _set_text(client,text:str):
    chapter=_first_chapter(client)
    loaded=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={
        'expected_revision':loaded['revision'],
        'blocks':[{'text_content':text,'content_html':text}],
    }))


def test_v8170_review_round_creates_real_permanent_checkpoint(client,fresh_project):
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=37
    _set_text(client,'Checkpoint baseline one')
    review=assert_ok(client.post('/api/reviews',json={'name':'Beta Reading','permission_read':True,'permission_comment':True}))
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    checkpoint_id=started['snapshot']['checkpoint_ids'][0]
    assert checkpoint_id
    checkpoints=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    checkpoint=next(row for row in checkpoints if row['id']==checkpoint_id)
    assert checkpoint['scope']=='book'
    assert checkpoint['name']=='Beta Reading · R1'
    snapshot=assert_ok(client.get(f"/api/checkpoints/{checkpoint_id}"))['snapshot']
    assert any('Checkpoint baseline one' in str(block.get('text_content') or '') for block in snapshot['blocks'])

    assert_ok(client.delete(f"/api/reviews/{review['id']}"))
    remaining=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    assert any(row['id']==checkpoint_id for row in remaining)


def test_v8170_paused_update_reuses_checkpoint_and_refreshes_frozen_text(client,fresh_project):
    _set_text(client,'Old baseline')
    review=assert_ok(client.post('/api/reviews',json={'name':'Developmental Review','permission_read':True,'permission_feedback':True}))
    questionnaire=assert_ok(client.post('/api/questionnaires',json={'name':'Developmental Questions'}))
    assert_ok(client.post(f"/api/questionnaires/{questionnaire['id']}/questions",json={'prompt':'What needs work?','required':False}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/questionnaires",json={'questionnaire_id':questionnaire['id']}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/start")); checkpoint_id=started['snapshot']['checkpoint_ids'][0]
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader")); question_id=reader['form']['questions'][0]['id']
    assert_ok(client.put(f"/api/reviews/{review['id']}/participants/{participant['id']}/feedback",json={'answers':[{'question_id':question_id,'answer_text':'Structure.'}],'general_feedback':'Keep this answer.'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/pause"))
    _set_text(client,'New baseline')
    updated=assert_ok(client.post(f"/api/reviews/{review['id']}/checkpoint/update"))
    assert updated['snapshot']['checkpoint_ids']==[checkpoint_id]
    assert len(assert_ok(client.get('/api/checkpoints'))['checkpoints'])==1
    frozen=assert_ok(client.get(f"/api/reviews/{review['id']}/snapshot"))
    assert any('New baseline' in str(block.get('text_content') or '') for block in frozen['blocks'])
    checkpoint=assert_ok(client.get(f"/api/checkpoints/{checkpoint_id}"))['snapshot']
    assert any('New baseline' in str(block.get('text_content') or '') for block in checkpoint['blocks'])
    form=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))['feedback_forms'][0]
    assert form['answers'][0]['answer_text']=='Structure.'
    assert form['general_feedback']=='Keep this answer.'


def test_v8170_inline_feedback_makes_review_checkpoint_immutable(client,fresh_project):
    _set_text(client,'Anchor me')
    review=assert_ok(client.post('/api/reviews',json={'name':'Line Review','permission_read':True,'permission_comment':True}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/start")); checkpoint_id=started['snapshot']['checkpoint_ids'][0]
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader")); block=reader['snapshot']['blocks'][0]
    assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{participant['id']}/comments",json={'selection':{'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':0,'end_offset':6},'body':'Keep history'}))
    paused=assert_ok(client.post(f"/api/reviews/{review['id']}/pause"))
    assert paused['snapshot']['checkpoint_update_allowed'] is False
    assert paused['snapshot']['checkpoint_update_blocker']=='inline_feedback'
    denied=client.post(f"/api/reviews/{review['id']}/checkpoint/update")
    assert denied.status_code==409 and 'REVIEW_CHECKPOINT_003' in denied.text
    assert any(row['id']==checkpoint_id for row in assert_ok(client.get('/api/checkpoints'))['checkpoints'])
