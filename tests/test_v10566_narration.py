import io
import json
import wave
import pytest
from pathlib import Path

from conftest import assert_ok

@pytest.fixture(autouse=True)
def local_authority(client):
    # Match the real localhost app; production never whitelists TestClient's
    # synthetic "testserver" hostname just to make a test pass.
    client.headers['host']='127.0.0.1'

@pytest.mark.parametrize('mode',['hosted','hosted_dev'])
def test_all_narration_routes_reject_hosted_before_parsing(client,fresh_project,monkeypatch,mode):
    from types import SimpleNamespace
    from api.manuscript import narration_boundary as owner
    monkeypatch.setattr(owner,'runtime',SimpleNamespace(mode=mode))
    routes=[
        ('GET','/api/narrations'),('POST','/api/narrations'),
        ('DELETE','/api/narrations/'+'0'*64),
        ('POST','/api/narrations/'+'0'*64+'/restore'),
        ('GET','/api/narrations/'+'0'*64+'/audio'),
        ('GET','/api/narration-voices'),
        ('POST','/api/narration-voices/af_heart/download'),
        ('POST','/api/narration-pronunciation'),
    ]
    for method,path in routes:
        response=client.request(method,path,content=b'invalid body',headers={'Content-Type':'application/json'})
        assert response.status_code==404,(method,path,response.text)

@pytest.mark.parametrize('timing',[
    {'time':0,'duration':.01,'start':.5,'end':1},
    {'time':float('nan'),'duration':.01,'start':0,'end':1},
    {'time':0,'duration':float('inf'),'start':0,'end':1},
    None, [], {'time':0,'duration':.01,'start':True,'end':1},
])
def test_reject_malformed_word_timing(client,fresh_project,timing):
    metadata,audio=_record(client,fresh_project)
    metadata['timings']=[timing]
    response=client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')})
    assert response.status_code==400
    assert client.get('/api/narrations').json()['records']==[]

def test_source_is_revalidated_after_upload(client,fresh_project,monkeypatch):
    from api.manuscript import narration as owner
    metadata,audio=_record(client,fresh_project)
    original=owner.manuscript_source_text;reads=[]
    def source(*args,**kwargs):
        reads.append(True)
        return original(*args,**kwargs) if len(reads)==1 else {'text_content':'Changed during upload.'}
    monkeypatch.setattr(owner,'manuscript_source_text',source)
    response=client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')})
    assert response.status_code==409
    assert client.get('/api/narrations').json()['records']==[]

def test_failed_regeneration_preserves_previous_audio(client,fresh_project,monkeypatch):
    from api.manuscript import narration as owner
    metadata,audio=_record(client,fresh_project)
    def save(payload):return client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',payload,'audio/wav')})
    record=assert_ok(save(audio));original=owner._atomic
    def fail_metadata(path,payload):
        if path.suffix=='.json':raise OSError('injected metadata publication failure')
        return original(path,payload)
    monkeypatch.setattr(owner,'_atomic',fail_metadata)
    with pytest.raises(OSError,match='injected'):save(audio[:-2]+b'\x01\x01')
    assert client.get(f"/api/narrations/{record['id']}/audio").content==audio
    assert client.get('/api/narrations').json()['records']==[record]

def test_corrupt_undo_metadata_fails_closed(client,fresh_project):
    from backend.environment import runtime
    metadata,audio=_record(client,fresh_project)
    record=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')}))
    undo=assert_ok(client.delete(f"/api/narrations/{record['id']}"))
    root=runtime.projects.workspace.project_root(fresh_project['project']['id'])/'narrations'
    tombstone=next(root.glob('.deleted-*.json'));tombstone.write_text('[]')
    assert client.post(f"/api/narrations/{record['id']}/restore",json=undo).status_code==409
    assert client.get('/api/narrations').json()['records']==[]

@pytest.mark.parametrize('patch',[{'id':'f'*64},{'duration':10**400},{'timings':[None]},{'timings':[{'start':0,'end':1,'time':float('nan'),'duration':.1}]}])
def test_corrupt_cache_records_do_not_break_listing(client,fresh_project,patch):
    from backend.environment import runtime
    metadata,audio=_record(client,fresh_project)
    record=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')}))
    root=runtime.projects.workspace.project_root(fresh_project['project']['id'])/'narrations'
    (root/(record['id']+'.json')).write_text(json.dumps({**record,**patch}))
    assert client.get('/api/narrations').json()['records']==[]

