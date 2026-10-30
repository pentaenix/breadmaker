"""Explicit, checksum-pinned local voice downloads. Never fetch manuscript text."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException
from backend.environment import STATIC, runtime
from .narration_boundary import NarrationRoute

router=APIRouter(route_class=NarrationRoute)

def narration_voice_catalog():
    return json.loads((STATIC/'narration-voices.json').read_text('utf-8'))

def _voice_root():
    if runtime.mode!='local':raise HTTPException(404)
    if runtime.context.current() is None:raise HTTPException(409,'PROJECT_CONTEXT_REQUIRED')
    catalog=narration_voice_catalog()
    root=STATIC/'vendor'/'narration'/'models'/catalog['model']/'voices'
    for path in (root,*root.parents):
        if path.is_symlink():raise HTTPException(409,'NARRATION_VOICES_UNSAFE')
        if path==STATIC:break
    return root

def _installed(root,voice):
    path=root/(voice['id']+'.bin')
    try:return not path.is_symlink() and path.stat().st_size==voice['size'] and hashlib.sha256(path.read_bytes()).hexdigest()==voice['sha256']
    except OSError:return False

class _VoiceRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parsed=urllib.parse.urlsplit(newurl);host=parsed.hostname or ''
        if parsed.scheme!='https' or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443) or not (host=='huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.hf.co')):raise urllib.error.URLError('Voice redirect refused')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def _download_voice_bytes(url,size):
    # Fixed server-owned URL + pinned digest. Bounded read and socket timeout.
    with urllib.request.build_opener(_VoiceRedirect()).open(url,timeout=20) as response:
        return response.read(size+1)

@router.get('/api/narration-voices')
def list_narration_voices():
    root=_voice_root();catalog=narration_voice_catalog()
    return {**catalog,'voices':[{**voice,'installed':_installed(root,voice)} for voice in catalog['voices']]}

@router.post('/api/narration-voices/{voice_id}/download')
def download_narration_voice(voice_id:str):
    root=_voice_root();catalog=narration_voice_catalog()
    voice=next((voice for voice in catalog['voices'] if voice['id']==voice_id),None)
    if voice is None:raise HTTPException(404)
    path=root/(voice_id+'.bin')
    if path.is_symlink():raise HTTPException(409,'NARRATION_VOICES_UNSAFE')
    if not _installed(root,voice):
        url=f"https://huggingface.co/{catalog['model']}/resolve/{catalog['revision']}/voices/{voice_id}.bin"
        try:payload=_download_voice_bytes(url,voice['size'])
        except (OSError,urllib.error.URLError):raise HTTPException(502,'NARRATION_VOICE_DOWNLOAD_FAILED')
        if len(payload)!=voice['size'] or hashlib.sha256(payload).hexdigest()!=voice['sha256']:raise HTTPException(502,'NARRATION_VOICE_CHECKSUM_FAILED')
        root.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.voice-',dir=root)
        try:
            with os.fdopen(fd,'wb') as output:output.write(payload);output.flush();os.fsync(output.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
    return {**voice,'installed':True}
