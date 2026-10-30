"""Real offline inference for all added language/gender combinations. Canned text only."""
import json
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
with tempfile.TemporaryDirectory(prefix='breadmaker-language-smoke-') as home:
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','src.server:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'BREADMAKER_HOME':home},stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if requests.get(base+'/api/health',timeout=.2).ok:break
            except requests.RequestException:pass
            time.sleep(.1)
        project=requests.post(base+'/api/projects',json={'name':'Language smoke fixture'});project.raise_for_status();project_id=project.json()['project']['id']
        catalog=requests.get(base+'/api/narration-voices').json()
        voices=[voice for voice in catalog['voices'] if voice['language'] not in ('en-us','es')]
        if '--numbers' in sys.argv:
            voices=[voice for voice in voices if voice['id'] in ('jf_alpha','zf_xiaobei')]
            catalog['samples'].update({'ja':'彼は「パンを12個」と言った。（本当？）','zh-CN':'他说：“面包有12个。”（真的吗？）'})
        if '--editor' in sys.argv:voices=[voice for voice in voices if voice['id'] in ('if_sara','jf_alpha','zf_xiaobei')]
        assert all(voice['installed'] for voice in voices),'Run the explicit language/voice installers first'
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True);page=browser.new_page();external=[]
            page.on('request',lambda request:external.append(request.url) if not request.url.startswith((base,'blob:','data:')) else None)
            pronunciation_headers=[]
            page.on('request',lambda request:pronunciation_headers.append(request.headers) if request.url==base+'/api/narration-pronunciation' else None)
            page.goto(base+('/p/'+project_id if '--editor' in sys.argv else '/static/dev/narration-benchmark.html'));page.set_default_timeout(180000)
            if '--editor' in sys.argv:page.locator('#app:not([inert])').wait_for(state='visible')
            page.evaluate('window.languageEngine=new window.BreadmakerKokoroEngine({backend:"wasm"})')
            for voice in voices:
                result=page.evaluate('''async ({voice,text})=>{
                    const output=await window.languageEngine.generate(text,voice.id),view=new DataView(await output.blob.arrayBuffer());let peak=0;
                    for(let at=44;at<view.byteLength;at+=2)peak=Math.max(peak,Math.abs(view.getInt16(at,true)));
                    if(peak<100||!output.timings.length)throw Error('Silent or untimed audio: '+voice.id);
                    for(const word of output.timings)if(!text.slice(word.start,word.end).trim()||word.time+word.duration>view.getUint32(40,true)/48000+1)throw Error('Invalid word timing');
                    return {voice:voice.id,seconds:view.getUint32(40,true)/48000,words:output.timings.map(word=>text.slice(word.start,word.end))};
                }''',{'voice':voice,'text':catalog['samples'][voice['language']]})
                print('PASS:',json.dumps(result,ensure_ascii=False),flush=True)
            page.evaluate('window.languageEngine.dispose()');browser.close()
            if '--editor' in sys.argv:
                assert len(pronunciation_headers)==len(voices)
                assert all(headers.get('x-breadmaker-client') and headers.get('x-breadmaker-project')==project_id for headers in pronunciation_headers),'Pronunciation must preserve the real editor project/client lease headers'
                print('PASS: pronunciation through real Editor API, bound project and client lease.',flush=True)
        assert not external,external
        assert requests.get(base+'/api/narrations').json()['records']==[]
        print('PASS: all added voices generated non-silent timed audio offline; no manuscript or recording publication.',flush=True)
    finally:
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