def test_external_origin_cannot_mutate_narration(client,fresh_project):
    response=client.post('/api/narrations',headers={'Origin':'https://attacker.example'},data={})
    assert response.status_code==403

def test_upload_request_size_is_bounded_before_multipart(client,fresh_project,monkeypatch):
    from api.manuscript import narration_boundary as owner
    monkeypatch.setattr(owner,'MAX_REQUEST',128)
    # Declared size is refused without invoking the multipart parser.
    response=client.post('/api/narrations',content=b'x',headers={'Content-Length':'129','Content-Type':'multipart/form-data; boundary=x'})
    assert response.status_code==413
    # A streamed body without Content-Length cannot bypass the same limit.
    response=client.post('/api/narrations',content=iter([b'--x\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n\r\n',b'x'*256,b'\r\n--x--\r\n']),headers={'Content-Type':'multipart/form-data; boundary=x'})
    assert response.status_code==413

def test_busy_pronunciation_does_not_queue_api_workers(client,fresh_project):
    from api.manuscript import narration_pronunciation as owner
    with owner._lock:
        response=client.post('/api/narration-pronunciation',json={'language':'ja','text':'パン'})
    assert response.status_code==429 and response.headers['retry-after']=='1'

@pytest.mark.parametrize('path',['/api/narrations','/api/narration-voices'])
@pytest.mark.parametrize('host',['attacker.example','127.0.0.1.attacker.example','user@127.0.0.1','127.0.0.1/anything'])
def test_narration_rejects_foreign_or_malformed_hosts(client,fresh_project,path,host):
    assert client.get(path,headers={'Host':host}).status_code==400

def test_expired_undo_token_cannot_restore_directly(client,fresh_project):
    import os
    import time
    from backend.environment import runtime
    metadata,audio=_record(client,fresh_project)
    record=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')}))
    undo=assert_ok(client.delete(f"/api/narrations/{record['id']}"))
    root=runtime.projects.workspace.project_root(fresh_project['project']['id'])/'narrations'
    tombstone=next(root.glob('.deleted-*.json'));old=time.time()-86401;os.utime(tombstone,(old,old))
    assert client.post(f"/api/narrations/{record['id']}/restore",json=undo).status_code==404

@pytest.mark.parametrize('target',['http://huggingface.co/model','https://attacker.example/model','https://user:password@huggingface.co/model','https://huggingface.co:8443/model'])
def test_voice_redirects_reject_unsafe_authorities(target):
    import urllib.request
    import urllib.error
    from api.manuscript.narration_voices import _VoiceRedirect
    with pytest.raises(urllib.error.URLError):
        _VoiceRedirect().redirect_request(urllib.request.Request('https://huggingface.co/model'),None,302,'redirect',{},target)

def test_chapter_title_cache_roundtrip_staleness_and_target_validation(client,fresh_project):
    _,audio=_record(client,fresh_project)
    chapter=fresh_project['bootstrap']['chapters'][0];cid=chapter['id'];title='Chapter 😀: Opening'
    assert_ok(client.patch(f'/api/chapters/{cid}/title',json={'title':title}))
    metadata={'block_id':f'chapter-title:{cid}','target_type':'chapter_title','chapter_id':str(cid),'voice':'af_heart','source_text':title,'text':title,'start':0,'end':len(title.encode('utf-16-le'))//2,'timings':[]}
    def save(data):return client.post('/api/narrations',data={'metadata':json.dumps(data)},files={'file':('audio.wav',audio,'audio/wav')})
    first=assert_ok(save(metadata));assert first['target_type']=='chapter_title' and first['chapter_id']==str(cid)
    assert assert_ok(save(metadata))['id']==first['id']
    assert client.get(f"/api/narrations/{first['id']}/audio").content==audio
    assert save({**metadata,'chapter_id':'999999'}).status_code==400
    assert save({**metadata,'block_id':'chapter-title:999999','chapter_id':'999999'}).status_code==404
    assert save({**metadata,'target_type':'unknown'}).status_code==400
    assert save({**metadata,'text':'Chapter','end':7}).status_code==400
    assert save({**metadata,'end':metadata['end']+1}).status_code==400
    assert_ok(client.patch(f'/api/chapters/{cid}/title',json={'title':'Renamed'}))
    assert save(metadata).status_code==409
    updated={**metadata,'source_text':'Renamed','text':'Renamed','end':7,'replaces':first['id']}
    replacement=assert_ok(save(updated));assert replacement['id']!=first['id']
    assert assert_ok(client.get('/api/narrations'))['records']==[replacement]
    undo=assert_ok(client.delete(f"/api/narrations/{replacement['id']}"));assert_ok(client.post(f"/api/narrations/{replacement['id']}/restore",json=undo))
    other=assert_ok(client.post('/api/projects',json={'name':'Other title project'}))['project']
    assert client.get(f"/api/narrations/{replacement['id']}/audio",headers={'X-Breadmaker-Project':other['id']}).status_code==404

def _record(client,fresh_project,text='A short narration.'):
    chapter=fresh_project['bootstrap']['chapters'][0]
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{**block,'content_html':text,'text_content':text}]}))
    output=io.BytesIO()
    with wave.open(output,'wb') as audio:audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000);audio.writeframes(b'\x00\x00'*2400)
    metadata={'block_id':block['id'],'voice':'af_heart','source_text':text,'text':text,'start':0,'end':len(text),'timings':[]}
    return metadata,output.getvalue()

