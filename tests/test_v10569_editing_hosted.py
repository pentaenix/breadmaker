"""Canonical packed Highlights, ordinary annotations, recovery and authority."""
from copy import deepcopy
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
from hosted.domain_store import HostedDomainStore
from hosted.editing_http import build_router
from hosted.editing_packs import PackedAnnotations, FIELD
from hosted.editing_search import HostedEditingSearch
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError, ProjectUnavailable
from hosted.narration.io_limits import deadline
from tests.test_v10569_narration_recordings import env, PID, UID, OTHER


@pytest.fixture
def editor(env):
    domain=HostedDomainStore(ActionDocumentStore(env[2]),env[1].objects)
    bid=env[4]['block_id'];block=domain.get('manuscript_block',{'project_id':PID,'block_id':bid},user_id=UID)
    domain.update('manuscript_block',{'project_id':PID,'block_id':bid},{'block_type':'paragraph',
        'text_content':'😀 She walked quietly and self‑consciously.','content_html':'😀 She walked quietly and self‑consciously.'},user_id=UID)
    return domain,HostedEditingSearch(domain),block['tab_id'],bid,env[2]


def run(editor,*,scan='adverbs',action='scan',tab=None,uid=UID):
    domain,service,tid,_,_=editor
    token=ActionJournal(domain.documents).begin(PID,uid)['token']
    with bind_action(token,PID,uid):result=service.run(PID,uid,dict(scan=scan,action=action,tab_id=tab or tid))
    ActionJournal(domain.documents).finish(PID,uid,token)
    return token,result


def rows(editor):return editor[0].list('annotation',{'project_id':PID},user_id=UID)


def test_unicode_expanded_vocabulary_publishes_canonical_ranges_and_supports_undo_redo(editor):
    domain,service,tab,bid,docs=editor
    before=domain.get('manuscript_block',{'project_id':PID,'block_id':bid},user_id=UID)
    token,result=run(editor)
    assert result['matches']==2 and result['added']==2
    assert [r['selected_text'] for r in sorted(rows(editor),key=lambda row:row['start_offset'])]==['quietly','self‑consciously']
    for row in rows(editor):assert before['text_content'][row['start_offset']:row['end_offset']]==row['selected_text']
    assert domain.get('manuscript_block',{'project_id':PID,'block_id':bid},user_id=UID)==before
    physical=docs.query_collection(('projects',PID,'annotations'))
    assert len(physical)==1 and FIELD in physical[0].data
    journal=ActionJournal(domain.documents)
    journal.restore(PID,UID,token,'undo');assert rows(editor)==[]
    journal.restore(PID,UID,token,'redo');assert len(rows(editor))==2
    assert min(r['generation'] for r in rows(editor))>1


def test_twenty_thousand_results_keep_existing_small_journal_bounds(editor):
    domain,_,tab,bid,docs=editor
    block=domain.get('manuscript_block',{'project_id':PID,'block_id':bid},user_id=UID)
    domain.update('manuscript_block',{'project_id':PID,'block_id':bid},{'text_content':'quietly '*10_000},user_id=UID)
    domain.create('manuscript_block',{'project_id':PID},{'tab_id':tab,'chapter_id':block['chapter_id'],
        'block_type':'paragraph','position':1,'text_content':'quietly '*10_000},user_id=UID)
    token,result=run(editor)
    assert result['matches']==20_000 and len(rows(editor))==20_000
    action=docs.get(('hosted_actions',UID,'tokens',token)).data
    assert len(action['records'])==2 and len(json.dumps(action))<10_000
    assert len(docs.query_collection(('projects',PID,'annotations')))==1
    ActionJournal(domain.documents).restore(PID,UID,token,'undo');assert not rows(editor)
    ActionJournal(domain.documents).restore(PID,UID,token,'redo');assert len(rows(editor))==20_000


def test_manual_ranges_custom_description_and_other_documents_survive_recheck(editor):
    domain,service,tab,bid,docs=editor
    _,first=run(editor);marker=first['marker']
    domain.update('markup_label',{'project_id':PID,'label_id':marker['id']},{'name':'My words','description':'My custom copy'},user_id=UID)
    domain.create('annotation',{'project_id':PID},{'block_id':bid,'label_id':marker['id'],'annotation_type':'highlight',
        'start_offset':13,'end_offset':20,'selected_text':'quietly','body':'manual','stale':False},user_id=UID)
    other=domain.create('document_tab',{'project_id':PID},{'title':'Other'},user_id=UID)
    chapter=domain.create('chapter',{'project_id':PID},{'tab_id':other['id'],'title':'Other'},user_id=UID)
    domain.create('manuscript_block',{'project_id':PID},{'tab_id':other['id'],'chapter_id':chapter['id'],
        'block_type':'paragraph','text_content':'softly'},user_id=UID)
    run(editor,tab=other['id'])
    _,second=run(editor,action='recheck')
    assert second['marker']['name']=='My words' and second['marker']['description']=='My custom copy'
    assert [r['selected_text'] for r in rows(editor)].count('softly')==1
    assert len([r for r in rows(editor) if r.get('body')=='manual'])==1
    assert len([r for r in rows(editor) if r.get('selected_text')=='quietly'])==1
    run(editor,action='clear')
    assert {r['selected_text'] for r in rows(editor)}=={'quietly','softly'}
    assert service.status(PID,UID,tab)['searches']['adverbs']['marker']['name']=='My words'


