"""Always-on ownership revision/cardinality fence; no metrics activation."""
from copy import deepcopy
import pytest
from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.contracts import DocumentConflict
from hosted.account_lifecycle import AccountLifecycleDocumentStore

A,B=['usr_'+c*32 for c in ('a','b')]
PID='1'*32
BORN='2026-11-01T00:00:00Z'
MEMBER='2026-11-02T00:00:00Z'
ROOT=('projects',PID)
def member(uid,**extra):return dict(user_id=uid,role='owner',created_at=MEMBER,**extra)

def seed(*,status='active',owners=(A,)):
 raw=MemoryDocumentStore()
 def put(tx):
  for uid in (A,B):tx.create(('users',uid),dict(user_id=uid,status='active',created_at=BORN))
  tx.create(ROOT,dict(status=status,generation=1,created_at=MEMBER))
  for uid in owners:tx.create(ROOT+('members',uid),member(uid))
 raw.run_transaction(put)
 return raw,AccountLifecycleDocumentStore(raw)

def bump(tx,**patch):
 d=dict(tx.get(ROOT).data);d.update(generation=d['generation']+1,**patch);tx.set(ROOT,d)

def unchanged_after_rejection(raw,store,fn):
 before=deepcopy(raw._documents)
 with pytest.raises(DocumentConflict):store.run_transaction(fn,max_attempts=1)
 assert raw._documents==before

@pytest.mark.parametrize('advance',[False,True])
def test_second_owner_is_rejected_at_writer_boundary(advance):
 raw,store=seed()
 def mutate(tx):
  if advance:bump(tx)
  tx.create(ROOT+('members',B),member(B))
 unchanged_after_rejection(raw,store,mutate)

def test_transfer_requires_atomic_root_advance():
 raw,store=seed()
 def transfer(tx):
  tx.delete(ROOT+('members',A));tx.create(ROOT+('members',B),member(B))
 unchanged_after_rejection(raw,store,transfer)
 store.run_transaction(lambda tx:(bump(tx),transfer(tx)),max_attempts=1)
 assert raw.get(ROOT).data['generation']==2
 assert raw.get(ROOT+('members',A)) is None
 assert raw.get(ROOT+('members',B)).data['role']=='owner'

@pytest.mark.parametrize('change',['delete','role','birth','uid'])
def test_owner_identity_mutations_cannot_bypass_revision(change):
 raw,store=seed()
 def mutate(tx):
  path=ROOT+('members',A)
  if change=='delete':tx.delete(path);return
  d=dict(tx.get(path).data);d.update({'role':'editor'} if change=='role' else {'created_at':'2026-11-03T00:00:00Z'} if change=='birth' else {'user_id':B});tx.set(path,d)
 unchanged_after_rejection(raw,store,mutate)

def test_ownerless_active_project_is_rejected_even_with_revision():
 raw,store=seed()
 unchanged_after_rejection(raw,store,lambda tx:(bump(tx),tx.delete(ROOT+('members',A))))

@pytest.mark.parametrize('owners',[(),(A,B)])
def test_reactivation_rejects_unverified_cardinality(owners):
 raw,store=seed(status='trashed',owners=owners)
 unchanged_after_rejection(raw,store,lambda tx:bump(tx,status='active'))

def test_reactivation_preserves_owner_and_requires_advance():
 raw,store=seed(status='trashed')
 unchanged_after_rejection(raw,store,lambda tx:tx.set(ROOT,dict(tx.get(ROOT).data,status='active')))
 store.run_transaction(lambda tx:bump(tx,status='active'),max_attempts=1)
 assert raw.get(ROOT).data['status']=='active'

def test_ambiguous_existing_owners_are_not_implicitly_repaired():
 raw,store=seed(owners=(A,B))
 unchanged_after_rejection(raw,store,lambda tx:(bump(tx),tx.delete(ROOT+('members',B))))

def test_presence_update_does_not_require_root_generation_change():
 raw,store=seed()
 store.run_transaction(lambda tx:tx.set(ROOT+('members',A),dict(tx.get(ROOT+('members',A)).data,last_opened_at=MEMBER)),max_attempts=1)
 assert raw.get(ROOT).data['generation']==1

