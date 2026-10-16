from __future__ import annotations

from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import schema as storage_schema
from source_contracts import backend_source

from pathlib import Path
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def _first_chapter(client):
    return assert_ok(client.get('/api/bootstrap'))['chapters'][0]


def _set_text(client, text:str):
    chapter=_first_chapter(client)
    loaded=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    return assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={
        'expected_revision':loaded['revision'],
        'blocks':[{'text_content':text,'content_html':text}],
    }))


def test_v8160_release_contract_rounds_questionnaires_and_current_schema():
    assert tuple(map(int,ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (8,16,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=37
    schema=ROOT.joinpath('src','storage','schema.py').read_text(encoding='utf-8')
    for table in ('questionnaires','questionnaire_questions','review_questionnaires','review_snapshot_questions'):
        assert f'CREATE TABLE {table}(' in schema
    assert 'round_number INTEGER NOT NULL' in schema
    assert "status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','paused','closed'))" in schema
    app=backend_source(ROOT)
    assert "/api/reviews/{rid}/pause" in app
    assert "/api/reviews/{rid}/resume" in app
    assert "/api/reviews/{rid}/duplicate" in app
    assert "/api/reviews/{rid}/participants/populate" in app
    assert "/api/reviews/populate" in app


def test_v8160_review_config_stays_editable_while_round_is_frozen(client,fresh_project):
    _set_text(client,'Round one frozen manuscript')
    review=assert_ok(client.post('/api/reviews',json={
        'name':'Reusable Review',
        'description':'First configuration',
        'permission_read':True,
        'permission_comment':True,
    }))
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    assert started['status']=='open' and started['rounds'][0]['round_number']==1
    snapshot_id=started['snapshot']['id']

    changed=assert_ok(client.patch(f"/api/reviews/{review['id']}",json={
        'name':'Reusable Review Updated',
        'description':'Configuration for the next round',
        'permission_suggest':True,
        'permission_feedback':True,
    }))
    assert changed['name']=='Reusable Review Updated'
    assert changed['permission_suggest'] is True
    assert changed['permission_feedback'] is True

    frozen=assert_ok(client.get(f"/api/reviews/{review['id']}/snapshot"))
    assert frozen['id']==snapshot_id
    assert frozen['permission_suggest'] is False
    assert frozen['permission_feedback'] is False


def test_v8160_pause_resume_close_and_second_round_preserve_history(client,fresh_project):
    _set_text(client,'Original round text')
    review=assert_ok(client.post('/api/reviews',json={'name':'Rounds','permission_read':True,'permission_comment':True}))
    one=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    first_snapshot=one['snapshot']['id']
    assert assert_ok(client.post(f"/api/reviews/{review['id']}/pause"))['status']=='paused'
    paused_reader=client.get(f"/api/reviews/{review['id']}/participants/not-real/reader")
    assert paused_reader.status_code==409
    assert assert_ok(client.post(f"/api/reviews/{review['id']}/resume"))['status']=='open'
    _set_text(client,'Second round text after writer kept working')
    two=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    assert two['status']=='open'
    rounds=two['rounds']
    assert [row['round_number'] for row in rounds]==[2,1]
    assert rounds[0]['id']!=first_snapshot
    assert rounds[1]['id']==first_snapshot and rounds[1]['status']=='closed'
    with storage_mutations.tx() as con:
        pid=int(con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0])
        assert storage_invariants.project_invariant_errors(con,pid)==[]


def test_v8160_questionnaire_is_reusable_but_each_round_freezes_its_prompts_and_answers(client,fresh_project):
    _set_text(client,'Questionnaire round manuscript')
    questionnaire=assert_ok(client.post('/api/questionnaires',json={'name':'Reader Check','description':'Reusable reader questions'}))
    question=assert_ok(client.post(f"/api/questionnaires/{questionnaire['id']}/questions",json={'prompt':'What worked?','required':False}))
    review=assert_ok(client.post('/api/reviews',json={'name':'Questionnaire Review','permission_read':True,'permission_feedback':True}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/questionnaires",json={'questionnaire_id':questionnaire['id']}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    first=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))
    first_frozen=reader['form']['questions'][0]
    assert first_frozen['prompt']=='What worked?'
    assert_ok(client.put(f"/api/reviews/{review['id']}/participants/{participant['id']}/feedback",json={
        'answers':[{'question_id':first_frozen['id'],'answer_text':'The opening.'}],
        'general_feedback':'Round one notes',
    }))
    assert_ok(client.patch(f"/api/questionnaire-questions/{question['id']}",json={'prompt':'What worked best this time?'}))
    second=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    assert second['rounds'][0]['round_number']==2
    reader2=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))
    assert reader2['form']['questions'][0]['prompt']=='What worked best this time?'
    assert reader2['form']['answers'][0]['answer_text']==''

    writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    first_form=next(row for row in writer['feedback_forms'] if row['round_number']==1)
    assert first_form['answers'][0]['prompt']=='What worked?'
    assert first_form['answers'][0]['answer_text']=='The opening.'
    assert first_form['general_feedback']=='Round one notes'


def test_v8160_populate_duplicate_and_participant_reuse_do_not_force_retyping(client,fresh_project):
    options=assert_ok(client.get('/api/reviews/populate-options'))
    assert len(options)>=4
    populated=assert_ok(client.post('/api/reviews/populate',json={'preset_id':'full'}))
    assert populated['count']>=4
    for review in populated['created']:
        assert review['description']
        assert review['scope_type']=='manuscript'
        assert review['participants']==[]
        assert 'category_id' not in review

    source=populated['created'][0]
    assert_ok(client.post(f"/api/reviews/{source['id']}/participants",json={'display_name':'Rebeca'}))
    assert_ok(client.post(f"/api/reviews/{source['id']}/participants",json={'display_name':'Ana'}))
    duplicate=assert_ok(client.post(f"/api/reviews/{source['id']}/duplicate"))
    assert duplicate['status']=='draft'
    assert duplicate['name']!=source['name']
    assert duplicate['description']==source['description']
    assert {p['display_name'] for p in duplicate['participants']}=={'Rebeca','Ana'}
    assert duplicate['rounds']==[]

    target=assert_ok(client.post('/api/reviews',json={'name':'Another Review'}))
    reused=assert_ok(client.post(f"/api/reviews/{target['id']}/participants/populate",json={'source_review_id':source['id']}))
    assert reused['count']==2
    loaded=assert_ok(client.get(f"/api/reviews/{target['id']}"))
    assert {p['display_name'] for p in loaded['participants']}=={'Rebeca','Ana'}


def test_v8160_live_manuscript_link_is_added_to_task_from_review_feedback(client,fresh_project):
    _set_text(client,'Link this section back into the manuscript')
    review=assert_ok(client.post('/api/reviews',json={'name':'Linked Review','permission_read':True,'permission_comment':True}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))
    block=next(row for row in reader['snapshot']['blocks'] if 'Link this section' in row['text_content'])
    comment=assert_ok(client.post(f"/api/reviews/{review['id']}/participants/{participant['id']}/comments",json={
        'selection':{'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':0,'end_offset':9},
        'body':'Revisit this.',
    }))
    task=assert_ok(client.post(f"/api/reviews/{review['id']}/feedback/comment/{comment['id']}/task",json={'title':'Review this line'}))
    with storage_mutations.tx() as con:
        refs=con.execute("SELECT target_type,target_id,relationship FROM references_map WHERE source_type='task' AND source_id=? ORDER BY target_type",(str(task['task']['id']),)).fetchall()
        kinds={row['target_type'] for row in refs}
    assert 'review_comment' in kinds
    assert 'manuscript_range' in kinds
