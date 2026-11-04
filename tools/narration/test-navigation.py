"""Focused real saved-audio/navigation check; no inference or external services."""
import io
import math
import struct
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import wave

import requests
from playwright.sync_api import sync_playwright
from navigation_ui import check_navigation_lifecycle

ROOT = Path(__file__).resolve().parents[2]
ENGINE = "window.BreadmakerKokoroEngine=class{constructor(){this.timing={threads:1};this.backend='wasm'}async load(){this.ready=true;return this}dispose(){}}"

with tempfile.TemporaryDirectory(prefix='breadmaker-navigation-') as home:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','src.server:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'BREADMAKER_HOME':home},stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if requests.get(base+'/api/health',timeout=.2).ok:break
            except requests.RequestException:pass
            time.sleep(.1)
        project=requests.post(base+'/api/projects',json={'name':'Disposable navigation regression'}).json()['project']
        chapter=requests.get(base+'/api/bootstrap').json()['chapters'][0]
        rows=[{'id':None,'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None} for text in ['First cached passage.','Next cached passage.','Last cached passage.']]
        requests.put(base+f"/api/chapters/{chapter['id']}/blocks",json={'blocks':rows}).raise_for_status()
        blocks=requests.get(base+f"/api/chapters/{chapter['id']}").json()['blocks']
        audio=io.BytesIO()
        with wave.open(audio,'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(b''.join(struct.pack('<h',int(5000*math.sin(i*2*math.pi*220/24000))) for i in range(24000*3)))
        for block in blocks:
            text=block['text_content'];metadata={'block_id':block['id'],'voice':'af_heart','source_text':text,'text':text,'start':0,'end':len(text),'timings':[]}
            requests.post(base+'/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('fixture.wav',audio.getvalue(),'audio/wav')}).raise_for_status()
        with sync_playwright() as pw:
            name=os.environ.get('BREADMAKER_NARRATION_BROWSER','webkit')
            assert name in ('chromium','firefox','webkit')
            browser=getattr(pw,name).launch(headless=True,**({'args':['--autoplay-policy=no-user-gesture-required']} if name=='chromium' else {}))
            page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/narration-engine.js?*',lambda route:route.fulfill(status=200,content_type='text/javascript',body=ENGINE))
            page.goto(base+'/p/'+project['id']);page.locator('#app:not([inert])').wait_for(state='visible');page.locator('#narrationBtn').click()
            page.wait_for_function('()=>narration.records.length===3&&!narration.loading')
            page.evaluate('async()=>{await narration.audio.setPitch(2);narration.audio.playbackRate=2}')
            check_navigation_lifecycle(page,base,project['id'],name)
            assert not errors,errors
            print(f'PASS: {name} navigation stops queued audio reads; cancelled navigation resumes; normal continuation and reload remain usable.',flush=True)
            browser.close()
    finally:
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
