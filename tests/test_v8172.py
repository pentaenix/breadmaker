from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from storage import schema as storage_schema

from pathlib import Path
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def test_v8172_attach_questionnaire_after_start_updates_participant_form_without_new_checkpoint(client,fresh_project):
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=37
    review=assert_ok(client.post('/api/reviews',json={'name':'Beta Reading','permission_read':True,'permission_feedback':False}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    checkpoint_id=started['snapshot']['checkpoint_ids'][0]
    assert len(assert_ok(client.get('/api/checkpoints'))['checkpoints'])==1
    before=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))
    assert before['form'] is None

    questionnaire=assert_ok(client.post('/api/questionnaires',json={'name':'Beta Questions'}))
    question=assert_ok(client.post(f"/api/questionnaires/{questionnaire['id']}/questions",json={'prompt':'Where did your attention drift?','required':False}))
    attached=assert_ok(client.post(f"/api/reviews/{review['id']}/questionnaires",json={'questionnaire_id':questionnaire['id']}))
    assert attached['active_round_questions_added']==1

    current=assert_ok(client.get(f"/api/reviews/{review['id']}"))
    assert current['permission_feedback'] is True
    assert current['snapshot']['permission_feedback'] is True
    assert current['snapshot']['checkpoint_ids']==[checkpoint_id]
    assert len(assert_ok(client.get('/api/checkpoints'))['checkpoints'])==1

    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))
    assert len(reader['form']['questions'])==1
    frozen=reader['form']['questions'][0]
    assert frozen['source_question_id']==question['id']
    assert frozen['prompt']=='Where did your attention drift?'


def test_v8172_questionnaire_populate_ui_does_not_auto_attach():
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    start=js.index("function openQuestionnairePopulateModal(reviewId)")
    end=js.index("function reviewParticipantById",start)
    block=js[start:end]
    assert "api('/api/questionnaires/populate'" in block
    assert "/questionnaires`,{method:'POST'" not in block
    assert "permission_feedback:true" not in block


def test_v8172_review_panel_rows_share_action_padding():
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    assert '.review-panel-fixed{\n  flex:none;padding:9px;' in css
    assert '.review-panel-scroll{\n  flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;\n  padding:9px;scrollbar-gutter:auto;' in css
