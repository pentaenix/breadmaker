"""Hosted Notes authority, atomicity, retries, preservation and source contracts."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from hosted.notes import HostedNotes
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, ProjectUnavailable
from hosted.idempotency import bind_operations
from tests.test_v10354 import _services, OWNER, READER


@pytest.fixture
def notes_env():
    docs,objects,authority,users,current,projects=_services()
    pid=projects.create('Notes QA')['id'];domain=projects.domain
    chapter=domain.create('chapter',{'project_id':pid},{'title':'Harbor','tab_id':'tab_manuscript','position':0},user_id=OWNER)
    block=domain.create('manuscript_block',{'project_id':pid},{'chapter_id':chapter['id'],'tab_id':'tab_manuscript','block_type':'paragraph','position':0,'text_content':'The quiet harbor','content_html':'The quiet harbor'},user_id=OWNER)
    ui=HostedNotes(domain,pid,OWNER)
    payload={'body':'Original note','items':[{'block_id':block['id'],'start_offset':4,'end_offset':9,'selected_text':'quiet'}]}
    return docs,projects,domain,pid,chapter,block,ui,payload


def test_notes_lifecycle_atomic_generation_payload_filter_and_other_thread_preservation(notes_env):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    before=d.project_generation(pid,OWNER);first=ui.create(payload)
    assert d.project_generation(pid,OWNER)==before+1
    assert first['first_message']=='Original note' and first['anchors'][0]['chapter_id']==ch['id']
    assert first['anchors'][0]['selected_text']=='quiet'
    other=ui.create({**payload,'body':'Unrelated note'})
    tid=first['id'];reply=ui.reply(tid,{'body':'Follow-up'})
    assert reply['id']==tid and len(reply['messages'])==2 and reply['reply_count']==1
    edited=ui.edit(tid,first['messages'][0]['id'],{'body':'Revised note'})
    assert edited['first_message']=='Revised note' and edited['anchors'][0]['body']=='Revised note'
    ui.update(tid,{'status':'resolved'})
    assert [x['id'] for x in ui.list('open')]==[other['id']]
    assert [x['id'] for x in ui.list('resolved','Revised')]==[tid]
    assert ui.list('all','absent-needle')==[]
    ui.update(tid,{'status':'open'})
    mid=reply['messages'][1]['id'];ui.edit(tid,mid,{'body':'Edited follow-up'})
    assert [m['body'] for m in ui.get(tid)['messages']]==['Revised note','Edited follow-up']
    ui.delete(tid)
    assert [x['id'] for x in ui.list('all')]==[other['id']]
    assert docs.query_collection(('projects',pid,'comment_threads',tid,'messages'))==[]
    assert docs.query_collection(('projects',pid,'annotations'),field='thread_id',value=tid)==[]
    assert ui.get(other['id'])['first_message']=='Unrelated note'


def test_notes_nonmember_and_revoked_editor_cannot_mutate_or_read_foreign_sources(notes_env):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    first=ui.create(payload);before=deepcopy(docs._documents)
    outsider=HostedNotes(d,pid,READER)
    for action in (lambda:outsider.list(),lambda:outsider.get(first['id']),lambda:outsider.create(payload),lambda:outsider.reply(first['id'],{'body':'x'}),lambda:outsider.edit_message(first['messages'][0]['id'],{'body':'x'}),lambda:outsider.delete(first['id'])):
        with pytest.raises(ProjectUnavailable):action()
    assert docs._documents==before
    projects.add_member(pid,READER,'editor')
    member=HostedNotes(d,pid,READER)
    assert member.get(first['id'])['first_message']=='Original note'
    projects.remove_member(pid,READER)
    before=deepcopy(docs._documents)
    for action in (lambda:member.create(payload),lambda:member.update(first['id'],{'status':'resolved'}),lambda:member.reply(first['id'],{'body':'x'}),lambda:member.edit(first['id'],first['messages'][0]['id'],{'body':'x'}),lambda:member.delete(first['id'])):
        with pytest.raises(ProjectUnavailable):action()
    assert docs._documents==before


def test_notes_foreign_anchor_and_message_ids_fail_without_partial_writes(notes_env):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    other=projects.create('Other QA')['id']
    foreign=d.create('chapter',{'project_id':other},{'title':'Foreign','tab_id':'tab_manuscript','position':1},user_id=OWNER)
    before=deepcopy(docs._documents)
    with pytest.raises(HTTPException) as error:ui.create({'body':'x','items':[payload['items'][0],{'chapter_id':foreign['id'],'start_offset':0,'end_offset':2}]})
    assert error.value.status_code==409 and docs._documents==before
    first=ui.create(payload);second=ui.create(payload);before=deepcopy(docs._documents)
    with pytest.raises(DocumentNotFound):ui.edit(first['id'],second['messages'][0]['id'],{'body':'Do not cross threads'})
    assert docs._documents==before


def test_notes_invalid_body_ranges_and_status_do_not_publish(notes_env):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    before=deepcopy(docs._documents)
    bad=[{**payload,'body':''},{**payload,'body':'x'*12001},{**payload,'generation':1},
        {**payload,'items':[]},{**payload,'items':[{**payload['items'][0],'end_offset':99}]},
        {**payload,'items':[{**payload['items'][0],'selected_text':'stale'}]},
        {**payload,'items':[{**payload['items'][0],'chapter_id':ch['id']}]}]
    for body in bad:
        with pytest.raises(HTTPException):ui.create(body)
        assert docs._documents==before
    first=ui.create(payload);before=deepcopy(docs._documents)
    for status in ('all','deleted',''):
        with pytest.raises(HTTPException):ui.update(first['id'],{'status':status})
        if status!='all':
            with pytest.raises(HTTPException):ui.list(status)
    assert docs._documents==before


def test_notes_create_source_generation_fence_prevents_stale_anchor(notes_env,monkeypatch):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    original=ui.checked_items
    def raced(items):
        result=original(items)
        d.update('chapter',{'project_id':pid,'chapter_id':ch['id']},{'title':'Changed'},user_id=OWNER)
        return result
    monkeypatch.setattr(ui,'checked_items',raced)
    with pytest.raises(DocumentConflict):ui.create(payload)
    assert ui.list('all')==[] and d.list('annotation',{'project_id':pid},user_id=OWNER)==[]


def test_notes_provider_retry_and_request_replay_never_duplicate_or_overwrite(notes_env,monkeypatch):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    original=docs.run_transaction;attempts=[]
    def retry(fn,**kwargs):
        if fn.__name__=='run':
            docs.retry_next_transaction();attempts.append('retry')
        return original(fn,**kwargs)
    monkeypatch.setattr(docs,'run_transaction',retry)
    with bind_operations('a'*64):first=ui.create(payload)
    generation=d.project_generation(pid,OWNER)
    with bind_operations('a'*64):again=ui.create(payload)
    assert first['id']==again['id'] and d.project_generation(pid,OWNER)==generation
    assert len(ui.list('all'))==1 and len(again['messages'])==len(again['anchors'])==1
    with bind_operations('a'*64),pytest.raises(DocumentConflict):ui.create({**payload,'body':'Changed request'})
    with bind_operations('b'*64):reply=ui.reply(first['id'],{'body':'Idempotent reply'})
    generation=d.project_generation(pid,OWNER)
    with bind_operations('b'*64):again=ui.reply(first['id'],{'body':'Idempotent reply'})
    assert len(again['messages'])==2 and d.project_generation(pid,OWNER)==generation
    ui.reply(first['id'],{'body':'Intervening edit'})
    with bind_operations('a'*64),pytest.raises(DocumentConflict):ui.create(payload)
    assert attempts


@pytest.mark.parametrize('kind',['create','reply','edit','status','delete'])
def test_notes_failed_provider_commit_rolls_back_all_children_and_receipt(notes_env,monkeypatch,kind):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    first=ui.create(payload)
    actions={'create':lambda:ui.create({**payload,'body':'Another note'}),'reply':lambda:ui.reply(first['id'],{'body':'New reply'}),
        'edit':lambda:ui.edit(first['id'],first['messages'][0]['id'],{'body':'Changed'}),
        'status':lambda:ui.update(first['id'],{'status':'resolved'}),'delete':lambda:ui.delete(first['id'])}
    before=deepcopy(docs._documents);original=docs.run_transaction
    def fail(fn,**kwargs):
        if fn.__name__!='run':return original(fn,**kwargs)
        def aborted(tx):
            fn(tx)
            raise RuntimeError('Injected provider commit failure')
        return original(aborted,**kwargs)
    monkeypatch.setattr(docs,'run_transaction',fail)
    with pytest.raises(RuntimeError,match='Injected provider'):actions[kind]()
    assert docs._documents==before


def test_notes_legacy_raw_body_items_are_preserved_read_edit_reply_delete(notes_env):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    old=d.create('comment_thread',{'project_id':pid},payload,user_id=OWNER)
    generation=d.project_generation(pid,OWNER);saved=ui.get(old['id'])
    assert saved['first_message']=='Original note' and saved['messages'][0]['id']==old['id']
    assert saved['anchors'][0]['block_id']==b['id']
    assert d.project_generation(pid,OWNER)==generation
    assert d.get('comment_thread',{'project_id':pid,'thread_id':old['id']},user_id=OWNER)['body']=='Original note'
    ui.reply(old['id'],{'body':'New reply'})
    edited=ui.edit(old['id'],old['id'],{'body':'Edited original'})
    assert [m['body'] for m in edited['messages']]==['Edited original','New reply']
    assert edited['items']==payload['items']
    ui.delete(old['id']);assert ui.list('all')==[]


def test_notes_title_and_unicode_anchors_and_deleted_source_remain_readable(notes_env):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    title=ui.create({'body':'Title note','items':[{'chapter_id':ch['id'],'start_offset':0,'end_offset':6,'selected_text':'Harbor'}]})
    assert title['anchors'][0]['chapter_title_anchor'] and title['chapter_id']==ch['id']
    d.update('manuscript_block',{'project_id':pid,'block_id':b['id']},{'text_content':'A 😀 harbor','content_html':'A 😀 harbor'},user_id=OWNER)
    emoji=ui.create({'body':'Emoji note','items':[{'block_id':b['id'],'start_offset':2,'end_offset':4,'selected_text':'😀'}]})
    assert emoji['anchors'][0]['selected_text']=='😀'
    with pytest.raises(HTTPException):ui.create({'body':'Bad split','items':[{'block_id':b['id'],'start_offset':2,'end_offset':3}]})
    d.delete('manuscript_block',{'project_id':pid,'block_id':b['id']},user_id=OWNER)
    generation=d.project_generation(pid,OWNER)
    assert ui.get(emoji['id'])['stale'] is True
    assert ui.get(emoji['id'])['first_message']=='Emoji note'
    assert d.project_generation(pid,OWNER)==generation


def test_notes_reads_use_authorized_source_metadata_without_downloading_prose(notes_env,monkeypatch):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    first=ui.create(payload);generation=d.project_generation(pid,OWNER)
    def forbidden(*args,**kwargs):raise AssertionError('Notes display must not fetch manuscript bodies')
    monkeypatch.setattr(d.objects,'read_chunks',forbidden)
    assert ui.get(first['id'])['anchors'][0]['chapter_title']=='Harbor'
    assert len(ui.list('all'))==1
    assert d.project_generation(pid,OWNER)==generation


def test_notes_large_legacy_thread_stays_readable_editable_without_partial_delete(notes_env):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    first=ui.create(payload);tid=first['id']
    def seed(tx):
        for i in range(1,91):
            mid='message_large_'+str(i)
            tx.create(ui.path('comment_message',mid,tid),ui.created(mid,'message_id','2026-11-03T00:00:00Z',body='Saved reply '+str(i),position=i))
    docs.run_transaction(seed)
    assert len(ui.get(tid)['messages'])==91
    assert ui.edit(tid,first['messages'][0]['id'],{'body':'Edited large original'})['first_message']=='Edited large original'
    before=deepcopy(docs._documents)
    with pytest.raises(HTTPException) as error:ui.delete(tid)
    assert error.value.status_code==413 and docs._documents==before
    assert len(ui.get(tid)['messages'])==91


def test_notes_transaction_byte_budget_rejects_before_publication(notes_env):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    before=deepcopy(docs._documents)
    with pytest.raises(HTTPException) as error:ui.create({'body':'x'*4000,'items':payload['items']*88})
    assert error.value.status_code==413 and docs._documents==before


def test_notes_old_message_route_resolves_only_authorized_project(notes_env):
    docs,projects,d,pid,ch,b,ui,payload=notes_env
    first=ui.create(payload)
    assert ui.edit_message(first['messages'][0]['id'],{'body':'Edited via old route'})['first_message']=='Edited via old route'
    other=projects.create('Another project')['id'];other_ui=HostedNotes(d,other,OWNER)
    before=deepcopy(docs._documents)
    with pytest.raises(DocumentNotFound):other_ui.edit_message(first['messages'][0]['id'],{'body':'Do not cross projects'})
    assert docs._documents==before
