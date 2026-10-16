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

import pytest
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


@pytest.fixture(scope='module')
def scene_live_server():
    home=Path(tempfile.mkdtemp(prefix='breadmaker-v41812-browser-'))
    port=_free_port(); env=os.environ.copy(); env['BREADMAKER_HOME']=str(home); env['PYTHONPATH']=str(ROOT/'src')
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
    base=f'http://127.0.0.1:{port}'
    try:
        for _ in range(120):
            try:
                if requests.get(base+'/api/health',timeout=.25).ok:
                    break
            except Exception:
                pass
            time.sleep(.05)
        else:
            raise AssertionError('server did not start')
        yield base
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=5)
        shutil.rmtree(home,ignore_errors=True)


@pytest.fixture(scope='module')
def scene_browser(scene_live_server):
    # Prefer Playwright's managed browser. On macOS the Chromium executable lives
    # inside an .app bundle and must be launched at its real path; symlinking only
    # the inner executable breaks its relative Frameworks lookup.
    pw=sync_playwright().start()
    managed=Path(pw.chromium.executable_path)
    executable=str(managed) if managed.is_file() else next((shutil.which(name) for name in ('chromium','chromium-browser','google-chrome','google-chrome-stable','chrome') if shutil.which(name)),None)
    if not executable:
        pw.stop();pytest.skip('Chromium is not installed')
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=executable)
    # Release verification runs one pytest item per OS process. Avoid synchronous
    # Playwright driver/browser teardown here: on some macOS Chromium builds those
    # calls can wedge after navigation even though the test already completed. The
    # isolated pytest process is the containment/cleanup boundary.
    yield browser


def _new_project(base,name):
    response=requests.post(base+'/api/projects',json={'name':name},timeout=10); response.raise_for_status()
    boot=requests.get(base+'/api/bootstrap',timeout=10).json()
    tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    chapter=next(c for c in boot['chapters'] if c['tab_id']==tab['id'])
    return tab,chapter


