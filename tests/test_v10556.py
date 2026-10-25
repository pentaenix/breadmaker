"""Preference batching and adaptive chapter reads retain actor/revision fences."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from hosted.firestore.contracts import HostedValidationError,ProjectUnavailable
from hosted.firestore.memory import _MemoryTransaction
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10554 import imported
from tests.test_v10552 import legacy_project,request


def test_preferences_batch_has_one_transaction_and_one_ordered_batch_get(monkeypatch):
    one,_,docs,pid,_=imported(monkeypatch,count=2)
    for key in ('tabs-expanded','canvas-active'):one.projects.put_member_state(pid,{'value':key},key=key)
    native=docs.run_transaction;transactions=[];batches=[];getter=_MemoryTransaction.get_many
    def transaction(*args,**kwargs):transactions.append(True);return native(*args,**kwargs)
    def batch(tx,paths):batches.append(paths);return getter(tx,paths)
    monkeypatch.setattr(docs,'run_transaction',transaction);monkeypatch.setattr(_MemoryTransaction,'get_many',batch)
    assert one.projects.member_states(pid,['tabs-expanded','missing','canvas-active','missing'])=={
        'tabs-expanded':'tabs-expanded','missing':None,'canvas-active':'canvas-active'}
    assert len(transactions)==len(batches)==1 and len(batches[0])==3
    assert all(path[:4]==('projects',pid,'members',OWNER) for path in batches[0])


def test_preferences_batch_rejects_foreign_actor_and_oversized_request(monkeypatch):
    one,_,_,pid,_=imported(monkeypatch,count=2)
    one.projects.put_member_state(pid,{'value':'owner private preference'},key='canvas-active')
    with pytest.raises(ProjectUnavailable):one.projects.member_states(pid,['canvas-active'],user_id='usr_'+'f'*32)
    with pytest.raises(HostedValidationError):one.projects.member_states(pid,[f'key-{i}' for i in range(65)])
    with pytest.raises(HostedValidationError):one.projects.member_states(pid,['../another-user'])
    assert one.projects.member_states(pid,[])=={}


def test_private_preference_batch_preserves_encryption_user_binding_and_tamper_rejection():
    from test_v10524_private_live_restore import private_store,OWNER as PRIVATE_OWNER,DEST
    from hosted.firestore.contracts import HostedAuthorizationError
    docs,_,source,_=private_store(PRIVATE_OWNER);pid=source.create('Synthetic private preferences')['id']
    source.put_member_state(pid,{'value':'private one'},key='one')
    source.put_member_state(pid,{'value':'private two'},key='two')
    assert source.member_states(pid,['one','missing'])=={'one':'private one','missing':None}
    with pytest.raises(HostedAuthorizationError):source.member_states(pid,['one'],user_id=DEST)
    a=('projects',pid,'members',PRIVATE_OWNER,'ui_state','one');b=a[:-1]+('two',)
    tampered=dict(docs.get(b).data);tampered['_bm_private_fields']=docs.get(a).data['_bm_private_fields']
    docs.run_transaction(lambda tx:tx.set(b,tampered))
    with pytest.raises(HostedValidationError,match='DECRYPT_FAILED'):source.member_states(pid,['two'])


def test_adaptive_read_returns_complete_bundled_184_block_chapter_in_one_request(monkeypatch):
    import hosted.http_api as http
    one,_,docs,pid,cid=imported(monkeypatch,count=184);monkeypatch.setattr(http,'runtime',one)
    before=deepcopy(docs.get(('projects',pid)).data)
    result=http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,'block_offset=0&adaptive=true'))
    assert len(result['blocks'])==184 and result['next_block_offset'] is None and result['block_count']==184
    assert result['blocks'][0]['text_content']=='Synthetic paragraph 0'
    assert result['blocks'][-1]['text_content']=='Synthetic paragraph 183'
    assert docs.get(('projects',pid)).data==before
    assert http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,'block_offset=0&adaptive=true',result['read_tag'])).status_code==304


def test_adaptive_read_keeps_512_record_ceiling_and_exact_continuation(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,cid=imported(monkeypatch,count=600);monkeypatch.setattr(http,'runtime',one)
    first=http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,'block_offset=0&adaptive=true'))
    last=http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,'block_offset=512&adaptive=true'))
    assert len(first['blocks'])==512 and first['next_block_offset']==512 and first['block_count']==600
    assert len(last['blocks'])==88 and last['next_block_offset'] is None
    assert len({b['id'] for b in first['blocks']+last['blocks']})==600


def test_adaptive_unbundled_chapter_still_uses_128_rows_and_no_partial_rebase(monkeypatch):
    import hosted.http_api as http
    one,_,docs,pid,cid=legacy_project(monkeypatch,count=300);monkeypatch.setattr(http,'runtime',one)
    docs.run_transaction(lambda tx:[tx.set(row.path,{**row.data,'chapter_id':cid,'position':i})
        for i,row in enumerate(tx.query_collection(('projects',pid,'blocks')))])
    result=http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,'block_offset=0&adaptive=true'))
    assert len(result['blocks'])==128 and result['next_block_offset']==128
    native=one.projects.domain.list
    def race(*args,**kwargs):
        result=native(*args,**kwargs);one.projects.rename(pid,'Concurrent synthetic revision');return result
    monkeypatch.setattr(one.projects.domain,'list',race)
    with pytest.raises(HTTPException) as changed:http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,'block_offset=0&adaptive=true'))
    assert changed.value.status_code==409
