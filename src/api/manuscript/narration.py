"""Local derived narration cache. Deliberately outside canonical assets and backups."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import secrets
import tempfile
import time
import wave
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from backend.environment import runtime
from .document import manuscript_source_text
from .narration_voices import narration_voice_catalog
from .narration_boundary import MAX_AUDIO, NarrationRoute

router=APIRouter(route_class=NarrationRoute)

def _root() -> Path:
    if runtime.mode!='local':raise HTTPException(404)
    root=getattr(runtime.context.current(),'root',None)
    if root is None:raise HTTPException(409,'PROJECT_CONTEXT_REQUIRED')
    cache=Path(root)/'narrations'
    if cache.is_symlink():raise HTTPException(409,'NARRATION_CACHE_UNSAFE')
    return cache

def _record_path(record_id:str,suffix:str) -> Path:
    if not re.fullmatch(r'[0-9a-f]{64}',record_id):raise HTTPException(404)
    return _root()/(record_id+suffix)

def _atomic(path:Path,payload:bytes):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def _publish(root:Path,record:dict,payload:bytes):
    """Keep an existing recording intact if metadata publication fails."""
    audio=root/(record['id']+'.wav');backup=None
    root.mkdir(parents=True,exist_ok=True)
    if audio.is_symlink():raise HTTPException(409,'NARRATION_CACHE_UNSAFE')
    if audio.exists():
        fd,name=tempfile.mkstemp(prefix='.previous-',dir=root);os.close(fd);os.unlink(name)
        backup=Path(name)
        os.link(audio,backup)
    try:
        _atomic(audio,payload)
        _atomic(root/(record['id']+'.json'),json.dumps(record,ensure_ascii=False).encode())
    except BaseException:
        if backup is not None:os.replace(backup,audio)
        else:audio.unlink(missing_ok=True)
        raise
    finally:
        if backup is not None:backup.unlink(missing_ok=True)

def _read_record(path:Path,record_id:str):
    try:
        if path.is_symlink() or path.stat().st_size>500000:return None
        record=json.loads(path.read_text('utf-8'))
        if not isinstance(record,dict) or record.get('id')!=record_id or not isinstance(record.get('block_id'),str):return None
        if not all(isinstance(record.get(key),str) for key in ('source_text','text','voice','engine')):return None
        if type(record.get('duration')) not in (int,float) or not math.isfinite(record['duration']) or record['duration']<=0:return None
        if type(record.get('start'))!=int or type(record.get('end'))!=int or not 0<=record['start']<record['end']:return None
        if not isinstance(record.get('timings'),list) or len(record['timings'])>10000:return None
        for word in record['timings']:
            if not isinstance(word,dict) or type(word.get('start'))!=int or type(word.get('end'))!=int:return None
            if not 0<=word['start']<word['end']<=len(record['text'].encode('utf-16-le'))//2:return None
            if any(type(word.get(key)) not in (int,float) or not 0<=word[key]<36000 for key in ('time','duration')):return None
        return record
    except (OSError,ValueError,TypeError,OverflowError):return None

def _validate_source(block_id,chapter_id,source):
    row=manuscript_source_text(block_id,chapter_id)
    if not row:raise HTTPException(404,'NARRATION_BLOCK_NOT_FOUND')
    if re.sub(r'\s','',source)!=re.sub(r'\s','',str(row['text_content'] or '')):raise HTTPException(409,'NARRATION_TEXT_CHANGED')

def _purge_deleted(root:Path):
    # Undo tombstones expire after a day, lazily on the next cache operation.
    for path in root.glob('.deleted-*.json'):
        match=re.fullmatch(r'\.deleted-[0-9a-f]{64}-([0-9a-f]{64})\.json',path.name)
        if not match or path.is_symlink() or path.stat().st_mtime>time.time()-86400:continue
        path.unlink(missing_ok=True)
        record_id=match[1]
        if not (root/(record_id+'.json')).exists() and not any(root.glob(f'.deleted-*-{record_id}.json')):
            (root/(record_id+'.wav')).unlink(missing_ok=True)

@router.get('/api/narrations')
def list_narrations():
    root=_root();_purge_deleted(root);records=[]
    if root.is_dir():
        for path in sorted(root.glob('*.json')):
            try:
                if not re.fullmatch(r'[0-9a-f]{64}\.json',path.name) or path.is_symlink() or path.stat().st_size>500000:continue
                record=_read_record(path,path.stem)
                if record and (root/(record['id']+'.wav')).is_file() and not (root/(record['id']+'.wav')).is_symlink():records.append(record)
            except (OSError,ValueError,TypeError,AttributeError):continue
    return {'records':records}

@router.post('/api/narrations')
async def save_narration(metadata:str=Form(...),file:UploadFile=File(...)):
    root=_root();_purge_deleted(root)
    if len(metadata)>400000:raise HTTPException(413)
    try:
        data=json.loads(metadata);block_id=str(data['block_id']);voice=data['voice'];source=data['source_text'];text=data['text'];start=data['start'];end=data['end'];timings=data.get('timings',[])
        target_type=data.get('target_type','block');chapter_id=None
        if target_type not in ('block','chapter_title'):raise ValueError()
        if target_type=='chapter_title':
            chapter_id=str(data['chapter_id'])
            if not re.fullmatch(r'[1-9][0-9]{0,18}',chapter_id) or block_id!=f'chapter-title:{chapter_id}':raise ValueError()
        if voice not in {item['id'] for item in narration_voice_catalog()['voices']} or not isinstance(source,str) or not isinstance(text,str) or not 0<len(text)<=20000 or len(source)>100000:raise ValueError()
        language=next(item['language'] for item in narration_voice_catalog()['voices'] if item['id']==voice)
        if data.get('language',language)!=language:raise ValueError()
        backend=data.get('backend','wasm')
        if backend not in ('wasm','webgpu'):raise ValueError()
        pace=data.get('generation_pace',1)
        if type(pace) not in (int,float) or pace not in (.85,.9,.95,1,1.05,1.1,1.15):raise ValueError()
        punctuation_pauses=data.get('punctuation_pauses',False)
        if type(punctuation_pauses)!=bool:raise ValueError()
        if type(start)!=int or type(end)!=int or start<0 or end<=start:raise ValueError()
        if end>len(source.encode('utf-16-le'))//2:raise ValueError()
        if source.encode('utf-16-le')[start*2:end*2].decode('utf-16-le')!=text:raise ValueError()
        if target_type=='chapter_title' and (start!=0 or text!=source):raise ValueError()
        if not isinstance(timings,list) or len(timings)>10000:raise ValueError()
        for item in timings:
            if not isinstance(item,dict) or type(item.get('start'))!=int or type(item.get('end'))!=int:raise ValueError()
            if not all(type(item[k]) in (int,float) for k in ('time','duration','start','end')):raise ValueError()
            if not 0<=item['time']<36000 or not 0<=item['duration']<36000 or not 0<=item['start']<item['end']<=len(text.encode('utf-16-le'))//2:raise ValueError()
        timings=[{key:item[key] for key in ('time','duration','start','end')} for item in timings]
    except (KeyError,ValueError,TypeError,UnicodeError):raise HTTPException(400,'NARRATION_METADATA_INVALID')
    _validate_source(block_id,chapter_id,source)
    payload=await file.read(MAX_AUDIO+1)
    if len(payload)>MAX_AUDIO:raise HTTPException(413)
    try:
        with wave.open(io.BytesIO(payload),'rb') as audio:
            if audio.getnchannels()!=1 or audio.getsampwidth()!=2 or audio.getframerate()!=24000:raise ValueError()
            frames=audio.getnframes()
            if frames<=0 or len(audio.readframes(frames))!=frames*2:raise ValueError()
            duration=frames/24000
    except (wave.Error,EOFError,ValueError):raise HTTPException(400,'NARRATION_AUDIO_INVALID')
    if any(item['time']+item['duration']>duration+1 for item in timings):raise HTTPException(400,'NARRATION_TIMING_INVALID')
    dtype='fp32' if backend=='webgpu' else 'q8'
    engine=f'kokoro-timestamped-{dtype}-es-piper-0.4.2-v1' if language=='es' else f'kokoro-timestamped-{dtype}-v1'
    if language not in ('en-us','es'):engine+=f'-{language}-frontend-v1'
    # Preserve default-pace identities; distinguish non-default synthesis output.
    if pace!=1:engine+=f'-pace-{pace:g}'
    if punctuation_pauses:engine+='-punctuation-v1'
    identity=json.dumps([block_id,source,start,end,voice,engine],ensure_ascii=False,separators=(',',':'))
    record_id=hashlib.sha256(identity.encode()).hexdigest()
    replaces=data.get('replaces')
    if replaces:
        if not isinstance(replaces,str):raise HTTPException(400,'NARRATION_REPLACEMENT_INVALID')
        previous=_record_path(replaces,'.json')
        try:
            previous_record=_read_record(previous,replaces)
            if not previous_record or previous_record['block_id']!=block_id:raise ValueError()
        except (OSError,ValueError,KeyError):raise HTTPException(409,'NARRATION_REPLACEMENT_INVALID')
    record={'id':record_id,'block_id':block_id,'source_text':source,'text':text,'start':start,'end':end,'voice':voice,'language':language,'timings':timings,'duration':duration,'engine':engine}
    record['generation_pace']=pace
    record['punctuation_pauses']=punctuation_pauses
    if target_type=='chapter_title':record.update(target_type=target_type,chapter_id=chapter_id)
    # UploadFile.read can yield: a writer may have changed/deleted the source.
    _validate_source(block_id,chapter_id,source)
    _publish(root,record,payload)
    # Publish the replacement successfully before retiring the selected recording.
    if replaces and replaces!=record_id:
        previous.unlink(missing_ok=True)
        _record_path(replaces,'.wav').unlink(missing_ok=True)
    return record

@router.delete('/api/narrations/{record_id}')
def delete_narration(record_id:str):
    path=_record_path(record_id,'.json')
    if path.is_symlink() or not path.is_file():raise HTTPException(404)
    token=secrets.token_hex(32)
    # Keep the derived audio for Undo; hidden metadata is not listed or playable.
    deleted=path.parent/f'.deleted-{token}-{record_id}.json'
    os.replace(path,deleted);os.utime(deleted,None)
    return {'undo_token':token}

@router.post('/api/narrations/{record_id}/restore')
def restore_narration(record_id:str,undo_token:str=Body(...,embed=True)):
    path=_record_path(record_id,'.json')
    if not re.fullmatch(r'[0-9a-f]{64}',undo_token):raise HTTPException(404)
    deleted=path.parent/f'.deleted-{undo_token}-{record_id}.json'
    audio=_record_path(record_id,'.wav')
    if deleted.is_symlink() or not deleted.is_file() or audio.is_symlink() or not audio.is_file():raise HTTPException(404)
    if deleted.stat().st_mtime<=time.time()-86400:raise HTTPException(404)
    if path.exists():raise HTTPException(409,'NARRATION_ALREADY_EXISTS')
    record=_read_record(deleted,record_id)
    if record is None:raise HTTPException(409,'NARRATION_CACHE_INVALID')
    os.replace(deleted,path)
    return record

@router.get('/api/narrations/{record_id}/audio')
def narration_audio(record_id:str):
    path=_record_path(record_id,'.wav')
    metadata=_record_path(record_id,'.json')
    if path.is_symlink() or not path.is_file() or metadata.is_symlink() or not metadata.is_file():raise HTTPException(404)
    return FileResponse(path,media_type='audio/wav',headers={'Cache-Control':'private, no-store'})