def test_marker_remove_is_atomic_restorable_and_preserves_linked_task_and_card(editor):
    domain,_,tab,bid,docs=editor
    _,first=run(editor);lid=first['marker']['id']
    task=domain.create('task',{'project_id':PID},{'title':'Keep task','marker_id':lid},user_id=UID)
    canvas=domain.create('canvas',{'project_id':PID},{'title':'Keep canvas'},user_id=UID)
    node=domain.create('canvas_node',{'project_id':PID,'canvas_id':canvas['id']},{'title':'Keep card','label_id':lid,'color':'#123abc'},user_id=UID)
    token,result=run(editor,action='remove')
    assert result['removed']==2 and not rows(editor)
    assert domain.get('task',{'project_id':PID,'task_id':task['id']},user_id=UID)['marker_id'] is None
    card=domain.get('canvas_node',{'project_id':PID,'canvas_id':canvas['id'],'node_id':node['id']},user_id=UID)
    assert card['title']=='Keep card' and card['color']=='#123abc' and card['label_id'] is None
    ActionJournal(domain.documents).restore(PID,UID,token,'undo')
    assert len(rows(editor))==2
    assert domain.get('task',{'project_id':PID,'task_id':task['id']},user_id=UID)['marker_id']==lid


def test_individual_generated_range_edit_delete_and_undo_never_reuse_generation(editor):
    domain,_,_,_,_=editor
    run(editor);row=rows(editor)[0]
    token=ActionJournal(domain.documents).begin(PID,UID)['token']
    with bind_action(token,PID,UID):
        changed=domain.update('annotation',{'project_id':PID,'annotation_id':row['id']},{'stale':True},
            user_id=UID,expected_generation=row['generation'])
    ActionJournal(domain.documents).finish(PID,UID,token)
    assert changed['stale']
    ActionJournal(domain.documents).restore(PID,UID,token,'undo')
    restored=domain.get('annotation',{'project_id':PID,'annotation_id':row['id']},user_id=UID)
    assert not restored['stale'] and restored['generation']>changed['generation']
    with pytest.raises(DocumentConflict):
        domain.update('annotation',{'project_id':PID,'annotation_id':row['id']},{'stale':True},user_id=UID,
            expected_generation=row['generation'])
    domain.delete('annotation',{'project_id':PID,'annotation_id':row['id']},user_id=UID)
    assert len(rows(editor))==1


def test_retry_reuses_committed_receipt_but_undo_or_changed_request_cannot_replay(editor):
    domain,service,tab,_,_=editor
    token=ActionJournal(domain.documents).begin(PID,UID)['token']
    data=dict(scan='adverbs',action='recheck',tab_id=tab)
    with bind_action(token,PID,UID):
        first=service.run(PID,UID,data);generation=domain.project_generation(PID,UID)
        assert service.run(PID,UID,data)==first
        assert domain.project_generation(PID,UID)==generation
        with pytest.raises(DocumentConflict):service.run(PID,UID,{**data,'scan':'telling'})
    ActionJournal(domain.documents).finish(PID,UID,token)
    ActionJournal(domain.documents).restore(PID,UID,token,'undo')
    with bind_action(token,PID,UID),pytest.raises(DocumentConflict):service.run(PID,UID,data)


def test_source_change_or_revocation_during_staging_cannot_publish(editor,monkeypatch):
    domain,service,tab,bid,docs=editor
    native=service.packs.stage
    def stage(*args,**kwargs):
        staged=native(*args,**kwargs)
        docs.run_transaction(lambda tx:tx.delete(('projects',PID,'members',UID)))
        return staged
    monkeypatch.setattr(service.packs,'stage',stage)
    token=ActionJournal(domain.documents).begin(PID,UID)['token']
    with bind_action(token,PID,UID),pytest.raises(ProjectUnavailable):
        service.run(PID,UID,dict(scan='adverbs',tab_id=tab))
    assert not docs.query_collection(('projects',PID,'annotations'))
    assert not docs.query_collection(('projects',PID,'markup_labels'))