def test_project_narration_cache_roundtrip_and_deduplication(client,fresh_project):
    metadata,audio=_record(client,fresh_project)
    def save():return assert_ok(client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')}))
    first=save();second=save()
    assert first['id']==second['id']
    assert len(assert_ok(client.get('/api/narrations'))['records'])==1
    assert client.get(f"/api/narrations/{first['id']}/audio").content==audio
    # Portable project backups are explicitly regenerated without derived narration.
    backup=assert_ok(client.post('/api/backup'))
    import zipfile
    from backend.environment import runtime
    root=runtime.projects.workspace.project_root(fresh_project['project']['id'])
    assert (root/'narrations'/(first['id']+'.wav')).is_file()
    with zipfile.ZipFile(root/'backups'/backup['file']) as package:assert not any('narrations/' in name for name in package.namelist())
    exported=client.get('/api/data/export')
    assert exported.status_code==200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as package:assert not any('/narrations/' in name for name in package.namelist())

def test_narration_generation_pace_identity_and_validation(client,fresh_project):
    metadata,audio=_record(client,fresh_project)
    def save(patch):return client.post('/api/narrations',data={'metadata':json.dumps({**metadata,**patch})},files={'file':('audio.wav',audio,'audio/wav')})
    default=assert_ok(save({}));explicit=assert_ok(save({'generation_pace':1}))
    assert default['id']==explicit['id'] and default['engine']=='kokoro-timestamped-q8-v1'
    paced=assert_ok(save({'generation_pace':.95}))
    assert paced['id']!=default['id'] and paced['generation_pace']==.95
    assert paced['engine']=='kokoro-timestamped-q8-v1-pace-0.95'
    for pace in (True,0,4,'0.95',None):assert save({'generation_pace':pace}).status_code==400
    replaced=assert_ok(save({'generation_pace':1.05,'replaces':paced['id']}))
    assert replaced['generation_pace']==1.05
    assert {record['id'] for record in assert_ok(client.get('/api/narrations'))['records']}=={default['id'],replaced['id']}

def test_optional_english_voice_cache_remains_replayable(client,fresh_project):
    metadata,audio=_record(client,fresh_project)
    record=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps({**metadata,'voice':'af_nicole'})},files={'file':('voice.wav',audio,'audio/wav')}))
    assert record['voice']=='af_nicole' and record['language']=='en-us'
    assert client.get(f"/api/narrations/{record['id']}/audio").content==audio

@pytest.mark.parametrize(('voice','language'),[('ff_siwis','fr'),('if_sara','it'),('im_nicola','it'),('jf_alpha','ja'),('jm_kumo','ja'),('pf_dora','pt-BR'),('pm_alex','pt-BR'),('zf_xiaobei','zh-CN'),('zm_yunxi','zh-CN')])
def test_multilingual_recordings_validate_language_and_engine(client,fresh_project,voice,language):
    metadata,audio=_record(client,fresh_project)
    def save(patch):return client.post('/api/narrations',data={'metadata':json.dumps({**metadata,'voice':voice,**patch})},files={'file':('voice.wav',audio,'audio/wav')})
    record=assert_ok(save({}));assert record['language']==language
    assert record['engine']==f'kokoro-timestamped-q8-v1-{language}-frontend-v1'
    assert save({'language':'en-us'}).status_code==400
    assert client.get(f"/api/narrations/{record['id']}/audio").content==audio

