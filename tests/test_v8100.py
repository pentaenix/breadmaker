from __future__ import annotations

from storage import mutations as storage_mutations
from storage import schema as storage_schema
from source_contracts import backend_source,frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def _chapter(client):
    return assert_ok(client.get('/api/bootstrap'))['chapters'][0]


def _set_blocks(client, chapter_id, blocks):
    loaded=assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'expected_revision':loaded['revision'],'blocks':blocks}))


def _open_suggest_review(client,text='The quick brown fox'):
    chapter=_chapter(client)
    _set_blocks(client,chapter['id'],[{'text_content':text,'content_html':text}])
    review=assert_ok(client.post('/api/reviews',json={'name':'Editor pass','permission_suggest':True}))
    rebeca=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    ana=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Ana'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/reader"))
    block=next(row for row in reader['snapshot']['blocks'] if row['text_content']==text)
    return chapter,review,rebeca,ana,block


def _selection(block,start,end):
    return {'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':start,'end_offset':end}


def test_v8100_release_contract_no_schema_bump():
    version=ROOT.joinpath('VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (8,10,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=30
    app=backend_source(ROOT)
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert '/participants/{participant_id}/suggestions' in app
    assert '/suggestions/{suggestion_id}/accept' in app
    assert '/suggestions/{suggestion_id}/reject' in app
    assert 'createReviewSuggestion' in js
    assert 'review-suggestions' in js


def test_v8100_suggestion_is_attributed_private_and_writer_visible(client,fresh_project):
    chapter,review,rebeca,ana,block=_open_suggest_review(client)
    selection=_selection(block,4,9)
    suggestion=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/suggestions",json={'selection':selection,'operation':'replace','proposed_text':'slow'}))
    assert suggestion['participant_id']==rebeca['id']
    assert suggestion['original_text']=='quick'
    assert suggestion['proposed_text']=='slow'
    assert suggestion['operation']=='replace' and suggestion['status']=='open'
    rebeca_reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/reader"))
    ana_reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{ana['id']}/reader"))
    assert [row['id'] for row in rebeca_reader['feedback']['suggestions']]==[suggestion['id']]
    assert ana_reader['feedback']['suggestions']==[]
    writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert writer['suggestions'][0]['participant_name']=='Rebeca'
    with storage_mutations.tx(track_generation=False) as con:
        event=con.execute("SELECT event_type,participant_id,entity_id FROM review_events WHERE entity_id=?",(suggestion['id'],)).fetchone()
    assert tuple(event)==('SUGGESTION_CREATED',rebeca['id'],suggestion['id'])


def test_v8100_accept_replace_preserves_inline_formatting_and_is_idempotent(client,fresh_project):
    chapter=_chapter(client)
    _set_blocks(client,chapter['id'],[{'text_content':'The quick brown fox','content_html':'The <strong>quick brown</strong> fox'}])
    review=assert_ok(client.post('/api/reviews',json={'name':'Formatted','permission_suggest':True}))
    person=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Carlos'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{person['id']}/reader"))
    block=next(row for row in reader['snapshot']['blocks'] if row['text_content']=='The quick brown fox')
    suggestion=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{person['id']}/suggestions",json={'selection':_selection(block,4,9),'operation':'replace','proposed_text':'nimble'}))
    before_gen=storage_mutations.current_project_generation()
    accepted=assert_ok(client.post(f"/api/reviews/{review['id']}/suggestions/{suggestion['id']}/accept"))
    assert accepted['suggestion']['status']=='accepted'
    assert accepted['block_ids']==[block['source_block_id']]
    loaded=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    live=next(row for row in loaded['blocks'] if row['id']==block['source_block_id'])
    assert live['text_content']=='The nimble brown fox'
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(live['content_html'],'html.parser')
    assert ''.join(tag.get_text() for tag in soup.find_all('strong'))=='nimble brown'
    writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    row=next(x for x in writer['suggestions'] if x['id']==suggestion['id'])
    assert row['status']=='accepted'
    assert int(row['applied_generation'])==before_gen+1
    again=client.post(f"/api/reviews/{review['id']}/suggestions/{suggestion['id']}/accept")
    assert again.status_code==409 and 'REVIEW_SUGGEST_008' in again.text


def test_v8100_insert_delete_and_reject(client,fresh_project):
    chapter,review,rebeca,ana,block=_open_suggest_review(client,'alpha beta gamma')
    insert=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/suggestions",json={'selection':_selection(block,6,10),'operation':'insert','proposed_text':' very'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/suggestions/{insert['id']}/accept"))
    loaded=assert_ok(client.get(f"/api/chapters/{chapter['id']}")); assert loaded['blocks'][0]['text_content']=='alpha beta very gamma'

    # A new Review snapshot is needed because the first Review intentionally remains frozen.
    review2=assert_ok(client.post('/api/reviews',json={'name':'Delete pass','permission_suggest':True}))
    person=assert_ok(client.post(f"/api/reviews/{review2['id']}/participants",json={'display_name':'Rebeca'})); assert_ok(client.post(f"/api/reviews/{review2['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review2['id']}/participants/{person['id']}/reader")); block2=reader['snapshot']['blocks'][0]
    delete=assert_ok(client.post(f"/api/reviews/{review2['id']}/participants/{person['id']}/suggestions",json={'selection':_selection(block2,6,10),'operation':'delete'}))
    rejected=assert_ok(client.post(f"/api/reviews/{review2['id']}/suggestions/{delete['id']}/reject"))
    assert rejected['status']=='rejected'
    loaded=assert_ok(client.get(f"/api/chapters/{chapter['id']}")); assert loaded['blocks'][0]['text_content']=='alpha beta very gamma'


