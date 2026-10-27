"""Cold book reads reuse verified accelerators, never authority or stale CAS."""
import json
import pytest
from fastapi import HTTPException
from hosted.firestore.contracts import ProjectUnavailable
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10552 import legacy_project,request
from tests.test_v10554 import imported


def book(http,one,pid):
    tab=one.projects.domain.list('document_tab',{'project_id':pid},user_id=OWNER)[0]['id']
    return http.hosted_book(request(pid,'/api/book',f'tab_id={tab}&manifest=true&bundled=true'))


def test_bundled_book_reads_each_object_once_in_order_without_canonical_writes(monkeypatch):
    import hosted.http_api as http
    one,_,docs,pid,_=imported(monkeypatch,count=600)
    monkeypatch.setattr(http,'runtime',one)
    before={row.path:row.data for row in docs.query_collection(('projects',pid,'blocks'))}
    reads=[];native=one.projects.objects.read_chunks
    def counted(*a,**kw):reads.append(a[0]);return native(*a,**kw)
    monkeypatch.setattr(one.projects.objects,'read_chunks',counted)
    with one.context.bind(pid,user_id=OWNER):result=json.loads(book(http,one,pid).body)
    assert result['bundled'] and len(result['blocks'])==600
    assert [r['text_content'] for r in result['blocks']]==[f'Synthetic paragraph {i}' for i in range(600)]
    assert len(reads)==len(set(reads))<20
    assert result['read_tag'] and str(result['project']['generation'])==result['chapters'][0]['revision']
    assert before=={row.path:row.data for row in docs.query_collection(('projects',pid,'blocks'))}


def test_missing_or_stale_bundles_fall_back_without_reading_canonical_objects(monkeypatch):
    one,_,docs,pid,cid=legacy_project(monkeypatch,600)
    monkeypatch.setattr(one.projects.objects,'read_chunks',lambda *a,**k:pytest.fail('Fallback downloaded a body'))
    assert one.projects.domain.bundled_manuscript(pid,[cid],user_id=OWNER) is None
    def stale(tx):
        for row in tx.query_collection(('projects',pid,'blocks')):
            tx.set(row.path,{**row.data,'_bm_read_bundle':{'source_sha256':'obsolete','bundle_entry':row.path[-1]}})
    docs.run_transaction(stale)
    assert one.projects.domain.bundled_manuscript(pid,[cid],user_id=OWNER) is None
    with pytest.raises(ProjectUnavailable):one.projects.domain.bundled_manuscript(pid,[cid],user_id='usr_'+'f'*32)


def test_bundle_hash_failure_does_not_silently_fall_back_or_return_text(monkeypatch):
    import hosted.http_api as http
    one,_,docs,pid,_=imported(monkeypatch,count=130)
    monkeypatch.setattr(http,'runtime',one)
    row=docs.query_collection(('projects',pid,'blocks'))[0]
    def corrupt(tx):
        data=dict(row.data);field='_bm_read_bundle' if '_bm_read_bundle' in data else 'payload_object'
        data[field]={**data[field],'object_sha256':'f'*64};tx.set(row.path,data)
    docs.run_transaction(corrupt)
    with one.context.bind(pid,user_id=OWNER),pytest.raises(HTTPException):book(http,one,pid)