def test_local_pronunciation_contract_and_bounds(client,fresh_project,monkeypatch):
    from api.manuscript import narration_pronunciation as owner
    seen=[]
    monkeypatch.setattr(owner,'_frontend',lambda language:seen.append(language) or object())
    monkeypatch.setattr(owner,'_items',lambda text,language,frontend:[{'type':'phonetic','value':'a','subtitles':text}])
    request=lambda data:client.post('/api/narration-pronunciation',json=data)
    assert assert_ok(request({'language':'ja','text':'こんにちは'}))['items'][0]['subtitles']=='こんにちは'
    assert seen==['ja']
    for data in ({'language':'en','text':'Hello'},{'language':'ja','text':'x'*20001},{'language':'ja','text':''},{'language':'it','words':['x'*301]},{'language':'ja','text':'日本語','words':['ignored']},{'language':'it','words':['ciao'],'extra':True}):assert request(data).status_code in (400,422)
    assert seen==['ja'],'Invalid requests must not load optional dependencies'
    from types import SimpleNamespace
    monkeypatch.setattr(owner,'runtime',SimpleNamespace(mode='hosted'))
    assert request({'language':'ja','text':'日本語'}).status_code==404

def test_missing_optional_language_dependency_is_explicit(client,fresh_project,monkeypatch):
    from api.manuscript import narration_pronunciation as owner
    def missing(language):raise ImportError('not installed')
    monkeypatch.setattr(owner,'_frontend',missing)
    assert client.post('/api/narration-pronunciation',json={'language':'ja','text':'日本語'}).status_code==503

