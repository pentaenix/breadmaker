"""Optional pinned downloads and real canned previews, isolated project, no manuscript."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='breadmaker-voice-smoke-') as home:
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','src.server:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'BREADMAKER_HOME':home},stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if requests.get(base+'/api/health',timeout=.2).ok:break
            except requests.RequestException:pass
            time.sleep(.1)
        requests.post(base+'/api/projects',json={'name':'Voice download fixture'}).raise_for_status()
        for voice in ('af_nicole','bm_fable'):
            response=requests.post(base+f'/api/narration-voices/{voice}/download',timeout=60)
            assert response.ok,(response.status_code,response.text)
            assert response.json()['installed']
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True);page=browser.new_page();external=[]
            page.on('request',lambda request:external.append(request.url) if not request.url.startswith((base,'blob:','data:')) else None)
            page.goto(base+'/static/dev/narration-benchmark.html');page.set_default_timeout(180000)
            results=page.evaluate('''async ()=>{
                const engine=new window.BreadmakerKokoroEngine({backend:'wasm'}),results=[];
                try{for(const voice of ['af_nicole','bm_fable']){
                    const output=await engine.generate('The bakery is open. Please come inside.',voice);
                    const view=new DataView(await output.blob.arrayBuffer());let peak=0;
                    for(let at=44;at<view.byteLength;at+=2)peak=Math.max(peak,Math.abs(view.getInt16(at,true)));
                    if(peak<100||!output.timings.length)throw Error('Silent or untimed preview');
                    results.push({voice,seconds:view.getUint32(40,true)/48000});
                }return results}finally{engine.dispose()}
            }''')
            assert not external,external
            assert requests.get(base+'/api/narrations').json()['records']==[],'Previews must not create recordings'
            print('PASS: pinned optional downloads, offline real previews, no project recordings or external browser requests:',results,flush=True)
            browser.close()
    finally:
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
