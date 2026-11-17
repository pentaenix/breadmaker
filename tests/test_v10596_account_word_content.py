"""Production reader with pinned SDK framing, synthetic objects, no sockets."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import pytest
from hosted.domain_store import _json_gzip
from hosted.security.account_word_backfill import AccountWordBackfill
from hosted.security.account_word_backfill_store import BackfillUnavailable
from hosted.security.account_word_content import BoundedCurrentBlockReader
from hosted.storage.google_driver import GoogleCloudObjectStore
from hosted.storage.keys import block_payload_key
from tests.test_v10596_account_word_transport import sdk, UID, PID, until


@pytest.fixture
def content(sdk):
    store, api, raw, _ = sdk
    encoded = _json_gzip(dict(text_content='three saved words', content_html='<p>three saved words</p>'))
    digest = hashlib.sha256(encoded).hexdigest()
    key = block_payload_key(PID, 'b_one', digest)
    pointer = dict(object_name=key, object_generation=7, object_size=len(encoded), object_sha256=digest)
    raw.run_transaction(lambda tx: tx.set(('projects', PID, 'blocks', 'b_one'),
        dict(generation=1, block_id='b_one', payload_object=pointer,
             _bm_read_bundle={'object_name': 'never/read/cache'}, content_html='not projected')))
    class Blob:
        size = len(encoded); generation = 7
        def __init__(self): self.calls=[]; self.value=encoded
        def reload(self, **kw):
            assert kw['retry'] is None and 0 < kw['timeout'] <= 2 and kw['if_generation_match']==7
            self.calls.append(('reload', kw))
        def download_as_bytes(self, **kw):
            assert kw['retry'] is None and 0 < kw['timeout'] <= 4 and kw['if_generation_match']==7
            assert kw['start']==0 and kw['end']==len(encoded)
            assert kw['raw_download'] and kw['single_shot_download'] and kw['checksum'] is None
            self.calls.append(('download', kw)); return self.value
    blob = Blob(); requested=[]
    def get_blob(name): requested.append(name); assert name==key; return blob
    objects = GoogleCloudObjectStore(bucket='synthetic-bucket',
        client=SimpleNamespace(bucket=lambda name: SimpleNamespace(blob=get_blob)))
    reader = BoundedCurrentBlockReader(store, objects)
    args = dict(user_id=UID, project_id=PID, block_id='b_one', expected_generation=1,
        deadline=until(), max_text_chars=1_000_000, max_text_bytes=4_000_000)
    return reader, args, raw, api, blob, requested, pointer


def test_bounded_current_content_reader_drives_complete_account_backfill(content):
    reader, args, raw, api, blob, requested, _ = content
    before=deepcopy(raw._documents)
    worker=AccountWordBackfill(reader.store, reader, enabled=True)
    plan=worker.inspect_account(UID,deadline=until())
    h=worker.initialize_account(plan,approved=True,deadline=until())['jobs'][0]
    assert worker.step(h,expected_cursor=None,approved=True,deadline=until())['phase']=='complete'
    assert raw.get(('users',UID,'metrics','saved_words_v1')).data['saved_words']==3
    assert len(requested)==1 and [c[0] for c in blob.calls]==['reload','download']
    for path,row in before.items():assert raw._documents[path]==row
    masks=[list(r['mask'].field_paths) for name,r in api.calls if name=='get' and r.get('mask')]
    assert all('text_content' not in fields for fields in masks)
    assert all('content_html' not in fields and 'email' not in fields and '_bm_read_bundle' not in fields for fields in masks)


@pytest.mark.parametrize('drift',['freeze','foreign_owner','generation','foreign_account'])
def test_current_authority_is_verified_before_any_object_read(content,drift):
    reader,args,raw,api,blob,requested,_=content
    def mutate(tx):
        if drift=='freeze':tx.set(('account_erasure_locks',UID),{'user_id':UID})
        elif drift=='foreign_owner':tx.set(('projects',PID,'members',UID),dict(user_id=UID,role='editor'))
        elif drift=='generation':tx.set(('projects',PID,'blocks','b_one'),dict(generation=2))
    raw.run_transaction(mutate)
    if drift=='foreign_account':args['user_id']='usr_'+'f'*32
    before=deepcopy(raw._documents)
    with pytest.raises((BackfillUnavailable,ValueError)):reader(**args)
    assert raw._documents==before and not blob.calls and not requested


@pytest.mark.parametrize('bad',['hash','size','oversize','private','foreign_pointer','decode_limit'])
def test_object_integrity_privacy_and_decode_limits_fail_closed(content,bad):
    reader,args,raw,api,blob,requested,pointer=content
    value=dict(raw.get(('projects',PID,'blocks','b_one')).data)
    if bad=='hash':blob.value=b'x'*len(blob.value)
    elif bad=='size':blob.size+=1
    elif bad=='oversize':pointer['object_size']=8*1024*1024+1;value['payload_object']=pointer
    elif bad=='private':value['_bm_private_fields']={'secret':'synthetic'}
    elif bad=='foreign_pointer':pointer['object_name']=pointer['object_name'].replace(PID,'c'*32);value['payload_object']=pointer
    else:
        data=_json_gzip({'text_content':'x'*(8*1024*1024+1)})
        digest=hashlib.sha256(data).hexdigest()
        value['payload_object']=dict(object_name=block_payload_key(PID,'b_one',digest),
            object_generation=7,object_size=len(data),object_sha256=digest)
        blob.value=data;blob.size=len(data)
        # Permit this new canonical key/size; keep real bounded decode path.
        reader.objects._bucket=SimpleNamespace(blob=lambda key:SimpleNamespace(size=len(data),generation=7,
            reload=lambda **kw:None,download_as_bytes=lambda **kw:data))
    raw.run_transaction(lambda tx:tx.set(('projects',PID,'blocks','b_one'),value))
    with pytest.raises(BackfillUnavailable):reader(**args)
    if bad in {'private','foreign_pointer','oversize'}:assert not blob.calls


def test_direct_legacy_text_is_bounded_without_an_object_read(content):
    reader,args,raw,api,blob,requested,_=content
    raw.run_transaction(lambda tx:tx.set(('projects',PID,'blocks','b_one'),dict(generation=1,text_content='two words')))
    assert reader(**args)==dict(generation=1,text_content='two words')
    assert not requested
    masks=[list(r['mask'].field_paths) for name,r in api.calls if name=='get' and r.get('mask')]
    assert sum('text_content' in fields for fields in masks)==1
    args['max_text_chars']=3
    with pytest.raises(BackfillUnavailable):reader(**args)


def test_gcs_pointer_does_not_request_a_redundant_firestore_body(content):
    reader,args,raw,api,_,_,_=content
    row=dict(raw.get(('projects',PID,'blocks','b_one')).data)
    row['text_content']='Synthetic redundant body that must not be fetched'
    raw.run_transaction(lambda tx:tx.set(('projects',PID,'blocks','b_one'),row))
    assert reader(**args)['text_content']=='three saved words'
    masks=[list(r['mask'].field_paths) for name,r in api.calls if name=='get' and r.get('mask')]
    assert all('text_content' not in fields for fields in masks)


@pytest.mark.parametrize('fault',['wide_metadata','compound_generation','compound_size','private_marker','encrypted_v1','encrypted_unknown'])
def test_malformed_or_unsupported_metadata_rejects_before_gcs_or_inline_body(content,fault):
    reader,args,raw,api,blob,requested,pointer=content
    row=dict(raw.get(('projects',PID,'blocks','b_one')).data)
    if fault=='wide_metadata':pointer['object_name']='a'*(32*1024+1)
    elif fault=='compound_generation':row['generation']={'nested':['synthetic']}
    elif fault=='compound_size':pointer['object_size']={'nested':['synthetic']}
    elif fault=='private_marker':row['_bm_private_fields']={'format':'future-format','nested':{'synthetic':'denied'}}
    else:pointer['encryption_format']='bm-hosted-envelope-v1' if fault=='encrypted_v1' else 'future-format'
    row['payload_object']=pointer
    raw.run_transaction(lambda tx:tx.set(('projects',PID,'blocks','b_one'),row))
    before=deepcopy(raw._documents)
    with pytest.raises(BackfillUnavailable):reader(**args)
    assert not requested and not blob.calls and raw._documents==before
    masks=[list(r['mask'].field_paths) for name,r in api.calls if name=='get' and r.get('mask')]
    assert all('text_content' not in fields for fields in masks)


def test_stale_inline_generation_rejects_before_requesting_text(content):
    reader,args,raw,api,_,requested,_=content
    raw.run_transaction(lambda tx:tx.set(('projects',PID,'blocks','b_one'),dict(generation=2,text_content='Synthetic unread body')))
    with pytest.raises(BackfillUnavailable):reader(**args)
    masks=[list(r['mask'].field_paths) for name,r in api.calls if name=='get' and r.get('mask')]
    assert not requested and all('text_content' not in fields for fields in masks)


@pytest.mark.parametrize('fault',['foreign_response','unexpected_field','stream_tail_failure'])
def test_raw_metadata_stream_cannot_supply_a_partial_or_foreign_body(content,monkeypatch,fault):
    from google.api_core.exceptions import DeadlineExceeded
    reader,args,raw,api,blob,requested,_=content;native=api.batch_get_documents;closed=[]
    def altered(request,**kw):
        rows=list(native(request,**kw))
        if not request['documents'][0].endswith('/blocks/b_one'):return iter(rows)
        if fault=='foreign_response':rows[0].found.name=rows[0].found.name.replace('/blocks/b_one','/blocks/b_other')
        elif fault=='unexpected_field':rows[0].found.fields['text_content']={'string_value':'Synthetic forbidden projection'}
        def stream():
            try:
                yield from rows
                if fault=='stream_tail_failure':raise DeadlineExceeded('Synthetic metadata stream tail failure')
            finally:closed.append(True)
        return stream()
    monkeypatch.setattr(api,'batch_get_documents',altered);before=deepcopy(raw._documents)
    with pytest.raises(BackfillUnavailable):reader(**args)
    assert closed and not requested and not blob.calls and raw._documents==before


def test_imported_optional_bundle_uses_actual_canonical_gzip_codec(content):
    from hosted.domain_store import HostedDomainStore
    from hosted.storage.memory import MemoryObjectStore
    from hosted.packages import HostedProjectPackageService
    reader,args,raw,api,_,_,_=content
    objects=MemoryObjectStore();domain=HostedDomainStore(raw,objects)
    service=HostedProjectPackageService(SimpleNamespace(documents=raw,objects=objects,domain=domain))
    path,stored=service._prepare_import_entity('manuscript_block',{'project_id':PID},
        {'text_content':'three imported words'},entity_id='b_one',
        read_bundle={'object_name':'never/read/optional/bundle','bundle_entry':'b_one'})
    raw.run_transaction(lambda tx:tx.set(path,stored));pointer=stored['payload_object'];names=[]
    def blob(name):
        names.append(name);assert name==pointer['object_name']
        data=b''.join(objects.read_chunks(name,max_bytes=8*1024*1024,if_generation_match=pointer['object_generation']))
        return SimpleNamespace(size=len(data),generation=pointer['object_generation'],reload=lambda **kw:None,
            download_as_bytes=lambda **kw:data)
    reader.objects._bucket=SimpleNamespace(blob=blob)
    before=deepcopy(raw._documents)
    worker=AccountWordBackfill(reader.store,reader,enabled=True)
    plan=worker.inspect_account(UID,deadline=until());job=worker.initialize_account(plan,approved=True,deadline=until())['jobs'][0]
    assert worker.step(job,expected_cursor=None,approved=True,deadline=until())['phase']=='complete'
    assert raw.get(('users',UID,'metrics','saved_words_v1')).data['saved_words']==3
    assert names==[pointer['object_name']]
    for p,row in before.items():assert raw._documents[p]==row
