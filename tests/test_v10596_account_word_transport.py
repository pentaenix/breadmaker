"""Real pinned SDK framing over a synthetic CAS backend; no credentials/network."""
from copy import deepcopy
from types import SimpleNamespace
import time
import socket
import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1 import Client, _helpers
from google.cloud.firestore_v1.types import (Document, BatchGetDocumentsResponse, RunQueryResponse)
from hosted.firestore.memory import MemoryDocumentStore
from hosted.security.account_word_backfill import AccountWordBackfill
from hosted.security.account_word_backfill_store import BoundedAccountWordStore, BackfillUnavailable, BackfillOutcomeUnknown
from hosted.security import account_word_state as records

UID='usr_'+'a'*32; PID='b'*32; BORN='2026-11-16T00:00:00Z'
DB='projects/breadmaker-test-123/databases/(default)'


@pytest.fixture
def sdk(monkeypatch):
    def denied(*a,**kw):raise AssertionError('Synthetic transport attempted network')
    monkeypatch.setattr(socket,'create_connection',denied)
    monkeypatch.setattr(socket.socket,'connect',denied)
    raw=MemoryDocumentStore()
    def seed(tx):
        tx.create(('users',UID),dict(user_id=UID,status='active',created_at=BORN))
        tx.create(('projects',PID),dict(status='active',generation=1,created_at=BORN))
        tx.create(('projects',PID,'members',UID),dict(user_id=UID,role='owner',created_at=BORN))
        tx.create(('projects',PID,'blocks','b_one'),dict(generation=1))
    raw.run_transaction(seed)
    class API:
        def __init__(self):self.calls=[];self.reads={};self.uncertain=False;self.before_commit=None
        def check(self,name,request,kw):
            assert kw['retry'] is None and 0<kw['timeout']<=2
            self.calls.append((name,deepcopy(request)))
        def begin_transaction(self,request,**kw):
            self.check('begin',request,kw);self.reads={}
            assert request['database']==DB
            return SimpleNamespace(transaction=b'synthetic-word-transaction')
        def document(self,row,fields=None):
            data={k:v for k,v in row.data.items() if fields is None or k in fields}
            return Document(name=DB+'/documents/'+'/'.join(row.path),fields=_helpers.encode_dict(data),
                            update_time={'seconds':row.revision})
        def batch_get_documents(self,request,**kw):
            self.check('get',request,kw)
            assert request['transaction']==b'synthetic-word-transaction'
            fields=list(request['mask'].field_paths) if request.get('mask') else None
            answers=[]
            for name in request['documents']:
                path=tuple(name.split('/documents/',1)[1].split('/'));row=raw.get(path)
                self.reads[path]=row.revision if row else 0
                answers.append(BatchGetDocumentsResponse(found=self.document(row,fields)) if row else
                               BatchGetDocumentsResponse(missing=name))
            return iter(answers)
        def run_query(self,request,**kw):
            self.check('query',request,kw)
            assert request['transaction']==b'synthetic-word-transaction'
            q=request['structured_query'];source=q.from_[0]
            parent=request['parent'].split('/documents',1)[1].strip('/')
            if source.all_descendants:
                assert source.collection_id=='members' and q.where.field_filter.field.field_path=='user_id'
                rows=raw.query_collection_group('members',field='user_id',value=q.where.field_filter.value.string_value)
            else:
                prefix=tuple((parent+'/'+source.collection_id).strip('/').split('/'))
                rows=raw.query_collection(prefix)
                if q.where:
                    f=q.where.field_filter
                    rows=[r for r in rows if r.data.get(f.field.field_path)==f.value.string_value]
            rows=sorted(rows,key=lambda r:r.path)
            if q.start_at.values:
                after=q.start_at.values[0].reference_value.split('/documents/',1)[1]
                rows=[r for r in rows if '/'.join(r.path)>after]
            fields=[f.field_path for f in q.select.fields]
            assert not {'text_content','content_html','title','email'} & set(fields)
            rows=rows[:q.limit]
            for row in rows:self.reads[row.path]=row.revision
            return iter([RunQueryResponse(document=self.document(r,fields)) for r in rows])
        def commit(self,request,**kw):
            self.check('commit',request,kw)
            if self.before_commit:
                callback,self.before_commit=self.before_commit,None;callback(raw)
            def apply(tx):
                for path,revision in self.reads.items():
                    current=tx.get(path)
                    if (current.revision if current else 0)!=revision:raise RuntimeError('synthetic CAS conflict')
                for write in request['writes']:
                    path=tuple(write.update.name.split('/documents/',1)[1].split('/'))
                    assert path[:2]==('users',UID) and path[2] in {'metrics','metric_jobs','word_block_counts'}
                    tx.set(path,_helpers.decode_dict(write.update.fields,client))
            raw.run_transaction(apply)
            if self.uncertain:self.uncertain=False;raise TimeoutError('synthetic lost commit response')
            return SimpleNamespace()
        def rollback(self,request,**kw):self.check('rollback',request,kw)
    client=Client(project='breadmaker-test-123',credentials=AnonymousCredentials())
    api=API();client._firestore_api_internal=api
    store=BoundedAccountWordStore(client,project_id='breadmaker-test-123',owner_user_id=UID)
    def reader(**kw):
        assert kw['user_id']==UID and kw['project_id']==PID and kw['expected_generation']==1
        return dict(generation=1,text_content='two synthetic words')
    return store,api,raw,AccountWordBackfill(store,reader,enabled=True)