def test_failed_upload_and_expired_work_leave_existing_results_and_text_unchanged(editor,monkeypatch):
    domain,service,tab,_,docs=editor
    run(editor);before=rows(editor)
    def failed(*args,**kwargs):raise TimeoutError('Synthetic interrupted immutable upload')
    monkeypatch.setattr(service.packs,'stage',failed)
    with pytest.raises(TimeoutError):run(editor,action='recheck')
    assert rows(editor)==before
    token=deadline.set(time.monotonic()-1)
    try:
        with pytest.raises(TimeoutError):PackedAnnotations(domain).commit(PID,UID,domain.project_generation(PID,UID),[])
    finally:deadline.reset(token)
    assert rows(editor)==before


def test_wrong_user_document_action_and_reserved_pack_identity_fail_closed(editor):
    domain,service,tab,bid,docs=editor
    with pytest.raises(ProjectUnavailable):service.status(PID,OTHER,tab)
    with pytest.raises(DocumentNotFound):service.status(PID,UID,'foreign_tab')
    with pytest.raises(HostedValidationError,match='STRUCTURAL_ACTION'):
        service.run(PID,UID,dict(scan='adverbs',tab_id=tab))
    run(editor)
    head=docs.query_collection(('projects',PID,'annotations'))[0]
    with pytest.raises(DocumentNotFound):domain.get('annotation',{'project_id':PID,'annotation_id':head.path[-1]},user_id=UID)
    domain.encrypted_objects=object()
    with pytest.raises(HostedValidationError,match='PRIVATE_ANNOTATION_PACK'):
        service.status(PID,UID,tab)


def test_corrupt_pack_or_wrong_object_context_is_never_silently_omitted(editor):
    domain,_,tab,_,docs=editor
    run(editor)
    head=docs.query_collection(('projects',PID,'annotations'))[0]
    changed=deepcopy(dict(head.data));changed[FIELD]['object_name']=changed[FIELD]['object_name'].replace(PID,'f'*32)
    docs.run_transaction(lambda tx:tx.set(head.path,changed))
    with pytest.raises(HostedValidationError,match='PACK_INVALID'):rows(editor)


def test_logical_export_projection_materializes_ranges_without_pack_internals(editor):
    from hosted.packages import _public
    domain,_,_,_,docs=editor
    run(editor)
    logical=[_public(row) for row in rows(editor)]
    assert len(logical)==2 and all(row['annotation_type']=='highlight' for row in logical)
    assert 'annotation-packs/' not in json.dumps(logical) and '_bm_editing_pack' not in json.dumps(logical)
    assert all(row['body']=='editing-search/v1:adverbs' for row in logical)


def test_default_off_http_rejects_before_body_provider_and_bounds_enabled_input(editor):
    domain,_,tab,_,_=editor
    active=[False]
    app=FastAPI();app.include_router(build_router(domain=lambda:domain,identity=lambda:UID,
        project=lambda _:PID,enabled=lambda:active[0]))
    http=TestClient(app)
    assert http.post('/api/annotations/editing-search',content=b'broken').status_code==404
    active[0]=True
    assert http.post('/api/annotations/editing-search',content=b'x'*2049).status_code==413


def test_selection_clear_uses_explicit_utf16_once_preserves_other_ranges_and_undo(editor):
    domain,service,tab,bid,docs=editor
    run(editor)
    token=ActionJournal(domain.documents).begin(PID,UID)['token']
    with bind_action(token,PID,UID):
        result=service.clear_selected(PID,UID,dict(offset_unit='utf16',items=[dict(block_id=bid,
            start_offset=14,end_offset=21)]))  # emoji uses TWO UTF16 units
    ActionJournal(domain.documents).finish(PID,UID,token)
    assert result['changed']==1 and [row['selected_text'] for row in rows(editor)]==['self‑consciously']
    ActionJournal(domain.documents).restore(PID,UID,token,'undo')
    assert len(rows(editor))==2


def test_account_erasure_fence_between_body_read_and_response_rejects(editor,monkeypatch):
    domain,_,_,_,docs=editor
    run(editor);native=domain.objects.read_chunks
    def fenced(*args,**kwargs):
        data=native(*args,**kwargs)
        docs.run_transaction(lambda tx:tx.set(('account_erasure_locks',UID),dict(request_id='synthetic')))
        return data
    monkeypatch.setattr(domain.objects,'read_chunks',fenced)
    with pytest.raises(DocumentNotFound):rows(editor)


