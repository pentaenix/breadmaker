"""Bound provider consumption before materializing mixed manuscript content."""
from types import SimpleNamespace
import pytest
from hosted.firestore.google_driver import _GoogleTransaction
from hosted.firestore.memory import MemoryDocumentStore
from hosted.account_lifecycle import _LifecycleTransaction
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10554 import imported


def test_google_stream_stops_and_closes_at_byte_budget():
    consumed=[];closed=[];transaction=object()
    def stream(**kwargs):
        assert kwargs == {'transaction': transaction}
        try:
            for index in range(10000):
                consumed.append(index)
                yield SimpleNamespace(reference=SimpleNamespace(path=f'projects/p/blocks/b{index}'),
                    to_dict=lambda:{'text_content':'x'*1000})
        finally:
            closed.append(True)
    query=SimpleNamespace(stream=stream)
    def limit(value):
        assert value == 101
        return query
    client=SimpleNamespace(collection=lambda path:SimpleNamespace(limit=limit))
    assert _GoogleTransaction(client,transaction).query_collection_bounded(
        ('projects','p','blocks'),max_records=100,max_bytes=2200) is None
    assert len(consumed)==3 and closed==[True]


def test_memory_limits_and_lifecycle_do_not_return_partial_or_pending_snapshot():
    docs=MemoryDocumentStore();path=('projects','p','blocks')
    docs.run_transaction(lambda tx:[tx.create(path+(f'b{i}',),{'text':'hello'}) for i in range(3)])
    def inspect(tx):
        assert tx.query_collection_bounded(path,max_records=2,max_bytes=1000) is None
        assert tx.query_collection_bounded(path,max_records=3,max_bytes=1) is None
        assert len(tx.query_collection_bounded(path,max_records=3,max_bytes=1000))==3
        wrapped=_LifecycleTransaction(tx)
        assert len(wrapped.query_collection_bounded(path,max_records=3,max_bytes=1000))==3
        wrapped.pending[path+('b4',)]={'text':'pending'}
        assert wrapped.query_collection_bounded(path,max_records=3,max_bytes=1000) is None
    docs.run_transaction(inspect)
    assert _LifecycleTransaction(object()).query_collection_bounded(path,max_records=3,max_bytes=1000) is None


def test_mixed_inline_and_bundles_preserve_exact_text_without_canonical_reads(monkeypatch):
    one,_,docs,pid,cid=imported(monkeypatch,count=130)
    domain=one.projects.domain
    expected=domain.bundled_manuscript(pid,[cid],user_id=OWNER)
    chosen=docs.query_collection(('projects',pid,'blocks'))[0]
    body=next(row for row in expected if row['block_id']==chosen.data['block_id'])
    inline={k:v for k,v in chosen.data.items() if k not in ('payload_object','_bm_read_bundle')}
    inline.update(content_html=body['content_html'],text_content=body['text_content'])
    docs.run_transaction(lambda tx:tx.set(chosen.path,inline))
    reads=[];native=domain.objects.read_chunks
    def read(name,*args,**kwargs):
        reads.append(name)
        assert name != chosen.data['payload_object']['object_name']
        return native(name,*args,**kwargs)
    monkeypatch.setattr(domain.objects,'read_chunks',read)
    actual=domain.bundled_manuscript(pid,[cid],user_id=OWNER)
    shape=lambda rows:sorted((r['block_id'],r['content_html'],r['text_content']) for r in rows)
    assert shape(actual)==shape(expected)
    assert len(reads)==len(set(reads))


def test_inline_byte_budget_falls_back_before_object_reads(monkeypatch):
    one,_,docs,pid,cid=imported(monkeypatch,count=130)
    rows=docs.query_collection(('projects',pid,'blocks'))
    def write(tx):
        for row in rows:
            value={k:v for k,v in row.data.items() if k not in ('payload_object','_bm_read_bundle')}
            value.update(content_html='x'*70000,text_content='x'*70000)
            tx.set(row.path,value)
    docs.run_transaction(write)
    monkeypatch.setattr(one.projects.domain.objects,'read_chunks',lambda *a,**k:pytest.fail('Read object above budget'))
    assert one.projects.domain.bundled_manuscript(pid,[cid],user_id=OWNER) is None


def test_all_inline_returns_content_without_using_stale_accelerator(monkeypatch):
    one,_,docs,pid,cid=imported(monkeypatch,count=2)
    def write(tx):
        for row in tx.query_collection(('projects',pid,'blocks')):
            value={k:v for k,v in row.data.items() if k != 'payload_object'}
            value.update(content_html='<p>Current</p>',text_content='Current')
            # _bm_read_bundle deliberately remains, but is not authoritative.
            tx.set(row.path,value)
    docs.run_transaction(write)
    monkeypatch.setattr(one.projects.domain.objects,'read_chunks',lambda *a,**k:pytest.fail('Inline read object'))
    result=one.projects.domain.bundled_manuscript(pid,[cid],user_id=OWNER)
    assert len(result)==2 and all(r['text_content']=='Current' for r in result)


def test_missing_bounded_capability_never_uses_unbounded_query(monkeypatch):
    from hosted.firestore.memory import _MemoryTransaction
    one,_,_,pid,cid=imported(monkeypatch,count=2)
    monkeypatch.setattr(_MemoryTransaction,'query_collection_bounded',None)
    monkeypatch.setattr(_MemoryTransaction,'query_collection',lambda *a,**k:pytest.fail('Unbounded query'))
    assert one.projects.domain.bundled_manuscript(pid,[cid],user_id=OWNER) is None


def test_flat_legacy_pointer_uses_existing_paged_fallback(monkeypatch):
    one,_,docs,pid,cid=imported(monkeypatch,count=2)
    row=docs.query_collection(('projects',pid,'blocks'))[0]
    value={k:v for k,v in row.data.items() if k not in ('payload_object','_bm_read_bundle')}
    value.update(row.data['payload_object'])
    docs.run_transaction(lambda tx:tx.set(row.path,value))
    monkeypatch.setattr(one.projects.domain.objects,'read_chunks',lambda *a,**k:pytest.fail('Legacy object read'))
    assert one.projects.domain.bundled_manuscript(pid,[cid],user_id=OWNER) is None


def test_bounded_snapshot_accounts_for_timestamp_and_binary_property_types():
    from datetime import datetime, timezone
    import json
    from hosted.firestore.contracts import StoredDocument, bounded_document_rows
    values={'updated_at':datetime(2026,11,10,tzinfo=timezone.utc),
            'binary':b'\x00\xff' * 20,'nested':{'enabled':True,'fraction':1.5,'empty':None}}
    row=StoredDocument(('projects','p','blocks','b'),values,1)
    size=len(json.dumps(dict(row.data),ensure_ascii=False,default=str).encode('utf-8'))
    assert bounded_document_rows(iter([row]),max_records=1,max_bytes=size) == [row]
    assert bounded_document_rows(iter([row]),max_records=1,max_bytes=size-1) is None