def until():return time.monotonic()+20


def test_real_sdk_uses_explicit_no_retry_projections_and_scoped_atomic_writes(sdk):
    store,api,raw,w=sdk;before=deepcopy(raw._documents)
    p=w.inspect_account(UID,deadline=until());h=w.initialize_account(p,approved=True,deadline=until())['jobs'][0]
    result=w.step(h,expected_cursor=None,approved=True,deadline=until())
    assert result['phase']=='complete' and raw.get(records.path(UID)).data['saved_words']==3
    for path,data in before.items():assert raw._documents[path]==data
    assert len([1 for name,_ in api.calls if name=='begin'])==4


def test_uncertain_commit_is_not_retried_and_cursor_inspection_resolves_it(sdk):
    store,api,raw,w=sdk
    h=w.initialize_account(w.inspect_account(UID,deadline=until()),approved=True,deadline=until())['jobs'][0]
    # The preparation is a read-only empty commit. Lose only the publication.
    original=api.commit
    def lose_write(request,**kw):
        if request['writes']:api.uncertain=True
        return original(request,**kw)
    api.commit=lose_write
    with pytest.raises(BackfillOutcomeUnknown):w.step(h,expected_cursor=None,approved=True,deadline=until())
    assert raw.get(records.path(UID)).data['saved_words']==3
    api.commit=original
    assert w.inspect_job(h,deadline=until())['phase']=='complete'
    before=deepcopy(raw._documents)
    assert w.step(h,expected_cursor=None,approved=True,deadline=until())['skip']
    assert raw._documents==before


@pytest.mark.parametrize('operation',['canonical_write','foreign_read','delete'])
def test_metadata_transport_cannot_change_content_or_cross_account_scope(sdk,operation):
    store,api,raw,_=sdk;before=deepcopy(raw._documents)
    def attempt(tx):
        if operation=='canonical_write':tx.set(('projects',PID,'blocks','b_one'),{'text_content':'forbidden'})
        elif operation=='foreign_read':tx.get(('users','usr_'+'f'*32))
        else:tx.delete(('users',UID))
    with pytest.raises(BackfillUnavailable):store.run_transaction(attempt,deadline=until())
    assert raw._documents==before
    assert all(name!='commit' for name,_ in api.calls)