def test_real_package_roundtrip_rebuilds_packs_and_keeps_recheck_and_undo(monkeypatch):
    from hosted.packages import HostedProjectPackageService
    from tests.test_v10353 import _services, OWNER
    docs,objects,authority,users,projects=_services()
    source=projects.create('Disposable Highlights recovery');pid=source['id'];domain=projects.domain
    tab=domain.list('document_tab',{'project_id':pid},user_id=OWNER)[0]['id']
    block=domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)[0]
    domain.update('manuscript_block',{'project_id':pid,'block_id':block['id']},{'text_content':'quietly and softly'},user_id=OWNER)
    # The normal runtime already wraps this port; this isolated fixture does not.
    domain.documents=ActionDocumentStore(docs)
    journal=ActionJournal(domain.documents);token=journal.begin(pid,OWNER)['token']
    with bind_action(token,pid,OWNER):HostedEditingSearch(domain).run(pid,OWNER,dict(scan='adverbs',tab_id=tab))
    journal.finish(pid,OWNER,token)
    service=HostedProjectPackageService(projects)
    package=service.export_bytes(pid,OWNER)
    restored=service.import_bytes(package,OWNER,name_override='Restored synthetic Highlights')
    rid=restored['id'];assert rid!=pid
    ranges=domain.list('annotation',{'project_id':rid},user_id=OWNER)
    assert {row['selected_text'] for row in ranges}=={'quietly','softly'}
    assert len(docs.query_collection(('projects',rid,'annotations')))==1
    tid=domain.list('document_tab',{'project_id':rid},user_id=OWNER)[0]['id']
    token=journal.begin(rid,OWNER)['token']
    with bind_action(token,rid,OWNER):HostedEditingSearch(domain).run(rid,OWNER,dict(scan='adverbs',action='recheck',tab_id=tid))
    journal.finish(rid,OWNER,token)
    journal.restore(rid,OWNER,token,'undo')
    assert {row['selected_text'] for row in domain.list('annotation',{'project_id':rid},user_id=OWNER)}=={'quietly','softly'}
    assert len(domain.list('annotation',{'project_id':pid},user_id=OWNER))==2


def test_source_edit_marks_old_generated_ranges_stale_until_explicit_recheck(editor):
    domain,_,_,bid,_=editor
    run(editor)
    generation=rows(editor)[0]['source_generation']
    domain.update('manuscript_block',{'project_id':PID,'block_id':bid},{'text_content':'She answered softly.'},user_id=UID)
    old=rows(editor)
    assert all(row['stale'] and row['source_generation']==generation for row in old)
    run(editor,action='recheck')
    fresh=rows(editor)
    assert len(fresh)==1 and fresh[0]['selected_text']=='softly' and not fresh[0]['stale']


def test_large_pack_package_restore_retains_bounded_physical_layout_and_recheck(editor):
    from hosted.packages import HostedProjectPackageService
    from hosted.runtime_services import HostedCloudProjectRepository
    from hosted.firestore import HostedUserRepository, HostedStructuredRepository
    domain,_,tab,bid,docs=editor
    block=domain.get('manuscript_block',{'project_id':PID,'block_id':bid},user_id=UID)
    domain.update('manuscript_block',{'project_id':PID,'block_id':bid},{'text_content':'quietly '*10_000},user_id=UID)
    domain.create('manuscript_block',{'project_id':PID},{'tab_id':tab,'chapter_id':block['chapter_id'],
        'block_type':'paragraph','text_content':'softly '*10_000},user_id=UID)
    run(editor)
    projects=HostedCloudProjectRepository(docs,domain.objects,domain.projects,
        HostedUserRepository(HostedStructuredRepository(docs)),lambda:UID)
    projects.domain=domain
    package=HostedProjectPackageService(projects)
    restored=package.import_bytes(package.export_bytes(PID,UID),UID)
    rid=restored['id']
    assert len(docs.query_collection(('projects',rid,'annotations')))==1
    assert len(domain.list('annotation',{'project_id':rid},user_id=UID))==20_000
    token=ActionJournal(domain.documents).begin(rid,UID)['token']
    with bind_action(token,rid,UID):
        result=HostedEditingSearch(domain).run(rid,UID,dict(scan='adverbs',action='recheck',tab_id=tab))
    assert result['matches']==20_000


def test_shared_pure_matcher_import_never_boots_a_runtime_or_native_sql():
    import os
    import subprocess
    import sys
    from pathlib import Path
    code='''import sys;from api.annotations.editing_search import find_matches
assert find_matches("quietly", "adverbs")==[(0,7)]
assert not {"backend.environment","storage.mutations","app","google.cloud.firestore"}&sys.modules.keys()
'''
    subprocess.run([sys.executable,'-c',code],env={**os.environ,'BREADMAKER_RUNTIME':'hosted',
        'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')},check=True,timeout=3)
