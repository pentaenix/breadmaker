"""Black-box canonical safety when optional operational authority is absent."""
from copy import deepcopy
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
import pytest
from hosted.firestore.contracts import DocumentConflict
from hosted.security import account_word_state as records
from hosted.security.saved_word_guard import LEDGER_COLLECTION
from hosted.security.saved_word_projection import DEFINITION
from hosted.security.metric_projection_budget import current_scope,transaction_scope
from tests.test_v10596_account_word_guard import runtime,new_project,A

class Budget:
    def __init__(self,*,reads=10**6,writes=10**6,write_bytes=2**40,fail_envelope=None,fail_start=False):
        self.left=dict(reads=reads,writes=writes,write_bytes=write_bytes);self.fail_envelope=fail_envelope;self.fail_start=fail_start
        self.retained=[]
    def projection(self):
        if self.fail_start:raise OSError('Synthetic admission unavailable')
        return self
    def reserve(self,**charges):
        if any(v>self.left[k] for k,v in charges.items()):raise ValueError('Synthetic credits exhausted')
        for k,v in charges.items():self.left[k]-=v
    def run_restore(self,handler,**kw):
        self.reserve(reads=1);return handler(**kw)
    def retain(self,path,*args):
        kind='account' if path[0]=='users' else 'project'
        if kind==self.fail_envelope:raise ValueError('Synthetic storage exhausted')
        self.retained.append((tuple(path),deepcopy(args)))


@pytest.mark.parametrize('present',[True,False])
def test_retained_storage_receives_the_real_prepublication_record_in_the_canonical_transaction(monkeypatch,present):
    app,raw,_=runtime(monkeypatch);pid,_=new_project(app,A,'two words')
    path=records.path(A)
    if not present:raw.run_transaction(lambda tx:tx.delete(path))
    previous=raw.get(path);ledger=raw.get((LEDGER_COLLECTION,pid))
    budget=Budget();app.projects.documents.store.store.resource_budget=budget
    callbacks=[];save(app,pid,callbacks)
    observations={p:args for p,args in budget.retained}
    assert observations[path][1]==(dict(previous.data) if previous is not None else None)
    assert observations[(LEDGER_COLLECTION,pid)][1]==dict(ledger.data)
    assert observations[path][0]==raw.get(path).data
    assert callbacks==['canonical callback']
    assert raw.get(('projects',pid,'blocks','b_words')).data['text_content']=='three saved words'
    if not present:assert raw.get(path).data['state']=='unknown'

def save(app,pid,callbacks):
    def mutate(tx):
        callbacks.append('canonical callback')
        path=('projects',pid,'blocks','b_words');row=dict(tx.get(path).data)
        row.update(text_content='three saved words',generation=row['generation']+1)
        row['_bm_saved_word_v1']=dict(definition=DEFINITION,source_generation=row['generation'],words=3)
        root=dict(tx.get(('projects',pid)).data);root['generation']+=1
        tx.set(path,row);tx.set(('projects',pid),root)
    return app.projects.documents.run_transaction(mutate)

@pytest.mark.parametrize('failure',['reads','writes','write_bytes','storage','start'])
def test_budget_failure_keeps_one_canonical_save_and_discards_all_optional_updates(monkeypatch,failure):
    app,raw,_=runtime(monkeypatch);pid,_=new_project(app,A,'two words')
    coverage=app.projects.documents.store;guard=coverage.store
    guard.resource_budget=Budget(reads=0 if failure=='reads' else 10**6,writes=0 if failure=='writes' else 10**6,
        write_bytes=0 if failure=='write_bytes' else 2**40,
        fail_envelope='account' if failure=='storage' else None,fail_start=failure=='start')
    word_before=raw.get(records.path(A));ledger_before=raw.get((LEDGER_COLLECTION,pid));revision=app.projects.get(pid)['generation'];calls=[]
    save(app,pid,calls)
    assert calls==['canonical callback']
    assert app.projects.get(pid)['generation']==revision+1
    assert raw.get(('projects',pid,'blocks','b_words')).data['text_content']=='three saved words'
    assert raw.get(records.path(A))==word_before and raw.get((LEDGER_COLLECTION,pid))==ledger_before
    row=dict(profile=raw.get(('users',A)).data,words=word_before.data,owned_projects={pid:(raw.get(('projects',pid)).data['created_at'],revision+1)})
    with pytest.raises(ValueError,match='COVERAGE_STALE'):records.sum_accounts([row])
    guard.resource_budget=Budget();save(app,pid,[])
    assert raw.get(records.path(A)).data['state']=='unknown' and raw.get(records.path(A)).data['saved_words'] is None