def test_concurrent_edit_during_bundle_reads_cannot_publish_a_mixed_book(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,_=imported(monkeypatch,count=130)
    monkeypatch.setattr(http,'runtime',one);domain=one.projects.domain;native=domain.bundled_manuscript
    def race(*a,**kw):
        result=native(*a,**kw);one.projects.rename(pid,'Concurrent synthetic edit');return result
    monkeypatch.setattr(domain,'bundled_manuscript',race)
    with one.context.bind(pid,user_id=OWNER),pytest.raises(HTTPException) as error:book(http,one,pid)
    assert error.value.status_code==409


def test_revocation_after_bundle_reads_cannot_publish_authorized_old_bytes(monkeypatch):
    import hosted.http_api as http
    one,_,docs,pid,_=imported(monkeypatch,count=130)
    monkeypatch.setattr(http,'runtime',one);domain=one.projects.domain;native=domain.bundled_manuscript
    def race(*a,**kw):
        result=native(*a,**kw);docs.run_transaction(lambda tx:tx.delete(('projects',pid,'members',OWNER)));return result
    monkeypatch.setattr(domain,'bundled_manuscript',race)
    with one.context.bind(pid,user_id=OWNER),pytest.raises(HTTPException) as error:book(http,one,pid)
    assert error.value.status_code==404


def test_fast_book_enforces_metadata_and_decompression_budgets(monkeypatch):
    from hosted.firestore.memory import _MemoryTransaction
    from hosted.domain_store import _json_gzip,_json_gunzip
    from hosted.firestore.contracts import HostedValidationError
    one,_,_,pid,cid=imported(monkeypatch,count=130)
    monkeypatch.setattr(_MemoryTransaction,'count_collection',lambda *a,**kw:10001)
    monkeypatch.setattr(_MemoryTransaction,'query_collection',lambda *a,**kw:pytest.fail('Enumerated oversized project'))
    assert one.projects.domain.bundled_manuscript(pid,[cid],user_id=OWNER) is None
    with pytest.raises(HostedValidationError,match='BUNDLED_BOOK_LIMIT'):
        _json_gunzip(_json_gzip({'text':'a'*600000}),max_decoded_bytes=512*1024)


def test_encoded_reply_budget_falls_back_instead_of_emitting_an_oversized_book(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,_=imported(monkeypatch,count=2)
    monkeypatch.setattr(http,'runtime',one)
    chapter=one.projects.domain.list('chapter',{'project_id':pid},user_id=OWNER)[0]
    monkeypatch.setattr(one.projects.domain,'bundled_manuscript',lambda *a,**kw:[{
        'id':'synthetic','chapter_id':chapter['id'],'position':0,'text_content':'a'*(8*1024*1024)}])
    with one.context.bind(pid,user_id=OWNER):result=json.loads(book(http,one,pid).body)
    assert result['bundled'] is False and result['blocks']==[]


def test_compressed_input_budget_declines_without_downloading_bundles(monkeypatch):
    one,_,docs,pid,cid=imported(monkeypatch,count=2)
    row=docs.query_collection(('projects',pid,'blocks'))[0]
    def oversized(tx):
        data=dict(row.data);field='_bm_read_bundle' if '_bm_read_bundle' in data else 'payload_object'
        data[field]={**data[field],'object_size':3*1024*1024};tx.set(row.path,data)
    docs.run_transaction(oversized)
    monkeypatch.setattr(one.projects.objects,'read_chunks',lambda *a,**kw:pytest.fail('Oversized bundle was read'))
    assert one.projects.domain.bundled_manuscript(pid,[cid],user_id=OWNER) is None


def test_opt_in_encrypted_store_uses_existing_paged_codec_not_plaintext_acceleration(monkeypatch):
    from tests.test_v10524_private_live_restore import private_store,OWNER as PRIVATE_OWNER
    _,objects,projects,_=private_store(PRIVATE_OWNER)
    pid=projects.create('Synthetic private book')['id']
    monkeypatch.setattr(objects,'read_chunks',lambda *a,**kw:pytest.fail('Private fast path inspected plaintext'))
    assert projects.domain.bundled_manuscript(pid,[],user_id=PRIVATE_OWNER) is None
    with pytest.raises(ProjectUnavailable):projects.domain.bundled_manuscript(pid,[],user_id='usr_'+'f'*32)


def test_fast_book_never_includes_another_document_tabs_blocks(monkeypatch):
    import hosted.http_api as http
    one,_,docs,pid,cid=imported(monkeypatch,count=130)
    monkeypatch.setattr(http,'runtime',one)
    chapter=dict(docs.get(('projects',pid,'chapters',cid)).data)
    row=docs.query_collection(('projects',pid,'blocks'))[0]
    def move(tx):
        other='chapter_synthetic_other_document'
        tx.set(('projects',pid,'chapters',other),{**chapter,'id':other,'chapter_id':other,'tab_id':'tab_other_document'})
        tx.set(row.path,{**row.data,'chapter_id':other})
    docs.run_transaction(move)
    with one.context.bind(pid,user_id=OWNER):result=json.loads(book(http,one,pid).body)
    assert result['bundled'] and len(result['blocks'])==129
    assert all(block['chapter_id']==cid for block in result['blocks'])


def test_busy_instance_uses_paging_instead_of_queueing_more_full_book_memory(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,_=imported(monkeypatch,count=2)
    monkeypatch.setattr(http,'runtime',one)
    monkeypatch.setattr(one.projects.domain,'bundled_manuscript',lambda *a,**kw:pytest.fail('Unbounded full-book worker'))
    http._bundled_book_capacity.acquire();http._bundled_book_capacity.acquire()
    try:
        with one.context.bind(pid,user_id=OWNER):result=json.loads(book(http,one,pid).body)
        assert result['bundled'] is False and result['blocks']==[]
    finally:
        http._bundled_book_capacity.release();http._bundled_book_capacity.release()


def test_verified_fast_book_keeps_real_write_cas_and_cannot_overwrite_a_newer_edit(monkeypatch):
    import hosted.http_api as http
    from hosted.firestore.contracts import DocumentConflict
    from tests.test_v10364 import _replace
    one,_,_,pid,cid=imported(monkeypatch,count=2)
    monkeypatch.setattr(http,'runtime',one)
    with one.context.bind(pid,user_id=OWNER):data=json.loads(book(http,one,pid).body)
    revision=data['project']['generation']
    _replace(one,pid,cid,[{'text_content':'New acknowledged synthetic edit'}],'op_fast_book_save',revision)
    with pytest.raises(DocumentConflict,match='GENERATION_CONFLICT'):
        _replace(one,pid,cid,[{'text_content':'Stale synthetic replacement'}],'op_stale_fast_book_save',revision)
    assert one.projects.domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)[0]['text_content']=='New acknowledged synthetic edit'
