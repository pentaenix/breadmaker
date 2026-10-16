from __future__ import annotations

from storage import mutations as storage_mutations
from storage import schema as storage_schema
from source_contracts import backend_source,frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def _feedback_review(client, *, read=False):
    review=assert_ok(client.post('/api/reviews',json={'name':'Feedback pass','permission_read':read,'permission_feedback':True}))
    rebeca=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    ana=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Ana'}))
    questionnaire=assert_ok(client.post('/api/questionnaires',json={'name':'Feedback pass questions','description':'Reusable Review feedback prompts.'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/questionnaires",json={'questionnaire_id':questionnaire['id']}))
    return review,rebeca,ana,questionnaire


def _question(client,questionnaire,prompt,required=False):
    return assert_ok(client.post(f"/api/questionnaires/{questionnaire['id']}/questions",json={'prompt':prompt,'required':required}))


def _reader(client,review,participant):
    return assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))


def test_v8110_release_contract_tracks_current_questionnaire_model():
    version=ROOT.joinpath('VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (8,11,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=30
    app=backend_source(ROOT)
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert '/api/questionnaires' in app
    assert '/api/reviews/{rid}/questionnaires' in app
    assert '/participants/{participant_id}/feedback' in app
    assert '/api/reviews/{rid}/questions' not in app
    assert '/api/review-questions/{question_id}' not in app
    assert 'reviewerFeedbackForm' in js
    assert 'questionnaireDetailsModal' in js


def test_v8110_questions_are_open_text_ordered_and_each_round_freezes_them(client,fresh_project):
    review,rebeca,ana,questionnaire=_feedback_review(client)
    q1=_question(client,questionnaire,'Do you think they will find the killer?',True)
    q2=_question(client,questionnaire,'Could you predict the next beat?',False)
    current=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    attached=current['questionnaires'][0]['questions']
    assert [q['id'] for q in attached]==[q1['id'],q2['id']]
    assert attached[0]['required']==1 and attached[1]['required']==0

    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    first=_reader(client,review,rebeca)['form']['questions']
    assert [q['source_question_id'] for q in first]==[q1['id'],q2['id']]
    assert all(q['id'] not in {q1['id'],q2['id']} for q in first)

    assert_ok(client.patch(f"/api/questionnaire-questions/{q2['id']}",json={'prompt':'What do you think happens next?','required':True}))
    still_frozen=_reader(client,review,rebeca)['form']['questions']
    assert still_frozen[1]['prompt']=='Could you predict the next beat?'
    assert still_frozen[1]['required']==0

    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    next_round=_reader(client,review,rebeca)['form']['questions']
    assert next_round[1]['prompt']=='What do you think happens next?'
    assert next_round[1]['required']==1


def test_v8110_feedback_only_reviewer_gets_form_without_manuscript(client,fresh_project):
    review,rebeca,ana,questionnaire=_feedback_review(client,read=False)
    q=_question(client,questionnaire,'What surprised you?',False)
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=_reader(client,review,rebeca)
    assert reader['snapshot']['permission_feedback'] is True
    assert reader['snapshot']['permission_read'] is False
    assert 'blocks' not in reader['snapshot']
    assert reader['form']['questions'][0]['source_question_id']==q['id']
    assert reader['feedback']=={'highlights':[],'comments':[],'suggestions':[]}


def test_v8110_required_metadata_survives_freeze_while_realtime_feedback_may_be_partial(client,fresh_project):
    review,rebeca,ana,questionnaire=_feedback_review(client)
    required=_question(client,questionnaire,'Why?',True)
    optional=_question(client,questionnaire,'Anything else?',False)
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    form=_reader(client,review,rebeca)['form']; frozen={q['source_question_id']:q['id'] for q in form['questions']}
    assert next(q for q in form['questions'] if q['source_question_id']==required['id'])['required']==1
    # Reviewer feedback autosaves while the user types. A required question is guidance
    # for the form, not a hard transaction barrier that would reject partial autosaves.
    partial=assert_ok(client.put(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/feedback",json={'answers':[],'general_feedback':'Strong opening.'}))
    assert partial['general_feedback']=='Strong opening.'
    assert next(a for a in partial['answers'] if a['question_id']==frozen[required['id']])['answer_text']==''
    saved=assert_ok(client.put(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/feedback",json={'answers':[{'question_id':frozen[required['id']],'answer_text':'Because the clue is obvious.'},{'question_id':frozen[optional['id']],'answer_text':''}],'general_feedback':'Strong opening.'}))
    assert saved['general_feedback']=='Strong opening.'
    assert next(a for a in saved['answers'] if a['question_id']==frozen[required['id']])['answer_text']=='Because the clue is obvious.'


def test_v8110_feedback_is_private_per_participant_and_writer_sees_attribution(client,fresh_project):
    review,rebeca,ana,questionnaire=_feedback_review(client)
    q=_question(client,questionnaire,'Prediction?',False)
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    frozen_qid=_reader(client,review,rebeca)['form']['questions'][0]['id']
    assert_ok(client.put(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/feedback",json={'answers':[{'question_id':frozen_qid,'answer_text':'The gardener did it.'}],'general_feedback':'I liked chapter two.'}))
    rebeca_reader=_reader(client,review,rebeca)
    ana_reader=_reader(client,review,ana)
    assert rebeca_reader['form']['general_feedback']=='I liked chapter two.'
    assert ana_reader['form']['general_feedback']==''
    assert ana_reader['form']['answers'][0]['answer_text']==''
    writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert len(writer['feedback_forms'])==1
    assert writer['feedback_forms'][0]['participant_name']=='Rebeca'
    assert writer['feedback_forms'][0]['answers'][0]['prompt']=='Prediction?'
    assert writer['feedback_forms'][0]['answers'][0]['question_id']==frozen_qid
    with storage_mutations.tx(track_generation=False) as con:
        event=con.execute("SELECT event_type,participant_id FROM review_events WHERE review_id=? AND event_type='FEEDBACK_SAVED'",(review['id'],)).fetchone()
    assert tuple(event)==('FEEDBACK_SAVED',rebeca['id'])


def test_v8110_feedback_permission_is_enforced_and_questionnaire_attachment_enables_it(client,fresh_project):
    review=assert_ok(client.post('/api/reviews',json={'name':'No form','permission_read':True,'permission_feedback':False}))
    person=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    reader=_reader(client,review,person)
    assert reader['form'] is None
    out=client.put(f"/api/reviews/{review['id']}/participants/{person['id']}/feedback",json={'answers':[],'general_feedback':'Nope'})
    assert out.status_code==403 and 'REVIEW_FORM_002' in out.text

    questionnaire=assert_ok(client.post('/api/questionnaires',json={'name':'Future form'}))
    question=_question(client,questionnaire,'Now available this round?',False)
    assert_ok(client.post(f"/api/reviews/{review['id']}/questionnaires",json={'questionnaire_id':questionnaire['id']}))
    current=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert current['permission_feedback'] is True
    reader=_reader(client,review,person)
    assert reader['form'] is not None
    assert reader['form']['questions'][0]['source_question_id']==question['id']