def test_budget_does_not_bypass_erasure_or_ownership_fence(monkeypatch):
    app,raw,_=runtime(monkeypatch);pid,_=new_project(app,A,'two words')
    app.projects.documents.store.store.resource_budget=Budget(reads=0)
    def freeze(tx):
        profile=dict(tx.get(('users',A)).data);profile['status']='erasure_pending'
        tx.set(('users',A),profile);tx.create(('account_erasure_locks',A),dict(user_id=A))
    raw.run_transaction(freeze)
    before=deepcopy(raw._documents)
    with pytest.raises(DocumentConflict):save(app,pid,[])
    assert raw._documents==before


def test_resource_scope_is_isolated_between_concurrent_threads():
    barrier=Barrier(2)
    def one(failed):
        with transaction_scope(None if failed else Budget()) as scope:
            barrier.wait();assert current_scope() is scope
            return scope.failed
    with ThreadPoolExecutor(max_workers=2) as executor:
        a=executor.submit(one,True);b=executor.submit(one,False)
        assert a.result() is True and b.result() is False
    assert current_scope() is None


def test_failed_optional_budget_keeps_mandatory_backend_root_erasure_cleanup():
    # Isolate reporting wrappers over synthetic storage. This does not grant the
    # application permission to erase, or enable the production erasure path.
    from hosted.firestore.memory import MemoryDocumentStore
    from hosted.security.account_word_guard import AccountWordGuard
    from hosted.security.saved_word_guard import SavedWordCoverageGuard
    raw=MemoryDocumentStore();pid='c'*32
    raw.run_transaction(lambda tx:(tx.create(('projects',pid),{'status':'active'}),
                                  tx.create((LEDGER_COLLECTION,pid),{'synthetic':True})))
    guarded=SavedWordCoverageGuard(AccountWordGuard(raw,enabled=True,resource_budget=Budget(fail_start=True)),enabled=True)
    calls=[]
    def erase(tx):calls.append('backend erase');tx.delete(('projects',pid))
    guarded.run_transaction(erase)
    assert calls==['backend erase'] and raw.get(('projects',pid)) is None
    assert raw.get((LEDGER_COLLECTION,pid)) is None


def test_optional_metadata_transport_failure_does_not_replay_or_block_canonical_save(monkeypatch):
    from hosted.firestore.memory import _MemoryTransaction
    app,raw,_=runtime(monkeypatch);pid,_=new_project(app,A,'two words')
    app.projects.documents.store.store.resource_budget=Budget()
    previous=raw.get(records.path(A));ledger=raw.get((LEDGER_COLLECTION,pid));calls=[]
    original=_MemoryTransaction.saved_word_owners;failed=[]
    def intermittent(tx,*args,**kw):
        if not failed:failed.append(True);raise OSError('Synthetic optional read outage')
        return original(tx,*args,**kw)
    monkeypatch.setattr(_MemoryTransaction,'saved_word_owners',intermittent)
    save(app,pid,calls)
    assert failed and calls==['canonical callback']
    assert raw.get(('projects',pid,'blocks','b_words')).data['text_content']=='three saved words'
    assert raw.get(records.path(A))==previous and raw.get((LEDGER_COLLECTION,pid))==ledger


def test_undo_and_redo_keep_manuscript_working_when_count_authority_is_exhausted(monkeypatch):
    from hosted.action_journal import ActionJournal,bind_action
    from tests.test_v10596_account_word_guard import _replace
    app,raw,_=runtime(monkeypatch);pid,cid=new_project(app,A,'two words')
    journal=ActionJournal(app.projects.documents);token=journal.begin(pid,A,'Synthetic quota undo')['token']
    budget=Budget(reads=0);app.projects.documents.store.store.resource_budget=budget
    app.projects.documents.restore_repair.resource_budget=budget
    before=raw.get(records.path(A))
    with bind_action(token,pid,A):
        _replace(app,pid,cid,[dict(id='b_words',text_content='three saved words')],'quota_edit',app.projects.get(pid)['generation'])
    journal.finish(pid,A,token)
    for direction,text in [('undo','two words'),('redo','three saved words')]:
        journal.restore(pid,A,token,direction)
        assert app.projects.domain.get('manuscript_block',dict(project_id=pid,block_id='b_words'),user_id=A)['text_content']==text
        assert raw.get(records.path(A))==before
        assert before.data['projects'][pid]['canonical_revision']!=app.projects.get(pid)['generation']
