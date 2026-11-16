from copy import deepcopy
import time
import pytest
from hosted.action_journal import ActionJournal, bind_action
from hosted.firestore.memory import MemoryDocumentStore
from hosted.security import account_word_state as records
from hosted.security.account_word_backfill import AccountWordBackfill
from hosted.security.account_word_backfill_store import BackfillUnavailable
from tests.test_v10596_account_word_guard import runtime, new_project, _replace, A, B


def deadline(): return time.monotonic() + 20


def worker(app, raw, calls=None):
    def read(**kw):
        if calls is not None: calls.append((kw['project_id'], kw['block_id']))
        row = app.projects.domain.get('manuscript_block',
            {'project_id': kw['project_id'], 'block_id': kw['block_id']}, user_id=kw['user_id'])
        assert len(row['text_content']) <= kw['max_text_chars']
        assert len(row['text_content'].encode()) <= kw['max_text_bytes']
        return row
    return AccountWordBackfill(raw, read, enabled=True)


def initialize(w, uid=A):
    plan = w.inspect_account(uid, deadline=deadline())
    return w.initialize_account(plan, approved=True, deadline=deadline())


def finish(w, handle):
    cursor = handle['cursor']
    for _ in range(1000):
        result = w.step(handle, expected_cursor=cursor, approved=True, deadline=deadline())
        if result['phase'] == 'complete': return result
        cursor = result['cursor']
    raise AssertionError('Synthetic job did not finish')


def test_backfill_is_account_owned_and_preserves_canonical_content_and_undo_images(monkeypatch):
    old, raw, objects = runtime(monkeypatch, enabled=False)
    pid, cid = new_project(old, A, 'two words')
    journal = ActionJournal(old.projects.documents)
    token = journal.begin(pid, A, 'Legacy synthetic action')['token']
    with bind_action(token, pid, A): _replace(old, pid, cid, [{'id': 'b_words', 'text_content': 'three saved words'}], 'legacy_action')
    journal.finish(pid, A, token)
    before = deepcopy(raw._documents)
    current, _, _ = runtime(monkeypatch, raw=raw, objects=objects)
    calls=[]; w=worker(current, raw, calls)
    initial=initialize(w); assert initial['state']=='unknown'
    handle=initial['jobs'][0]; finish(w, handle)
    assert raw.get(records.path(A)).data['saved_words']==3 and len(calls)==1
    for path,row in before.items(): assert raw._documents[path]==row
    assert all(path[:2]==('users',A) for path in set(raw._documents)-set(before))
    # Backfill has not changed strict journal CAS. An old uncounted restored
    # image becomes unknown; counting the now-current state repairs it safely.
    ActionJournal(current.projects.documents).restore(pid,A,token,'undo')
    assert raw.get(records.path(A)).data['saved_words'] is None
    repaired=initialize(w);finish(w,repaired['jobs'][0])
    assert raw.get(records.path(A)).data['saved_words']==2


def test_pages_resume_without_double_counting_or_extra_plaintext_reads():
    raw=MemoryDocumentStore(); born='2026-11-16T00:00:00Z'; pid='c'*32
    def seed(tx):
        tx.create(('users',A),{'user_id':A,'status':'active','created_at':born})
        tx.create(('projects',pid),{'status':'active','generation':1,'created_at':born})
        tx.create(('projects',pid,'members',A),{'user_id':A,'role':'owner','created_at':born})
        for i in range(41):tx.create(('projects',pid,'blocks',f'b_{i:04d}'),{'generation':1})
    raw.run_transaction(seed); calls=[]
    def read(**kw):calls.append(kw['block_id']);return dict(generation=1,text_content='two words')
    w=AccountWordBackfill(raw,read,enabled=True);h=initialize(w)['jobs'][0]
    first=w.step(h,expected_cursor=None,approved=True,deadline=deadline())
    assert first['seen_blocks']==8 and len(calls)==8 and raw.get(records.path(A)).data['saved_words'] is None
    before=deepcopy(raw._documents)
    replay=w.step(h,expected_cursor=None,approved=True,deadline=deadline())
    assert replay['skip'] and replay['cursor']==first['cursor'] and len(calls)==8 and raw._documents==before
    h['cursor']=first['cursor'];finish(w,h)
    assert len(calls)==len(set(calls))==41
    assert raw.get(records.path(A)).data['saved_words']==82
    before=deepcopy(raw._documents);finish(w,h);assert raw._documents==before