def test_voice_download_is_explicit_pinned_bounded_and_idempotent(client,fresh_project,monkeypatch,tmp_path):
    import hashlib
    from api.manuscript import narration_voices as owner
    payload=b'v'*522240;voice={'id':'af_nicole','name':'Nicole','language':'en-us','accent':'us','gender':'female','default':False,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
    static=tmp_path/'static';static.mkdir();monkeypatch.setattr(owner,'STATIC',static)
    monkeypatch.setattr(owner,'narration_voice_catalog',lambda:{'model':'onnx-community/Kokoro-82M-v1.0-ONNX-timestamped','revision':'dd4401a9add81ac692d20e240d22ec9dda82cc29','samples':{},'voices':[voice]})
    calls=[]
    def fetch(url,size):calls.append((url,size));return payload
    monkeypatch.setattr(owner,'_download_voice_bytes',fetch)
    assert assert_ok(client.get('/api/narration-voices'))['voices'][0]['installed'] is False
    assert not calls,'Listing must never access the internet'
    assert client.post('/api/narration-voices/not_allowed/download').status_code==404 and not calls
    assert assert_ok(client.post('/api/narration-voices/af_nicole/download'))['installed'] is True
    assert calls==[('https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/resolve/dd4401a9add81ac692d20e240d22ec9dda82cc29/voices/af_nicole.bin',522240)]
    assert assert_ok(client.get('/api/narration-voices'))['voices'][0]['installed'] is True
    assert_ok(client.post('/api/narration-voices/af_nicole/download'));assert len(calls)==1

def test_voice_download_bad_checksum_and_symlinks_are_rejected(client,fresh_project,monkeypatch,tmp_path):
    from api.manuscript import narration_voices as owner
    static=tmp_path/'static';static.mkdir();monkeypatch.setattr(owner,'STATIC',static)
    catalog=json.loads((Path(__file__).resolve().parents[1]/'static/narration-voices.json').read_text())
    monkeypatch.setattr(owner,'narration_voice_catalog',lambda:catalog)
    monkeypatch.setattr(owner,'_download_voice_bytes',lambda url,size:b'x'*(size+1))
    assert client.post('/api/narration-voices/af_nicole/download').status_code==502
    monkeypatch.setattr(owner,'_download_voice_bytes',lambda url,size:b'x'*size)
    assert client.post('/api/narration-voices/af_nicole/download').status_code==502,'Correct size but wrong digest must still fail'
    assert not list(static.rglob('*.bin'))
    (static/'vendor').symlink_to(tmp_path,target_is_directory=True)
    assert client.get('/api/narration-voices').status_code==409
    assert client.post('/api/narration-voices/af_nicole/download').status_code==409

def test_voice_download_is_disabled_on_hosted(client,fresh_project,monkeypatch):
    from types import SimpleNamespace
    from api.manuscript import narration_voices as owner
    monkeypatch.setattr(owner,'runtime',SimpleNamespace(mode='hosted'))
    assert client.get('/api/narration-voices').status_code==404
    assert client.post('/api/narration-voices/af_nicole/download').status_code==404

def test_punctuation_pauses_have_validated_identity_and_replace_without_duplicates(client,fresh_project):
    metadata,audio=_record(client,fresh_project)
    def save(patch):return client.post('/api/narrations',data={'metadata':json.dumps({**metadata,**patch})},files={'file':('audio.wav',audio,'audio/wav')})
    old=assert_ok(save({}));assert old['punctuation_pauses'] is False
    assert assert_ok(save({'punctuation_pauses':False}))['id']==old['id']
    for bad in ('true',1,None,[]):assert save({'punctuation_pauses':bad}).status_code==400
    updated=assert_ok(save({'punctuation_pauses':True,'replaces':old['id']}))
    assert updated['id']!=old['id'] and updated['engine'].endswith('-punctuation-v1')
    assert updated['punctuation_pauses'] is True
    assert assert_ok(client.get('/api/narrations'))['records']==[updated]
    combined=assert_ok(save({'punctuation_pauses':True,'generation_pace':.95,'backend':'webgpu','replaces':updated['id']}))
    assert combined['engine']=='kokoro-timestamped-fp32-v1-pace-0.95-punctuation-v1'

def test_narration_rejects_changed_text_and_invalid_audio(client,fresh_project):
    metadata,audio=_record(client,fresh_project)
    bad={**metadata,'source_text':'Different text','text':'Different text','end':14}
    assert client.post('/api/narrations',data={'metadata':json.dumps(bad)},files={'file':('audio.wav',audio,'audio/wav')}).status_code==409
    assert client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',b'not audio','audio/wav')}).status_code==400

def test_narration_cache_is_project_scoped(client,fresh_project):
    metadata,audio=_record(client,fresh_project)
    first=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')}))
    other=assert_ok(client.post('/api/projects',json={'name':'Independent narration cache'}))['project']
    headers={'X-Breadmaker-Project':other['id']}
    assert assert_ok(client.get('/api/narrations',headers=headers))['records']==[]
    assert client.get(f"/api/narrations/{first['id']}/audio",headers=headers).status_code==404
    assert client.delete(f"/api/narrations/{first['id']}",headers=headers).status_code==404

def test_delete_hides_audio_and_undo_restores_recording(client,fresh_project):
    metadata,audio=_record(client,fresh_project)
    first=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')}))
    undo=assert_ok(client.delete(f"/api/narrations/{first['id']}"))
    assert assert_ok(client.get('/api/narrations'))['records']==[]
    assert client.get(f"/api/narrations/{first['id']}/audio").status_code==404
    assert client.post(f"/api/narrations/{first['id']}/restore",json={'undo_token':'0'*64}).status_code==404
    restored=assert_ok(client.post(f"/api/narrations/{first['id']}/restore",json=undo))
    assert restored==first
    assert client.get(f"/api/narrations/{first['id']}/audio").content==audio
    assert client.post(f"/api/narrations/{first['id']}/restore",json=undo).status_code==404

def test_recreate_replaces_selected_record_and_failure_preserves_original(client,fresh_project):
    metadata,audio=_record(client,fresh_project)
    def save(data,payload=audio):return client.post('/api/narrations',data={'metadata':json.dumps(data)},files={'file':('audio.wav',payload,'audio/wav')})
    first=assert_ok(save(metadata))
    replacement={**metadata,'voice':'af_bella','replaces':first['id']}
    assert save(replacement,b'bad audio').status_code==400
    assert assert_ok(client.get('/api/narrations'))['records']==[first]
    second=assert_ok(save(replacement))
    assert second['id']!=first['id']
    assert assert_ok(client.get('/api/narrations'))['records']==[second]
    assert client.get(f"/api/narrations/{first['id']}/audio").status_code==404
    assert_ok(save({**replacement,'replaces':second['id']}))
    assert len(assert_ok(client.get('/api/narrations'))['records'])==1

def test_expired_deleted_recordings_are_purged(client,fresh_project):
    import os
    import time
    from backend.environment import runtime
    metadata,audio=_record(client,fresh_project)
    first=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')}))
    assert_ok(client.delete(f"/api/narrations/{first['id']}"))
    root=runtime.projects.workspace.project_root(fresh_project['project']['id'])/'narrations'
    deleted=next(root.glob('.deleted-*.json'));expired=time.time()-86401
    os.utime(deleted,(expired,expired))
    assert assert_ok(client.get('/api/narrations'))['records']==[]
    assert not deleted.exists() and not (root/(first['id']+'.wav')).exists()

@pytest.mark.parametrize('voice',['ef_dora','em_alex','em_santa'])
def test_spanish_voices_have_language_and_separate_engine_identity(client,fresh_project,voice):
    metadata,audio=_record(client,fresh_project,'¡Hola, niño! Mañana habrá 12 panes.')
    english=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('audio.wav',audio,'audio/wav')}))
    data={**metadata,'voice':voice,'replaces':english['id']}
    spanish=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps(data)},files={'file':('audio.wav',audio,'audio/wav')}))
    assert spanish['language']=='es'
    assert spanish['engine']=='kokoro-timestamped-q8-es-piper-0.4.2-v1'
    assert spanish['id']!=english['id']
    assert len(assert_ok(client.get('/api/narrations'))['records'])==1
    data['language']='en-us'
    assert client.post('/api/narrations',data={'metadata':json.dumps(data)},files={'file':('audio.wav',audio,'audio/wav')}).status_code==400