def _set_blocks(base,chapter_id,texts):
    current=requests.get(base+f'/api/chapters/{chapter_id}',timeout=10).json()['blocks'][0]
    payload=[]
    for index,text in enumerate(texts):
        payload.append({'id':current['id'] if index==0 else None,'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None})
    response=requests.put(base+f'/api/chapters/{chapter_id}/blocks',json={'blocks':payload},timeout=10); response.raise_for_status()
    return requests.get(base+f'/api/chapters/{chapter_id}',timeout=10).json()['blocks']


def _page(base,browser):
    soup=BeautifulSoup((ROOT/'static/index.html').read_text(encoding='utf-8'),'html.parser')
    for link in soup.find_all('link'): link.decompose()
    for script in soup.find_all('script'): script.decompose()
    style=soup.new_tag('style'); style.string=frontend_styles_source_from_styles_path((ROOT/'static/styles.css')); soup.head.append(style)
    page=browser.new_page(viewport={'width':1400,'height':950}); page.set_default_timeout(5000)
    errors=[]; page.on('pageerror',lambda error: errors.append(str(error)))
    page.set_content(str(soup),wait_until='domcontentloaded')
    page.evaluate("""() => {const makeStore=()=>{const d=new Map();return {getItem:k=>d.has(String(k))?d.get(String(k)):null,setItem:(k,v)=>d.set(String(k),String(v)),removeItem:k=>d.delete(String(k)),clear:()=>d.clear()}};Object.defineProperty(window,'localStorage',{value:makeStore(),configurable:true});Object.defineProperty(window,'sessionStorage',{value:makeStore(),configurable:true});sessionStorage.setItem('breadmaker-client-id','v41812-browser')}""")
    def bridge(payload):
        response=requests.request(str(payload.get('method') or 'GET'),base+str(payload.get('url') or ''),headers={str(k):str(v) for k,v in (payload.get('headers') or {}).items()},data=(payload.get('body') or '').encode('utf-8') if isinstance(payload.get('body'),str) else None,timeout=20)
        return {'status':response.status_code,'body':response.text,'headers':{k.lower():v for k,v in response.headers.items()}}
    page.expose_function('__breadmakerBridge',bridge)
    page.evaluate("""() => {
      const nativeSetInterval=window.setInterval.bind(window),nativeClearInterval=window.clearInterval.bind(window),intervals=new Set();
      window.setInterval=(fn,delay,...args)=>{const id=nativeSetInterval(fn,delay,...args);intervals.add(id);return id};
      window.clearInterval=id=>{intervals.delete(id);nativeClearInterval(id)};
      window.__v41812Browser={pending:0,stopping:false,intervals};
      const stopped=()=>({ok:true,status:204,headers:{get:()=>null},json:async()=>null,text:async()=>''});
      window.fetch=async(url,opts={})=>{if(window.__v41812Browser.stopping)return stopped();const headers={};for(const [k,v] of Object.entries(opts.headers||{}))headers[k]=v;window.__v41812Browser.pending++;try{const result=await window.__breadmakerBridge({url:String(url),method:opts.method||'GET',headers,body:typeof opts.body==='string'?opts.body:null});return {ok:result.status>=200&&result.status<300,status:result.status,headers:{get:n=>result.headers[String(n).toLowerCase()]||null},json:async()=>JSON.parse(result.body||'null'),text:async()=>result.body}}finally{window.__v41812Browser.pending--}};
      window.__v41812Shutdown=async()=>{window.__v41812Browser.stopping=true;for(const id of [...intervals])nativeClearInterval(id);intervals.clear();for(let i=0;i<200&&window.__v41812Browser.pending>0;i++)await new Promise(resolve=>setTimeout(resolve,10));return window.__v41812Browser.pending};
    }""")
    page.add_script_tag(content=frontend_source_from_app_path(ROOT/'static/app.js'))
    page.wait_for_function("document.querySelectorAll('.manuscript-block').length >= 1",timeout=15000)
    # The harness strips index.html scripts before injecting app.js, so the visual
    # data-initial-view bootstrap marker is not a valid readiness signal here.
    # app.js exposes one explicit promise/marker only after boot() has completed all
    # state restoration and its final route/view activation. Wait on that boundary.
    page.wait_for_function("()=>Boolean(window.__breadmakerBootPromise) && document.documentElement.dataset.breadmakerBootReady==='true'",timeout=15000)
    return page,errors


def _select_text(page,index=0):
    page.evaluate("""index => {const prose=document.querySelectorAll('.manuscript-block-wrap:not(.scene-gap-hidden) .manuscript-block')[index];const range=document.createRange();range.selectNodeContents(prose);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));positionSelectionToolbar(range)}""",index)
    page.locator('#selectionToolbar').wait_for(state='visible')


def _select_block_range(page,start,end):
    page.evaluate("""([start,end]) => {const blocks=[...document.querySelectorAll('.manuscript-block-wrap:not(.scene-gap-hidden) .manuscript-block')];const first=blocks[start],last=blocks[end];const range=document.createRange();range.setStart(first,0);range.setEnd(last,last.childNodes.length);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));positionSelectionToolbar(range)}""",[start,end])
    page.locator('#selectionToolbar').wait_for(state='visible')


def _shutdown_page(page):
    # Deliberately no synchronous Playwright close/evaluate here. Each release test
    # item runs in its own pytest process, so OS-process exit is the hard cleanup
    # boundary and cannot consume another test's 10-second budget.
    return


def test_v41812_scene_focus_gap_and_view_feedback(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'Scene focus browser')
    b1=_set_blocks(base,ch1['id'],['Scene one','edge gap','gap in chapter one'])
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    b2=_set_blocks(base,ch2['id'],['gap in chapter two','Scene two'])
    s1=requests.post(base+'/api/scenes',json={'block_ids':[b1[0]['id']]},timeout=10).json()
    s2=requests.post(base+'/api/scenes',json={'block_ids':[b2[1]['id']]},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        # A mixed selection containing one Scene plus adjacent unassigned prose extends that Scene.
        page.locator('#bookViewBtn').click(); page.wait_for_timeout(200)
        _select_block_range(page,0,1)
        scene_action=page.locator('#selectionToolbar [data-selection-action="scene"]')
        assert scene_action.inner_text().strip()=='Add to Scene'
        scene_action.click(); page.wait_for_timeout(550)
        current=requests.get(base+f"/api/scenes/{s1['id']}/manuscript",timeout=10).json()
        assert len(current['blocks'])==2

        # Selecting only removable Scene prose puts Remove from Scene in the primary position.
        _select_text(page,1)
        scene_action=page.locator('#selectionToolbar [data-selection-action="scene"]')
        assert scene_action.inner_text().strip()=='Remove from Scene' and not scene_action.is_disabled()
        scene_action.click(); page.wait_for_timeout(550)
        current=requests.get(base+f"/api/scenes/{s1['id']}/manuscript",timeout=10).json()
        assert len(current['blocks'])==1

        page.locator('#scenesNavMode').click(); page.wait_for_function("document.querySelectorAll('.scene-outline-row').length===2")
        rows=page.locator('.scene-outline-row')
        rows.nth(0).locator('.scene-outline-main').dblclick()
        page.wait_for_function("document.querySelectorAll('.scene-view-boundary').length===2 && document.querySelector('.scene-view-boundary').innerText.includes('Scene')")
        # Once Scene focus is entered, a normal click switches to another Scene without returning to Full Text.
        rows=page.locator('.scene-outline-row'); rows.nth(1).locator('.scene-outline-main').click(); page.wait_for_timeout(400)
        assert page.locator('.scene-view-boundary').count()==2
        assert page.locator('.scene-outline-row.active').count()==1
        assert page.locator('.scene-outline-row.active').get_attribute('data-scene-id')==str(s2['id'])

        # The one unassigned navigator dash represents the whole cross-Chapter span.
        gap=page.locator('.scene-gap-row'); assert gap.count()==1; gap.dblclick(); page.wait_for_timeout(350)
        assert page.locator('.scene-gap-view-boundary').count()==2
        visible=page.locator('#editor .manuscript-block-wrap:not(.scene-gap-hidden)')
        assert visible.count()==3
        assert set(visible.evaluate_all("els=>els.map(e=>e.dataset.chapterId)"))=={str(ch1['id']),str(ch2['id'])}
        assert page.locator('#editor .book-chapter-heading').count()==2

        # View -> Show Chapters also governs focused Scene/gap presentation.
        page.locator('#viewBtn').click(); page.locator('#viewChaptersBtn').click(); page.wait_for_timeout(100)
        assert page.locator('#viewChaptersBtn').get_attribute('aria-checked')=='false'
        assert page.locator('#editor .book-chapter-heading').count()==0
        page.locator('#viewBtn').click()

        # Creating a Scene from unassigned prose forces Show Scenes back on for feedback.
        page.locator('#viewBtn').click(); page.locator('#viewScenesBtn').click(); page.wait_for_timeout(100)
        assert page.locator('#viewScenesBtn').get_attribute('aria-checked')=='false'; page.locator('#viewBtn').click()
        _select_text(page,0)
        assert page.locator('#selectionToolbar [data-selection-action="scene"]').inner_text().strip()=='Scene'
        page.locator('#selectionToolbar [data-selection-action="scene"]').click(); page.wait_for_timeout(650)
        assert page.locator('#viewScenesBtn').get_attribute('aria-checked')=='true'
        assert page.locator('.scene-view-boundary').count()==2
        assert not errors,errors
    finally:
        try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_v41812_chapterless_tab_is_writable_and_styleable(scene_live_server,scene_browser):
    base=scene_live_server; tab,chapter=_new_project(base,'Chapterless browser')
    response=requests.delete(base+f"/api/chapters/{chapter['id']}/permanent",timeout=10); response.raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        assert page.locator('.chapter-outline-row').count()==0
        assert page.locator('#editor .book-chapter-heading').count()==0
        prose=page.locator('.manuscript-block').first; prose.click(); page.keyboard.type('Plain chapterless prose'); page.wait_for_timeout(100)
        _select_text(page,0)
        page.locator('#selectionToolbar [data-selection-action="style"]').click()
        page.locator('#contextMenu button').filter(has_text='H2  Section').click(); page.wait_for_timeout(80)
        assert page.locator('.manuscript-block-wrap').first.get_attribute('data-style')=='heading2'
        _select_text(page,0)
        page.locator('#selectionToolbar [data-selection-action="style"]').click()
        page.locator('#contextMenu button').filter(has_text='Body').click(); page.wait_for_timeout(80)
        assert page.locator('.manuscript-block-wrap').first.get_attribute('data-style')=='body'
        assert page.locator('#editor .book-chapter-heading').count()==0
        page.evaluate('flushSave()'); page.wait_for_timeout(350)
        book=requests.get(base+f"/api/book?tab_id={tab['id']}",timeout=10).json()
        assert len(book['chapters'])==1 and book['chapters'][0]['is_implicit']==1
        assert 'Plain chapterless prose' in book['blocks'][0]['text_content']
        assert book['blocks'][0]['semantic_style']=='body'
        assert not errors,errors
    finally:
        try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
        except Exception: pass
        _shutdown_page(page)
