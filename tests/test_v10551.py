"""Book-sized, interrupted and unauthorized imports use durable bounded steps."""
import asyncio
import threading
from io import BytesIO

import pytest
from starlette.datastructures import UploadFile

from hosted.document_import import DocumentImport,BATCH_SIZE
from hosted.firestore.contracts import DocumentConflict,DocumentNotFound,ProjectUnavailable,HostedValidationError
from hosted.runtime_services import _DEV_USER_ID as OWNER
from hosted.chapter_mutations import replace_chapter_blocks,MAX_REPLACEMENT_BLOCKS
from tests.test_v10364 import _replicas


def begin(service,paragraphs=130,operation='op_synthetic_import'):
    raw='\n'.join(f'Synthetic paragraph {i}' for i in range(paragraphs)).encode()
    return service.begin(raw,'Synthetic.txt','heading1','Synthetic import',OWNER,operation)


def test_book_sized_import_batches_metadata_and_publishes_only_at_finish(monkeypatch):
    one,make,docs,original,_=_replicas(monkeypatch)
    service=DocumentImport(one.projects)
    progress=begin(service,2000)
    job=progress['job_id'];data=service._load(OWNER,job);pid=data['project_id']
    assert progress['total_batches']==32
    with pytest.raises(ProjectUnavailable):make().projects.get(pid)
    with pytest.raises(DocumentConflict,match='NOT_FINISHED'):service.finish(OWNER,job)
    calls=[];native=docs.run_transaction
    def counted(fn,**kwargs):
        calls.append(fn.__name__)
        return native(fn,**kwargs)
    monkeypatch.setattr(docs,'run_transaction',counted)
    while not progress['ready']:
        # A new service/runtime can resume every batch; no in-process job state.
        progress=DocumentImport(make().projects).step(OWNER,job,progress['next_batch'])
        assert docs.get(('projects',pid)) is None
    assert calls.count('stage')==32 # not one metadata transaction per paragraph
    result=service.finish(OWNER,job)
    assert result['project']['id']==pid and result['chapters']==1
    assert service.finish(OWNER,job)['project']['id']==pid
    reads=[];native_read=one.projects.domain.objects.read_chunks
    def read(name,**kwargs):
        reads.append(name)
        return native_read(name,**kwargs)
    monkeypatch.setattr(one.projects.domain.objects,'read_chunks',read)
    rows=make().projects.domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)
    assert len(rows)==2000 and rows[0]['text_content']=='Synthetic paragraph 0'
    assert rows[-1]['text_content']=='Synthetic paragraph 1999'
    assert len(reads)==32 # one verified object per batch, not per paragraph
    cid=rows[0]['chapter_id'];generation=one.projects.get(pid)['generation']
    edited=[{'id':row['id'],'text_content':row['text_content'],'content_html':row['content_html']}
        for row in rows]
    edited[-1].update(text_content='Edited last paragraph',content_html='Edited last paragraph')
    publishes=[];native_publish=one.projects.domain._publish_hybrid
    def publish(*args,**kwargs):
        publishes.append(args[0]);return native_publish(*args,**kwargs)
    monkeypatch.setattr(one.projects.domain,'_publish_hybrid',publish)
    saved=replace_chapter_blocks(one.projects.domain,project_id=pid,chapter_id=cid,user_id=OWNER,
        blocks=edited,expected_revision=generation,operation='op_large_single_edit')
    assert saved['blocks'][-1]['text_content']=='Edited last paragraph'
    assert len(saved['blocks'])==2000 and publishes==['manuscript_block']
    replay=replace_chapter_blocks(make().projects.domain,project_id=pid,chapter_id=cid,user_id=OWNER,
        blocks=edited,expected_revision=generation,operation='op_large_single_edit')
    assert replay['revision']==saved['revision'] and replay['ids']==saved['ids']
    rebuilt=make().projects.domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)
    assert rebuilt[-1]['text_content']=='Edited last paragraph'
    # The original 150-record WRITE cap remains. Input size is not a license
    # to perform unbounded Firestore writes or bypass the action-journal budget.
    for row in edited[:MAX_REPLACEMENT_BLOCKS+1]:row['text_content']='Too many writes'
    with pytest.raises(HostedValidationError,match='TOO_LARGE'):
        replace_chapter_blocks(one.projects.domain,project_id=pid,chapter_id=cid,user_id=OWNER,
            blocks=edited,expected_revision=saved['revision'])
    assert publishes==['manuscript_block']
    assert make().projects.get(original)['generation']==one.projects.get(original)['generation']


def test_failure_and_retry_preserve_original_and_never_expose_partial_project(monkeypatch):
    one,make,docs,original,_=_replicas(monkeypatch)
    service=DocumentImport(one.projects);progress=begin(service)
    job=progress['job_id'];pid=service._load(OWNER,job)['project_id']
    before=one.projects.get(original)['generation']
    native=service.domain._publish_hybrid
    def fail(*args,**kwargs):raise RuntimeError('SYNTHETIC_STORAGE_FAILURE')
    monkeypatch.setattr(service.domain,'_publish_hybrid',fail)
    with pytest.raises(RuntimeError,match='SYNTHETIC_STORAGE_FAILURE'):service.step(OWNER,job,0)
    assert service.status(OWNER,job)['next_batch']==0
    assert docs.get(('projects',pid)) is None
    assert make().projects.get(original)['generation']==before
    monkeypatch.setattr(service.domain,'_publish_hybrid',native)
    first=service.step(OWNER,job,0)
    assert service.step(OWNER,job,0)==first
    assert begin(service)['job_id']==job
    with pytest.raises(DocumentConflict,match='REQUEST_CHANGED'):begin(service,131)
    with pytest.raises(DocumentConflict,match='OUT_OF_ORDER'):service.step(OWNER,job,2)
    foreign='usr_'+'f'*32
    for action in (lambda:service.status(foreign,job),lambda:service.step(foreign,job,1),lambda:service.finish(foreign,job)):
        with pytest.raises(DocumentNotFound):action()
    for index in (True,-1,BATCH_SIZE):
        with pytest.raises(HostedValidationError):service.step(OWNER,job,index)


