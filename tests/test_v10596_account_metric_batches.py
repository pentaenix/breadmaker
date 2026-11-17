from datetime import datetime,timezone
import time
import pytest
from google.cloud.firestore_v1.types import BatchGetDocumentsResponse
from types import SimpleNamespace
from hosted.security.account_metric_eligibility import GoogleFirestoreAccountEligibility
from hosted.security.saved_word_reader import SavedWordsUnavailable
from tests.test_v10596_account_word_transport import sdk,UID,BORN


def test_same_transaction_batch_proves_absence_and_accepts_unordered_results(sdk):
    store,api,raw,_=sdk;other='usr_'+'c'*32
    raw.run_transaction(lambda tx:tx.create(('users',other),dict(user_id=other,status='active',created_at=BORN)))
    tx=store.client.transaction(max_attempts=1);tx._id=b'synthetic-word-transaction'
    original=api.batch_get_documents
    def unordered(request,**kw):return iter(reversed(list(original(request,**kw))))
    api.batch_get_documents=unordered
    result=GoogleFirestoreAccountEligibility(store.client,transaction=tx,enabled=True).snapshot(deadline=time.monotonic()+20)
    assert result['locks']=={UID:None,other:None}
    batches=[r for name,r in api.calls if name=='get']
    assert len(batches)==1 and len(batches[0]['documents'])==2 and batches[0]['transaction']==tx.id


@pytest.mark.parametrize('fault',['omitted','duplicate','foreign','stream_failure'])
def test_bad_batch_never_publishes_a_partial_eligibility_view(sdk,fault):
    store,api,raw,_=sdk;original=api.batch_get_documents
    def broken(request,**kw):
        rows=list(original(request,**kw))
        if fault=='omitted':return iter([])
        if fault=='duplicate':return iter(rows+rows)
        if fault=='foreign':return iter([BatchGetDocumentsResponse(missing='projects/breadmaker-test-123/databases/(default)/documents/users/foreign')])
        def failed():
            yield from rows
            raise TimeoutError('Synthetic tail failure')
        return failed()
    api.batch_get_documents=broken
    tx=store.client.transaction(max_attempts=1);tx._id=b'synthetic-word-transaction'
    with pytest.raises((SavedWordsUnavailable,TimeoutError,ValueError)):
        GoogleFirestoreAccountEligibility(store.client,transaction=tx,enabled=True).snapshot(deadline=time.monotonic()+20)


def test_batch_scope_is_finite_unique_and_checked_before_io(sdk):
    store,api,_,_=sdk;reader=GoogleFirestoreAccountEligibility(store.client,enabled=True)
    for paths in ([('users',UID)]*2,[('users','usr_'+format(i,'032x')) for i in range(101)]):
        with pytest.raises(SavedWordsUnavailable):reader._many(paths,['user_id'],time.monotonic()+20)
    assert not api.calls


def test_projected_batch_rejects_excess_bytes_and_unrequested_private_fields():
    paths=[('users','usr_'+format(i,'032x')) for i in range(9)]
    class Client:
        def document(self,path):return SimpleNamespace(path=path)
        def get_all(self,refs,**kw):
            for ref in refs:
                yield SimpleNamespace(reference=ref,exists=True,to_dict=lambda:{'user_id':'x'*(1024*1024)})
    reader=GoogleFirestoreAccountEligibility(Client(),enabled=True)
    with pytest.raises(SavedWordsUnavailable,match='BATCH_BYTES'):
        reader._many(paths,['user_id'],time.monotonic()+20)
    reader.client.get_all=lambda refs,**kw:iter([SimpleNamespace(reference=refs[0],exists=True,
        to_dict=lambda:{'user_id':UID,'email':'synthetic-unrequested'})])
    with pytest.raises(SavedWordsUnavailable,match='BATCH_UNVERIFIED'):
        reader._many([('users',UID)],['user_id'],time.monotonic()+20)
