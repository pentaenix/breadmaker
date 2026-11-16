from copy import deepcopy
from hosted.action_journal import ActionJournal,bind_action,_path
from hosted.security import account_word_state as records
from tests.test_v10596_account_word_guard import runtime,new_project,_replace,A,B
from tests.test_v10596_account_word_backfill import worker,initialize,finish
from tests.test_v10596_account_word_content import content
from tests.test_v10596_account_word_transport import sdk,UID,PID,until


def legacy(monkeypatch,number=1):
    old,raw,objects=runtime(monkeypatch,enabled=False);pid,cid=new_project(old,A,'two words')
    before=[dict(id=f'b_{i:03d}',text_content='two words') for i in range(number)]
    _replace(old,pid,cid,before,'legacy_before')
    j=ActionJournal(old.projects.documents);token=j.begin(pid,A,'Synthetic legacy restore')['token']
    with bind_action(token,pid,A):
        _replace(old,pid,cid,[dict(row,text_content='three saved words') for row in before],'legacy_after')
    j.finish(pid,A,token)
    app,_,_=runtime(monkeypatch,raw=raw,objects=objects)
    w=worker(app,raw)
    for h in initialize(w)['jobs']:finish(w,h)
    return app,raw,pid,token


def test_legacy_undo_redo_repair_counts_current_text_without_rewriting_images(monkeypatch):
    app,raw,pid,token=legacy(monkeypatch)
    j=ActionJournal(app.projects.documents);path=_path(A,token)
    before=deepcopy(raw.get(path).data);calls=[]
    ordinary=app.projects.documents.restore_repair.memory_reader
    def read(**kw):calls.append(kw['block_id']);return ordinary(**kw)
    app.projects.documents.restore_repair.memory_reader=read
    for direction,words in [('undo',2),('redo',3)]:
        assert j.restore(pid,A,token,direction)=={'ok':True,'changed':True}
        assert raw.get(records.path(A)).data['saved_words']==words
        block=raw.get(('projects',pid,'blocks','b_000')).data
        assert '_bm_saved_word_v1' not in block  # old canonical image stays old
        after=raw.get(path).data
        for previous,current in zip(before['records'],after['records']):
            assert previous['before']==current['before'] and previous['after']==current['after']
    assert calls==['b_000','b_000']


def test_large_restore_repairs_a_bounded_subset_and_idempotent_retry_finishes(monkeypatch):
    app,raw,pid,token=legacy(monkeypatch,11);j=ActionJournal(app.projects.documents)
    calls=[];ordinary=app.projects.documents.restore_repair.memory_reader
    def read(**kw):calls.append(kw['block_id']);return ordinary(**kw)
    app.projects.documents.restore_repair.memory_reader=read
    j.restore(pid,A,token,'undo')
    assert len(calls)==8 and raw.get(records.path(A)).data['saved_words'] is None
    generation=raw.get(('projects',pid)).data['generation']
    assert j.restore(pid,A,token,'undo')=={'ok':True,'changed':False}
    assert raw.get(('projects',pid)).data['generation']==generation
    assert len(calls)==11 and raw.get(records.path(A)).data['saved_words']==22
    j.restore(pid,A,token,'undo');assert len(calls)==11


def test_failed_post_commit_repair_never_reapplies_or_fails_successful_undo(monkeypatch):
    app,raw,pid,token=legacy(monkeypatch);j=ActionJournal(app.projects.documents)
    def unavailable(**kw):raise TimeoutError('synthetic content unavailable')
    app.projects.documents.restore_repair.memory_reader=unavailable
    assert j.restore(pid,A,token,'undo')==dict(ok=True,changed=True)
    assert raw.get(records.path(A)).data['saved_words'] is None
    assert app.projects.domain.get('manuscript_block',dict(project_id=pid,block_id='b_000'),user_id=A)['text_content']=='two words'


def test_real_sdk_repair_publishes_only_user_proof_and_account_state(content):
    reader,args,raw,api,blob,requested,_=content
    born=raw.get(('users',UID)).data['created_at']
    record=records.state(UID,born,initialized=True,projects={PID:records.project_entry(
        created_at=born,revision=1,blocks=1,known_blocks=0,known_words=0,initialized=True)})
    raw.run_transaction(lambda tx:tx.set(records.path(UID),record));before=deepcopy(raw._documents)
    from hosted.security.account_word_backfill import AccountWordBackfill
    w=AccountWordBackfill(reader.store,reader,enabled=True)
    assert w.repair_current_blocks(UID,PID,['b_one'],deadline=until())['state']=='complete'
    assert raw.get(records.path(UID)).data['saved_words']==3 and len(requested)==1
    assert w.repair_current_blocks(UID,PID,['b_one'],deadline=until())['repaired_blocks']==0
    assert len(requested)==1
    for p,row in before.items():
        if p!=records.path(UID):assert raw._documents[p]==row