@pytest.mark.parametrize('drift',['edit','freeze','new_project','owner'])
def test_mutation_during_content_read_cannot_publish_complete_counts(monkeypatch, drift):
    old,raw,objects=runtime(monkeypatch,enabled=False);pid,cid=new_project(old,A,'two words')
    current,_,_=runtime(monkeypatch,raw=raw,objects=objects)
    ordinary=worker(current,raw).read_block
    def read(**kw):
        value=ordinary(**kw)
        if drift=='edit': _replace(current,pid,cid,[{'id':'b_words','text_content':'new content now'}],'concurrent_backfill_edit')
        elif drift=='freeze':
            profile=dict(raw.get(('users',A)).data);profile['status']='erasure_pending'
            raw.run_transaction(lambda tx:tx.set(('users',A),profile))
        elif drift=='new_project':new_project(old,A,'new owned project')
        else:
            member=dict(raw.get(('projects',pid,'members',A)).data);member['role']='editor'
            raw.run_transaction(lambda tx:tx.set(('projects',pid,'members',A),member))
        return value
    w=AccountWordBackfill(raw,read,enabled=True);h=initialize(w)['jobs'][0]
    with pytest.raises((BackfillUnavailable,ValueError)):
        w.step(h,expected_cursor=None,approved=True,deadline=deadline())
    assert raw.get(records.path(A)).data['saved_words'] is None


def test_empty_verified_account_is_zero_and_participant_project_is_not_owned(monkeypatch):
    one,raw,objects=runtime(monkeypatch,enabled=False)
    two,_,_=runtime(monkeypatch,uid=B,enabled=False,raw=raw,objects=objects)
    pid,_=new_project(two,B,'three saved words')
    two.projects.authority.add_member(pid,B,A,'editor',expected_project_generation=two.projects.get(pid)['generation'],operation_id='backfill_share')
    current,_,_=runtime(monkeypatch,raw=raw,objects=objects)
    result=initialize(worker(current,raw))
    assert result['jobs']==[] and raw.get(records.path(A)).data['saved_words']==0
    assert raw.get(records.path(A)).data['projects']=={}


def test_unapproved_initialization_never_writes_and_failed_commit_is_atomic(monkeypatch):
    old,raw,objects=runtime(monkeypatch,enabled=False);new_project(old,A,'two words')
    current,_,_=runtime(monkeypatch,raw=raw,objects=objects);w=worker(current,raw)
    p=w.inspect_account(A,deadline=deadline());before=deepcopy(raw._documents)
    with pytest.raises(BackfillUnavailable):w.initialize_account(p,deadline=deadline())
    assert raw._documents==before
    h=w.initialize_account(p,approved=True,deadline=deadline())['jobs'][0]
    before=deepcopy(raw._documents);write=raw._write_locked
    def failed(path,data):
        if path==records.path(A):raise RuntimeError('synthetic backfill commit failure')
        write(path,data)
    monkeypatch.setattr(raw,'_write_locked',failed)
    with pytest.raises(RuntimeError):w.step(h,expected_cursor=None,approved=True,deadline=deadline())
    assert raw._documents==before


def test_more_than_initialization_cap_pages_without_plaintext_or_double_counting():
    from hosted.security.saved_word_projection import DEFINITION
    raw=MemoryDocumentStore();born='2026-11-16T00:00:00Z';pid='c'*32
    def seed(tx):
        tx.create(('users',A),dict(user_id=A,status='active',created_at=born))
        tx.create(('projects',pid),dict(status='active',generation=1,created_at=born))
        tx.create(('projects',pid,'members',A),dict(user_id=A,role='owner',created_at=born))
        for i in range(1001):tx.create(('projects',pid,'blocks',f'b_{i:04d}'),dict(generation=1,
            _bm_saved_word_v1=dict(definition=DEFINITION,source_generation=1,words=2)))
    raw.run_transaction(seed)
    def denied(**kw):raise AssertionError('Verified current metadata must not open plaintext')
    w=AccountWordBackfill(raw,denied,enabled=True);h=initialize(w)['jobs'][0]
    result=finish(w,h)
    assert result['seen_blocks']==1001 and raw.get(records.path(A)).data['saved_words']==2002
    job=w.inspect_job(h,deadline=deadline())
    assert job['pages']==32 and job['plaintext_reads']==0


@pytest.mark.parametrize('bad',['duplicate','reversed','foreign_path'])
def test_malformed_provider_page_cannot_publish_account_counts(monkeypatch,bad):
    from hosted.firestore.contracts import StoredDocument
    from hosted.firestore.memory import _MemoryTransaction
    old,raw,objects=runtime(monkeypatch,enabled=False);new_project(old,A,'two words')
    current,_,_=runtime(monkeypatch,raw=raw,objects=objects);w=worker(current,raw)
    h=initialize(w)['jobs'][0];original=_MemoryTransaction.saved_word_page
    def page(tx,*a,**kw):
        result=original(tx,*a,**kw)
        if bad=='duplicate':return result+result
        if bad=='reversed':
            r=result[0];return [StoredDocument(r.path[:-1]+('b_z',),r.data,r.revision),r]
        r=result[0];return [StoredDocument(('projects','f'*32,'blocks',r.path[-1]),r.data,r.revision)]
    monkeypatch.setattr(_MemoryTransaction,'saved_word_page',page)
    before=deepcopy(raw._documents)
    with pytest.raises(BackfillUnavailable,match='PAGE_UNVERIFIED'):
        w.step(h,expected_cursor=None,approved=True,deadline=deadline())
    assert raw._documents==before
