"""Real offline CPU inference, canned English/Spanish, no owner's project data."""
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
with tempfile.TemporaryDirectory(prefix='breadmaker-punctuation-') as home:
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','src.server:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'BREADMAKER_HOME':home},stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if requests.get(base+'/api/health',timeout=.2).ok:break
            except requests.RequestException:pass
            time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True);page=browser.new_page();external=[];errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:external.append(request.url) if not request.url.startswith((base,'blob:','data:')) else None)
            page.goto(base+'/static/dev/narration-benchmark.html');page.set_default_timeout(180000)
            for text,voice in [('Hello, world. Go!','af_heart'),('Hola, Ana. Sí.','ef_dora')]:
                result=page.evaluate('''async ({text,voice})=>{
                    const engine=new window.BreadmakerKokoroEngine({backend:'wasm'}),results=[];
                    try{for(const enabled of [false,true]){
                        let duration=0,chunks=0;
                        const output=await engine.generate(text,voice,()=>{},part=>{duration=part.duration;chunks++},1,enabled);
                        const view=new DataView(await output.blob.arrayBuffer()),seconds=view.getUint32(40,true)/48000;
                        if(Math.abs(seconds-duration)>1/24000)throw Error('Preview and saved WAV disagree');
                        if(!output.timings.length||!chunks||output.timings.some(word=>word.start<0||word.end>text.length||word.time+word.duration>seconds+1))throw Error('Invalid timing');
                        let peak=0;for(let at=44;at<view.byteLength;at+=2)peak=Math.max(peak,Math.abs(view.getInt16(at,true)));
                        if(peak<100)throw Error('Silent recording');results.push({enabled,seconds,chunks,timings:output.timings});
                    }return results}finally{engine.dispose()}
                }''',{'text':text,'voice':voice})
                assert result[1]['seconds']>=result[0]['seconds'],result
                assert [(word['start'],word['end']) for word in result[0]['timings']]==[(word['start'],word['end']) for word in result[1]['timings']]
                print(f'PASS {voice}: original {result[0]["seconds"]:.3f}s, punctuation {result[1]["seconds"]:.3f}s; real preview/WAV/timings agree.',flush=True)
            assert not errors,errors
            assert not external,external
            browser.close()
    finally:
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