@pytest.mark.parametrize('voice',['am_michael','bm_george'])
def test_english_male_voices_are_accepted(client,fresh_project,voice):
    metadata,audio=_record(client,fresh_project)
    record=assert_ok(client.post('/api/narrations',data={'metadata':json.dumps({**metadata,'voice':voice})},files={'file':('audio.wav',audio,'audio/wav')}))
    assert record['voice']==voice and record['language']=='en-us'
    assert record['engine']=='kokoro-timestamped-q8-v1'

def test_narration_thread_isolation_headers_are_local_only(monkeypatch):
    from types import SimpleNamespace
    from starlette.requests import Request
    from backend import http_boundary
    request=Request({'type':'http','scheme':'http','path':'/','headers':[(b'host',b'localhost')]})
    for mode in ('local','hosted','hosted_dev'):
        monkeypatch.setattr(http_boundary,'runtime',SimpleNamespace(mode=mode,sessions=SimpleNamespace(metadata=lambda:{'auth_mode':'disabled'})))
        headers=http_boundary._security_headers(request)
        if mode=='local':
            assert headers['Cross-Origin-Opener-Policy']=='same-origin'
            assert headers['Cross-Origin-Embedder-Policy']=='require-corp'
        else:
            assert 'Cross-Origin-Opener-Policy' not in headers and 'Cross-Origin-Embedder-Policy' not in headers

@pytest.mark.parametrize('voice,language_suffix',[('af_heart',''),('ef_dora','-es-piper-0.4.2')])
def test_gpu_cache_provenance_and_atomic_replacement(client,fresh_project,voice,language_suffix):
    metadata,audio=_record(client,fresh_project)
    metadata['voice']=voice
    def save(data):return client.post('/api/narrations',data={'metadata':json.dumps(data)},files={'file':('audio.wav',audio,'audio/wav')})
    cpu=assert_ok(save(metadata))
    gpu=assert_ok(save({**metadata,'backend':'webgpu','replaces':cpu['id']}))
    assert gpu['engine']==f'kokoro-timestamped-fp32{language_suffix}-v1'
    assert gpu['id']!=cpu['id']
    assert assert_ok(client.get('/api/narrations'))['records']==[gpu]
    assert save({**metadata,'backend':'untrusted'}).status_code==400
