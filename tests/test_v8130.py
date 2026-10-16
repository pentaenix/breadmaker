from __future__ import annotations

from storage import schema as storage_schema
from source_contracts import backend_source,frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok
import app as app_module
from backend import portable_review_runtime
from formats import breadreview
from workspace import APP_SCHEMA_VERSION
from test_v8100 import _chapter, _set_blocks

ROOT=Path(__file__).resolve().parents[1]


def _review(client):
    chapter=_chapter(client)
    _set_blocks(client,chapter['id'],[{'text_content':'alpha beta gamma','content_html':'alpha beta gamma'}])
    review=assert_ok(client.post('/api/reviews',json={
        'name':'Portable Beta','permission_read':True,'permission_comment':True,
        'permission_suggest':True,'permission_feedback':True,
    }))
    rebeca=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    questionnaire=assert_ok(client.post('/api/questionnaires',json={'name':'Portable beta questions'}))
    question=assert_ok(client.post(f"/api/questionnaires/{questionnaire['id']}/questions",json={'prompt':'What did you expect?','required':False}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/questionnaires",json={'questionnaire_id':questionnaire['id']}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    return review,rebeca,question


def test_v8130_release_contract_and_no_schema_bump():
    version=ROOT.joinpath('VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (8,13,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=30
    assert ROOT.joinpath('src','formats','breadreview.py').is_file()
    app=backend_source(ROOT)
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    run=ROOT.joinpath('run').read_text()
    for token in ('/breadreview','/api/reviews/import-breadreview','/api/portable-review/status','/api/portable-review/reader'):
        assert token in app
    assert '.breadreview' in run and 'BREADMAKER_PORTABLE_REVIEW' in run
    assert 'reviews.portable_export' in js and 'reviews.portable_import' in js


def test_v8130_export_contains_only_frozen_review_scope(client,fresh_project,tmp_path):
    review,rebeca,question=_review(client)
    response=client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/breadreview")
    assert response.status_code==200,response.text
    path=tmp_path/'rebeca.breadreview';path.write_bytes(response.content)
    manifest=breadreview.read(path)
    assert manifest['package_format']=='breadmaker-review'
    assert manifest['review_id']==review['id'] and manifest['participant_id']==rebeca['id']
    assert manifest['snapshot']['blocks'][0]['text_content']=='alpha beta gamma'
    assert manifest['questions'][0]['source_question_id']==question['id']
    assert manifest['questions'][0]['id']!=question['id']
    # The portable package carries Review data, not a project database or other workspaces.
    import zipfile
    with zipfile.ZipFile(path) as z:
        names=set(z.namelist())
        assert 'review.json' in names
        assert not any(name.endswith('project.sqlite3') or 'canvas' in name.lower() or 'maps/' in name.lower() for name in names)


def test_v8130_portable_review_round_trip_is_idempotent(client,fresh_project,tmp_path,monkeypatch):
    review,rebeca,question=_review(client)
    exported=client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/breadreview")
    path=tmp_path/'rebeca.breadreview';path.write_bytes(exported.content)

    monkeypatch.setattr(portable_review_runtime,'_PORTABLE_REVIEW_PATH',path)
    status=assert_ok(client.get('/api/portable-review/status'))
    assert status['active'] is True and status['payload']['participant']['display_name']=='Rebeca'
    assert_ok(client.post('/api/portable-review/begin'))
    reader=assert_ok(client.get('/api/portable-review/reader'));block=reader['snapshot']['blocks'][0]
    selection={'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':0,'end_offset':5}
    assert_ok(client.post('/api/portable-review/comments',json={'selection':selection,'body':'Portable comment'}))
    assert_ok(client.post('/api/portable-review/suggestions',json={'selection':selection,'operation':'replace','proposed_text':'omega'}))
    frozen_question_id=breadreview.read(path)['questions'][0]['id']
    assert_ok(client.put('/api/portable-review/feedback',json={'answers':[{'question_id':frozen_question_id,'answer_text':'I expected a turn.'}],'general_feedback':'Portable general'}))
    completed=assert_ok(client.post('/api/portable-review/complete'));assert completed['status']=='completed'
    saved=breadreview.read(path)
    assert saved['participant']['status']=='completed'
    assert any(row['body']=='Portable comment' for row in saved['feedback']['comments'])

    # Nothing entered the writer project merely because the portable file was reviewed.
    monkeypatch.setattr(portable_review_runtime,'_PORTABLE_REVIEW_PATH',None)
    before=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert not before['comments'] and not before['suggestions'] and not before['feedback_forms']

    files={'file':('rebeca.breadreview',path.read_bytes(),'application/octet-stream')}
    imported=assert_ok(client.post('/api/reviews/import-breadreview',files=files))
    assert imported['duplicate'] is False and imported['imported']>0
    writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert [row['body'] for row in writer['comments']]==['Portable comment']
    assert [row['proposed_text'] for row in writer['suggestions']]==['omega']
    assert writer['feedback_forms'][0]['general_feedback']=='Portable general'
    participant=assert_ok(client.get(f"/api/reviews/{review['id']}/participants"))[0]
    assert participant['status']=='completed'

    duplicate=assert_ok(client.post('/api/reviews/import-breadreview',files={'file':('rebeca.breadreview',path.read_bytes(),'application/octet-stream')}))
    assert duplicate['duplicate'] is True and duplicate['imported']==0
    writer2=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert len(writer2['comments'])==1 and len(writer2['suggestions'])==1


def test_v8130_import_rejects_wrong_project_even_with_valid_package_hash(client,fresh_project,tmp_path):
    review,rebeca,_question=_review(client)
    exported=client.get(f"/api/reviews/{review['id']}/participants/{rebeca['id']}/breadreview")
    path=tmp_path/'wrong.breadreview';path.write_bytes(exported.content)
    manifest=breadreview.read(path);manifest['project_id']='another-project'
    breadreview.rewrite(path,manifest)
    response=client.post('/api/reviews/import-breadreview',files={'file':('wrong.breadreview',path.read_bytes(),'application/octet-stream')})
    assert response.status_code==409 and 'different project' in response.text
