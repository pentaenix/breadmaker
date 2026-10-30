"""Local-only narration timing probe. Uses a disposable home and canned prose."""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('--device',choices=['probe','wasm','webgpu'],default='probe')
parser.add_argument('--threads',type=int,choices=[1,2,4],default=4)
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='breadmaker-narration-benchmark-') as home:
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','src.server:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=root,env={**os.environ,'PYTHONPATH':str(root/'src'),'BREADMAKER_HOME':home},stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if requests.get(base+'/api/health',timeout=.2).ok:break
            except requests.RequestException:pass
            time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            context=browser.new_context()
            def configure_threads(route):
                response=route.fetch()
                route.fulfill(response=response,body=re.sub(r'numThreads=[^;]+;',f'numThreads={args.threads};',response.text()))
            context.route('**/transformers-local.mjs',configure_threads)
            page=context.new_page();page.goto(base)
            print(json.dumps(page.evaluate("""async()=>{const adapter=await navigator.gpu?.requestAdapter();return {gpu:!!adapter,isolated:crossOriginIsolated,cores:navigator.hardwareConcurrency,info:adapter?.info?{vendor:adapter.info.vendor,architecture:adapter.info.architecture}:null}}""")),flush=True)
            if args.device!='probe':
                page.set_default_timeout(180000)
                result=page.evaluate("""async device=>{
                  const {HeadTTS}=await import('/static/vendor/narration/modules/headtts.mjs');
                  const engine=new HeadTTS({endpoints:[device],audioCtx:new AudioContext({sampleRate:24000}),transformersModule:new URL('/static/vendor/narration/transformers-local.mjs',location.origin).href,model:'onnx-community/Kokoro-82M-v1.0-ONNX-timestamped',dtypeWebgpu:'fp32',dtypeWasm:'q8',dictionaryURL:'/static/vendor/narration/dictionaries/',voiceURL:'/static/vendor/narration/models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/voices/',languages:['en-us'],voices:[],splitLength:400});
                  const started=performance.now();await engine.connect();const loadMs=performance.now()-started;
                  await engine.setup({voice:'af_heart',language:'en-us',speed:1,audioEncoding:'wav'});
                  const samples=[];
                  for(const text of ['The bakery is open. Please come inside.','The bakery is open. Please come inside.','A quiet morning settled over the village. The baker opened the shutters and smiled as the first customers arrived.']){
                    const t=performance.now(),messages=await engine.synthesize({input:text});
                    samples.push({chars:text.length,ms:performance.now()-t,audioSeconds:messages.reduce((sum,m)=>sum+(m.data?.audio?.duration||0),0),parts:messages.length});
                  }
                  engine.ww?.terminate();return {device,loadMs,samples};
                }""",args.device)
                print(json.dumps({**result,'threads':args.threads}),flush=True)
            browser.close()
    finally:
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