def test_new_root_requires_one_owner_and_verifies_account():
 raw,store=seed();other=('projects','2'*32)
 unchanged_after_rejection(raw,store,lambda tx:tx.create(other,dict(status='active',generation=1,created_at=MEMBER)))
 store.run_transaction(lambda tx:(tx.create(other,dict(status='active',generation=1,created_at=MEMBER)),tx.create(other+('members',B),member(B))),max_attempts=1)
 assert raw.get(other).data['generation']==1

def test_freezing_owner_still_blocks_reactivation():
 raw,store=seed(status='trashed')
 raw.run_transaction(lambda tx:tx.set(('users',A),dict(tx.get(('users',A)).data,status='erasure_pending')))
 unchanged_after_rejection(raw,store,lambda tx:bump(tx,status='active'))


@pytest.mark.parametrize('operation',['transfer','create','reactivate'])
def test_lock_only_active_owner_is_fenced(operation):
 raw,store=seed(status='trashed' if operation=='reactivate' else 'active')
 uid=A if operation=='reactivate' else B
 raw.run_transaction(lambda tx:tx.create(('account_erasure_locks',uid),dict(user_id=uid)))
 def mutate(tx):
  if operation=='reactivate':bump(tx,status='active')
  elif operation=='transfer':
   bump(tx);tx.delete(ROOT+('members',A));tx.create(ROOT+('members',B),member(B))
  else:
   other=('projects','2'*32);tx.create(other,dict(status='active',generation=1,created_at=MEMBER));tx.create(other+('members',B),member(B))
 unchanged_after_rejection(raw,store,mutate)


def test_presence_keeps_batched_fence_without_extra_root_or_owner_read():
 raw,store=seed();original=raw.run_transaction;calls=[]
 class Observed:
  def __init__(self,tx):self.tx=tx
  def __getattr__(self,key):return getattr(self.tx,key)
  def get(self,path):calls.append(('get',tuple(path)));return self.tx.get(path)
  def get_many(self,paths):calls.append(('batch',tuple(paths)));return self.tx.get_many(paths)
  def saved_word_owners(self,*args,**kw):calls.append(('owners',args));return self.tx.saved_word_owners(*args,**kw)
 def run(fn,**kw):return original(lambda tx:fn(Observed(tx)),**kw)
 raw.run_transaction=run
 store.run_transaction(lambda tx:tx.set(ROOT+('members',A),dict(tx.get(ROOT+('members',A)).data,last_opened_at=MEMBER)),max_attempts=1)
 assert [x for x in calls if x[0]=='get']==[('get',ROOT+('members',A))]
 assert not any(x[0]=='owners' for x in calls)
 assert [x for x in calls if x[0]=='batch']==[('batch',(('users',A),ROOT))]

def test_new_owner_lock_inserted_before_commit_conflicts_without_partial_transfer():
 raw,_=seed()
 class CASStore:
  def run_transaction(self,fn,**kw):
   reads={};writes={}
   class Tx:
    def get(self,path):
     row=raw.get(path);reads[tuple(path)]=row.revision if row else 0;return row
    def get_many(self,paths):return [self.get(p) for p in paths]
    def saved_word_owners(self,pid,**kw):
     rows=raw.run_transaction(lambda tx:tx.saved_word_owners(pid,**kw))
     for row in rows:reads[row.path]=row.revision
     return rows
    def set(self,path,data):writes[tuple(path)]=deepcopy(data)
    create=set
    def delete(self,path):writes[tuple(path)]=None
   answer=fn(Tx())
   assert ('account_erasure_locks',B) in reads
   raw.run_transaction(lambda tx:tx.create(('account_erasure_locks',B),dict(user_id=B)))
   def commit(tx):
    for path,revision in reads.items():
     row=tx.get(path)
     if (row.revision if row else 0)!=revision:raise DocumentConflict('SYNTHETIC_CAS_CONFLICT')
    for path,data in writes.items():
     if data is None:tx.delete(path)
     else:tx.set(path,data)
   raw.run_transaction(commit);return answer
 store=AccountLifecycleDocumentStore(CASStore())
 with pytest.raises(DocumentConflict,match='SYNTHETIC_CAS_CONFLICT'):
  store.run_transaction(lambda tx:(bump(tx),tx.delete(ROOT+('members',A)),tx.create(ROOT+('members',B),member(B))),max_attempts=1)
 assert raw.get(ROOT).data['generation']==1 and raw.get(ROOT+('members',A)) is not None
 assert raw.get(ROOT+('members',B)) is None and raw.get(('account_erasure_locks',B)) is not None
