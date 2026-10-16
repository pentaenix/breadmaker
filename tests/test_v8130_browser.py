from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks

ROOT=Path(__file__).resolve().parents[1]


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def test_v8130_breadreview_opens_directly_into_reviewer_mode(scene_live_server,scene_browser):
    base=scene_live_server
    _tab,chapter=_new_project(base,'8.13 portable browser')
    _set_blocks(base,chapter['id'],['portable alpha beta'])
    review=requests.post(base+'/api/reviews',json={'name':'Portable Review','permission_read':True,'permission_comment':True},timeout=10).json()
    participant=requests.post(base+f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'},timeout=10).json()
    requests.post(base+f"/api/reviews/{review['id']}/start",timeout=10).raise_for_status()
    exported=requests.get(base+f"/api/reviews/{review['id']}/participants/{participant['id']}/breadreview",timeout=10);exported.raise_for_status()
    temp=Path(tempfile.mkdtemp(prefix='breadreview-browser-'));package=temp/'rebeca.breadreview';package.write_bytes(exported.content)
    port=_free_port();env=os.environ.copy();env['BREADMAKER_PORTABLE_REVIEW']=str(package);env['BREADMAKER_INSTANCE_ID']='v8130-portable';env['BREADMAKER_HOME']=str(temp/'unused-home');env['PYTHONPATH']=str(ROOT/'src')
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    portable=f'http://127.0.0.1:{port}'
    page=None
    try:
        for _ in range(80):
            try:
                if requests.get(portable+'/api/health',timeout=.2).ok:break
            except Exception:pass
            time.sleep(.04)
        else:raise AssertionError('portable server did not start')
        soup=BeautifulSoup((ROOT/'static/index.html').read_text(encoding='utf-8'),'html.parser')
        for link in soup.find_all('link'):link.decompose()
        for script in soup.find_all('script'):script.decompose()
        style=soup.new_tag('style');style.string=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'));soup.head.append(style)
        page=scene_browser.new_page(viewport={'width':1200,'height':850});page.set_default_timeout(5000);page.set_content(str(soup),wait_until='domcontentloaded')
        page.evaluate("""() => {const d=new Map();const store={getItem:k=>d.has(String(k))?d.get(String(k)):null,setItem:(k,v)=>d.set(String(k),String(v)),removeItem:k=>d.delete(String(k)),clear:()=>d.clear()};Object.defineProperty(window,'localStorage',{value:store,configurable:true});Object.defineProperty(window,'sessionStorage',{value:store,configurable:true});sessionStorage.setItem('breadmaker-client-id','v8130-portable')}""")
        def bridge(payload):
            response=requests.request(str(payload.get('method') or 'GET'),portable+str(payload.get('url') or ''),headers={str(k):str(v) for k,v in (payload.get('headers') or {}).items()},data=(payload.get('body') or '').encode('utf-8') if isinstance(payload.get('body'),str) else None,timeout=10)
            return {'status':response.status_code,'body':response.text,'headers':{k.lower():v for k,v in response.headers.items()}}
        page.expose_function('__portableBridge',bridge)
        page.evaluate("""() => {window.fetch=async(url,opts={})=>{const headers={};for(const [k,v] of Object.entries(opts.headers||{}))headers[k]=v;const result=await window.__portableBridge({url:String(url),method:opts.method||'GET',headers,body:typeof opts.body==='string'?opts.body:null});return {ok:result.status>=200&&result.status<300,status:result.status,headers:{get:n=>result.headers[String(n).toLowerCase()]||null},json:async()=>JSON.parse(result.body||'null'),text:async()=>result.body}}}""")
        page.add_script_tag(content=frontend_source_from_app_path(ROOT/'static/app.js'))
        page.locator('#reviewerMode').wait_for(state='visible')
        assert 'portable alpha beta' in page.locator('#reviewerPaper').inner_text()
        assert 'Rebeca' in page.locator('#reviewerModeParticipant').inner_text()
        assert page.locator('#app').is_hidden()
        assert page.evaluate("document.body.classList.contains('portable-review-active')") is True
    finally:
        # Release verifier runs this browser test in its own process; leave the page
        # to process teardown rather than racing an in-flight JS fetch during close.
        proc.terminate()
        try:proc.wait(timeout=3)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
        shutil.rmtree(temp,ignore_errors=True)