def test_v8100_accept_fails_closed_if_live_source_changed(client,fresh_project):
    chapter,review,rebeca,ana,block=_open_suggest_review(client)
    suggestion=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/suggestions",json={'selection':_selection(block,4,9),'operation':'replace','proposed_text':'slow'}))
    _set_blocks(client,chapter['id'],[{'id':block['source_block_id'],'text_content':'The QUICK brown fox','content_html':'The QUICK brown fox'}])
    out=client.post(f"/api/reviews/{review['id']}/suggestions/{suggestion['id']}/accept")
    assert out.status_code==409 and 'REVIEW_SUGGEST_APPLY_003' in out.text
    writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert next(x for x in writer['suggestions'] if x['id']==suggestion['id'])['status']=='open'


def test_v8100_multi_block_suggestion_is_valid_and_auto_applies_when_span_is_unchanged(client,fresh_project):
    chapter=_chapter(client)
    _set_blocks(client,chapter['id'],[{'text_content':'First block','content_html':'First block'},{'text_content':'Second block','content_html':'Second block'}])
    review=assert_ok(client.post('/api/reviews',json={'name':'Multi','permission_suggest':True}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Carlos'})); assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader")); blocks=reader['snapshot']['blocks'][:2]
    suggestion=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{participant['id']}/suggestions",json={'selection':{'start_block_id':blocks[0]['source_block_id'],'end_block_id':blocks[1]['source_block_id'],'start_offset':6,'end_offset':6},'operation':'replace','proposed_text':'rewritten'}))
    assert suggestion['original_text']=='block\nSecond'
    accepted=assert_ok(client.post(f"/api/reviews/{review['id']}/suggestions/{suggestion['id']}/accept"))
    assert accepted['suggestion']['status']=='accepted'
    loaded=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert [row['text_content'] for row in loaded['blocks']]==['First rewritten',' block']


def test_v8100_suggestion_requires_permission(client,fresh_project):
    chapter=_chapter(client)
    _set_blocks(client,chapter['id'],[{'text_content':'Read only prose','content_html':'Read only prose'}])
    review=assert_ok(client.post('/api/reviews',json={'name':'No suggestions','permission_read':True,'permission_suggest':False}))
    person=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'})); assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{person['id']}/reader")); block=reader['snapshot']['blocks'][0]
    out=client.post(f"/api/reviews/{review['id']}/participants/{person['id']}/suggestions",json={'selection':_selection(block,0,4),'operation':'replace','proposed_text':'Edit'})
    assert out.status_code==403 and 'REVIEW_SUGGEST_002' in out.text