def test_interrupted_metadata_staging_remains_unpublished_and_retries_on_new_replica(monkeypatch):
    one,make,docs,_,_=_replicas(monkeypatch)
    service=DocumentImport(one.projects);progress=begin(service,65)
    job=progress['job_id'];pid=service._load(OWNER,job)['project_id']
    native=docs.run_transaction
    def interrupt(fn,**kwargs):
        def fail(tx):
            value=fn(tx)
            if fn.__name__=='stage':raise RuntimeError('SYNTHETIC_COMMIT_FAILURE')
            return value
        return native(fail,**kwargs)
    monkeypatch.setattr(docs,'run_transaction',interrupt)
    with pytest.raises(RuntimeError,match='COMMIT_FAILURE'):service.step(OWNER,job,0)
    # MemoryDocumentStore deliberately does not roll back writes on callback
    # exceptions. Even that stronger partial-stage fault must remain invisible.
    assert docs.get(('projects',pid)) is None
    assert service.status(OWNER,job)['next_batch']==0
    with pytest.raises(ProjectUnavailable):make().projects.get(pid)
    monkeypatch.setattr(docs,'run_transaction',native)
    assert DocumentImport(make().projects).step(OWNER,job,0)['next_batch']==1


def test_upload_parsing_and_storage_do_not_block_the_async_request_loop(monkeypatch):
    import hosted.http_compat as http
    one,_,_,_,_=_replicas(monkeypatch)
    monkeypatch.setattr(http,'runtime',one)
    started=threading.Event();release=threading.Event()
    def slow(self,*args,**kwargs):
        started.set()
        if not release.wait(2):raise RuntimeError('EVENT_LOOP_WAS_BLOCKED')
        return {'job_id':'import_synthetic'}
    monkeypatch.setattr(DocumentImport,'begin',slow)
    async def check():
        task=asyncio.create_task(http.hosted_import_new_project(None,
            UploadFile(BytesIO(b'Synthetic'),filename='book.txt'),resumable=True))
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.005)
            assert started.is_set()
            release.set()
            assert (await task)['job_id']=='import_synthetic'
        finally:release.set()
    asyncio.run(check())


def test_bundle_entry_integrity_authorization_and_portable_roundtrip(monkeypatch):
    from copy import deepcopy
    from hosted.packages import HostedProjectPackageService
    one,make,docs,_,_=_replicas(monkeypatch)
    service=DocumentImport(one.projects);progress=begin(service,65)
    while not progress['ready']:
        progress=service.step(OWNER,progress['job_id'],progress['next_batch'])
    pid=service.finish(OWNER,progress['job_id'])['project']['id']
    metadata=dict(docs.query_collection(('projects',pid,'blocks'))[0].data)
    bad=deepcopy(metadata);bad['payload_object']['bundle_entry']='b_wrong'
    with pytest.raises(HostedValidationError,match='BUNDLE_ID_INVALID'):
        one.projects.domain._hydrate('manuscript_block',bad)
    bad=deepcopy(metadata);bad['payload_object']['object_sha256']='0'*64
    with pytest.raises(RuntimeError,match='HASH_MISMATCH'):
        one.projects.domain._hydrate('manuscript_block',bad)
    reads=[];native=one.projects.domain.objects.read_chunks
    def read(*args,**kwargs):reads.append(True);return native(*args,**kwargs)
    monkeypatch.setattr(one.projects.domain.objects,'read_chunks',read)
    with pytest.raises(ProjectUnavailable):
        one.projects.domain.list('manuscript_block',{'project_id':pid},user_id='usr_'+'f'*32)
    assert not reads
    # Package export must preserve every paragraph, not serialize bundle metadata
    # as text or lose the immutable body during recovery/portability.
    package=HostedProjectPackageService(one.projects).export_local_bytes(pid,OWNER)
    restored=HostedProjectPackageService(make().projects).import_bytes(package,OWNER)
    rows=make().projects.domain.list('manuscript_block',{'project_id':restored['id']},user_id=OWNER)
    assert len(rows)==65 and rows[-1]['text_content']=='Synthetic paragraph 64'


def test_google_batch_get_uses_one_transaction_and_preserves_missing_and_order():
    from types import SimpleNamespace
    from hosted.firestore.google_driver import _GoogleTransaction
    paths=[('projects','a'),('projects','b'),('projects','c')]
    transaction=object();calls=[]
    class Client:
        def document(self,path):return SimpleNamespace(path=path)
        def get_all(self,refs,**kwargs):
            calls.append((refs,kwargs))
            return [SimpleNamespace(reference=refs[2],exists=True,to_dict=lambda:{'order':3}),
                    SimpleNamespace(reference=refs[0],exists=True,to_dict=lambda:{'order':1}),
                    SimpleNamespace(reference=refs[1],exists=False)]
    result=_GoogleTransaction(Client(),transaction).get_many(paths)
    assert len(calls)==1 and calls[0][1]['transaction'] is transaction
    assert result[0].path==paths[0] and result[1] is None and result[2].path==paths[2]
