from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

import json
import os
import socket
import subprocess
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

APP_ROOT=Path(__file__).resolve().parents[1]

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1",0)); return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    configured_home=os.environ.get("BREADMAKER_DURABLE_E2E_HOME")
    home=Path(configured_home) if configured_home else Path(tempfile.mkdtemp(prefix="breadmaker-e2e-home-"))
    home.mkdir(parents=True,exist_ok=True)
    port=_free_port()
    env=os.environ.copy(); env["BREADMAKER_HOME"]=str(home); env["PYTHONPATH"]=str(APP_ROOT/"src")
    proc=subprocess.Popen([sys.executable,"-m","uvicorn","app:app","--host","127.0.0.1","--port",str(port),"--log-level","warning"],cwd=APP_ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
    base=f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if requests.get(base+"/api/health",timeout=.25).ok: break
            except Exception: pass
            time.sleep(.05)
        else:
            proc.terminate(); proc.wait(timeout=2); raise AssertionError("server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=5)
        finally:
            pass


@pytest.fixture(autouse=True)
def durable_fresh_project(live_server,request):
    """Give every durable workflow a clean active Breadmaker project.

    The module still shares uvicorn + Chromium for speed, but project data and
    project-scoped UI state must never leak from one historical contract into the
    next. Tests that need multiple projects can still create them explicitly.
    """
    suffix=next(tempfile._get_candidate_names())
    name=f"Durable {request.node.name[:72]} {suffix}"
    # Use the same synthetic editor identity as the browser page.  Project creation
    # is itself a mutating API request, so omitting the client header can correctly
    # trip the production single-editor lease if a prior page is still unwinding.
    response=requests.post(
        live_server+"/api/projects",
        json={"name":name},
        headers={"X-Breadmaker-Client":"e2e-tab"},
        timeout=4,
    )
    assert response.ok,response.text
    yield


@pytest.fixture(scope="module")
def browser_runtime(live_server, request):
    """Share one Chromium process across the module; isolate each test by page.

    One browser process with explicitly drained pages keeps the test lifecycle
    deterministic. In full verification, prestart its expensive launch before
    individual tests are timed; direct pytest calls keep their original fixture.
    Depending on live_server ensures browser teardown precedes server teardown.
    """
    if os.environ.get("BREADMAKER_DURABLE_VERIFY") == "1":
        # The verifier starts shared Chromium in pytest_sessionstart, outside an
        # individual workflow's hard 15s budget. Preserve the dependency on
        # live_server and close Chromium before the server fixture tears down.
        prepared = getattr(request.config, "_breadmaker_durable_browser", None)
        if prepared is None:
            raise RuntimeError("BREADMAKER_DURABLE_BROWSER_PRESTART_MISSING")
        try:
            yield prepared[1]
        finally:
            from conftest import _close_durable_browser
            _close_durable_browser(request.config)
        return

    pw=sync_playwright().start()
    configured=os.environ.get("BREADMAKER_TEST_BROWSER")
    executable=configured or next((shutil.which(name) for name in ("chromium","chromium-browser","google-chrome","google-chrome-stable","chrome") if shutil.which(name)),None)
    launch_args={"headless":True,"args":["--no-sandbox"]}
    if executable: launch_args["executable_path"]=executable
    browser=pw.chromium.launch(**launch_args)
    try:
        yield browser
    finally:
        # The bounded E2E process owns the full Playwright lifecycle. Pages are
        # drained per test; close Chromium first, then stop the driver cleanly.
        try:
            browser.close()
        finally:
            pw.stop()




def _navigator_action(page,label):
    page.locator('#navigatorMenuBtn').click()
    page.locator('#contextMenu button').filter(has_text=label).click()

def _open_project_menu(page):
    page.locator('#brandButton').click()
    page.wait_for_selector('#projectMenu:not(.hidden)')

def _open_project_settings(page):
    _open_project_menu(page)
    page.locator('#projectMenuSettings').click()
    page.wait_for_selector('#modal:not(.hidden)')
    assert page.locator('#modalTitle').inner_text()=='Project Settings'

def _open_project_pdf(page):
    _open_project_menu(page)
    page.locator('#projectMenuExport').click()
    page.wait_for_selector('#projectSubmenu:not(.hidden)')
    page.locator('#projectMenuExportPdf').click()
    page.wait_for_selector('#modal:not(.hidden)')
    assert page.locator('#modalTitle').inner_text()=='Export PDF'

def _focus_first_chapter(page):
    """Enter the first visible Chapter for legacy chapter-specific E2E workflows.

    Fresh Breadmaker correctly falls back to Full Text when there is no remembered
    position or bookmark. Tests that specifically exercise Chapter-only behavior
    must opt into Chapter focus rather than depending on historical startup state.
    """
    return page.evaluate("""async()=>{
      if(!state.chapter){
        const chapter=visibleChapters(state.activeDocumentId)[0];
        if(chapter) await selectChapter(chapter.id,false);
      }
      return state.chapter?.id||null;
    }""")


def _select_manuscript_block_text(page,text):
    """Create an exact Range, then cross Breadmaker's real pointer-release boundary.

    Headless Chromium does not guarantee native word selection from Playwright's
    synthetic dblclick().  The contract we need is Breadmaker's selection lifecycle,
    so establish the browser Range deterministically and let
    showSelectionToolbarAfterPointer() perform the same application-side work as a
    real pointer release.
    """
    result=page.evaluate("""text=>{
      const prose=[...document.querySelectorAll('[data-testid="manuscript-block"]')].find(el=>el.innerText.trim()===text);
      if(!prose)return {ok:false,reason:'missing block'};
      const range=document.createRange();range.selectNodeContents(prose);
      const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
      state.manuscriptPointerSelecting=false;
      showSelectionToolbarAfterPointer();
      return {
        ok:true,
        selected:selection.toString(),
        items:state.selectionToolbarItems?.length||0,
        visible:!document.querySelector('[data-testid="manuscript-selection-toolbar"]')?.classList.contains('hidden')
      };
    }""",text)
    assert result['ok'],result
    assert result['selected'].strip()==text,result
    assert result['items']==1,result
    assert result['visible'],result


def _map_line_client_point_for_position(page,line_id,position):
    """Return a visible client-space point for an exact logical Map Line position.

    Route SVG nodes are render segmentation, not semantic geometry.  Use the same
    Line plan conversion as Breadmaker's drag handler, scroll the target into the Map
    viewport, then transform the exact logical point into client coordinates.
    """
    return page.evaluate("""([lineId,position])=>{
      const svg=document.querySelector('#mapSvg'),stage=document.querySelector('#mapStage');
      const layout=svg?._mapLayout,plan=layout?.linePlans?.get(+lineId),lane=layout?.lineLane?.get(+lineId);
      if(!svg||!stage||!layout||!plan||lane===undefined)throw new Error('Map Line geometry is unavailable');
      const axis=mapLineDisplayAxisAtPosition(plan,position),coord=mapAxisCoordinate(layout,axis,lane),zoom=Math.max(.01,state.mapZoom||1);
      if(layout.orientation==='horizontal')stage.scrollLeft=Math.max(0,coord.x*zoom-stage.clientWidth*.72);
      else stage.scrollTop=Math.max(0,coord.y*zoom-stage.clientHeight*.72);
      const matrix=svg.getScreenCTM();if(!matrix)throw new Error('Map SVG has no screen transform');
      const point=svg.createSVGPoint();point.x=coord.x;point.y=coord.y;
      const client=point.matrixTransform(matrix);
      return {x:client.x,y:client.y,axis,position,viewportWidth:innerWidth,viewportHeight:innerHeight};
    }""",[line_id,position])

def _enable_metro(page):
    """Enable every gate required by the current experimental Metro renderer."""
    page.evaluate("""()=>{
      state.features.maps_metro_view=true;
      state.mapExperimentalFeatures={...(state.mapExperimentalFeatures||{}),map_renderer:true};
      applyFeatureFlags();
    }""")
    page.wait_for_function("()=>{const button=document.querySelector('#mapMetroViewBtn');return !!button&&!button.classList.contains('hidden')&&!button.disabled}")


_E2E_SHELL_HTML=None
_E2E_APP_JS=None
def _browser_assets():
    global _E2E_SHELL_HTML,_E2E_APP_JS
    if _E2E_SHELL_HTML is None:
        html=(APP_ROOT/"static/index.html").read_text(encoding="utf-8")
        css=frontend_styles_source_from_styles_path((APP_ROOT/"static/styles.css"))
        _E2E_APP_JS=frontend_source_from_app_path(APP_ROOT/"static/app.js")
        soup=BeautifulSoup(html,"html.parser")
        for link in soup.find_all("link"): link.decompose()
        for script in soup.find_all("script"): script.decompose()
        style=soup.new_tag("style");style.string=css;soup.head.append(style)
        _E2E_SHELL_HTML=str(soup)
    return _E2E_SHELL_HTML,_E2E_APP_JS

def _browser_page(base,browser):
    shell_html,js=_browser_assets()
    page=browser.new_page(viewport={"width":1440,"height":1000})
    page.set_default_timeout(5000)
    console=[]; errors=[]
    page.on("console",lambda msg: console.append((msg.type,msg.text)))
    page.on("pageerror",lambda err: errors.append(str(err)))
    page.set_content(shell_html,wait_until="domcontentloaded")
    # about:blank has an opaque storage origin in this environment. Breadmaker only
    # needs the Storage interface semantics, so provide deterministic in-memory stores.
    page.evaluate("""
      () => {
        const makeStore=()=>{const d=new Map();return {getItem:k=>d.has(String(k))?d.get(String(k)):null,setItem:(k,v)=>d.set(String(k),String(v)),removeItem:k=>d.delete(String(k)),clear:()=>d.clear()}};
        Object.defineProperty(window,'localStorage',{value:makeStore(),configurable:true});
        Object.defineProperty(window,'sessionStorage',{value:makeStore(),configurable:true});
        sessionStorage.setItem('breadmaker-client-id','e2e-tab');
      }
    """)
    def bridge(payload):
        url=str(payload.get("url") or "")
        method=str(payload.get("method") or "GET")
        headers={str(k):str(v) for k,v in (payload.get("headers") or {}).items()}
        body=payload.get("body")
        response=requests.request(method,base+url,headers=headers,data=body.encode("utf-8") if isinstance(body,str) else None,timeout=20)
        return {"status":response.status_code,"body":response.text,"headers":{k.lower():v for k,v in response.headers.items()}}
    page.expose_function("__breadmakerBridge",bridge)
    page.evaluate("""
      () => {
        // Track application intervals and bridge requests so each test can stop
        // cleanly before Playwright tears down. This avoids leaving a lease
        // heartbeat callback alive while the sync Playwright loop is closing.
        const nativeSetInterval=window.setInterval.bind(window);
        const nativeClearInterval=window.clearInterval.bind(window);
        const intervals=new Set();
        window.setInterval=(fn,delay,...args)=>{const id=nativeSetInterval(fn,delay,...args);intervals.add(id);return id};
        window.clearInterval=id=>{intervals.delete(id);nativeClearInterval(id)};
        window.__breadmakerE2E={pending:0,stopping:false,intervals};
        const stoppedResponse=()=>({ok:true,status:204,headers:{get:()=>null},json:async()=>null,text:async()=>''});
        window.fetch=async (url,opts={})=>{
          if(window.__breadmakerE2E.stopping)return stoppedResponse();
          const headers={}; if(opts.headers){for(const [k,v] of Object.entries(opts.headers))headers[k]=v}
          window.__breadmakerE2E.pending++;
          try{
            const result=await window.__breadmakerBridge({url:String(url),method:opts.method||'GET',headers,body:typeof opts.body==='string'?opts.body:null});
            return {ok:result.status>=200&&result.status<300,status:result.status,headers:{get:n=>result.headers[String(n).toLowerCase()]||null},json:async()=>JSON.parse(result.body||'null'),text:async()=>result.body};
          }finally{window.__breadmakerE2E.pending--}
        };
        window.__breadmakerE2EShutdown=async()=>{
          window.__breadmakerE2E.stopping=true;
          for(const id of [...intervals])nativeClearInterval(id);
          intervals.clear();
          for(let i=0;i<200&&window.__breadmakerE2E.pending>0;i++)await new Promise(resolve=>setTimeout(resolve,10));
          return window.__breadmakerE2E.pending;
        };
      }
    """)
    page.add_script_tag(content=js)
    page.wait_for_function("document.querySelectorAll('.manuscript-block').length >= 1",timeout=15000)
    return page,console,errors


def _shutdown_page(page):
    """Drain page work, then release the synthetic editor lease, then close.

    Ordering matters: releasing the lease while the heartbeat interval is still live
    creates a race where that interval can immediately reclaim it.  The bridge is
    intentionally used after shutdown because the test fetch shim stops forwarding
    ordinary browser requests once cleanup begins.
    """
    if page.is_closed():
        return
    pending=page.evaluate("window.__breadmakerE2EShutdown ? window.__breadmakerE2EShutdown() : 0")
    assert pending==0, f"E2E bridge did not drain before shutdown: {pending} request(s)"
    released=page.evaluate("""async()=>await window.__breadmakerBridge({
      url:'/api/lease/release',method:'POST',
      headers:{'X-Breadmaker-Client':'e2e-tab'},body:null
    })""")
    assert 200 <= int(released.get('status',0)) < 300, released
    page.close()


def test_browser_writing_undo_find_and_whole_book(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        first=page.locator('.manuscript-block').first
        first.click()
        page.keyboard.type('Alpha')
        page.keyboard.press('Enter')
        page.keyboard.type('Beta')
        page.wait_for_timeout(900)
        assert page.locator('.manuscript-block').count()>=2
        assert 'Beta' in page.locator('.manuscript-block').nth(1).inner_text()

        # Real working-session text Undo/Redo, including keyboard shortcuts.
        page.keyboard.type(' XYZ')
        page.wait_for_timeout(50)
        page.keyboard.press('Control+z')
        page.wait_for_function("!document.querySelectorAll('.manuscript-block')[1].innerText.includes('XYZ')", timeout=5000)
        assert 'XYZ' not in page.locator('.manuscript-block').nth(1).inner_text()
        page.keyboard.press('Control+y')
        page.wait_for_function("document.querySelectorAll('.manuscript-block')[1].innerText.includes('XYZ')", timeout=5000)
        assert 'XYZ' in page.locator('.manuscript-block').nth(1).inner_text()

        # Native browser selection spans distinct stable paragraph blocks.
        selected=page.evaluate("""
          () => {const blocks=[...document.querySelectorAll('.manuscript-block')];const r=document.createRange();r.setStart(blocks[0].firstChild||blocks[0],0);const last=blocks[1];r.setEnd(last.lastChild||last,(last.lastChild?.textContent||last.textContent).length);const s=getSelection();s.removeAllRanges();s.addRange(r);return selectionItems().length}
        """)
        assert selected>=2

        # Add a second chapter through the actual UI, then confirm Book can
        # hold one selection across chapter boundaries.
        _navigator_action(page,'Add Chapter'); page.wait_for_timeout(250)
        page.locator('.manuscript-block').first.click(); page.keyboard.type('Second chapter prose'); page.wait_for_timeout(850)
        page.locator('#bookViewBtn').click(); page.wait_for_timeout(400)
        chapter_ids=page.locator('.manuscript-block-wrap').evaluate_all("els=>[...new Set(els.map(e=>e.dataset.chapterId))]")
        assert len(chapter_ids)>=2
        cross=page.evaluate("""
          () => {const b=[...document.querySelectorAll('.manuscript-block')].filter(x=>x.textContent.length);const r=document.createRange();r.setStart(b[0].firstChild||b[0],0);const z=b[b.length-1];r.setEnd(z.lastChild||z,(z.lastChild?.textContent||z.textContent).length);const s=getSelection();s.removeAllRanges();s.addRange(r);return {items:selectionItems().length,chapters:new Set(selectionItems().map(x=>x.chapter_id)).size}}
        """)
        assert cross["items"]>=2 and cross["chapters"]>=2

        # Google-Docs-style Find stays on top of the manuscript and navigates hits.
        page.locator('#viewBtn').click(); page.locator('#viewFindReplaceBtn').click(); page.locator('#findInput').fill('chapter'); page.wait_for_timeout(250)
        counter=page.locator('#findCounter').inner_text(); assert '/' in counter and not counter.startswith('0 / 0')
        page.locator('#findNext').click(); page.wait_for_timeout(100)
        assert not page.locator('#findPopover').get_attribute('class').endswith('hidden')
        # 4.17.6: Find uses the same centered SVG chevron state language as View.
        expand=page.locator('#findExpand'); chevron=expand.locator('svg.find-menu-chevron')
        assert expand.get_attribute('aria-expanded')=='false'
        centers=page.evaluate("() => {const b=document.querySelector('#findExpand').getBoundingClientRect(),s=document.querySelector('#findExpand svg').getBoundingClientRect();return {button:b.top+b.height/2,svg:s.top+s.height/2}}")
        assert abs(centers['button']-centers['svg'])<1
        expand.click(); assert expand.get_attribute('aria-expanded')=='true'; assert page.locator('#replaceRow').is_visible()
        transform=chevron.evaluate("el=>getComputedStyle(el).transform"); assert transform!='none'
        expand.click(); assert expand.get_attribute('aria-expanded')=='false'; assert not page.locator('#replaceRow').is_visible()
        page.locator('#findClose').click()

        # No unexpected browser errors during the writing / whole-book path.
        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_canvas_tasks_maps_and_project_center(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        # Canvas empty state -> create -> nested child -> node side panel.
        page.locator('[data-view="canvas"]').click(); page.wait_for_timeout(150)
        assert page.locator('#canvasEmpty').is_visible()
        page.locator('#emptyCreateCanvas').click(); page.locator('#newCanvasName').fill('Cinna'); page.locator('#createCanvasConfirm').click()
        page.locator('.canvas-item').filter(has_text='Cinna').wait_for(state='visible',timeout=5000)
        assert page.locator('.canvas-item').filter(has_text='Cinna').count()==1
        cinna=page.locator('.canvas-item').filter(has_text='Cinna')
        cinna.click(button='right'); page.get_by_text('New canvas inside…',exact=True).click(); page.locator('#newCanvasName').fill('Character Arc'); page.locator('#createCanvasConfirm').click()
        child=page.locator('.canvas-item').filter(has_text='Character Arc'); child.wait_for(state='visible',timeout=5000)
        assert child.count()==1
        parent=page.locator('.canvas-item').filter(has_text='Cinna'); toggle=parent.locator('.canvas-tree-toggle'); assert toggle.inner_text() in ('▸','▾')
        # Current Add Card is a compact palette + direct-draw tool, not the retired side-panel form.
        page.locator('#addNodePanelBtn').click(); palette=page.locator('#canvasCardPalette'); palette.wait_for(state='visible')
        assert page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')")
        palette.locator('[data-canvas-card-shape="rect"]').click()
        stage=page.locator('#canvasStage'); stage_box=stage.bounding_box(); assert stage_box
        x1,y1=stage_box['x']+260,stage_box['y']+210
        page.mouse.move(x1,y1); page.mouse.down(); page.mouse.move(x1+220,y1+130,steps=6); page.mouse.up()
        page.wait_for_function("()=>state.canvasNodes.length>=1")
        assert page.locator('.canvas-node').count()>=1

        # Tasks and Maps are independent workspaces, and project management stays
        # behind the Breadmaker brand rather than becoming another permanent tab.
        page.locator('[data-view="tasks"]').click(); page.locator('#newTaskBtn').click(); page.locator('#taskName').fill('Fix kingdom explanation'); page.locator('#saveTask').click()
        page.locator('.task-row').filter(has_text='Fix kingdom explanation').wait_for(state='visible',timeout=5000)
        assert page.locator('.task-row').filter(has_text='Fix kingdom explanation').count()==1
        page.locator('[data-view="maps"]').click(); page.locator('#newMapBtn').click()
        page.locator('#contextMenu button').filter(has_text='Add Map').click(); page.wait_for_selector('#dialogText')
        page.locator('#dialogText').fill('Main Story'); page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.maps.some(map=>map.name==='Main Story')",timeout=5000)
        page.evaluate("async()=>{const map=state.currentMap||state.maps.find(m=>m.name==='Main Story');await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Main Plot'})});await selectMap(map.id,{reload:true})}");
        page.locator('.map-tree-line-row').filter(has_text='Main Plot').wait_for(state='visible',timeout=5000)
        assert page.locator('.map-tree-line-row').filter(has_text='Main Plot').count()==1
        _open_project_menu(page); assert page.locator('#projectMenuSettings').is_visible(); assert page.locator('#projectMenuSwitch').is_visible(); assert page.locator('#projectModal').count()==0


        # No unexpected browser errors during the Canvas / workspace path.
        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_structural_action_rolls_back_as_one_unit(live_server,browser_runtime):
    """A compound UI action must not strand its earlier API writes if a later step fails."""
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        before=requests.get(live_server+'/api/tasks',timeout=5).json()
        result=page.evaluate("""
          async () => {
            try {
              await withStructuralUndo('Injected compound failure', async()=>{
                await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Must roll back',description:'',status:'open'})});
                throw new Error('injected-test-failure');
              },{view:'tasks'});
              return 'unexpected-success';
            } catch (error) {
              return String(error.message||error);
            }
          }
        """)
        assert 'injected-test-failure' in result
        after=requests.get(live_server+'/api/tasks',timeout=5).json()
        assert [t['id'] for t in after]==[t['id'] for t in before]
        # The expected injected exception is caught by the UI helper; rollback itself
        # must not produce a page error or a structural rollback diagnostic.
        assert not errors, errors
        assert not [text for typ,text in console if typ=='error' and 'STRUCTURAL_ROLLBACK_001' in text]
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v41_task_draft_survives_nested_marker_and_later_is_reversible(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.locator('[data-view="tasks"]').click()
        page.locator('#newTaskBtn').click()
        page.locator('#taskName').fill('Preserve this draft')
        page.locator('#taskDescription').fill('This description must survive creating a marker.')

        page.locator('#taskCreateMarker').click()
        assert page.locator('#markerName').is_visible()
        page.locator('#markerName').fill('Continuity')
        page.locator('#createMarkerConfirm').click()
        page.wait_for_timeout(250)

        # Parent modal DOM is restored, not reconstructed from persisted task data.
        assert page.locator('#taskName').input_value()=='Preserve this draft'
        assert page.locator('#taskDescription').input_value()=='This description must survive creating a marker.'
        assert page.locator('#taskMarker option:checked').inner_text()=='Continuity'
        page.locator('#saveTask').click(); page.wait_for_timeout(250)

        row=page.locator('.task-row').filter(has_text='Preserve this draft')
        assert row.count()==1
        # The row itself opens the task; no title-only Open control is required.
        row.locator('.task-title').click()
        assert page.locator('#taskName').input_value()=='Preserve this draft'
        page.locator('#taskStatus').select_option('later')
        page.locator('#saveTask').click(); page.wait_for_timeout(250)

        page.locator('[data-task-filter="later"]').click(); page.wait_for_timeout(150)
        later=page.locator('.task-row').filter(has_text='Preserve this draft')
        assert later.count()==1
        later.locator('[data-task-more]').click()
        page.get_by_text('Move to Open',exact=True).click(); page.wait_for_timeout(250)
        page.locator('[data-task-filter="open"]').click(); page.wait_for_timeout(150)
        assert page.locator('.task-row').filter(has_text='Preserve this draft').count()==1

        # Focus exits through the same toolbar control (renamed Unfocus) or Esc.
        page.locator('[data-view="manuscript"]').click()
        page.locator('#viewBtn').click();page.locator('#viewFocusBtn').click()
        assert page.locator('body').evaluate("el=>el.classList.contains('focus')") is True
        page.locator('#viewBtn').click();page.locator('#viewFocusBtn').click()
        assert page.locator('body').evaluate("el=>el.classList.contains('focus')") is False

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v41_canvas_connector_is_a_real_selectable_object(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""
          async () => {
            const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Connector UX',canvas_type:'blank'})});
            const a=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Start',x:100,y:120,w:220,h:120})});
            const b=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'End',x:520,y:310,w:220,h:120})});
            const edge=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:b.id,from_port:'bottom_right',to_port:'top_left'})});
            await refresh(false); setView('canvas'); await selectCanvas(cv.id);
            return {canvas:cv.id,edge:edge.id};
          }
        """)
        page.wait_for_selector('.edge-hit')

        # Click the actual SVG stroke midpoint, not a synthetic handler call.
        point=page.evaluate("""
          () => {
            const path=document.querySelector('.edge-hit');
            const p=path.getPointAtLength(path.getTotalLength()/2),m=path.getScreenCTM();
            return {x:m.a*p.x+m.c*p.y+m.e,y:m.b*p.x+m.d*p.y+m.f};
          }
        """)
        page.mouse.click(point['x'],point['y']); page.wait_for_timeout(120)
        assert page.evaluate('(id)=>state.selectedEdgeId===id',created['edge'])
        assert page.locator('#canvasPanelTitle').inner_text()=='Connector'

        # Current connector inspector uses compact autosaving controls; endpoint
        # retargeting itself is a direct-manipulation gesture.
        assert page.locator('#edgeFromPort').count()==0 and page.locator('#edgeToPort').count()==0
        page.locator('[data-edge-choice="route_style"][data-edge-value="straight"]').click()
        page.locator('[data-edge-choice="line_width"][data-edge-value="4"]').click()
        page.locator('#edgeLabel').fill('Changed after creation')
        page.wait_for_timeout(650)
        saved=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['edges'][0]
        assert saved['route_style']=='straight' and saved['line_width']==4 and saved['label']=='Changed after creation'

        # Selected connector responds to Delete/Backspace when canvas focus is active;
        # editing text in the inspector must keep normal text-field Delete behavior.
        point=page.evaluate("""
          () => {const path=document.querySelector('.edge-hit');const p=path.getPointAtLength(path.getTotalLength()/2),m=path.getScreenCTM();return {x:m.a*p.x+m.c*p.y+m.e,y:m.b*p.x+m.d*p.y+m.f};}
        """)
        page.mouse.click(point['x'],point['y']); page.keyboard.press('Delete'); page.wait_for_timeout(220)
        assert requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['edges']==[]

        # Empty-canvas context menu creates directly at the pointer.
        before=page.locator('.canvas-node').count()
        stage=page.locator('#canvasStage').bounding_box()
        page.mouse.click(stage['x']+900,stage['y']+560,button='right'); page.wait_for_timeout(80)
        page.get_by_text('Add card here',exact=True).click(); page.wait_for_timeout(220)
        assert page.locator('.canvas-node').count()==before+1

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)



def test_browser_v41_split_view_uses_native_spatial_renderers(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""
          async () => {
            const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Split Spatial',canvas_type:'blank'})});
            const a=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Near',x:80,y:100,w:220,h:120})});
            const b=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Far',x:1320,y:500,w:220,h:120})});
            await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:b.id,from_port:'bottom_right',to_port:'top_left'})});
            const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Split Map',description:'Native split renderer'})});
            const line=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Main Story'})});
            await api(`/api/map-lines/${line.id}/items`,{method:'POST',body:JSON.stringify({title:'Planned beat',planned_start:.4,schematic_start:.4})});
            await refresh(false);
            return {canvas:cv.id,map:map.id};
          }
        """)

        page.evaluate('(id)=>openCanvasSplit(id)',created['canvas'])
        page.wait_for_selector('#splitCanvasStage .split-canvas-node')
        assert page.locator('#splitCanvasStage .split-canvas-node').count()==2
        assert page.locator('#splitCanvasEdges .edge-line').count()==1
        canvas_metrics=page.evaluate("""
          () => ({
            bodyClient: document.querySelector('#splitBody').clientWidth,
            bodyScroll: document.querySelector('#splitBody').scrollWidth,
            stageWidth: document.querySelector('#splitCanvasStage').getBoundingClientRect().width
          })
        """)
        assert canvas_metrics['stageWidth']>canvas_metrics['bodyClient']
        assert canvas_metrics['bodyScroll']>canvas_metrics['bodyClient']

        page.evaluate('(id)=>openMapSplit(id)',created['map'])
        page.wait_for_selector('#splitMapSvg .map-route',state='attached')
        assert page.locator('#splitMapSvg .map-line-group').count()==1
        assert page.locator('#splitMapSvg .map-stop-outer').count()==1
        assert page.locator('#splitMapSvg .split-card').count()==0

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v42_native_writing_focus_undo_and_find_follows_context(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        setup=page.evaluate("""
          async () => {
            const tab=state.activeDocumentId;
            const a=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'V42 Search One',tab_id:tab})});
            const b=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'V42 Search Two',tab_id:tab})});
            await api(`/api/chapters/${a.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{block_type:'paragraph',semantic_style:'body',content_html:'needlev42 first',text_content:'needlev42 first'}]})});
            await api(`/api/chapters/${b.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{block_type:'paragraph',semantic_style:'body',content_html:'needlev42 second needlev42',text_content:'needlev42 second needlev42'}]})});
            await refresh(false); await selectChapter(a.id,false);
            return {a:a.id,b:b.id};
          }
        """)

        # Breadmaker deliberately does not cancel ordinary manuscript right-click.
        native=page.evaluate("""
          () => {
            const prose=document.querySelector('.manuscript-block');
            const ev=new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:20,clientY:20});
            return prose.dispatchEvent(ev);
          }
        """)
        assert native is True

        # Selection reveals Breadmaker tools without replacing the browser context menu.
        page.evaluate("""
          () => {
            const prose=document.querySelector('.manuscript-block'),node=prose.firstChild||prose;
            const r=document.createRange();r.setStart(node,0);r.setEnd(node,Math.min(8,node.textContent.length));
            const s=getSelection();s.removeAllRanges();s.addRange(r);
          }
        """)
        page.wait_for_selector('#selectionToolbar:not(.hidden)')
        before_scene_count=page.evaluate('state.scenes.length')
        page.locator('[data-selection-action="scene"]').click()
        page.wait_for_function('(n)=>state.scenes.length===n+1',arg=before_scene_count)
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')") is True

        # Focus is a writing mode: navigation disappears, writing controls remain.
        page.locator('#viewBtn').click();page.locator('#viewFocusBtn').click()
        assert page.locator('body').evaluate("el=>el.classList.contains('focus')") is True
        assert page.locator('.document-toolbar').is_visible()
        assert page.locator('#blockStyleBtn').count()==0
        assert page.locator('#markupBtn').is_visible()
        assert page.locator('#viewBtn').is_visible()

        # Stress the exact Cmd+Z path that previously hung. The command queue must
        # serialize the requests and settle without page errors or a stuck busy flag.
        prose=page.locator('.manuscript-block').first
        prose.click(); page.keyboard.press('End'); page.keyboard.type(' undo-one')
        page.wait_for_timeout(900)
        page.keyboard.type(' undo-two')
        page.wait_for_timeout(900)
        for _ in range(8):
            page.keyboard.press('Meta+z')
        page.evaluate("state.historyChain.then(()=>true)")
        assert page.evaluate('state.historyBusy') is False
        assert page.locator('.document-toolbar').is_visible()
        assert page.locator('body').evaluate("el=>el.classList.contains('focus')") is True
        page.keyboard.press('Escape')
        assert page.locator('body').evaluate("el=>el.classList.contains('focus')") is False

        # Find re-runs automatically when manuscript context changes.
        awaitable=page.evaluate("(id)=>selectChapter(id)", setup['a'])
        page.locator('#viewBtn').click(); page.locator('#viewFindReplaceBtn').click(); page.locator('#findInput').fill('needlev42')
        page.wait_for_function("document.querySelector('#findCounter').textContent.trim()==='1 / 1'")
        page.locator(f'.chapter-row[data-id="{setup["b"]}"]').click()
        page.wait_for_function("document.querySelector('#findCounter').textContent.trim()==='1 / 2'")
        assert page.locator('#findScopeLabel').inner_text()=='Chapter'
        page.locator('#bookViewBtn').click()
        page.wait_for_function("document.querySelector('#findScopeLabel').textContent.trim()==='Book'")
        page.wait_for_function("document.querySelector('#findCounter').textContent.trim()==='1 / 3'")

        # Narrow windows keep one toolbar row; View may scroll horizontally,
        # but it never increases the writing toolbar height.
        page.set_viewport_size({'width':680,'height':900}); page.wait_for_timeout(100)
        metrics=page.evaluate("""
          () => {const t=document.querySelector('.document-toolbar'),b=document.querySelector('#viewBtn');
            return {height:t.getBoundingClientRect().height,scrollHeight:t.scrollHeight,buttonHeight:b.getBoundingClientRect().height,text:b.textContent.trim()};}
        """)
        assert metrics['height']==43
        assert metrics['scrollHeight']<=43
        assert metrics['buttonHeight']<=31
        assert metrics['text'].startswith('View')

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v42_connector_endpoint_style_retarget_and_drag_create(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""
          async () => {
            const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'V42 Connector Reliability',canvas_type:'blank'})});
            const a=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Start',x:100,y:120,w:220,h:120})});
            const b=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Middle',x:520,y:140,w:220,h:120})});
            const c=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Retarget',x:520,y:390,w:220,h:120})});
            const edge=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:b.id,from_port:'right',to_port:'left'})});
            await refresh(false); setView('canvas'); await selectCanvas(cv.id);
            return {canvas:cv.id,a:a.id,b:b.id,c:c.id,edge:edge.id};
          }
        """)
        page.wait_for_selector('.edge-end-hit')

        # Endpoint/arrow-area hit geometry must select the connector, not merely a
        # lucky click near the middle of its path.
        page.evaluate("""edgeId=>{
          const endpoint=[...document.querySelectorAll(`g[data-edge-id="${edgeId}"] .edge-end-hit`)][1],r=endpoint.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
          endpoint.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,buttons:1,clientX:x,clientY:y,pointerId:31,isPrimary:true}));
          document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,button:0,buttons:0,clientX:x,clientY:y,pointerId:31,isPrimary:true}));
        }""",created['edge'])
        selected=page.evaluate('(id)=>({expected:id,selected:state.selectedEdgeId})',created['edge'])
        assert selected['selected']==selected['expected'], selected
        assert page.locator('#canvasPanelTitle').inner_text()=='Connector'

        assert page.locator('#edgeToNode').count()==0 and page.locator('#edgeToPort').count()==0
        page.locator('[data-edge-choice="line_style"][data-edge-value="dashed"]').click()
        page.locator('[data-edge-choice="end_style"][data-edge-value="diamond"]').click()
        page.locator('[data-edge-choice="line_width"][data-edge-value="4"]').click()
        page.locator('#edgeColorChoice').select_option('custom')
        page.locator('#edgeColorCustom').fill('#223344'); page.locator('#edgeColorCustom').dispatch_event('change')
        page.wait_for_timeout(650)
        saved=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['edges'][0]
        assert saved['to_node_id']==created['b']
        assert saved['color']=='#223344'
        assert saved['line_style']=='dashed' and saved['end_style']=='diamond' and saved['line_width']==4

        # Delete through the selected connector's own inspector.
        page.wait_for_selector('.edge-end-hit')
        page.evaluate("""edgeId=>{
          const endpoint=[...document.querySelectorAll(`g[data-edge-id="${edgeId}"] .edge-end-hit`)][1],r=endpoint.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
          endpoint.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,buttons:1,clientX:x,clientY:y,pointerId:32,isPrimary:true}));
          document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,button:0,buttons:0,clientX:x,clientY:y,pointerId:32,isPrimary:true}));
        }""",created['edge'])
        assert page.evaluate('(id)=>state.selectedEdgeId===id',created['edge'])
        page.locator('#deleteEdge').click(); page.wait_for_timeout(180)
        assert requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['edges']==[]

        # Releasing a connector on empty Canvas is intentionally a no-op.
        # Connections are only created by dropping onto another node.
        before_nodes=page.locator('.canvas-node').count()
        node=page.locator(f'.canvas-node[data-id="{created["a"]}"]')
        node.hover(); port=node.locator('.node-port.right'); box=port.bounding_box(); stage=page.locator('#canvasStage').bounding_box()
        start=(box['x']+box['width']/2,box['y']+box['height']/2)
        end=(stage['x']+min(stage['width']-90,900),stage['y']+min(stage['height']-90,560))
        page.mouse.move(*start); page.mouse.down(); page.mouse.move(*end,steps=8); page.mouse.up(); page.wait_for_timeout(180)
        assert page.locator('.canvas-node').count()==before_nodes
        edges=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['edges']
        assert edges==[]

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v421_canvas_pan_resize_drag_target_cancel_and_autosave(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""
          async () => {
            const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'V421 Canvas',canvas_type:'blank'})});
            const a=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Start',x:100,y:120,w:180,h:60,body:'Short'})});
            const b=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Target B',x:470,y:130,w:180,h:80})});
            const c=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Target C',x:700,y:380,w:180,h:80})});
            const edge=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:b.id,from_port:'right',to_port:'left'})});
            await refresh(false);setView('canvas');await selectCanvas(cv.id);
            return {canvas:cv.id,a:a.id,b:b.id,c:c.id,edge:edge.id};
          }
        """)
        page.wait_for_selector('.canvas-node')

        # Text nodes grow beyond their stored minimum height when prose needs room.
        body=page.locator(f'.canvas-node[data-id="{created["a"]}"] .node-body')
        body.fill('This is enough canvas prose to wrap across several lines. ' * 10)
        page.wait_for_timeout(500)
        dimensions=page.locator(f'.canvas-node[data-id="{created["a"]}"]').evaluate("el=>({h:el.offsetHeight,w:el.offsetWidth})")
        assert dimensions['h'] > 60

        # Resize is a first-class persisted gesture, independent of content growth.
        # A deliberately large configured node font can make long nodes taller than
        # the initial viewport, so pan the Canvas until its bottom-right affordance is
        # visible before exercising the resize gesture.
        node=page.locator(f'.canvas-node[data-id="{created["a"]}"]')
        page.locator('#canvasStage').evaluate("(stage,id)=>{const n=document.querySelector(`.canvas-node[data-id=\"${id}\"]`);stage.scrollTop=Math.max(0,n.offsetTop+n.offsetHeight-stage.clientHeight+40)}",created['a'])
        node.hover()
        handle=node.locator('.node-resize-handle');box=handle.bounding_box()
        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+110,box['y']+90,steps=7);page.mouse.up();page.wait_for_timeout(350)
        saved_nodes=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['nodes']
        saved_a=next(n for n in saved_nodes if n['id']==created['a'])
        assert saved_a['w'] > 180 and saved_a['h'] > 60

        # Wheel is viewport zoom in the current Canvas model and must not alter
        # world-space Card geometry.
        stage=page.locator('#canvasStage');stage_box=stage.bounding_box();page.mouse.move(stage_box['x']+stage_box['width']*.7,stage_box['y']+stage_box['height']*.7)
        before_zoom=page.evaluate('state.canvasZoom'); before_world=page.evaluate('(id)=>{const n=state.canvasNodes.find(x=>x.id===id);return[n.x,n.y,n.w,n.h]}',created['a'])
        page.mouse.wheel(0,190);page.wait_for_timeout(100)
        assert page.evaluate('state.canvasZoom') != before_zoom
        assert page.evaluate('(id)=>{const n=state.canvasNodes.find(x=>x.id===id);return[n.x,n.y,n.w,n.h]}',created['a'])==before_world

        # Hand mode: plain drag on empty canvas pans; Pointer mode owns marquee selection.
        stage.evaluate('el=>{el.scrollLeft=0;el.scrollTop=0}')
        start=(stage_box['x']+stage_box['width']*.82,stage_box['y']+stage_box['height']*.78)
        end=(start[0]-220,start[1]-140)
        page.mouse.move(*start);page.mouse.down();page.mouse.move(*end,steps=8);page.mouse.up();page.wait_for_timeout(80)
        drag_scroll=stage.evaluate('el=>({left:el.scrollLeft,top:el.scrollTop})')
        assert drag_scroll['left']>0 and drag_scroll['top']>0
        stage.evaluate('el=>{el.scrollLeft=0;el.scrollTop=0}')

        # Retargeting is owned by the concrete endpoint handle. Clicking the line body
        # selects a Connector; it no longer starts a target drag.
        page.wait_for_selector(f'g[data-edge-id="{created["edge"]}"] .edge-end-hit')
        page.evaluate("""({edgeId,targetId})=>{
          const endpoint=[...document.querySelectorAll(`g[data-edge-id="${edgeId}"] .edge-end-hit`)][1],target=document.querySelector(`.canvas-node[data-id="${targetId}"]`);
          const a=endpoint.getBoundingClientRect(),b=target.getBoundingClientRect(),sx=a.x+a.width/2,sy=a.y+a.height/2,tx=b.x+b.width/2,ty=b.y+b.height/2;
          endpoint.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,buttons:1,clientX:sx,clientY:sy,pointerId:41,isPrimary:true}));
          document.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,button:0,buttons:1,clientX:tx,clientY:ty,pointerId:41,isPrimary:true}));
          document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,button:0,buttons:0,clientX:tx,clientY:ty,pointerId:41,isPrimary:true}));
        }""",{'edgeId':created['edge'],'targetId':created['c']})
        page.evaluate("()=>state.edgeSaveChain.then(()=>true)")
        edge=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['edges'][0]
        assert edge['to_node_id']==created['c']

        # Esc cancels an in-progress endpoint drag with no persisted retarget.
        page.evaluate("""({edgeId,targetId})=>{
          const endpoint=[...document.querySelectorAll(`g[data-edge-id="${edgeId}"] .edge-end-hit`)][1],target=document.querySelector(`.canvas-node[data-id="${targetId}"]`);
          const a=endpoint.getBoundingClientRect(),b=target.getBoundingClientRect(),sx=a.x+a.width/2,sy=a.y+a.height/2,tx=b.x+b.width/2,ty=b.y+b.height/2;
          endpoint.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,buttons:1,clientX:sx,clientY:sy,pointerId:42,isPrimary:true}));
          document.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,button:0,buttons:1,clientX:tx,clientY:ty,pointerId:42,isPrimary:true}));
        }""",{'edgeId':created['edge'],'targetId':created['b']})
        page.keyboard.press('Escape'); page.wait_for_timeout(40)
        edge=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['edges'][0]
        assert edge['to_node_id']==created['c']

        # Connector inspector has no Save button; edits persist automatically.
        midpoint=page.evaluate("""
          () => {const p=document.querySelector('.edge-hit'),q=p.getPointAtLength(p.getTotalLength()/2),m=p.getScreenCTM();return{x:m.a*q.x+m.c*q.y+m.e,y:m.b*q.x+m.d*q.y+m.f}}
        """)
        page.mouse.click(midpoint['x'],midpoint['y']);page.wait_for_timeout(100)
        assert page.locator('#saveEdge').count()==0
        page.locator('[data-edge-choice="line_style"][data-edge-value="dotted"]').click();page.locator('#edgeLabel').fill('Autosaved connector');page.wait_for_timeout(650)
        edge=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['edges'][0]
        assert edge['line_style']=='dotted' and edge['label']=='Autosaved connector'

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v421_focus_import_location_and_archived_task_read_only(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        # Focus uses one control: Focus -> Unfocus, with Esc as the keyboard exit.
        page.locator('#viewBtn').click();page.locator('#viewFocusBtn').click();assert page.locator('body').evaluate("el=>el.classList.contains('focus')") is True;assert page.locator('#focusExitBtn').count()==0
        page.keyboard.press('Escape');assert page.locator('body').evaluate("el=>el.classList.contains('focus')") is False

        # Manuscript import belongs to the Breadmaker project menu, not chapter navigation.
        assert page.locator('#importManuscriptBtn').count()==0
        _open_project_menu(page)
        page.locator('#projectMenuImport').click();page.wait_for_selector('#projectSubmenu:not(.hidden)')
        assert page.locator('#projectMenuImportManuscript').is_visible()
        assert page.locator('#importInput').count()==1
        page.locator('#brandButton').click()

        # Archiving a Done task remembers Done, then locks the task until Unarchive.
        task=page.evaluate("""async()=>{
          const t=await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Immutable archive',description:'Keep this',status:'done'})});
          await api(`/api/tasks/${t.id}`,{method:'PATCH',body:JSON.stringify({status:'archived'})});
          return t;
        }""")
        page.evaluate("async()=>{await refresh(false);setView('tasks');state.taskFilter='archived';await loadTasks()}")
        row=page.locator('.task-row').filter(has_text='Immutable archive');row.wait_for(state='visible')
        assert row.locator('.task-check input').is_disabled()
        row.locator('.task-title').click();page.wait_for_selector('#unarchiveTask')
        assert page.locator('#taskName').is_disabled();assert page.locator('#taskDescription').is_disabled();assert page.locator('#saveTask').count()==0
        page.locator('#unarchiveTask').click();page.wait_for_timeout(350)
        saved=next(t for t in requests.get(live_server+'/api/tasks',timeout=5).json() if t['id']==task['id'])
        assert saved['status']=='done' and saved['archived_from_status'] is None

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v422_canvas_modes_multiselect_zoom_and_routing(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""
          async () => {
            const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'V422 Spatial',canvas_type:'blank'})});
            const a=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'One',x:120,y:110,w:150,h:80})});
            const b=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Two',x:390,y:140,w:150,h:80})});
            const c=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Three',x:680,y:260,w:150,h:80})});
            await refresh(false);setView('canvas');await selectCanvas(cv.id);
            return {canvas:cv.id,a:a.id,b:b.id,c:c.id};
          }
        """)
        page.wait_for_selector('.canvas-node')
        assert 'active' in (page.locator('#canvasHandMode').get_attribute('class') or '')

        # Modifier selection works in Hand mode and a group moves as one unit.
        a=page.locator(f'.canvas-node[data-id="{created["a"]}"]')
        b=page.locator(f'.canvas-node[data-id="{created["b"]}"]')
        a.click();b.click(modifiers=['Meta'])
        assert page.evaluate('state.selectedNodes.size')==2
        before=page.evaluate('(ids)=>ids.map(id=>{const n=state.canvasNodes.find(x=>x.id===id);return [n.x,n.y]})',[created['a'],created['b']])
        strip=a.locator('.node-drag-strip');box=strip.bounding_box()
        page.mouse.move(box['x']+20,box['y']+6);page.mouse.down();page.mouse.move(box['x']+100,box['y']+46,steps=6);page.mouse.up();page.wait_for_timeout(250)
        after=page.evaluate('(ids)=>ids.map(id=>{const n=state.canvasNodes.find(x=>x.id===id);return [n.x,n.y]})',[created['a'],created['b']])
        assert round(after[0][0]-before[0][0])==round(after[1][0]-before[1][0])
        assert round(after[0][1]-before[0][1])==round(after[1][1]-before[1][1])

        # Pointer mode owns empty-space marquee selection.
        page.locator('#canvasPointerMode').click()
        assert 'active' in (page.locator('#canvasPointerMode').get_attribute('class') or '')
        stage=page.locator('#canvasStage');sb=stage.bounding_box();stage.evaluate('el=>{el.scrollLeft=0;el.scrollTop=0}')
        page.mouse.move(sb['x']+55,sb['y']+55);page.mouse.down();page.mouse.move(sb['x']+920,sb['y']+420,steps=8);page.mouse.up();page.wait_for_timeout(100)
        assert page.evaluate('state.selectedNodes.size')>=3
        assert page.locator('#canvasPanelTitle').inner_text()=='Selected Cards'

        # Multi-selection uses the same single Background channel as one node.
        page.locator('#multiBackgroundChoice').select_option('custom');page.wait_for_selector('#multiBackgroundCustom:not(.hidden)')
        page.locator('#multiBackgroundCustom').fill('#ddeeff');page.locator('#multiBackgroundCustom').dispatch_event('change');page.wait_for_timeout(450)
        selected_colors=page.evaluate("[...state.selectedNodes].map(id=>state.canvasNodes.find(n=>n.id===id)?.color)")
        selected_markers=page.evaluate("[...state.selectedNodes].map(id=>state.canvasNodes.find(n=>n.id===id)?.label_id)")
        assert selected_colors and all(color=='#ddeeff' for color in selected_colors)
        assert all(marker is None for marker in selected_markers)

        # Space temporarily invokes Hand without changing persistent Pointer mode.
        stage.focus();page.keyboard.down('Space')
        assert 'active' in (page.locator('#canvasHandMode').get_attribute('class') or '')
        page.keyboard.up('Space')
        assert 'active' in (page.locator('#canvasPointerMode').get_attribute('class') or '')

        # Zoom is viewport-only: it never mutates world-space node geometry.
        coords=page.evaluate('(id)=>{const n=state.canvasNodes.find(x=>x.id===id);return [n.x,n.y,n.w,n.h]}',created['a'])
        page.locator('#canvasViewBtn').click();page.wait_for_selector('#canvasViewPopover:not(.hidden)')
        page.locator('#canvasZoomOut').click();page.wait_for_timeout(80)
        assert page.evaluate('state.canvasZoom')<1
        assert page.locator('#canvasZoomValue').inner_text()!='100%'
        page.locator('#canvasZoomValue').click();assert page.locator('#canvasZoomValue').inner_text()=='100%'
        coords2=page.evaluate('(id)=>{const n=state.canvasNodes.find(x=>x.id===id);return [n.x,n.y,n.w,n.h]}',created['a'])
        assert coords==coords2

        # Each canvas remembers its own zoom, viewport and tool mode.
        page.locator('#canvasZoomOut').click();page.wait_for_timeout(80);remembered_zoom=page.evaluate('state.canvasZoom')
        stage.evaluate('el=>{el.scrollLeft=230;el.scrollTop=140}');page.evaluate('()=>persistCurrentCanvasViewport()')
        page.evaluate("async()=>{const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Viewport Other',canvas_type:'blank'})});await refresh(false);await selectCanvas(cv.id)}")
        assert page.evaluate('state.canvasMode')=='hand'
        page.evaluate('(id)=>selectCanvas(id)',created['canvas']);page.wait_for_timeout(180)
        assert page.evaluate('state.canvasMode')=='pointer'
        assert abs(page.evaluate('state.canvasZoom')-remembered_zoom)<0.001
        restored_scroll=stage.evaluate('el=>[el.scrollLeft,el.scrollTop]')
        assert restored_scroll[0]>=200 and restored_scroll[1]>=110

        # Routing points are tiny connector-only diamonds.
        sb=stage.bounding_box();page.mouse.click(sb['x']+600,sb['y']+500,button='right');page.get_by_text('Add routing point',exact=True).click();page.wait_for_timeout(220)
        routing=page.locator('.canvas-node.routing').last;assert routing.count()==1
        rb=routing.bounding_box();assert rb['width']<30 and rb['height']<30
        rid=int(routing.get_attribute('data-id'))

        # Routing endpoints suppress intermediate arrowheads; real nodes terminate them.
        edges=page.evaluate("""async ({cv,a,r,b})=>{
          const e1=await api(`/api/canvases/${cv}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a,to_node_id:r,from_port:'right',to_port:'left',route_style:'straight'})});
          const e2=await api(`/api/canvases/${cv}/edges`,{method:'POST',body:JSON.stringify({from_node_id:r,to_node_id:b,from_port:'right',to_port:'left',route_style:'curved'})});
          const data=await api(`/api/canvases/${cv}`);state.canvasEdges=data.edges;drawEdges();return [e1.id,e2.id];
        }""",{'cv':created['canvas'],'a':created['a'],'r':rid,'b':created['b']})
        assert page.locator('.edge-end-visible').count()==1
        first_path=page.locator(f'g[data-edge-id="{edges[0]}"] .edge-line').get_attribute('d')
        second_path=page.locator(f'g[data-edge-id="{edges[1]}"] .edge-line').get_attribute('d')
        assert ' C' not in first_path and ' L' in first_path
        assert ' C' in second_path

        # Rounded/Straight routing is independent from stroke style and autosaves.
        point=page.evaluate("""(id)=>{const p=document.querySelector(`g[data-edge-id="${id}"] .edge-hit`),q=p.getPointAtLength(p.getTotalLength()/2),m=p.getScreenCTM();return{x:m.a*q.x+m.c*q.y+m.e,y:m.b*q.x+m.d*q.y+m.f}}""",edges[1])
        page.mouse.click(point['x'],point['y']);page.wait_for_selector('[data-edge-choice="route_style"]')
        assert 'active' in (page.locator('[data-edge-choice="route_style"][data-edge-value="curved"]').get_attribute('class') or '')
        page.locator('[data-edge-choice="route_style"][data-edge-value="straight"]').click();page.wait_for_timeout(350)
        saved=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()['edges']
        assert next(e for e in saved if e['id']==edges[1])['route_style']=='straight'

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v433_labels_generated_legend_and_unified_marker_controls(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'V433 Markers',canvas_type:'blank'})});
          const a=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Scene A',x:100,y:100,w:180,h:90})});
          const b=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Scene B',x:520,y:100,w:180,h:90})});
          const route=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'routing',shape:'routing',x:370,y:135,w:18,h:18})});
          await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:a.id,to_node_id:route.id,from_port:'right',to_port:'left'})});
          await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:route.id,to_node_id:b.id,from_port:'right',to_port:'left'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return{cv:cv.id,a:a.id,b:b.id,route:route.id};
        }""")
        # Legend is generated from the two actual reusable concepts: background Markers and Icon Markers.
        page.locator('#canvasViewBtn').click();page.wait_for_selector('#canvasViewPopover:not(.hidden)');page.locator('#canvasLegendToggle').click();page.wait_for_selector('#canvasLegendDrawer:not(.hidden)')
        page.locator('#legendDrawerAdd').click();page.wait_for_selector('#newCanvasColorMarker')
        page.locator('#newCanvasColorMarker').click();page.wait_for_selector('#markerName')
        page.locator('#markerName').fill('Melancholy');page.locator('#markerColorText').fill('#6688aa');page.locator('#createMarkerConfirm').click();page.wait_for_timeout(220)
        page.wait_for_selector('#newCanvasIconMarker');page.locator('#newCanvasIconMarker').click();page.wait_for_selector('#iconMarkerName')
        page.locator('#iconMarkerName').fill('Important');page.locator('#iconMarkerIcon').fill('★');page.locator('#iconMarkerDescription').fill('Important scene');page.locator('#createIconMarkerConfirm').click();page.wait_for_timeout(220)
        assert page.locator('.legend-drawer-entry').filter(has_text='Melancholy').count()==1
        assert page.locator('.legend-drawer-entry').filter(has_text='Important').count()==1
        marker_id=page.evaluate("state.markers.find(m=>m.name==='Melancholy').id")
        icon_id=page.evaluate("state.iconMarkers.find(m=>m.name==='Important').id")

        # One Background field and one Icon field can each select a reusable Marker.
        page.locator(f'.canvas-node[data-id="{created["a"]}"] .node-drag-strip').click();page.wait_for_selector('#nodeBackgroundChoice')
        assert page.locator('#nodeBackgroundChoice').count()==1 and page.locator('#nodeIconChoice').count()==1
        assert page.locator('#nodeColorLegend').count()==0 and page.locator('#nodeIconLegend').count()==0
        page.locator('#nodeBackgroundChoice').select_option(f'marker:{marker_id}');page.wait_for_function('window.__breadmakerE2E.pending===0')
        page.locator('#nodeIconChoice').select_option(f'marker:{icon_id}');page.wait_for_function('window.__breadmakerE2E.pending===0')
        assigned=next(n for n in requests.get(live_server+f"/api/canvases/{created['cv']}",timeout=5).json()['nodes'] if n['id']==created['a'])
        assert assigned['label_id']==marker_id and assigned['icon_marker_id']==icon_id
        assert assigned['color']=='#6688aa' and assigned['icon']=='★'

        # Scene B can use the same concrete color without acquiring the Marker meaning.
        page.locator(f'.canvas-node[data-id="{created["b"]}"] .node-drag-strip').click();page.wait_for_selector('#nodeBackgroundChoice')
        page.locator('#nodeBackgroundChoice').select_option('custom');page.wait_for_selector('#nodeBackgroundCustom:not(.hidden)')
        page.locator('#nodeBackgroundCustom').fill('#6688aa');page.locator('#nodeBackgroundCustom').dispatch_event('change');page.wait_for_function('window.__breadmakerE2E.pending===0')
        raw=next(n for n in requests.get(live_server+f"/api/canvases/{created['cv']}",timeout=5).json()['nodes'] if n['id']==created['b'])
        assert raw['color']=='#6688aa' and raw['label_id'] is None
        color_row=page.locator('.legend-drawer-entry').filter(has_text='Melancholy');assert color_row.locator('.legend-entry-count').inner_text()=='1'

        # The generated Legend filters by meaning, not coincidental visual equality.
        color_row.click();page.wait_for_timeout(80)
        assert 'legend-match' in (page.locator(f'.canvas-node[data-id="{created["a"]}"]').get_attribute('class') or '')
        assert 'legend-match' in (page.locator(f'.canvas-node[data-id="{created["route"]}"]').get_attribute('class') or '')
        assert 'legend-dimmed' in (page.locator(f'.canvas-node[data-id="{created["b"]}"]').get_attribute('class') or '')

        # Marker edits update only nodes actually using that Marker.
        color_row=page.locator('.legend-drawer-entry').filter(has_text='Melancholy');color_row.locator('.legend-drawer-config').click();page.get_by_text('Configure marker…',exact=True).click();page.wait_for_selector('#markerColorText');page.locator('#markerColorText').fill('#334455');page.locator('#saveMarkerConfirm').click();page.wait_for_timeout(260)
        icon_row=page.locator('.legend-drawer-entry').filter(has_text='Important');icon_row.locator('.legend-drawer-config').click();page.get_by_text('Configure icon marker…',exact=True).click();page.wait_for_selector('#iconMarkerIcon');page.locator('#iconMarkerIcon').fill('!');page.locator('#saveIconMarkerConfirm').click();page.wait_for_timeout(220)
        data=requests.get(live_server+f"/api/canvases/{created['cv']}",timeout=5).json()['nodes']
        node_a=next(n for n in data if n['id']==created['a']);node_b=next(n for n in data if n['id']==created['b'])
        assert node_a['color']=='#334455' and node_a['label_id']==marker_id
        assert node_a['icon']=='!' and node_a['icon_marker_id']==icon_id
        assert node_b['color']=='#6688aa' and node_b['label_id'] is None

        # Choosing a raw value through the same fields detaches reusable meaning.
        page.locator(f'.canvas-node[data-id="{created["a"]}"] .node-drag-strip').click();page.wait_for_selector('#nodeBackgroundChoice')
        page.locator('#nodeBackgroundChoice').select_option('custom');page.wait_for_selector('#nodeBackgroundCustom:not(.hidden)');page.locator('#nodeBackgroundCustom').fill('#556677');page.locator('#nodeBackgroundCustom').dispatch_event('change')
        page.locator('#nodeIconChoice').select_option('custom');page.wait_for_selector('#nodeIconCustom:not(.hidden)');page.locator('#nodeIconCustom').fill('?');page.locator('#nodeIconCustom').dispatch_event('change');page.wait_for_function('window.__breadmakerE2E.pending===0')
        detached=next(n for n in requests.get(live_server+f"/api/canvases/{created['cv']}",timeout=5).json()['nodes'] if n['id']==created['a'])
        assert detached['label_id'] is None and detached['icon_marker_id'] is None
        assert detached['color']=='#556677' and detached['icon']=='?'

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v433_connector_creation_is_one_undo_transaction(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Undo transaction',canvas_type:'blank'})});
          const source=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Source',x:100,y:100,w:180,h:90})});
          const target=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Target',x:520,y:240,w:180,h:90})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);return{cv:cv.id,source:source.id,target:target.id};
        }""")
        before=page.evaluate('()=>({nodes:state.canvasNodes.length,edges:state.canvasEdges.length})')
        page.evaluate("([a,b])=>makeEdge(a,b,'right','left')",[created['source'],created['target']])
        page.wait_for_function('(before)=>state.canvasEdges.length===before.edges+1',arg=before)
        page.keyboard.press('Control+z')
        page.wait_for_function('(before)=>state.canvasNodes.length===before.nodes&&state.canvasEdges.length===before.edges',arg=before)
        restored=requests.get(live_server+f"/api/canvases/{created['cv']}",timeout=5).json()
        assert len(restored['nodes'])==before['nodes'] and len(restored['edges'])==before['edges']
        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)

def test_browser_v44_maps_render_metro_geometry_orientation_focus_and_scaled_chronology(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""
          async () => {
            const first=state.chapters[0];
            const manuscript=state.documents.find(x=>x.is_manuscript);
            const second=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Chapter 2',tab_id:manuscript.id})});
            const saved=await api(`/api/chapters/${first.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{text_content:'Opening words',content_html:'Opening words'},{text_content:Array(700).fill('middle').join(' '),content_html:Array(700).fill('middle').join(' ')},{text_content:'Ending words',content_html:'Ending words'}]})});
            const a=await api('/api/scenes',{method:'POST',body:JSON.stringify({title:'Opening Scene',block_ids:[saved.ids[0]]})});
            const b=await api('/api/scenes',{method:'POST',body:JSON.stringify({title:'Late Scene',block_ids:[saved.ids[2]]})});
            const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Current Story',layout_mode:'scaled',ruler:'percent'})});
            const main=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Main Story'})});
            const side=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Finn'})});
            const opening=await api(`/api/map-lines/${main.id}/items`,{method:'POST',body:JSON.stringify({title:'Opening',start_anchor_type:'scene',start_anchor_id:String(a.id)})});
            const late=await api(`/api/map-lines/${main.id}/items`,{method:'POST',body:JSON.stringify({title:'Late Beat',start_anchor_type:'scene',start_anchor_id:String(b.id)})});
            const span=await api(`/api/map-lines/${side.id}/items`,{method:'POST',body:JSON.stringify({title:'Fun and Games',item_type:'span',planned_start:.2,planned_end:.5,schematic_start:.2,schematic_end:.5})});
            await api(`/api/maps/${map.id}/junctions`,{method:'POST',body:JSON.stringify({from_item_id:opening.id,to_item_id:span.id,kind:'branch'})});
            await refresh(false); setView('maps'); await selectMap(map.id);
            return {map:map.id,main:main.id,side:side.id,opening:opening.id,late:late.id,span:span.id,scene:b.id,second:second.id};
          }
        """)
        page.wait_for_selector('#mapSvg .map-route',state='attached')
        assert page.locator('#mapSvg .map-line-group').count()==2
        assert page.locator('#mapSvg .map-stop-outer').count()==2
        assert page.locator('#mapSvg .map-span-shape').count()==1
        metrics=page.evaluate("""
          () => ({
            route:+document.querySelector('#mapSvg .map-route').getAttribute('stroke-width'),
            outer:+document.querySelector('#mapSvg .map-stop-outer').getAttribute('r'),
            inner:+document.querySelector('#mapSvg .map-stop-inner').getAttribute('r'),
            main:document.querySelector(`[data-map-line='${state.mapData.lines[0].id}'] .map-route`).getAttribute('stroke'),
            side:document.querySelector(`[data-map-line='${state.mapData.lines[1].id}'] .map-route`).getAttribute('stroke'),
            background:document.querySelector('#mapSvg').style.background,
            orientation:document.querySelector('#mapSvg').dataset.orientation,
            mode:document.querySelector('#mapSvg').dataset.layoutMode
          })
        """)
        assert metrics['route']==10 and metrics['outer']==13 and metrics['inner']==8
        assert metrics['main']=='#f3680e' and metrics['side']=='#0e8ef3'
        assert metrics['background'] in ('rgb(243, 243, 241)','#f3f3f1')
        assert metrics['orientation']=='horizontal' and metrics['mode']=='scaled'

        # Focus mode keeps the selected route vivid and dims unrelated Lines.
        page.evaluate('(id)=>toggleMapLineFocus(id)',created['side'])
        opacities=page.evaluate("""() => [...document.querySelectorAll('#mapSvg .map-line-group')].map(g=>[+g.dataset.mapLine,+g.getAttribute('opacity')])""")
        opacity=dict(opacities)
        assert opacity[created['side']]==1 and opacity[created['main']]<.2

        # Orientation is a real rerender, not a rotated DOM trick.
        page.locator('#mapViewBtn').click(); page.wait_for_selector('#mapViewPopover:not(.hidden)'); page.locator('#mapViewOrientation').click(); page.wait_for_timeout(250)
        assert page.locator('#mapSvg').get_attribute('data-orientation')=='vertical'
        viewbox=page.locator('#mapSvg').get_attribute('viewBox').split()
        assert float(viewbox[3])>float(viewbox[2])

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v44_framework_population_selection_link_and_split_are_maps(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""
          async () => {
            const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Framework Map'})});
            const line=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Main Story'})});
            await refresh(false); setView('maps'); await selectMap(map.id); return {map:map.id,line:line.id};
          }
        """)
        page.wait_for_selector('#mapSvg')

        # Framework Populate is one structural undo transaction and creates real spans.
        before=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        page.evaluate('(id)=>populateMap(id,"save_the_cat","Save the Cat")',created['map'])
        page.wait_for_function("state.mapData && state.mapData.lines.some(l=>l.items?.some(i=>i.title==='Fun and Games'))",timeout=7000)
        detail=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        framework=next(l for l in detail['lines'] if any(i['title']=='Fun and Games' for i in l['items']))
        fun=next(i for i in framework['items'] if i['title']=='Fun and Games')
        assert fun['item_type']=='span' and fun['planned_start']==.2 and fun['planned_end']==.5
        page.keyboard.press('Control+z'); page.wait_for_timeout(350)
        undone=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        assert len(undone['lines'])==len(before['lines'])

        # A manuscript selection can become a Map stop through the actual selection toolbar.
        page.locator('[data-view="manuscript"]').click(); page.wait_for_timeout(100)
        block=page.locator('.manuscript-block').first; block.click(); page.keyboard.type('The cat steals the ribbon and everything changes.'); page.wait_for_timeout(700)
        selected=page.evaluate("""
          () => {const el=document.querySelector('.manuscript-block'),r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);state.lastManuscriptRange=r.cloneRange();positionSelectionToolbar(r);return selectionItems().length}
        """)
        assert selected==1
        page.locator('[data-selection-action="more"]').click(); page.wait_for_selector('#contextMenu:not(.hidden)')
        page.locator('#contextMenu button').filter(has_text='Map').click(); page.wait_for_selector('#selectionMapSelect')
        page.locator('#selectionMapSelect').select_option(str(created['map'])); page.wait_for_timeout(100)
        page.locator('#selectionMapLine').select_option(str(created['line']))
        page.locator('#selectionMapTitle').fill('Cat steals ribbon')
        page.locator('#addSelectionMapConfirm').click(); page.wait_for_timeout(250)
        linked=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        beat=next(i for l in linked['lines'] for i in l['items'] if i['title']=='Cat steals ribbon')
        assert beat['start_anchor_type']=='manuscript_range' and beat['start_source']=='manuscript'

        page.locator('[data-view="maps"]').click(); page.wait_for_timeout(180)
        assert page.locator('#mapSvg .map-stop-outer').count()>=1
        page.evaluate('(id)=>openMapSplit(id)',created['map']); page.wait_for_selector('#splitMapSvg .map-route',state='attached')
        assert page.locator('#splitMapSvg .map-stop-outer').count()>=1

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)



def test_browser_v45_maps_readability_dark_theme_pinning_and_split_are_stable(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""
          async () => {
            const first=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Reliability A',layout_mode:'scaled',label_mode:'minimal',ruler:'percent'})});
            const main=await api(`/api/maps/${first.id}/lines`,{method:'POST',body:JSON.stringify({name:'Main'})});
            const side=await api(`/api/maps/${first.id}/lines`,{method:'POST',body:JSON.stringify({name:'Side'})});
            const a=await api(`/api/map-lines/${main.id}/items`,{method:'POST',body:JSON.stringify({title:'Near A',planned_start:.400})});
            const b=await api(`/api/map-lines/${main.id}/items`,{method:'POST',body:JSON.stringify({title:'Near B with a deliberately long label',planned_start:.401})});
            await api(`/api/map-lines/${side.id}/items`,{method:'POST',body:JSON.stringify({title:'Side beat',planned_start:.6})});
            const second=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Reliability B'})});
            const other=await api(`/api/maps/${second.id}/lines`,{method:'POST',body:JSON.stringify({name:'Other'})});
            await api(`/api/map-lines/${other.id}/items`,{method:'POST',body:JSON.stringify({title:'Other beat',planned_start:.5,schematic_start:.5})});
            await refresh(false); setView('maps'); await selectMap(first.id);
            return {first:first.id,main:main.id,side:side.id,a:a.id,b:b.id,second:second.id,other:other.id};
          }
        """)
        page.wait_for_selector('#mapSvg [data-map-item]',state='attached')

        # Scaled view preserves chronology but imposes a screen-space readability
        # floor instead of allowing adjacent Stations to collapse into each other.
        distance=page.evaluate("""(ids) => {
          const a=document.querySelector(`[data-map-item="${ids[0]}"] .map-stop-outer`),b=document.querySelector(`[data-map-item="${ids[1]}"] .map-stop-outer`);
          const ra=a.getBoundingClientRect(),rb=b.getBoundingClientRect();return Math.abs((rb.left+rb.width/2)-(ra.left+ra.width/2));
        }""",[created['a'],created['b']])
        assert distance>=27

        # Minimal labels are the default and truncate by measured render width.
        label_text=page.locator(f'[data-map-label="{created["b"]}"]').text_content() or ''
        assert label_text.endswith('…')
        assert page.locator('#mapSvg').get_attribute('data-label-mode')=='minimal'

        # Hiding the focused line clears focus rather than dimming every survivor.
        page.evaluate('(id)=>toggleMapLineFocus(id)',created['main'])
        page.evaluate("""async (id) => {await api(`/api/map-lines/${id}`,{method:'PATCH',body:JSON.stringify({visible:false})});await reloadCurrentMap()}""",created['main'])
        focus=page.evaluate("""() => ({focus:state.mapFocusLineId,opacity:[...document.querySelectorAll('#mapSvg .map-line-group')].map(g=>+g.getAttribute('opacity'))})""")
        assert focus['focus'] is None
        assert focus['opacity'] and all(x==1 for x in focus['opacity'])
        page.evaluate("""async (id) => {await api(`/api/map-lines/${id}`,{method:'PATCH',body:JSON.stringify({visible:true})});await reloadCurrentMap()}""",created['main'])

        # Dragging an automatic label pins its actual displayed offset. Pinned
        # labels remain visible even when the Map's label mode is Hidden.
        label=page.locator(f'#mapSvg [data-map-label="{created["b"]}"]')
        before=float(label.get_attribute('data-label-offset'))
        box=label.bounding_box(); assert box
        x=box['x']+box['width']/2; y=box['y']+box['height']/2
        page.mouse.move(x,y); page.mouse.down(); page.mouse.move(x,y+24,steps=4); page.mouse.up(); page.wait_for_timeout(300)
        dragged=page.evaluate("""async (id) => {const d=await api(`/api/maps/${state.currentMap.id}`);return d.lines.flatMap(l=>l.items).find(i=>i.id===id)}""",created['b'])
        assert dragged['label_pinned']==1 and dragged['label_offset']>before+15
        page.evaluate("""async()=>{await api(`/api/maps/${state.currentMap.id}`,{method:'PATCH',body:JSON.stringify({label_mode:'hidden'})});await reloadCurrentMap()}""")
        assert page.locator(f'#mapSvg [data-map-label="{created["b"]}"]').count()==1
        assert page.locator(f'#mapSvg [data-map-label="{created["a"]}"]').count()==0

        # Theme changes affect only the neutral canvas layer. Line colors remain exact.
        colors=page.evaluate("""() => {
          setTheme('dark');renderMapVisual();
          const svg=document.querySelector('#mapSvg'),mark=document.querySelector('.brand-mark'),brand=getComputedStyle(mark),body=getComputedStyle(document.body);
          return {bg:svg.style.background,colors:[...svg.querySelectorAll('.map-route')].map(x=>x.getAttribute('stroke')),brandColor:brand.color,brandBackground:brand.backgroundColor,bodyColor:body.color,brandMask:brand.webkitMaskImage||brand.maskImage}
        }""")
        assert colors['bg'] in ('rgb(25, 26, 25)','#191a19')
        # The current logo is a monochrome SVG mask colored with the theme text token.
        assert colors['brandColor']==colors['bodyColor']
        assert colors['brandBackground']==colors['bodyColor']
        assert 'breadmaker_logo.svg' in colors['brandMask']
        assert '#f3680e' in colors['colors'] and '#0e8ef3' in colors['colors']
        page.evaluate("setTheme('light')")

        # Split View is independent of active focus and uses the same renderer.
        page.evaluate('(id)=>toggleMapLineFocus(id)',created['side'])
        page.evaluate('(id)=>openMapSplit(id)',created['second'])
        page.wait_for_selector('#splitMapSvg .map-line-group',state='attached')
        assert page.locator('#splitMapSvg .map-line-group').first.get_attribute('opacity') in (None,'1','1.0')

        # Dense rulers are sampled according to zoom.
        ruler_count=page.evaluate("""() => {
          const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
          const fake=structuredClone(state.mapData);fake.map.ruler='scenes';fake.axis.scenes=Array.from({length:120},(_,i)=>({position:i/119,title:`Scene ${i+1}`}));
          renderMapSvg(svg,fake,{interactive:false,zoom:.3});return svg.querySelectorAll('.map-ruler-label').length;
        }""")
        assert 2 <= ruler_count < 40

        # Tooltips stay inside very narrow panes.
        tooltip=page.evaluate("""(id) => {
          const stage=document.querySelector('#mapStage'),found=mapItemById(id);
          Object.defineProperty(stage,'clientWidth',{value:120,configurable:true});
          Object.defineProperty(stage,'clientHeight',{value:90,configurable:true});
          showMapTooltip({clientX:0,clientY:0},found.item,found.line);
          const tip=document.querySelector('#mapTooltip');return {left:parseFloat(tip.style.left),top:parseFloat(tip.style.top)};
        }""",created['a'])
        assert tooltip['left']>=8 and tooltip['top']>=8

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)

def test_browser_v433_free_images_card_text_and_arrow_direction(live_server,browser_runtime):
    import base64
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        tiny=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n7sAAAAASUVORK5CYII=')
        uploaded=requests.post(live_server+'/api/assets',headers={'X-Breadmaker-Client':'e2e-tab'},files={'file':('tiny.png',tiny,'image/png')},timeout=5)
        assert uploaded.ok, uploaded.text
        asset=uploaded.json()['path']
        created=page.evaluate("""async(asset)=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'V432 Canvas Objects',canvas_type:'blank'})});
          const image=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({node_type:'image',shape:'rect',image_path:asset,x:120,y:120,w:240,h:160})});
          const card=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',title:'Decision',x:470,y:100,w:220,h:150,color:'#ddeeff'})});
          const text=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'text',title:'Plain text',x:470,y:330,w:190,h:70})});
          const target=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Target',x:820,y:360,w:180,h:100})});
          const edge=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:card.id,to_node_id:target.id,from_port:'right',to_port:'top',route_style:'straight',end_style:'arrow'})});
          const imageEdge=await api(`/api/canvases/${cv.id}/edges`,{method:'POST',body:JSON.stringify({from_node_id:text.id,to_node_id:image.id,from_port:'left',to_port:'right',route_style:'curved'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
          return{cv:cv.id,image:image.id,card:card.id,text:text.id,target:target.id,edge:edge.id,imageEdge:imageEdge.id};
        }""",asset)
        page.wait_for_selector(f'.canvas-node[data-id="{created["image"]}"]')

        # Hand/Pointer lives over the upper-left of the canvas rather than consuming toolbar space.
        stage=page.locator('#canvasStage').bounding_box();overlay=page.locator('.canvas-mode-overlay').bounding_box()
        assert overlay['x']>=stage['x'] and overlay['x']<stage['x']+40
        assert overlay['y']>=stage['y'] and overlay['y']<stage['y']+40
        assert page.locator('.canvas-toolbar #canvasHandMode').count()==0

        # Image is a free object: no card interior/drag strip, but it has selection,
        # eight arrow anchors, direct dragging, and a resize knob.
        image=page.locator(f'.canvas-node[data-id="{created["image"]}"]')
        assert 'image-object' in (image.get_attribute('class') or '')
        assert image.locator('.node-inner').count()==0 and image.locator('.node-drag-strip').count()==0
        assert image.locator('.canvas-free-image').count()==1 and image.locator('.node-port').count()==8
        assert image.locator('.node-resize-handle').count()==1
        before=page.evaluate('(id)=>{const n=state.canvasNodes.find(x=>x.id===id);return{x:n.x,y:n.y,w:n.w,h:n.h}}',created['image'])
        box=image.bounding_box();page.mouse.move(box['x']+80,box['y']+60);page.mouse.down();page.mouse.move(box['x']+135,box['y']+100,steps=5);page.mouse.up();page.wait_for_timeout(180)
        moved=page.evaluate('(id)=>{const n=state.canvasNodes.find(x=>x.id===id);return{x:n.x,y:n.y}}',created['image'])
        assert moved['x']>before['x']+40 and moved['y']>before['y']+25
        handle=image.locator('.node-resize-handle');hb=handle.bounding_box();page.mouse.move(hb['x']+5,hb['y']+5);page.mouse.down();page.mouse.move(hb['x']+65,hb['y']+45,steps=5);page.mouse.up();page.wait_for_timeout(180)
        resized=page.evaluate('(id)=>{const n=state.canvasNodes.find(x=>x.id===id);return{w:n.w,h:n.h}}',created['image'])
        assert resized['w']>before['w']+40 and resized['h']>before['h']+25
        image.click();page.wait_for_timeout(80);assert page.locator('#canvasToolPanel').evaluate("el=>el.classList.contains('hidden')")
        image.click(button='right');page.wait_for_selector('#contextMenu:not(.hidden)')
        menu_text=page.locator('#contextMenu').inner_text();assert 'Replace Image…' in menu_text and 'Delete Image' in menu_text
        page.keyboard.press('Escape')

        # Canonical Cards keep rectangular geometry and the current eight-direction connector anchors.
        card=page.locator(f'.canvas-node[data-id="{created["card"]}"]')
        geometry=card.evaluate("el=>({transform:getComputedStyle(el).transform,ports:el.querySelectorAll('.node-port').length,shape:state.canvasNodes.find(n=>n.id===Number(el.dataset.id))?.shape})")
        assert geometry['transform']=='none' and geometry['ports']==8 and geometry['shape']=='rect'

        # Text-only carries no card chrome and remains directly editable.
        text=page.locator(f'.canvas-node[data-id="{created["text"]}"]')
        text_style=text.evaluate("el=>{const cs=getComputedStyle(el),line=getComputedStyle(el.querySelector('.node-title-line'));return{bg:cs.backgroundColor,border:cs.borderTopWidth,shadow:cs.boxShadow,divider:line.borderBottomWidth,editable:el.querySelector('.node-title').contentEditable}}")
        assert text_style['bg'] in ('rgba(0, 0, 0, 0)','transparent')
        assert text_style['border']=='0px' and text_style['shadow']=='none' and text_style['divider']=='0px'
        assert text_style['editable']=='true'

        # Arrowhead direction follows the connector's real final tangent rather
        # than blindly using the target port's cardinal direction.
        alignment=page.evaluate("""id=>{
          const e=state.canvasEdges.find(x=>x.id===id),a=state.canvasNodes.find(n=>n.id===e.from_node_id),b=state.canvasNodes.find(n=>n.id===e.to_node_id),g=edgeGeometry(a,b,e.from_port,e.to_port,e.route_style),end=edgeEndElement(e,b,g.endTangent),nums=(end.getAttribute('d').match(/-?\\d+(?:\\.\\d+)?/g)||[]).map(Number),tip=[nums[0],nums[1]],base=[(nums[2]+nums[4])/2,(nums[3]+nums[5])/2],v=normalizeVector(tip[0]-base[0],tip[1]-base[1]);return{t:g.endTangent,dot:v[0]*g.endTangent[0]+v[1]*g.endTangent[1]};
        }""",created['edge'])
        assert alignment['t'][0]>0 and alignment['t'][1]>0
        assert alignment['dot']>.99

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)

def test_browser_v434_canvas_populate_menu_is_json_driven_repeatable_and_atomic_undo(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        canvas_id=page.evaluate("""async()=>{const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Populate Test',canvas_type:'blank'})});await refresh(false);setView('canvas');await selectCanvas(cv.id);return cv.id}""")
        page.wait_for_selector('#canvasView.active')
        stage=page.locator('#canvasStage')
        stage.click(button='right',position={'x':360,'y':280})
        populate=page.locator('#contextMenu button').filter(has_text='Populate').first
        populate.hover()
        page.locator('.context-submenu button').filter(has_text='Save the Cat Beats').click()
        page.wait_for_function('state.canvasNodes.length===15 && state.canvasEdges.length===14')
        assert page.evaluate('state.canvasNodes.map(n=>n.title)')[0]=='Opening Image'
        assert page.evaluate('state.canvasNodes.map(n=>n.title)')[-1]=='Final Image'

        # One canvas undo removes the entire population operation.
        page.keyboard.press('Control+z')
        page.wait_for_function('state.canvasNodes.length===0 && state.canvasEdges.length===0')

        # The same template can be populated again at a new position.
        stage.click(button='right',position={'x':700,'y':500})
        page.locator('#contextMenu button').filter(has_text='Populate').first.hover()
        page.locator('.context-submenu button').filter(has_text='Hero\'s Journey').click()
        page.wait_for_function('state.canvasNodes.length===12 && state.canvasEdges.length===11')
        assert min(page.evaluate('state.canvasNodes.map(n=>n.x)'))>400

        assert not errors,errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected,unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v435_project_center_hands_off_child_modals(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        _open_project_menu(page)

        # Brand chevron is geometry-aligned rather than relying on a baseline-sensitive glyph.
        centers=page.evaluate("""()=>{const name=document.querySelector('.brand-name').getBoundingClientRect(),arrow=document.querySelector('.brand-chevron').getBoundingClientRect();return {name:name.top+name.height/2,arrow:arrow.top+arrow.height/2}}""")
        assert abs(centers['name']-centers['arrow']) <= 1.5
        assert page.locator('#projectMenuRename').is_visible()

        # Project Settings owns administrative child dialogs. Closing a child returns
        # to the same settings surface instead of resurrecting the retired Project Center.
        page.locator('#projectMenuSettings').click();page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text()=='Project Settings'
        page.locator('#settingsRenameProject').click();page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text()=='Rename Project'
        page.locator('#modalClose').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Settings'")

        page.locator('#settingsRenameProject').click();page.wait_for_selector('#renameProjectName')
        page.locator('#renameProjectName').fill('Renamed in Project Settings')
        page.locator('#renameProjectConfirm').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Settings'")
        assert page.locator('.project-settings-name h2').inner_text()=='Renamed in Project Settings'

        page.locator('#settingsVerify').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Verify Project'")
        page.locator('#modalClose').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Settings'")

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v45_line_endpoint_drag_creates_junction(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Direct Line Geometry',orientation:'horizontal',layout_mode:'scaled',label_mode:'minimal',ruler:'percent'})});
          const a=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'A',schematic_start:.15,schematic_end:.42,planned_start:.15,planned_end:.42})});
          const b=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'B',schematic_start:.55,schematic_end:.84,planned_start:.55,planned_end:.84,layout_locked:false})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,a:a.id,b:b.id};
        }""")
        page.wait_for_selector('#mapSvg [data-map-line-route]',state='attached')
        page.locator(f'#mapSvg [data-map-line-route="{created["a"]}"]').first.dispatch_event('click')
        page.evaluate('closeMapInspector()')
        end_handle=page.locator(f'#mapSvg [data-map-line-handle="{created["a"]}"][data-line-edge="end"]')
        end_handle.wait_for(state='visible')
        target=page.locator(f'#mapSvg [data-map-line-route="{created["b"]}"]').first
        hb=end_handle.bounding_box();tb=target.bounding_box();assert hb and tb
        page.mouse.move(hb['x']+hb['width']/2,hb['y']+hb['height']/2);page.mouse.down()
        page.mouse.move(tb['x']+tb['width']*.12,tb['y']+tb['height']/2,steps=8);page.mouse.up()
        page.wait_for_function("async id=>(await api(`/api/maps/${id}`)).junctions.length===1",arg=created['map'])
        detail=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        junction=detail['junctions'][0]
        assert junction['from_line_id']==created['a'] and junction['to_line_id']==created['b']
        assert junction['from_item_id'] is None and junction['to_item_id'] is None
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v45_station_drag_persists_position(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Direct Station Geometry',orientation:'horizontal',layout_mode:'scaled',label_mode:'minimal',ruler:'percent'})});
          const line=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Movable Line',planned_start:.08,planned_end:.92,schematic_start:.08,schematic_end:.92,layout_locked:false})});
          const stop=await api(`/api/map-lines/${line.id}/items`,{method:'POST',body:JSON.stringify({title:'Movable',planned_start:.28,schematic_start:.28})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,line:line.id,stop:stop.id};
        }""")
        stop=page.locator(f'#mapSvg [data-map-item="{created["stop"]}"] .map-stop-outer')
        stop.wait_for(state='visible')
        sb=stop.bounding_box();assert sb
        sx=sb['x']+sb['width']/2;sy=sb['y']+sb['height']/2
        page.mouse.move(sx,sy);page.mouse.down();page.mouse.move(sx+120,sy,steps=8);page.mouse.up()
        page.wait_for_function("""async ([mapId,stopId])=>{const detail=await api(`/api/maps/${mapId}`);const stop=detail.lines.flatMap(line=>line.items||[]).find(item=>+item.id===+stopId);return !!stop&&stop.planned_start>.29}""",arg=[created['map'],created['stop']])
        moved=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        moved_stop=next(i for l in moved['lines'] for i in l['items'] if i['id']==created['stop'])
        assert moved_stop['planned_start'] > .29
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v45_span_endpoint_resize_persists(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Direct Span Geometry',orientation:'horizontal',layout_mode:'scaled',label_mode:'minimal',ruler:'percent'})});
          const line=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Resize',planned_start:.08,planned_end:.92,schematic_start:.08,schematic_end:.92,layout_locked:false})});
          const span=await api(`/api/map-lines/${line.id}/items`,{method:'POST',body:JSON.stringify({title:'Range',item_type:'span',planned_start:.42,planned_end:.62,schematic_start:.42,schematic_end:.62})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,line:line.id,span:span.id};
        }""")
        shape=page.locator(f'#mapSvg [data-map-item="{created["span"]}"] .map-span-shape')
        shape.wait_for(state='visible')
        shape.evaluate("el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true}))")
        handle=page.locator(f'[data-testid="map-span-handle"][data-map-item-id="{created["span"]}"][data-span-edge="end"]')
        handle.wait_for(state='visible')
        before=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        before_span=next(i for line in before['lines'] for i in line['items'] if i['id']==created['span'])
        before_line=next(line for line in before['lines'] if line['id']==created['line'])
        target_p=min(float(before_line['planned_end'])-.04,float(before_span['planned_end'])+.10)
        assert target_p>float(before_span['planned_end'])+.01,(target_p,before_span,before_line)
        target=_map_line_client_point_for_position(page,created['line'],target_p)
        eb=handle.bounding_box();assert eb
        ex=eb['x']+eb['width']/2;ey=eb['y']+eb['height']/2
        assert 0<=target['x']<target['viewportWidth'] and 0<=target['y']<target['viewportHeight'],target
        assert target['x']>ex+8,(target,eb)
        page.mouse.move(ex,ey);page.mouse.down();page.mouse.move(target['x'],target['y'],steps=12);page.mouse.up()
        page.wait_for_function("""async ([mapId,spanId,beforeEnd])=>{const detail=await api(`/api/maps/${mapId}`);const span=detail.lines.flatMap(line=>line.items||[]).find(item=>+item.id===+spanId);return !!span&&span.planned_end>beforeEnd+.01}""",arg=[created['map'],created['span'],before_span['planned_end']],timeout=5000)
        after=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        after_span=next(i for line in after['lines'] for i in line['items'] if i['id']==created['span'])
        assert after_span['planned_end'] > before_span['planned_end']+.01
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)

def test_browser_v451_compact_spans_direct_crosslinks_and_reconnect_handles(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async () => {
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'4.5.1 Visual',layout_mode:'schematic',label_mode:'hidden',ruler:'chapters'})});
          const bohor=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Bohor',color:'#d84c8b',schematic_start:.08,schematic_end:.88,planned_start:.08,planned_end:.88})});
          const story=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Story',color:'#0e8ef3',schematic_start:.08,schematic_end:.88,planned_start:.08,planned_end:.88})});
          const stop=await api(`/api/map-lines/${story.id}/items`,{method:'POST',body:JSON.stringify({title:'Bohor appears',planned_start:.62,schematic_start:.62})});
          const span=await api(`/api/map-lines/${story.id}/items`,{method:'POST',body:JSON.stringify({title:'Multi-scene beat',item_type:'span',planned_start:.20,planned_end:.48,schematic_start:.20,schematic_end:.48})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,bohor:bohor.id,story:story.id,stop:stop.id,span:span.id,color:bohor.color};
        }""")
        page.wait_for_selector('#mapSvg [data-map-line-route]',state='attached')

        # A duration Span should be roughly the same visual thickness as a Station,
        # not the oversized 4.5.0 capsule.
        span_box=page.locator(f'#mapSvg [data-map-item="{created["span"]}"] .map-span-shape').bounding_box()
        stop_box=page.locator(f'#mapSvg [data-map-item="{created["stop"]}"] .map-stop-outer').bounding_box()
        assert span_box and stop_box
        assert span_box['height'] <= stop_box['height'] + 4

        # Horizontal Chapter ruler labels are vertical and intentionally subdued.
        ruler=page.locator('#mapSvg .map-ruler-chapter-label').first
        if ruler.count():
            assert 'rotate(90' in (ruler.get_attribute('transform') or '')
            opacity=float(ruler.evaluate("el=>getComputedStyle(el).fillOpacity"))
            assert opacity < .6

        # Current Maps exposes a delayed direct line-connection handle.
        source=page.locator(f'#mapSvg [data-map-line-route="{created["bohor"]}"]').first
        handle=page.locator(f'#mapSvg [data-map-line-connect="{created["bohor"]}"]')
        sb=source.bounding_box(); hb=handle.bounding_box(); assert sb and hb
        page.mouse.move(hb['x']+hb['width']/2,sb['y']+sb['height']/2);page.wait_for_timeout(360)
        target=page.locator(f'#mapSvg [data-map-item="{created["stop"]}"] .map-stop-outer');tb=target.bounding_box();assert tb
        page.mouse.move(hb['x']+hb['width']/2,hb['y']+hb['height']/2);page.mouse.down();page.mouse.move(tb['x']+tb['width']/2,tb['y']+tb['height']/2,steps=9);page.mouse.up();page.wait_for_timeout(350)
        detail=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        assert len(detail['junctions'])==1
        junction=detail['junctions'][0]
        assert junction['from_line_id']==created['bohor'] and junction['to_item_id']==created['stop']
        path=page.locator(f'#mapSvg [data-map-junction="{junction["id"]}"]')
        assert path.get_attribute('stroke').lower()==created['color'].lower()

        # Selecting a connection exposes two draggable endpoint handles. Move the
        # Bohor-side endpoint along Bohor without deleting/recreating the junction.
        path.click();page.wait_for_timeout(100)
        handles=page.locator(f'#mapSvg [data-map-junction-handle="{junction["id"]}"]')
        assert handles.count()==2
        from_handle=page.locator(f'#mapSvg [data-map-junction-handle="{junction["id"]}"][data-junction-side="from"]')
        hb=from_handle.bounding_box(); sb=source.bounding_box(); assert hb and sb
        page.mouse.move(hb['x']+hb['width']/2,hb['y']+hb['height']/2);page.mouse.down()
        page.mouse.move(sb['x']+sb['width']*.72,sb['y']+sb['height']/2,steps=8);page.mouse.up();page.wait_for_timeout(350)
        moved=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])['junctions'][0]
        assert moved['id']==junction['id']
        assert moved['from_line_id']==created['bohor']
        assert moved['from_schematic_position'] > junction['from_schematic_position']
        assert not errors
        assert not [entry for entry in console if entry[0]=='error']
    finally:
        _shutdown_page(page)


def test_browser_v452_readability_floor_is_line_local_and_stations_drag_connect(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async () => {
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'4.5.2 Maps',layout_mode:'scaled',label_mode:'hidden',ruler:'chapters'})});
          const framework=await api(`/api/maps/${map.id}/populate`,{method:'POST',body:JSON.stringify({template_id:'save_the_cat'})});
          const a=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Bohor',color:'#d84c8b',planned_start:.08,planned_end:.90,schematic_start:.08,schematic_end:.90})});
          const b=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Story',color:'#0e8ef3',planned_start:.08,planned_end:.90,schematic_start:.08,schematic_end:.90})});
          const a1=await api(`/api/map-lines/${a.id}/items`,{method:'POST',body:JSON.stringify({title:'Bohor beat',planned_start:.30,schematic_start:.30})});
          const b1=await api(`/api/map-lines/${b.id}/items`,{method:'POST',body:JSON.stringify({title:'Story beat',planned_start:.62,schematic_start:.62})});
          await refresh(false);setView('maps');await selectMap(map.id);
          const detail=await api(`/api/maps/${map.id}`);
          const source=detail.lines.find(line=>line.id===framework.line_id);
          const ids=Object.fromEntries(source.items.slice(0,4).map(item=>[item.title,item.id]));
          return {map:map.id,framework:framework.line_id,a:a.id,b:b.id,a1:a1.id,b1:b1.id,color:a.color,ids};
        }""")
        page.wait_for_selector('#mapSvg [data-map-item]',state='attached')

        # Spans may intentionally pass under Stations. The readability floor is
        # enforced between Station centers, not mixed Station/Span footprints.
        centers=page.evaluate('(id)=>{const line=state.mapData.lines.find(l=>l.id===id),plan=mapLineRenderPlan(line,state.mapData.map.layout_mode,1000,state.mapZoom);return plan.positioned.filter(x=>x.item.item_type==="stop").map(x=>x.renderStartAxis)}',created['framework'])
        assert all((b-a)>=27 for a,b in zip(centers,centers[1:])), centers

        # Dragging a Station on Bohor may change Bohor's own local readability
        # layout, but it must not re-warp an unrelated Station on Story.
        story_stop=page.locator(f'#mapSvg [data-map-item="{created["b1"]}"] .map-stop-outer')
        before=story_stop.bounding_box(); assert before
        bohor_stop=page.locator(f'#mapSvg [data-map-item="{created["a1"]}"] .map-stop-outer')
        ab=bohor_stop.bounding_box(); assert ab
        ax=ab['x']+ab['width']/2; ay=ab['y']+ab['height']/2
        page.mouse.move(ax,ay);page.mouse.down();page.mouse.move(ax+85,ay,steps=7);page.mouse.up();page.wait_for_timeout(300)
        after=story_stop.bounding_box(); assert after
        assert abs(after['x']-before['x']) < 1.0

        # Selecting a Station exposes a dedicated drag-to-connect handle. Dragging
        # that handle to another Station creates a relationship without coupling
        # either Station's position.
        page.locator(f'#mapSvg [data-map-item="{created["a1"]}"] .map-stop-inner').click();page.wait_for_timeout(80)
        handle=page.locator(f'#mapSvg [data-map-item="{created["a1"]}"] .map-station-connect-handle')
        assert handle.count()==1
        hb=handle.bounding_box(); tb=story_stop.bounding_box(); assert hb and tb
        page.mouse.move(hb['x']+hb['width']/2,hb['y']+hb['height']/2);page.mouse.down()
        page.mouse.move(tb['x']+tb['width']/2,tb['y']+tb['height']/2,steps=9);page.mouse.up();page.wait_for_timeout(350)
        detail=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        direct=next(j for j in detail['junctions'] if j['from_item_id']==created['a1'] and j['to_item_id']==created['b1'])
        path=page.locator(f'#mapSvg [data-map-junction="{direct["id"]}"]')
        assert path.get_attribute('stroke').lower()==created['color'].lower()

        # Horizontal chapter labels read from top to bottom.
        ruler=page.locator('#mapSvg .map-ruler-chapter-label').first
        if ruler.count():
            assert 'rotate(90' in (ruler.get_attribute('transform') or '')

        assert not errors
        assert not [entry for entry in console if entry[0]=='error']
    finally:
        _shutdown_page(page)


def test_browser_v46_metro_view_generates_a4_poster_and_stays_read_only(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async () => {
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Metro Poster Test',layout_mode:'schematic',label_mode:'minimal'})});
          const main=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Main Story',color:'#e43b2f',planned_start:.05,planned_end:.95,schematic_start:.05,schematic_end:.95})});
          const bohor=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Bohor',color:'#7e3794',planned_start:.18,planned_end:.82,schematic_start:.18,schematic_end:.82})});
          const cinna=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Cinna',color:'#178f60',planned_start:.10,planned_end:.90,schematic_start:.10,schematic_end:.90})});
          const mainItems=[];for(const [title,p] of [['Opening',.08],['Bakery',.28],['Midpoint',.50],['Confrontation',.72],['Moving On',.92]])mainItems.push(await api(`/api/map-lines/${main.id}/items`,{method:'POST',body:JSON.stringify({title,planned_start:p,schematic_start:p})}));
          const b1=await api(`/api/map-lines/${bohor.id}/items`,{method:'POST',body:JSON.stringify({title:'Bohor Appears',planned_start:.30,schematic_start:.30})});
          const b2=await api(`/api/map-lines/${bohor.id}/items`,{method:'POST',body:JSON.stringify({title:'Bohor Returns',planned_start:.70,schematic_start:.70})});
          const c1=await api(`/api/map-lines/${cinna.id}/items`,{method:'POST',body:JSON.stringify({title:'Cinna Chooses',planned_start:.48,schematic_start:.48})});
          await api(`/api/maps/${map.id}/junctions`,{method:'POST',body:JSON.stringify({kind:'merge',from_item_id:b1.id,to_item_id:mainItems[1].id})});
          await api(`/api/maps/${map.id}/junctions`,{method:'POST',body:JSON.stringify({kind:'merge',from_item_id:b2.id,to_item_id:mainItems[3].id})});
          await api(`/api/maps/${map.id}/junctions`,{method:'POST',body:JSON.stringify({kind:'merge',from_item_id:c1.id,to_item_id:mainItems[2].id})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,main:main.id,bohor:bohor.id,cinna:cinna.id};
        }""")
        _enable_metro(page)
        page.wait_for_selector('#mapMetroViewBtn:not(.hidden)')
        before=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        page.locator('#mapMetroViewBtn').click();page.wait_for_selector('#modal:not(.hidden) .metro-poster-svg')
        poster=page.locator('#metroPosterPreview .metro-poster-svg')
        assert poster.get_attribute('data-paper')=='portrait'
        assert poster.get_attribute('viewBox')=='0 0 210 297'
        assert page.locator('#metroPosterPreview [data-metro-line]').count()==3
        assert page.locator('#metroPosterPreview .metro-terrain').count()==1
        poster_text=poster.text_content() or ''
        assert 'LEGEND' in poster_text and 'Main Story' in poster_text and 'Bohor' in poster_text and 'Cinna' in poster_text
        # The automatically selected trunk is the most central/complete narrative Line.
        main_width=float(page.locator(f'#metroPosterPreview [data-metro-line="{created["main"]}"]').get_attribute('stroke-width'))
        bohor_width=float(page.locator(f'#metroPosterPreview [data-metro-line="{created["bohor"]}"]').get_attribute('stroke-width'))
        assert main_width > bohor_width

        # Portrait is the default, but A4 landscape is one compact option away.
        page.locator('#metroOrientation').select_option('landscape')
        assert poster.get_attribute('data-paper')=='landscape'
        assert poster.get_attribute('viewBox')=='0 0 297 210'
        page.locator('#metroTerrain').select_option('none')
        assert page.locator('#metroPosterPreview .metro-terrain').count()==0
        page.locator('#metroLabels').select_option('minimal')
        assert page.locator('#metroPosterPreview .metro-labels').count()==1

        # Both advertised image formats are real downloads from the SVG master.
        with page.expect_download() as svg_download_info:
            page.locator('#metroDownloadSvg').click()
        svg_download=svg_download_info.value
        assert svg_download.suggested_filename.endswith('.svg')
        svg_bytes=Path(svg_download.path()).read_bytes()
        assert b'<svg' in svg_bytes and b'viewBox="0 0 297 210"' in svg_bytes
        with page.expect_download(timeout=15000) as png_download_info:
            page.locator('#metroDownloadPng').click()
        png_download=png_download_info.value
        assert png_download.suggested_filename.endswith('.png')
        assert Path(png_download.path()).read_bytes()[:8]==b'\x89PNG\r\n\x1a\n'

        # Generating/recomposing a poster never mutates the editable Map.
        page.locator('#metroRegenerate').click();page.wait_for_timeout(80)
        after=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        assert [(l['id'],l['name'],[(i['id'],i['planned_start'],i['schematic_start']) for i in l['items']]) for l in before['lines']]==[(l['id'],l['name'],[(i['id'],i['planned_start'],i['schematic_start']) for i in l['items']]) for l in after['lines']]
        assert [(j['id'],j['from_item_id'],j['to_item_id']) for j in before['junctions']]==[(j['id'],j['from_item_id'],j['to_item_id']) for j in after['junctions']]

        page.locator('#modalClose').click();page.wait_for_selector('#modal',state='hidden')
        # Disabling the data-driven flag removes the affordance instead of leaving dead UI.
        page.evaluate("state.features.maps_metro_view=false;applyFeatureFlags()")
        assert page.locator('#mapMetroViewBtn').evaluate("el=>getComputedStyle(el).display")=='none'
        assert not errors
        assert not [entry for entry in console if entry[0]=='error']
    finally:
        _shutdown_page(page)


def test_browser_v461_metro_view_keeps_top_reachable_and_seeds_station_turns(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async () => {
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Metro Turns Test',layout_mode:'schematic',label_mode:'minimal'})});
          const main=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Main Story',color:'#d33b32',planned_start:.04,planned_end:.96,schematic_start:.04,schematic_end:.96})});
          const side=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Bohor',color:'#74358c',planned_start:.16,planned_end:.84,schematic_start:.16,schematic_end:.84})});
          const mainItems=[];
          for(const [title,p] of [['Opening',.06],['Inciting',.20],['Choice',.35],['Midpoint',.50],['Fallout',.65],['Confrontation',.80],['Ending',.94]])mainItems.push(await api(`/api/map-lines/${main.id}/items`,{method:'POST',body:JSON.stringify({title,planned_start:p,schematic_start:p})}));
          const b1=await api(`/api/map-lines/${side.id}/items`,{method:'POST',body:JSON.stringify({title:'First Appearance',planned_start:.28,schematic_start:.28})});
          const b2=await api(`/api/map-lines/${side.id}/items`,{method:'POST',body:JSON.stringify({title:'Returns',planned_start:.70,schematic_start:.70})});
          await api(`/api/maps/${map.id}/junctions`,{method:'POST',body:JSON.stringify({kind:'merge',from_item_id:b1.id,to_item_id:mainItems[2].id})});
          await api(`/api/maps/${map.id}/junctions`,{method:'POST',body:JSON.stringify({kind:'merge',from_item_id:b2.id,to_item_id:mainItems[4].id})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,main:main.id};
        }""")
        _enable_metro(page)
        page.locator('#mapMetroViewBtn').click();page.wait_for_selector('#modal:not(.hidden) .metro-poster-svg')

        # A tall A4 preview is top-aligned inside its scroll surface, so its border/title remain reachable.
        wrap=page.locator('.metro-preview-wrap').bounding_box();poster=page.locator('#metroPosterPreview .metro-poster-svg').bounding_box()
        assert wrap and poster
        assert poster['y'] >= wrap['y'] + 18

        # The derived poster spine stays inside the protected map rectangle and includes a seeded turn at a core station.
        first=page.evaluate("""() => {
          const m=metroBuildPosterModel(state.mapData,state.metroView);
          const dirs=[];for(let i=1;i<m.spine.length;i++){const a=m.spine[i-1],b=m.spine[i];dirs.push({dx:b.x-a.x,dy:b.y-a.y})}
          return {spine:m.spine,map:m.map,dirs,path:document.querySelector(`[data-metro-line="${m.core.id}"]`).getAttribute('d')};
        }""")
        assert all(first['map']['x'] <= p['x'] <= first['map']['x']+first['map']['w'] for p in first['spine'])
        assert all(first['map']['y'] <= p['y'] <= first['map']['y']+first['map']['h'] for p in first['spine'])
        assert min(p['y'] for p in first['spine']) >= first['map']['y'] + 8
        # Every planner segment is octolinear; the seeded route contains at least one deliberate heading change.
        headings=[]
        for d in first['dirs']:
            ax,ay=abs(d['dx']),abs(d['dy'])
            assert ax < .05 or ay < .05 or abs(ax-ay) < .05
            headings.append((0 if ax<.05 else (1 if d['dx']>0 else -1),0 if ay<.05 else (1 if d['dy']>0 else -1)))
        assert len(set(headings)) >= 2

        # Regenerate advances the deterministic seed and produces another poster geography without editing the Map.
        page.locator('#metroRegenerate').click();page.wait_for_timeout(100)
        second_path=page.locator(f'#metroPosterPreview [data-metro-line="{created["main"]}"]').get_attribute('d')
        assert second_path != first['path']
        assert not errors
        assert not [entry for entry in console if entry[0]=='error']
    finally:
        _shutdown_page(page)


def test_browser_v462_metro_planner_separates_disconnected_lines_preserves_distance_and_varies_terrain(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async () => {
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Planner Regression',layout_mode:'schematic',label_mode:'minimal'})});
          const chapters=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Chapters',color:'#168fee',planned_start:0,planned_end:1,schematic_start:0,schematic_end:1})});
          const beats=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Save the Cat Beats',color:'#ef2b9b',planned_start:0,planned_end:1,schematic_start:0,schematic_end:1})});
          for(const [i,p] of [[1,.05],[2,.10],[3,.50],[4,.90],[5,.95]])await api(`/api/map-lines/${chapters.id}/items`,{method:'POST',body:JSON.stringify({title:`Chapter ${i}`,planned_start:p,schematic_start:p})});
          for(const [title,p] of [['Opening Image',.03],['Setup',.10],['Catalyst',.22],['Midpoint',.50],['All Is Lost',.72],['Finale',.90],['Final Image',.98]])await api(`/api/map-lines/${beats.id}/items`,{method:'POST',body:JSON.stringify({title,planned_start:p,schematic_start:p})});
          await api(`/api/map-lines/${beats.id}/items`,{method:'POST',body:JSON.stringify({item_type:'span',title:'Fun and Games',planned_start:.25,planned_end:.45,schematic_start:.25,schematic_end:.45})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,chapters:chapters.id,beats:beats.id};
        }""")
        _enable_metro(page)
        page.locator('#mapMetroViewBtn').click();page.wait_for_selector('#modal:not(.hidden) .metro-poster-svg')
        diagnostics=page.evaluate("""ids => {
          const m=metroBuildPosterModel(state.mapData,state.metroView);
          const byId=id=>m.lines.find(e=>e.line.id===id);
          const a=byId(ids.chapters),b=byId(ids.beats);
          const boxes=m.components.map(c=>c.territory);
          const overlap=(x,y)=>x.x<y.x+y.w&&x.x+x.w>y.x&&x.y<y.y+y.h&&x.y+x.h>y.y;
          return {
            components:m.components.length,
            territoriesOverlap:overlap(boxes[0],boxes[1]),
            selfA:metroRouteSelfIntersects(a.route),selfB:metroRouteSelfIntersects(b.route),
            stationT:a.stations.map(s=>({source:s.sourceT,rendered:s.t})),
            routeSizes:[a.route.length,b.route.length],
            budgets:[metroLineTurnBudget(a.line),metroLineTurnBudget(b.line)],
            spans:m.spans.length
          };
        }""",created)
        assert diagnostics['components']==2
        assert diagnostics['territoriesOverlap'] is False
        assert diagnostics['selfA'] is False and diagnostics['selfB'] is False
        assert diagnostics['spans']==1
        assert all(abs(row['source']-row['rendered']) < .04 for row in diagnostics['stationT'])
        assert all(size <= budget*2+8 for size,budget in zip(diagnostics['routeSizes'],diagnostics['budgets']))

        poster=page.locator('#metroPosterPreview .metro-poster-svg')
        text=poster.text_content() or ''
        assert 'Span / phase' in text
        assert 'Interchange' not in text
        assert 'Generated by Breadmaker' not in text
        assert page.locator('#metroPosterPreview .metro-span').count()==1
        assert page.locator('#metroPosterPreview .metro-span').get_attribute('stroke-dasharray')

        families=page.evaluate("""() => {
          const old=state.metroView.seedOffset,seen=[];
          for(let i=0;i<7;i++){
            state.metroView.seedOffset=i;
            const svg=metroRenderPoster(state.mapData,state.metroView).svg;
            seen.push(svg.querySelector('.metro-terrain')?.getAttribute('data-terrain-family')||'none');
          }
          state.metroView.seedOffset=old;
          return seen;
        }""")
        assert len(set(families)) >= 3
        assert not errors
        assert not [entry for entry in console if entry[0]=='error']
    finally:
        _shutdown_page(page)


def test_browser_v463_metro_includes_all_visible_lines_and_enforces_item_edge_spacing(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async () => {
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Metro Inclusion Regression',layout_mode:'schematic',label_mode:'minimal'})});
          const chapters=await api(`/api/maps/${map.id}/populate`,{method:'POST',body:JSON.stringify({template_id:'chapters'})});
          const beats=await api(`/api/maps/${map.id}/populate`,{method:'POST',body:JSON.stringify({template_id:'save_the_cat'})});
          const green=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Green Thread',color:'#42a66c',planned_start:.12,planned_end:.88,schematic_start:.12,schematic_end:.88})});
          const g1=await api(`/api/map-lines/${green.id}/items`,{method:'POST',body:JSON.stringify({title:'Green Start',planned_start:.20,schematic_start:.20})});
          const g2=await api(`/api/map-lines/${green.id}/items`,{method:'POST',body:JSON.stringify({title:'Green End',planned_start:.76,schematic_start:.76})});
          let detail=await api(`/api/maps/${map.id}`),blue=detail.lines.find(l=>l.id===chapters.line_id);
          const blueTarget=(blue.items||[])[0];
          if(blueTarget)await api(`/api/maps/${map.id}/junctions`,{method:'POST',body:JSON.stringify({kind:'branch',from_line_id:green.id,from_edge:'start',to_item_id:blueTarget.id})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,chapters:chapters.line_id,beats:beats.line_id,green:green.id};
        }""")
        _enable_metro(page)
        page.locator('#mapMetroViewBtn').click();page.wait_for_selector('#modal:not(.hidden) .metro-poster-svg')
        assert page.locator('#metroPosterPreview [data-metro-line]').count()==3
        poster_text=page.locator('#metroPosterPreview .metro-poster-svg').text_content() or ''
        assert 'Chapters' in poster_text and 'Save the Cat Beats' in poster_text and 'Green Thread' in poster_text
        toggles=page.locator('#metroLineToggles [data-metro-line-toggle]')
        assert toggles.count()==3
        assert all(toggles.nth(i).is_checked() for i in range(toggles.count()))

        # Every adjacent visual item respects the configured edge-to-edge route floor,
        # including Station -> Span and Span -> Station transitions at identical source positions.
        spacing=page.evaluate("""() => {
          const m=metroBuildPosterModel(state.mapData,state.metroView),rows=[];
          for(const entry of m.lines){
            const resolved=metroResolveLineFractions(entry.line,entry.bounds,entry.routeLength),events=resolved.events;
            for(let i=1;i<events.length;i++)rows.push({line:entry.line.name,actual:(events[i].renderT-events[i-1].renderT)*entry.routeLength,required:metroEventGapMm(events[i-1],events[i]),a:events[i-1].kind,b:events[i].kind});
          }
          return rows;
        }""")
        assert spacing
        assert all(row['actual']+0.03>=row['required'] for row in spacing),spacing
        assert page.locator('#metroPosterPreview .metro-span').count()>=1
        assert page.locator('#metroPosterPreview .metro-span-tick').count()>=2

        # Line inclusion is explicit per Line; topology/provenance never hides a Line implicitly.
        page.locator(f'#metroLineToggles [data-metro-line-toggle="{created["chapters"]}"]').uncheck();page.wait_for_timeout(60)
        page.locator(f'#metroLineToggles [data-metro-line-toggle="{created["beats"]}"]').uncheck();page.wait_for_timeout(80)
        assert page.locator('#metroPosterPreview [data-metro-line]').count()==1
        assert 'Green Thread' in (page.locator('#metroPosterPreview .metro-poster-svg').text_content() or '')
        assert not errors
        assert not [entry for entry in console if entry[0]=='error']
    finally:
        _shutdown_page(page)


def test_browser_v463_metro_routing_is_direction_neutral_and_terrain_obeys_terrain(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.evaluate("""async () => {
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Metro Direction Config',layout_mode:'schematic',label_mode:'minimal'})});
          const line=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Direction Test',color:'#2478b9',planned_start:0,planned_end:1,schematic_start:0,schematic_end:1})});
          for(const [i,p] of [[1,.05],[2,.18],[3,.34],[4,.50],[5,.68],[6,.84],[7,.96]])await api(`/api/map-lines/${line.id}/items`,{method:'POST',body:JSON.stringify({title:`Point ${i}`,planned_start:p,schematic_start:p})});
          await refresh(false);setView('maps');await selectMap(map.id);
        }""")
        _enable_metro(page)
        page.locator('#mapMetroViewBtn').click();page.wait_for_selector('#modal:not(.hidden) .metro-poster-svg')
        direction=page.evaluate("""() => {
          const line=state.mapData.lines[0],territory={x:20,y:50,w:165,h:170},seen=[];
          for(let seed=1;seed<=12;seed++)seen.push(metroPlanCandidate({x:territory.x+8,y:territory.y+8,w:territory.w-16,h:territory.h-16},line,seed,0,90,metroLineTurnBudget(line),[]).initialHeading);
          const original={...state.metroConfig.routing.initial_heading_weights};
          for(const key of Object.keys(state.metroConfig.routing.initial_heading_weights))state.metroConfig.routing.initial_heading_weights[key]=0;
          state.metroConfig.routing.initial_heading_weights['270']=100;
          const forced=metroPlanCandidate({x:28,y:58,w:149,h:154},line,77,0,80,0,[]).initialHeading;
          state.metroConfig.routing.initial_heading_weights=original;
          return {seen,forced};
        }""")
        assert direction['forced']==270
        assert len(set(direction['seen']))>=3
        assert any(angle!=90 for angle in direction['seen'])

        page.locator('#metroTerrain').select_option('full');page.wait_for_timeout(80)
        field_count=page.locator('#metroPosterPreview .metro-topography-contour').count()
        assert field_count>=30
        terrain=page.evaluate("""() => {
          const m=metroBuildPosterModel(state.mapData,state.metroView),plans=[];
          for(let seed=0;seed<30;seed++){
            const plan=metroBuildTerrainPlan(m,seed);
            if(plan)plans.push(plan);
          }
          const mixed=plans.find(plan=>plan.rivers.length&&plan.highlands.length);
          if(!mixed)return {families:[...new Set(plans.map(p=>p.family))],found:false};
          let collision=false;
          for(const river of mixed.rivers)for(const point of river)for(const highland of mixed.highlands)if(metroTerrainEllipseContains({cx:highland.cx,cy:highland.cy,rx:highland.rx*1.25,ry:highland.ry*1.25},point,0))collision=true;
          return {families:[...new Set(plans.map(p=>p.family))],found:true,collision};
        }""")
        assert len(terrain['families'])>=4
        assert terrain['found'] is True
        assert terrain['collision'] is False
        assert page.locator('#metroPosterPreview .metro-terrain').get_attribute('data-full-topography')=='true'
        assert not errors
        assert not [entry for entry in console if entry[0]=='error']
    finally:
        _shutdown_page(page)



def test_browser_v464_metro_scale_connections_separation_labels_and_topography(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async () => {
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Metro 464 Regression',layout_mode:'schematic',label_mode:'minimal'})});
          const blue=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Main Story',color:'#2d6cb4',planned_start:.02,planned_end:.98,schematic_start:.02,schematic_end:.98})});
          const green=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Short Green',color:'#48a06d',planned_start:.43,planned_end:.58,schematic_start:.43,schematic_end:.58})});
          const pink=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Independent Pink',color:'#ec3d9d',planned_start:.12,planned_end:.88,schematic_start:.12,schematic_end:.88})});
          const blueItems=[];
          for(const [i,p] of [[1,.03],[2,.12],[3,.28],[4,.50],[5,.71],[6,.90],[7,.97]])blueItems.push(await api(`/api/map-lines/${blue.id}/items`,{method:'POST',body:JSON.stringify({title:`Blue ${i}`,planned_start:p,schematic_start:p})}));
          const g1=await api(`/api/map-lines/${green.id}/items`,{method:'POST',body:JSON.stringify({title:'Green Start',planned_start:.45,schematic_start:.45})});
          const g2=await api(`/api/map-lines/${green.id}/items`,{method:'POST',body:JSON.stringify({title:'Green End',planned_start:.56,schematic_start:.56})});
          for(const [i,p] of [[1,.15],[2,.35],[3,.62],[4,.85]])await api(`/api/map-lines/${pink.id}/items`,{method:'POST',body:JSON.stringify({title:`Pink ${i}`,planned_start:p,schematic_start:p})});
          await api(`/api/map-lines/${blue.id}/items`,{method:'POST',body:JSON.stringify({item_type:'span',title:'Blue Span',planned_start:.285,planned_end:.40,schematic_start:.285,schematic_end:.40})});
          await api(`/api/maps/${map.id}/junctions`,{method:'POST',body:JSON.stringify({kind:'branch',from_item_id:g1.id,to_item_id:blueItems[3].id})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,blue:blue.id,green:green.id,pink:pink.id};
        }""")
        _enable_metro(page)
        page.locator('#mapMetroViewBtn').click();page.wait_for_selector('#modal:not(.hidden) .metro-poster-svg')
        diagnostics=page.evaluate("""ids => {
          const m=metroBuildPosterModel(state.mapData,state.metroView),by=id=>m.lines.find(e=>e.line.id===id),blue=by(ids.blue),green=by(ids.green),pink=by(ids.pink),sep=Number(metroConfig('routing.segments.line_separation_mm',0));
          const spacing=[];for(const entry of m.lines){const resolved=metroResolveLineFractions(entry.line,entry.bounds,entry.routeLength),events=resolved.events;for(let i=1;i<events.length;i++)spacing.push({actual:(events[i].renderT-events[i-1].renderT)*entry.routeLength,required:metroEventGapMm(events[i-1],events[i])})}
          const labels=metroPosterLabels(m);
          return {blue:blue.routeLength,green:green.routeLength,pink:pink.routeLength,blueGreenDistance:metroRoutesDistance(blue.route,green.route),sep,spacing,labelHits:labels.filter(label=>metroLabelNetworkHits(m,label.rect)).length,labels:labels.length};
        }""",created)
        assert diagnostics['green'] < diagnostics['blue']*.72, diagnostics
        assert diagnostics['blueGreenDistance']+0.05 >= diagnostics['sep'], diagnostics
        assert all(row['actual']+0.03>=row['required'] for row in diagnostics['spacing']), diagnostics
        assert diagnostics['labels']>0 and diagnostics['labelHits']==0, diagnostics

        connector=page.locator('#metroPosterPreview .metro-connection')
        assert connector.count()>=1
        assert connector.first.get_attribute('stroke-dasharray') is None
        assert page.locator('#metroPosterPreview .metro-span').count()==1
        assert page.locator('#metroPosterPreview .metro-span-tick').count()==2
        assert 'BREADMAKER METRO VIEW' not in (page.locator('#metroPosterPreview .metro-poster-svg').text_content() or '')

        # Full topography is a genuine nested elevation field; waterways avoid high-elevation mountain cores.
        page.locator('#metroTerrain').select_option('full');page.wait_for_timeout(100)
        topo=page.evaluate("""() => {
          const m=metroBuildPosterModel(state.mapData,state.metroView),plan=metroBuildTerrainPlan(m,metroPosterSeed(state.mapData,state.metroView));
          const levels=[...document.querySelectorAll('#metroPosterPreview [data-contour-level]')].map(el=>+el.getAttribute('data-contour-level'));
          let highRiver=false;
          for(const river of plan.rivers)for(const p of river)if(metroElevationAt(plan.elevation,p) > Number(metroConfig('terrain.topography.high_elevation_threshold',.5)))highRiver=true;
          return {levels:[...new Set(levels)].length,segments:levels.length,highRiver,peaks:plan.highlands.length};
        }""")
        assert topo['levels']>=7 and topo['segments']>=40 and topo['peaks']>=3, topo
        assert topo['highRiver'] is False, topo
        assert not errors
        assert not [entry for entry in console if entry[0]=='error']
    finally:
        _shutdown_page(page)


def test_browser_v465_metro_long_lines_stay_bounded_avoid_snakes_and_connectors_depart_cleanly(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async () => {
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Metro 465 Quality',layout_mode:'scaled',label_mode:'minimal'})});
          const blue=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'63 Chapters',color:'#2d6cb4',planned_start:.01,planned_end:.99,schematic_start:.01,schematic_end:.99})});
          const blueItems=[];
          for(let i=0;i<63;i++){
            const p=.01+.98*(i/62);
            blueItems.push(await api(`/api/map-lines/${blue.id}/items`,{method:'POST',body:JSON.stringify({title:`Chapter ${i+1}`,planned_start:p,schematic_start:p})}));
          }
          const green=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Short Thread',color:'#48a06d',planned_start:.44,planned_end:.57,schematic_start:.44,schematic_end:.57})});
          const g1=await api(`/api/map-lines/${green.id}/items`,{method:'POST',body:JSON.stringify({title:'Thread Start',planned_start:.45,schematic_start:.45})});
          const g2=await api(`/api/map-lines/${green.id}/items`,{method:'POST',body:JSON.stringify({title:'Thread End',planned_start:.56,schematic_start:.56})});
          await api(`/api/maps/${map.id}/junctions`,{method:'POST',body:JSON.stringify({kind:'branch',from_item_id:g1.id,to_item_id:blueItems[31].id})});
          await refresh(false);setView('maps');await selectMap(map.id);
          return {map:map.id,blue:blue.id,green:green.id};
        }""")
        # Toolbar icon is optically centered before the modal changes the page composition.
        centers=page.evaluate("""() => {const b=document.querySelector('#mapMetroViewBtn').getBoundingClientRect(),i=document.querySelector('#mapMetroViewBtn .metro-view-icon').getBoundingClientRect();return{dx:Math.abs((b.left+b.width/2)-(i.left+i.width/2)),dy:Math.abs((b.top+b.height/2)-(i.top+i.height/2))}}""")
        assert centers['dx'] < .75 and centers['dy'] < .75, centers

        _enable_metro(page)
        page.locator('#mapMetroViewBtn').click();page.wait_for_selector('#modal:not(.hidden) .metro-poster-svg')
        diagnostics=page.evaluate("""ids => {
          const m=metroBuildPosterModel(state.mapData,state.metroView),blue=m.lines.find(e=>e.line.id===ids.blue),green=m.lines.find(e=>e.line.id===ids.green),c=m.connectors[0],rmax=Number(metroConfig('spacing.interchange_radius_mm',2.15));
          const stationsInside=m.lines.every(e=>e.stations.every(s=>s.coord.x-rmax>=m.map.x-.01&&s.coord.x+rmax<=m.map.x+m.map.w+.01&&s.coord.y-rmax>=m.map.y-.01&&s.coord.y+rmax<=m.map.y+m.map.h+.01));
          const routesInside=m.lines.every(e=>metroRouteInsideBox(e.route,m.map,0));
          const connInside=m.connectors.every(x=>metroRouteInsideBox(x.route||[x.from,x.to],m.map,.45));
          const firstDelta=c?metroHeadingDelta(metroSegmentHeading(c.route[0],c.route[1]),metroRouteTangent(c.fromEntry.route,c.fromEntry.stations.find(s=>s.item.id===c.junction.from_item_id)?.t??.5).angle):0;
          const lastDelta=c?metroHeadingDelta(metroSegmentHeading(c.route.at(-2),c.route.at(-1)),metroRouteTangent(c.toEntry.route,c.toEntry.stations.find(s=>s.item.id===c.junction.to_item_id)?.t??.5).angle):0;
          return {snake:metroSnakePenalty(blue.route),sharp:metroSharpTurnPenalty(blue.route),points:blue.route.length,routesInside,stationsInside,connInside,connectionPoints:c?.route?.length||0,firstDelta,lastDelta,bottomMargin:m.paper.h-m.legendY-m.legendH,blueLength:blue.routeLength,greenLength:green.routeLength};
        }""",created)
        assert diagnostics['routesInside'] and diagnostics['stationsInside'] and diagnostics['connInside'], diagnostics
        assert diagnostics['snake'] < 8, diagnostics
        assert diagnostics['sharp'] < 8, diagnostics
        assert diagnostics['connectionPoints'] >= 4, diagnostics
        assert diagnostics['firstDelta'] == 90 and diagnostics['lastDelta'] == 90, diagnostics
        assert 4.5 <= diagnostics['bottomMargin'] <= 5.5, diagnostics
        assert diagnostics['greenLength'] < diagnostics['blueLength']*.65, diagnostics

        connector=page.locator('#metroPosterPreview .metro-connection').first
        source_path=page.locator(f'#metroPosterPreview [data-metro-line="{created["green"]}"]').first
        connector_width=float(connector.get_attribute('stroke-width'))
        source_width=float(source_path.get_attribute('stroke-width'))
        assert source_width*.78 < connector_width < source_width*.88, (connector_width,source_width)

        # Landscape preview itself must retain the actual A4 landscape aspect ratio.
        page.locator('#metroOrientation').select_option('landscape');page.wait_for_timeout(100)
        ratio=page.evaluate("""() => {const r=document.querySelector('#metroPosterPreview .metro-poster-svg').getBoundingClientRect();return r.width/r.height}""")
        assert abs(ratio-(297/210)) < .025, ratio
        assert not errors
        assert not [entry for entry in console if entry[0]=='error']
    finally:
        _shutdown_page(page)


def test_browser_v47_maps_compact_rail_exact_fit_and_span_three_point_targets(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Zebra Map 4.7',layout_mode:'scaled',label_mode:'hidden',ruler:'chapters'})});
          const orange=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Orange Story',color:'#f3680e',planned_start:.05,planned_end:.95,schematic_start:.05,schematic_end:.95})});
          const blue=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Blue Duration',color:'#0e8ef3',planned_start:.08,planned_end:.92,schematic_start:.08,schematic_end:.92})});
          const source=await api(`/api/map-lines/${orange.id}/items`,{method:'POST',body:JSON.stringify({title:'Source',planned_start:.25,schematic_start:.25})});
          const span=await api(`/api/map-lines/${blue.id}/items`,{method:'POST',body:JSON.stringify({item_type:'span',title:'Long Span',planned_start:.30,planned_end:.70,schematic_start:.30,schematic_end:.70})});
          await refresh(false);setView('maps');await selectMap(map.id);return{map:map.id,orange:orange.id,blue:blue.id,source:source.id,span:span.id,color:blue.color};
        }""")
        page.wait_for_selector('#mapSvg [data-map-line-route]',state='attached')
        # Durable workflows must not inherit another test's local presentation scale.
        page.evaluate("()=>{applyMapTextSize(16,{persist:false,render:false});applyMapRulerScale(1,{persist:false,render:false});renderMapVisual()}")

        # Collapsing keeps a real compact navigation rail instead of removing it.
        page.locator('#toggleMapRail').click();page.wait_for_timeout(220)
        rail=page.locator('#mapRail').bounding_box();assert rail and 56 <= rail['width'] <= 60
        assert page.locator('#mapRail .map-tree-map-short').filter(has_text='Z').is_visible()
        line_row=page.locator(f'#mapRail [data-map-tree-line="{created["blue"]}"]')
        assert line_row.locator('.map-tree-line-short').is_hidden()
        dot=line_row.locator('.map-tree-line-dot'); assert dot.is_visible()
        assert dot.evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(14, 142, 243)'

        # Horizontal ruler gets dedicated breathing room before the first Line.
        first_lane=page.evaluate('(id)=>document.querySelector("#mapSvg")._mapLayout.lineLane.get(id)',created['orange'])
        assert first_lane >= 114

        # Fit solves the final zoom-dependent layout, not the stale 100% layout.
        page.evaluate('()=>setMapZoom(2.5)');page.wait_for_timeout(80)
        page.locator('#mapViewBtn').click();page.wait_for_selector('#mapViewPopover:not(.hidden)')
        page.locator('#mapViewPopover #mapZoomFit').click();page.wait_for_timeout(180)
        fits=page.evaluate("""()=>{const stage=document.querySelector('#mapStage'),svg=document.querySelector('#mapSvg'),s=stage.getBoundingClientRect(),b=svg.getBoundingClientRect();return{zoom:state.mapZoom,w:b.width,h:b.height,sw:stage.clientWidth,sh:stage.clientHeight}}""")
        assert fits['zoom'] < 2.5
        assert fits['w'] <= fits['sw'] + 1 and fits['h'] <= fits['sh'] + 1

        # While linking, a Span exposes start / middle / end as real targets.
        page.evaluate('(id)=>beginMapLineConnection(mapLineById(id),.25)',created['orange'])
        page.wait_for_selector(f'#mapSvg [data-map-span-item="{created["span"]}"][data-map-span-anchor="start"]',state='attached')
        assert page.locator(f'#mapSvg [data-map-span-item="{created["span"]}"]').count()==3
        page.locator(f'#mapSvg [data-map-span-item="{created["span"]}"][data-map-span-anchor="end"]').click();page.wait_for_timeout(350)
        detail=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        junction=detail['junctions'][-1]
        assert junction['to_item_id']==created['span'] and junction['to_edge']=='end'

        # A Span can also originate a connection from its semantic middle.
        page.evaluate('([lineId,itemId])=>beginMapItemConnection(mapLineById(lineId),mapItemById(itemId).item,"point")',[created['blue'],created['span']])
        page.wait_for_selector(f'#mapSvg [data-map-item="{created["source"]}"]',state='attached')
        page.locator(f'#mapSvg [data-map-item="{created["source"]}"]').click();page.wait_for_timeout(350)
        detail=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        middle=detail['junctions'][-1]
        assert middle['from_item_id']==created['span'] and middle['from_edge']=='point'
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v471_maps_toolbar_double_click_focus_and_color_autosave(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'4.7.1 Details',layout_mode:'scaled',label_mode:'hidden',ruler:'chapters'})});
          const a=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Alpha',color:'#f3680e',planned_start:.1,planned_end:.9,schematic_start:.1,schematic_end:.9})});
          const b=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Beta',color:'#0e8ef3',planned_start:.15,planned_end:.85,schematic_start:.15,schematic_end:.85})});
          await refresh(false);setView('maps');await selectMap(map.id);return{map:map.id,a:a.id,b:b.id};
        }""")
        page.wait_for_selector(f'#mapSvg [data-map-line-route="{created["a"]}"]',state='attached')

        # The current Maps toolbar leaves exactly the rail-owned collapse control.
        # View and Metro are top-level; Fit belongs to the View popover.
        assert page.locator('#mapSidebarToggle').count()==0
        assert page.locator('#toggleMapRail').count()==1
        order=page.evaluate("""()=>['mapViewBtn','mapMetroViewBtn'].map(id=>{const el=document.getElementById(id);return [...document.querySelector('.map-toolbar').children].findIndex(child=>child===el||child.contains(el))})""")
        assert order[0]>=0 and order[1]>order[0], order
        assert page.locator('#mapViewPopover #mapZoomFit').count()==1

        # Route double-click opens the current Line Details editor and must not
        # create a Station as the historical interaction once did.
        before=page.evaluate('(id)=>mapLineById(id).items.length',created['a'])
        page.locator(f'#mapSvg [data-map-line-route="{created["a"]}"]').first.dispatch_event('dblclick')
        page.wait_for_selector('#modal:not(.hidden) #mapLineDetailsName')
        assert page.locator('#modalTitle').inner_text()=='Line Details'
        assert page.locator('#mapLineDetailsName').input_value()=='Alpha'
        after=page.evaluate('(id)=>mapLineById(id).items.length',created['a'])
        assert after==before
        page.locator('#cancelMapLineDetails').click();page.wait_for_selector('#modal',state='hidden')

        # The rail row owns the same Details interaction. Color changes are now
        # explicit modal edits, not the retired inspector autosave field.
        page.locator(f'#mapTree [data-map-tree-line="{created["b"]}"]').dblclick()
        page.wait_for_selector('#modal:not(.hidden) #mapLineDetailsColorText')
        assert page.locator('#modalTitle').inner_text()=='Line Details'
        assert page.locator('#mapLineDetailsName').input_value()=='Beta'
        color='#34a853'
        page.locator('#mapLineDetailsColorText').fill(color)
        page.locator('#saveMapLineDetails').click();page.wait_for_selector('#modal',state='hidden')
        page.wait_for_function('(id)=>mapLineById(id)?.color?.toLowerCase()==="#34a853"',arg=created['b'])
        stroke=page.locator(f'#mapSvg [data-map-line-route="{created["b"]}"]').first.get_attribute('stroke')
        assert stroke.lower()==color
        detail=page.evaluate('(id)=>api(`/api/maps/${id}`)',created['map'])
        saved=next(line for line in detail['lines'] if line['id']==created['b'])
        assert saved['color'].lower()==color
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v472_route_state_contract_and_empty_map_double_click_clears_focus(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        # Route parsing is deterministic even though this sandbox does not permit
        # Chromium top-level navigation to a local HTTP origin.
        routes=page.evaluate("""()=>['/','/canvas','/maps','/tasks','/maps/'].map(path=>[path,viewFromRoute(path)])""")
        assert routes == [['/','manuscript'],['/canvas','canvas'],['/maps','maps'],['/tasks','tasks'],['/maps/','maps']]

        # setView owns route synchronization. Stub only the browser-history writer so
        # the normal workspace transition can be verified on the sandbox's about:blank origin.
        transition=page.evaluate("""()=>{
          const calls=[];syncViewRoute=(view,options={})=>calls.push([view,options.replace===true]);
          setView('canvas');setView('maps',{historyMode:'replace'});return {calls,active:document.querySelector('.view.active')?.id};
        }""")
        assert transition['calls'] == [['canvas',False],['maps',True]]
        assert transition['active'] == 'mapsView'

        created=page.evaluate("""async()=>{
          const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'4.7.2 Route Details',layout_mode:'scaled',label_mode:'hidden',ruler:'chapters'})});
          const line=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Open Me',color:'#7a4dd8',planned_start:.1,planned_end:.9,schematic_start:.1,schematic_end:.9})});
          await refresh(false);setView('maps',{historyMode:'none'});await selectMap(map.id);return{map:map.id,line:line.id};
        }""")
        route_line=page.locator(f'#mapSvg [data-map-line-route="{created["line"]}"]').first
        route_line.wait_for(state='attached')

        # The current route double-click contract is Details, not the retired
        # user-facing Focus Line mode. Closing Details returns to the map cleanly.
        route_line.dispatch_event('dblclick')
        page.wait_for_selector('#modal:not(.hidden) #mapLineDetailsName')
        assert page.locator('#modalTitle').inner_text()=='Line Details'
        assert page.locator('#mapLineDetailsName').input_value()=='Open Me'
        page.locator('#cancelMapLineDetails').click();page.wait_for_selector('#modal',state='hidden')
        assert page.evaluate('()=>state.currentMapLineId')==created['line']

        # Popstate resolves the current route back into a workspace. Stub route lookup
        # because location.pathname cannot be changed from the sandbox's opaque origin.
        page.evaluate("""()=>{viewFromRoute=()=> 'tasks';window.dispatchEvent(new PopStateEvent('popstate'))}""")
        page.wait_for_selector('#tasksView.active')
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v48_editors_eye_is_temporary_rerolls_and_respects_theme(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        before=page.evaluate("""() => {
          const s=document.querySelector('#documentScroller'),e=document.querySelector('#editor'),p=document.querySelector('.paper'),t=document.querySelector('.document-toolbar');
          return {html:e.innerHTML,bg:getComputedStyle(s).backgroundColor,font:getComputedStyle(e).fontFamily,size:getComputedStyle(e).fontSize,width:getComputedStyle(p).width,toolbar:getComputedStyle(t).backgroundColor,dirty:state.dirty};
        }""")
        button=page.locator('#editorsEyeBtn')
        tip=page.locator('#editorsEyeTooltip')
        assert button.get_attribute('aria-pressed')=='false'
        assert button.get_attribute('title') is None
        button.hover(); page.wait_for_timeout(430)
        assert tip.is_visible()
        assert 'Editor’s Eye' in tip.inner_text()
        assert 'Temporarily changes your font, background, and layout' in tip.inner_text()
        page.locator('#editor').hover(); page.wait_for_timeout(50)
        assert not tip.is_visible()

        button.click()
        # 4.8.1 keeps the old manuscript under the leading edge of the wave, then swaps once.
        page.wait_for_timeout(55)
        early_on=page.evaluate("""() => ({active:document.querySelector('#documentScroller').classList.contains('editors-eye-active'),waves:document.querySelectorAll('.editors-eye-wave').length})""")
        assert not early_on['active'] and early_on['waves']==1
        page.wait_for_timeout(135)
        first=page.evaluate("""() => {
          const s=document.querySelector('#documentScroller'),e=document.querySelector('#editor'),p=document.querySelector('.paper'),t=document.querySelector('.document-toolbar');
          return {active:s.classList.contains('editors-eye-active'),pressed:document.querySelector('#editorsEyeBtn').getAttribute('aria-pressed'),index:state.editorsEyePresetIndex,font:getComputedStyle(e).fontFamily,size:getComputedStyle(e).fontSize,bg:getComputedStyle(s).backgroundColor,width:getComputedStyle(p).width,toolbar:getComputedStyle(t).backgroundColor,html:e.innerHTML,dirty:state.dirty,waves:document.querySelectorAll('.editors-eye-wave').length};
        }""")
        assert first['active'] and first['pressed']=='true' and first['index']>=0
        assert first['font']!=before['font'] or first['size']!=before['size']
        assert first['bg']!=before['bg']
        assert first['toolbar']==before['toolbar']
        assert first['html']==before['html'] and first['dirty']==before['dirty']

        page.wait_for_timeout(400)
        assert page.locator('.editors-eye-wave').count()==0
        button.hover(); page.wait_for_timeout(430)
        assert 'On — click to restore' in tip.inner_text()
        page.locator('#editor').hover(); page.wait_for_timeout(30)

        button.click()
        # The reported 4.8.0 bug: normal styles must NOT be restored before the return wave.
        page.wait_for_timeout(55)
        early_off=page.evaluate("""() => ({active:document.querySelector('#documentScroller').classList.contains('editors-eye-active'),bg:getComputedStyle(document.querySelector('#documentScroller')).backgroundColor,waves:document.querySelectorAll('.editors-eye-wave').length})""")
        assert early_off['active'] and early_off['bg']==first['bg'] and early_off['waves']==1
        page.wait_for_timeout(135)
        off=page.evaluate("""() => ({active:document.querySelector('#documentScroller').classList.contains('editors-eye-active'),pressed:document.querySelector('#editorsEyeBtn').getAttribute('aria-pressed'),font:getComputedStyle(document.querySelector('#editor')).fontFamily,size:getComputedStyle(document.querySelector('#editor')).fontSize,bg:getComputedStyle(document.querySelector('#documentScroller')).backgroundColor,html:document.querySelector('#editor').innerHTML,dirty:state.dirty})""")
        assert not off['active'] and off['pressed']=='false'
        assert off['font']==before['font'] and off['size']==before['size'] and off['bg']==before['bg']
        assert off['html']==before['html'] and off['dirty']==before['dirty']

        page.wait_for_timeout(350)
        button.click(); page.wait_for_timeout(190)
        second=page.evaluate("""() => ({index:state.editorsEyePresetIndex,font:getComputedStyle(document.querySelector('#editor')).fontFamily,size:getComputedStyle(document.querySelector('#editor')).fontSize,bg:getComputedStyle(document.querySelector('#documentScroller')).backgroundColor})""")
        assert second['index']!=first['index']
        assert second['font']!=first['font'] and second['size']!=first['size'] and second['bg']!=first['bg']

        page.locator('#themeBtn').click(); page.wait_for_timeout(90)
        dusk=page.evaluate("""() => ({theme:document.documentElement.dataset.theme,index:state.editorsEyePresetIndex,bg:getComputedStyle(document.querySelector('#documentScroller')).backgroundColor,active:document.querySelector('#documentScroller').classList.contains('editors-eye-active')})""")
        assert dusk['theme']=='dusk' and dusk['active'] and dusk['index']==second['index'] and dusk['bg']!=second['bg']
        page.locator('#themeBtn').click(); page.wait_for_timeout(90)
        dark=page.evaluate("""() => ({theme:document.documentElement.dataset.theme,index:state.editorsEyePresetIndex,bg:getComputedStyle(document.querySelector('#documentScroller')).backgroundColor,active:document.querySelector('#documentScroller').classList.contains('editors-eye-active')})""")
        assert dark['theme']=='dark' and dark['active'] and dark['index']==second['index'] and dark['bg']!=dusk['bg']

        page.locator('[data-view="canvas"]').click(); page.wait_for_timeout(100)
        page.locator('[data-view="manuscript"]').click(); page.wait_for_timeout(100)
        assert page.locator('#documentScroller').evaluate("el=>el.classList.contains('editors-eye-active')") is True
        assert page.evaluate('state.editorsEyePresetIndex')==second['index']
        assert not errors, errors
        assert not [text for typ,text in console if typ=='error' and 'favicon' not in text.lower()], console
    finally:
        _shutdown_page(page)


def test_browser_v49_task_milestones_are_lightweight_progressive_and_linkable(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.locator('[data-view="tasks"]').click();page.wait_for_selector('#tasksView.active')
        page.locator('#newTaskBtn').click();page.wait_for_selector('#modal:not(.hidden) #taskMilestones')
        page.locator('#taskName').fill('Developmental edit Chapter 12')

        add=page.locator('#taskMilestoneNew')
        add.fill('Strengthen opening');add.press('Enter')
        page.wait_for_selector('[data-milestone-id="draft_1"]')
        page.locator('[data-milestone-id="draft_1"] [data-milestone-description]').fill('Make the first page land faster.')
        add.fill('Final continuity read');add.press('Enter')
        page.wait_for_selector('[data-milestone-id="draft_2"]')
        assert page.locator('#taskMilestoneProgress').inner_text()=='0 / 2'
        page.locator('#saveTask').click();page.wait_for_selector('#modal',state='hidden')

        row=page.locator('.task-row').filter(has_text='Developmental edit Chapter 12')
        row.wait_for(state='visible')
        assert '0 / 2 milestones' in row.inner_text()
        row.click();page.wait_for_selector('#modal:not(.hidden) [data-milestone-id]')

        cards=page.locator('#taskMilestones .milestone-card')
        assert cards.count()==2
        cards.nth(0).locator('[data-milestone-check]').check();page.wait_for_timeout(180)
        assert page.locator('#taskMilestoneProgress').inner_text()=='1 / 2'
        assert page.locator('#taskStatus').input_value()=='open'

        # Expanded milestone supports both Breadmaker and ordinary web references.
        first=page.locator('#taskMilestones .milestone-card').nth(0)
        first.locator('[data-milestone-toggle]').click();page.wait_for_timeout(80)
        first=page.locator('#taskMilestones .milestone-card').nth(0)
        assert first.locator('.milestone-details').is_visible()
        # The visible chevron is part of the toggle target: clicking it collapses and reopens the Milestone.
        first.locator('[data-milestone-chevron]').click();page.wait_for_timeout(60)
        first=page.locator('#taskMilestones .milestone-card').nth(0)
        assert not first.locator('.milestone-details').is_visible()
        assert first.locator('[data-milestone-toggle]').get_attribute('aria-expanded')=='false'
        first.locator('[data-milestone-chevron]').click();page.wait_for_timeout(60)
        first=page.locator('#taskMilestones .milestone-card').nth(0)
        assert first.locator('.milestone-details').is_visible()
        assert first.locator('[data-milestone-toggle]').get_attribute('aria-expanded')=='true'
        first.locator('[data-milestone-add-web]').click();page.wait_for_selector('#milestoneWebUrl')
        page.locator('#milestoneWebLabel').fill('Style guide')
        page.locator('#milestoneWebUrl').fill('https://example.com/style')
        page.locator('#milestoneWebSave').click()
        milestone_link=page.locator('[data-milestone-links] a').filter(has_text='Style guide')
        milestone_link.wait_for(state='visible')
        assert milestone_link.count()==1
        milestone_count=page.locator('[data-milestone-link-count]').filter(has_text='1 link')
        milestone_count.wait_for(state='visible')
        assert milestone_count.count()==1

        # Completing every milestone never silently completes the parent Task.
        page.locator('#taskMilestones .milestone-card').nth(1).locator('[data-milestone-check]').check();page.wait_for_timeout(180)
        assert 'All complete' in page.locator('#taskMilestoneProgress').inner_text()
        assert page.locator('#taskStatus').input_value()=='open'
        page.locator('#modalClose').click();page.wait_for_selector('#modal',state='hidden')
        row=page.locator('.task-row').filter(has_text='Developmental edit Chapter 12');row.wait_for(state='visible')
        assert '2 / 2 milestones' in row.inner_text()

        # Reopen one milestone, then task completion asks before overriding unfinished work.
        row.click();page.wait_for_selector('#modal:not(.hidden) [data-milestone-id]')
        page.locator('#taskMilestones .milestone-card').nth(1).locator('[data-milestone-check]').uncheck();page.wait_for_timeout(160)
        page.locator('#modalClose').click();page.wait_for_selector('#modal',state='hidden')
        row=page.locator('.task-row').filter(has_text='Developmental edit Chapter 12')
        checkbox=row.locator('.task-check input');checkbox.check();page.wait_for_selector('#dialogConfirm')
        assert 'still has 1 unfinished milestone' in page.locator('.dialog-message').inner_text()
        page.locator('#dialogCancel').click();page.wait_for_timeout(80)
        assert checkbox.is_checked() is False

        task=page.evaluate("() => api('/api/tasks').then(rows=>rows.find(t=>t.title==='Developmental edit Chapter 12'))")
        assert task['status']=='open' and task['milestone_count']==2 and task['milestone_completed_count']==1

        # Breadmaker links are real navigation, not inert attachment labels.
        row=page.locator('.task-row').filter(has_text='Developmental edit Chapter 12');row.click();page.wait_for_selector('#modal:not(.hidden) [data-milestone-id]')
        first=page.locator('#taskMilestones .milestone-card').nth(0);first.locator('[data-milestone-toggle]').click();page.wait_for_timeout(60)
        first=page.locator('#taskMilestones .milestone-card').nth(0);first.locator('[data-milestone-add-link]').click();page.wait_for_selector('#entityLinkType')
        page.locator('#entityLinkType').select_option('chapter');page.wait_for_timeout(60)
        page.locator('#saveEntityLink').click();page.wait_for_timeout(120)
        page.locator('[data-milestone-links] .reference-target').click();page.wait_for_selector('#manuscriptView.active')

        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)



def test_browser_v410_manuscript_outline_lives_in_sidebar(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const boot=await api('/api/bootstrap'),ch=boot.chapters[0];
          await api(`/api/chapters/${ch.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[
            {block_type:'paragraph',semantic_style:'body',content_html:'Opening prose',text_content:'Opening prose'},
            {block_type:'paragraph',semantic_style:'heading2',content_html:'Section A',text_content:'Section A'},
            {block_type:'paragraph',semantic_style:'body',content_html:'A prose',text_content:'A prose'},
            {block_type:'paragraph',semantic_style:'heading3',content_html:'Section B',text_content:'Section B'},
            {block_type:'paragraph',semantic_style:'heading4',content_html:'Section C',text_content:'Section C'},
            {block_type:'paragraph',semantic_style:'body',content_html:'C prose',text_content:'C prose'}
          ]})});await refresh(false);await selectChapter(ch.id,false);return{first:ch.id};
        }""")
        page.wait_for_timeout(80)
        assert page.locator('.outline-toggle').count()==0
        assert page.locator('.book-chapter-toggle').count()==0
        chapter_row=page.locator(f'.chapter-row[data-id="{created["first"]}"]')
        assert chapter_row.locator('.chapter-outline-toggle:not(.placeholder)').count()==1
        assert page.locator('.outline-nav').count()==3
        assert page.locator('.outline-nav').nth(0).inner_text().strip().endswith('Section A')
        page.locator('.outline-nav').nth(0).locator('.outline-nav-toggle:not(.placeholder)').click();page.wait_for_timeout(60)
        assert page.locator('.outline-nav').count()==1
        assert page.locator('.manuscript-block-wrap').filter(has_text='Section B').is_visible()
        chapter_row.locator('.chapter-outline-toggle').click();page.wait_for_timeout(60)
        assert page.locator('.outline-nav').count()==0
        assert page.locator('.manuscript-block-wrap').filter(has_text='A prose').is_visible()
        chapter_row.locator('.chapter-outline-toggle').click();page.wait_for_timeout(60)
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v410_h1_creates_chapter_in_focused_and_book_views(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const boot=await api('/api/bootstrap'),ch=boot.chapters[0];
          await api(`/api/chapters/${ch.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[
            {block_type:'paragraph',semantic_style:'body',content_html:'Opening prose',text_content:'Opening prose'},
            {block_type:'paragraph',semantic_style:'body',content_html:'Chapter From Line',text_content:'Chapter From Line'},
            {block_type:'paragraph',semantic_style:'body',content_html:'New chapter prose',text_content:'New chapter prose'}
          ]})});await refresh(false);await selectChapter(ch.id,false);return{first:ch.id};
        }""")
        _select_manuscript_block_text(page,'Chapter From Line')
        opened=page.evaluate("""()=>{
          const button=document.querySelector('[data-testid=\"manuscript-selection-style\"]');
          if(!button)return false;button.click();
          return !!document.querySelector('[data-testid=\"manuscript-style-chapter\"]');
        }""")
        assert opened
        page.evaluate("""()=>document.querySelector('[data-testid=\"manuscript-style-chapter\"]')?.click()""")
        page.wait_for_function("()=>state.viewMode==='chapter'&&state.chapter?.title==='Chapter From Line'",timeout=2500)
        assert page.locator('#chapterTitle').inner_text().strip()=='Chapter From Line'
        boot=page.evaluate("()=>api('/api/bootstrap')")
        new_ch=next(ch for ch in boot['chapters'] if ch['title']=='Chapter From Line')
        detail=page.evaluate("id=>api(`/api/chapters/${id}`)",new_ch['id'])
        assert [b['text_content'] for b in detail['blocks']]==['New chapter prose']

        page.locator('#bookViewBtn').click();page.wait_for_function("()=>state.viewMode==='book'")
        last=page.locator('.manuscript-block-wrap').filter(has_text='New chapter prose').first
        last.locator('[data-testid="manuscript-block"]').click();page.keyboard.press('End');page.keyboard.press('Enter');page.keyboard.type('Another Chapter')
        page.evaluate('flushSave()');page.wait_for_function("()=>!state.saveInFlight&&!state.dirty&&state.dirtyChapters.size===0",timeout=5000)
        page.wait_for_function("()=>[...document.querySelectorAll('[data-testid=\"manuscript-block\"]')].some(x=>x.textContent==='Another Chapter')")
        _select_manuscript_block_text(page,'Another Chapter')
        opened=page.evaluate("""()=>{
          const button=document.querySelector('[data-testid=\"manuscript-selection-style\"]');
          if(!button)return false;button.click();
          return !!document.querySelector('[data-testid=\"manuscript-style-chapter\"]');
        }""")
        assert opened
        page.evaluate("""()=>document.querySelector('[data-testid=\"manuscript-style-chapter\"]')?.click()""")
        page.wait_for_function("()=>[...document.querySelectorAll('.book-chapter-heading')].some(x=>x.textContent.includes('Another Chapter'))",timeout=5000)
        assert 'active' in (page.locator('#bookViewBtn').get_attribute('class') or '')
        assert page.locator('#chapterTitle').is_visible() is False
        assert page.locator('.book-chapter-heading').filter(has_text='Another Chapter').count()==1
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v4101_aa_toolbar_and_selection_context_menu(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.evaluate("""async()=>{
          const boot=await api('/api/bootstrap'),ch=boot.chapters[0];
          await api(`/api/chapters/${ch.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[
            {block_type:'paragraph',semantic_style:'body',content_html:'Alpha beta gamma',text_content:'Alpha beta gamma'},
            {block_type:'paragraph',semantic_style:'body',content_html:'Delta epsilon zeta',text_content:'Delta epsilon zeta'}
          ]})});
          await refresh(false);await selectChapter(ch.id,false);
        }""")
        page.wait_for_timeout(80)

        def select_text(index,start,end):
            return page.evaluate("""({index,start,end})=>{
              const prose=document.querySelectorAll('.manuscript-block')[index],node=prose.firstChild||prose;
              const r=document.createRange();r.setStart(node,start);r.setEnd(node,end);
              const s=getSelection();s.removeAllRanges();s.addRange(r);return s.toString();
            }""",{'index':index,'start':start,'end':end})

        # Partial text selection targets the touched paragraph, not just a caret.
        assert select_text(0,6,10)=='beta'
        page.wait_for_timeout(40)
        page.locator('#selectionToolbar [data-selection-action="style"]').click()
        page.locator('#contextMenu button').filter(has_text='H2  Section').click()
        page.wait_for_timeout(80)
        first=page.locator('.manuscript-block-wrap').nth(0)
        assert first.get_attribute('data-style')=='heading2'
        assert page.evaluate("getSelection().toString()")=='beta'

        # An ordinary right-click with no active text selection remains native.
        page.evaluate("getSelection().removeAllRanges()")
        native=page.evaluate("""()=>{
          const prose=document.querySelectorAll('.manuscript-block')[1];
          const ev=new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:40,clientY:40});
          return prose.dispatchEvent(ev);
        }""")
        assert native is True

        # A selected manuscript range gets Breadmaker's Aa menu and can style it.
        assert select_text(1,6,13)=='epsilon'
        page.wait_for_timeout(40)
        custom=page.evaluate("""()=>{
          const prose=document.querySelectorAll('.manuscript-block')[1];
          const ev=new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:120,clientY:120});
          return prose.dispatchEvent(ev);
        }""")
        assert custom is False
        parent=page.locator('#contextMenu > .context-menu-row > button').filter(has_text='Aa').first
        assert parent.is_visible()
        parent.hover();page.wait_for_timeout(40)
        submenu=page.locator('#contextMenu .context-submenu button').filter(has_text='H2').first
        submenu.click();page.wait_for_timeout(80)
        second=page.locator('.manuscript-block-wrap').nth(1)
        assert second.get_attribute('data-style')=='heading2'
        assert page.evaluate("getSelection().toString()")=='epsilon'

        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v4102_aa_popover_is_visibly_clickable(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.set_viewport_size({'width':820,'height':520})
        page.evaluate("""async()=>{
          const boot=await api('/api/bootstrap'),ch=boot.chapters[0];
          const blocks=[];for(let i=0;i<18;i++)blocks.push({block_type:'paragraph',semantic_style:'body',content_html:`Paragraph ${i} with enough text to select`,text_content:`Paragraph ${i} with enough text to select`});
          await api(`/api/chapters/${ch.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks})});await refresh(false);await selectChapter(ch.id,false);
        }""")
        last=page.locator('.manuscript-block').last
        last.scroll_into_view_if_needed();page.wait_for_timeout(80)
        page.evaluate("""()=>{const p=document.querySelectorAll('.manuscript-block')[17],node=p.firstChild||p,r=document.createRange();r.setStart(node,0);r.setEnd(node,Math.min(12,node.textContent.length));const s=getSelection();s.removeAllRanges();s.addRange(r)}""")
        page.wait_for_selector('#selectionToolbar:not(.hidden)')
        aa=page.locator('#selectionToolbar [data-selection-action="style"]')
        assert aa.is_visible()
        aa.click();page.wait_for_selector('#contextMenu:not(.hidden)')
        geometry=page.evaluate("""()=>{const m=document.querySelector('#contextMenu').getBoundingClientRect();return{top:m.top,bottom:m.bottom,left:m.left,right:m.right,w:innerWidth,h:innerHeight,hit:!!document.elementFromPoint(m.left+10,m.top+10)?.closest('#contextMenu')}}""")
        assert geometry['top']>=0 and geometry['bottom']<=geometry['h']
        assert geometry['left']>=0 and geometry['right']<=geometry['w']
        assert geometry['hit'] is True
        assert page.locator('#contextMenu button').filter(has_text='H4  Minor section').is_visible()
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)

def test_browser_v4104_tab_context_menu_exposes_import_document(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        doc_id=page.evaluate("()=>state.activeDocumentId")
        page.evaluate("()=>{const input=document.querySelector('#documentImportInput');input.click=()=>{window.__documentImportPickerOpened=true}}")
        page.locator('#tabsMenuBtn').click()
        action=page.locator('#contextMenu button').filter(has_text='Import Tabs…').first
        assert action.is_visible()
        action.click()
        state=page.evaluate("()=>({opened:!!window.__documentImportPickerOpened,after:document.querySelector('#documentImportInput').dataset.afterDocumentId})")
        assert state['opened'] is True
        assert int(state['after'])==int(doc_id)
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v411_checkpoints_integrate_with_manuscript_panel_and_split_view(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.locator('#checkpointBtn').click()
        page.wait_for_selector('#checkpointPanel:not(.hidden)')
        assert page.locator('.modal-card.checkpoint-modal').count()==0
        assert page.locator('.manuscript-block').first.evaluate('el=>el.isContentEditable') is True

        page.locator('#checkpointCreateBtn').click()
        page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text()=='New Checkpoint'
        page.locator('#checkpointCreateName').fill('Before dialogue rewrite')
        page.locator('#checkpointCreateDescription').fill('The quieter dialogue version.')
        page.locator('#checkpointCreateConfirm').click()
        row=page.locator('.checkpoint-list-row').filter(has_text='Before dialogue rewrite')
        page.wait_for_timeout(100)
        assert row.is_visible()

        # Close the management panel and keep editing normally.
        page.locator('#checkpointBtn').click()
        assert page.locator('#checkpointPanel').evaluate("el=>el.classList.contains('hidden')") is True
        page.evaluate("""async()=>{
          const el=document.querySelector('.manuscript-block');
          el.innerText='A deliberately changed dialogue passage.';
          el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
          await flushSave();
        }""")

        # Reopen the panel, search, then compare in the EXISTING split view.
        page.locator('#checkpointBtn').click()
        page.locator('#checkpointSearch').fill('quieter')
        row=page.locator('.checkpoint-list-row').filter(has_text='Before dialogue rewrite')
        assert row.is_visible()
        row.locator('[data-checkpoint-compare]').click()
        page.wait_for_selector('#splitPane:not(.hidden)')
        assert page.locator('#checkpointCompareToolbar').is_visible()
        assert 'Read only' in page.locator('#splitTitle').inner_text()
        assert page.locator('.manuscript-block').first.evaluate('el=>el.isContentEditable') is True
        assert page.locator('#splitBody .checkpoint-split-paper').is_visible()

        # Comparison automatically tucks the management panel away so the live manuscript keeps useful width.
        assert page.locator('#checkpointPanel').evaluate("el=>el.classList.contains('hidden')") is True
        assert page.locator('#splitPane').is_visible()
        assert page.locator('.manuscript-block').first.evaluate('el=>el.isContentEditable') is True
        # The panel can be reopened without ending comparison.
        page.locator('#checkpointBtn').click()
        assert page.locator('#checkpointPanel').is_visible()
        assert page.locator('#splitPane').is_visible()
        page.locator('#checkpointBtn').click()

        # Diff is progressive disclosure: clean first, then show changes when requested.
        assert page.locator('#checkpointSplitShowDiff').is_checked() is False
        page.locator('#checkpointSplitShowDiff').check()
        page.wait_for_function("document.querySelectorAll('#splitBody .checkpoint-compare-row.changed,#splitBody .checkpoint-compare-row.added,#splitBody .checkpoint-compare-row.removed').length>0")
        assert page.locator('#splitBody mark.checkpoint-diff-added,#splitBody mark.checkpoint-diff-removed,#splitBody .checkpoint-current-only').count()>0
        assert page.locator('.checkpoint-live-change').count()>0
        assert page.locator('#checkpointSplitLinked').is_checked()

        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v4111_checkpoint_split_selector_and_metadata_stay_in_sync(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        chapter_id=page.evaluate("()=>state.chapter.id")
        created=page.evaluate("""async chapterId=>{
          const a=await api('/api/checkpoints',{method:'POST',body:JSON.stringify({scope:'chapter',chapter_id:chapterId,name:'Version A',description:'Keep A'})});
          const b=await api('/api/checkpoints',{method:'POST',body:JSON.stringify({scope:'chapter',chapter_id:chapterId,name:'Version B',description:'Keep B'})});
          return {a:a.id,b:b.id};
        }""",chapter_id)
        page.locator('#checkpointBtn').click()
        page.wait_for_selector('#checkpointPanel:not(.hidden)')
        row_a=page.locator(f'[data-checkpoint-row="{created["a"]}"]')
        row_b=page.locator(f'[data-checkpoint-row="{created["b"]}"]')
        assert row_a.is_visible() and row_b.is_visible()
        row_a.locator('[data-checkpoint-compare]').click()
        page.wait_for_selector('#checkpointCompareToolbar:not(.hidden)')
        assert page.locator('#checkpointSplitSelect').input_value()==str(created['a'])

        # Switching versions in Split View retargets the active compact card.
        page.locator('#checkpointSplitSelect').select_option(str(created['b']))
        page.wait_for_timeout(100)
        assert page.locator('#checkpointSplitSelect').input_value()==str(created['b'])
        page.locator('#checkpointBtn').click()
        assert page.locator('#checkpointPanel').is_visible()
        row_b=page.locator(f'[data-checkpoint-row="{created["b"]}"]')
        assert row_b.evaluate("el=>el.classList.contains('active')") is True

        # Metadata lives behind the integrated overflow menu rather than a lower inspector.
        row_b.locator('[data-checkpoint-more]').click()
        page.locator('#contextMenu button').filter(has_text='Details').click()
        page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#checkpointEditName').input_value()=='Version B'
        page.locator('#checkpointEditName').fill('Version B renamed')
        page.locator('#checkpointEditSave').click()
        page.wait_for_timeout(100)
        rows=page.evaluate("async()=> (await api('/api/checkpoints')).checkpoints.map(x=>({id:x.id,name:x.name}))")
        names={int(x['id']):x['name'] for x in rows}
        assert names[int(created['b'])]=='Version B renamed'
        assert names[int(created['a'])]=='Version A'

        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v412_history_compare_is_integrated_and_read_only(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        page.evaluate("""async()=>{
          const el=document.querySelector('.manuscript-block');
          el.innerText='History version worth comparing.';
          el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
          await flushSave();await captureAutoHistory('quiet');
          el.innerText='Current manuscript remains editable.';
          el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
          await flushSave();
        }""")
        page.evaluate("()=>openHistory()")
        page.wait_for_selector('#modal:not(.hidden) .history-modal-row')
        row=page.locator('.history-modal-row').first
        row.locator('[data-history-more]').click();page.locator('#historyRowMenu [data-history-menu="compare"]').click()
        page.wait_for_selector('#historyCompareToolbar:not(.hidden)')
        assert page.locator('#splitPane').is_visible()
        assert page.locator('.manuscript-block').first.evaluate('el=>el.isContentEditable') is True
        assert 'History' in page.locator('#splitTitle').inner_text()
        assert page.locator('#historySplitShowDiff').is_checked() is False
        page.locator('#historySplitShowDiff').check()
        page.wait_for_function("()=>document.querySelectorAll('#splitBody .checkpoint-compare-row.changed,#splitBody .checkpoint-compare-row.added,#splitBody .checkpoint-compare-row.removed').length>0",timeout=2500)
        assert page.locator('#splitBody mark.checkpoint-diff-added,#splitBody mark.checkpoint-diff-removed,#splitBody .checkpoint-current-only').count()>0
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)


def test_browser_v412_history_can_pin_checkpoint_and_is_a_split_source(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        page.evaluate("""async()=>{
          const el=document.querySelector('.manuscript-block');
          el.innerText='History checkpoint source.';
          el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
          await flushSave();await captureAutoHistory('quiet');
        }""")
        before=page.evaluate("async()=> (await api('/api/checkpoints')).checkpoints.length")
        page.evaluate("()=>openHistory()")
        page.wait_for_selector('#modal:not(.hidden) .history-modal-row')
        page.locator('.history-modal-row').first.locator('[data-history-more]').click()
        page.locator('#historyRowMenu [data-history-menu="checkpoint"]').click()
        page.wait_for_function("async n => (await (await fetch('/api/checkpoints')).json()).checkpoints.length>n",arg=before,timeout=2500)
        after=page.evaluate("async()=> (await api('/api/checkpoints')).checkpoints")
        assert len(after)==before+1
        assert any('History' in (x.get('description') or '') or 'history' in (x.get('tags') or []) for x in after)
        if page.locator('#modal:not(.hidden)').count(): page.locator('#modalClose').click()
        page.locator('#viewBtn').click();page.locator('#viewSplitBtn').click()
        assert page.locator('#splitType option[value="history"]').count()==1
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)



def test_browser_v4122_project_center_modal_cleanup(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        _open_project_menu(page)
        assert page.get_by_text('Design principles',exact=True).count()==0
        assert page.locator('#projectModal').count()==0

        # Browse All now lives under Switch Project.
        page.locator('#projectMenuSwitch').click();page.wait_for_selector('#projectSubmenu:not(.hidden)')
        page.locator('#projectMenuBrowseAll').click();page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text()=='All Projects'
        assert page.locator('#projectExplorerSearch').is_visible()
        assert page.locator('.project-explorer-card').count()>=1
        page.locator('#modalClose').click()

        _open_project_pdf(page)
        assert page.locator('#pdfCover').is_checked()
        assert page.locator('#pdfPageNumbers').is_checked()
        assert page.locator('#pdfFont').is_visible() and page.locator('#pdfFontSize').is_visible()
        page.locator('#modalClose').click()

        # Verify is a Project Settings action and must acknowledge the click before
        # an intentionally delayed deep check returns.
        _open_project_settings(page)
        page.evaluate("""()=>{const real=window.api;window.api=async(...args)=>{if(String(args[0]).includes('/api/project/health'))await new Promise(resolve=>setTimeout(resolve,350));return real(...args)}}""")
        page.locator('#settingsVerify').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Verify Project'")
        assert page.get_by_text('Checking project health…',exact=True).is_visible()
        page.wait_for_function("document.querySelector('.verify-summary')!==null",timeout=10000)
        assert page.locator('.verify-summary').is_visible()
        section_titles=page.locator('.verify-section-title').all_inner_texts()
        assert section_titles==['INTEGRITY','STORAGE','CURRENT FEATURES','RECOVERY']

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4123_pdf_export_controls_are_compact_and_functional(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    submitted={}
    try:
        _open_project_pdf(page)

        # Labels and checkboxes sit together rather than being centered across the form.
        label=page.locator('label[for="pdfCover"]'); box=page.locator('#pdfCover')
        lb=label.bounding_box(); bb=box.bounding_box()
        assert lb and bb and 0 <= bb['x']-(lb['x']+lb['width']) < 16
        assert abs((lb['y']+lb['height']/2)-(bb['y']+bb['height']/2)) < 5

        # The label itself toggles the styled checkbox and cover metadata visibility.
        label.click(); assert not box.is_checked(); assert page.locator('#pdfCoverFields').is_hidden()
        label.click(); assert box.is_checked(); assert page.locator('#pdfCoverFields').is_visible()
        page.locator('#pdfAuthor').fill('Ziro Writer')
        page.locator('#pdfCoverDate').fill('August 14, 2026')
        page.locator('#pdfVersion').fill('Draft 7')
        page.locator('#pdfNotes').fill('Review notes')

        # Other checkboxes are independently functional.
        page.locator('label[for="pdfPageNumbers"]').click(); assert not page.locator('#pdfPageNumbers').is_checked()
        assert page.locator('#pdfChapterBreaks').is_checked()

        # The E2E fetch bridge intentionally implements JSON/text only. Replace the
        # download helper with a capture stub so this test can verify the exact payload
        # assembled by the real Export button without teaching the whole harness Blob.
        page.evaluate("""() => { window.__pdfPayload=null; window.downloadPostFile=async (_url,payload,_name)=>{window.__pdfPayload=payload}; }""")
        page.locator('#pdfExportConfirm').click()
        page.wait_for_function("window.__pdfPayload!==null")
        submitted=page.evaluate("window.__pdfPayload")
        assert submitted['cover'] is True
        assert submitted['page_numbering'] is False
        assert submitted['chapter_page_breaks'] is True
        assert submitted['author']=='Ziro Writer'
        assert submitted['cover_date']=='August 14, 2026'
        assert submitted['version']=='Draft 7'
        assert submitted['notes']=='Review notes'
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4125_canvas_card_button_is_compact_and_typography_uses_config_as_default(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""
          async()=>{
            const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Readable Canvas',canvas_type:'blank'})});
            const node=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Readable node',body:'A few lines of readable planning text.',x:110,y:110,w:240,h:90})});
            await refresh(false);setView('canvas');await selectCanvas(cv.id);return {canvas:cv.id,node:node.id};
          }
        """)
        page.set_viewport_size({'width':760,'height':720})
        page.wait_for_selector(f'.canvas-node[data-id="{created["node"]}"]')
        button=page.locator('#addNodePanelBtn')
        button_metrics=button.evaluate("""el=>{const r=document.createRange();r.selectNodeContents(el);return{whiteSpace:getComputedStyle(el).whiteSpace,rects:r.getClientRects().length,height:el.getBoundingClientRect().height}}""")
        assert button_metrics['whiteSpace']=='nowrap'
        assert button_metrics['rects']==1
        assert button_metrics['height']<40
        expected=page.evaluate('configuredCanvasTextSize()')
        font_px=page.locator(f'.canvas-node[data-id="{created["node"]}"] .node-body').evaluate("el=>parseFloat(getComputedStyle(el).fontSize)")
        assert abs(font_px-expected)<0.2
        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v4126_project_metadata_and_danger_zone(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        _open_project_settings(page)
        assert page.locator('#settingsMetadata').is_visible()
        assert page.get_by_text('Danger Zone',exact=True).is_visible()
        assert page.locator('#settingsDeleteProject').is_visible()
        assert page.locator('#settingsDeleteAll').is_visible()

        page.locator('#settingsMetadata').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Metadata'")
        page.wait_for_selector('.metadata-section')
        assert page.get_by_text('Words',exact=True).is_visible()
        assert page.get_by_text('Cards',exact=True).is_visible()
        assert page.locator('#projectMetadataBody').get_by_text('Checkpoints',exact=True).is_visible()
        page.locator('#modalClose').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Settings'")

        page.locator('#settingsDeleteProject').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Delete Project'")
        page.locator('#dialogConfirm').click();page.wait_for_selector('#dangerConfirmInput')
        code=page.locator('.danger-confirm-code').inner_text().strip()
        assert len(code)==10 and code.isalnum() and code==code.lower()
        assert page.locator('#dangerCodeConfirm').is_disabled()
        page.locator('#dangerConfirmInput').fill(code)
        assert page.locator('#dangerCodeConfirm').is_enabled()
        page.locator('#dangerCodeCancel').click()
        page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Settings'")
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4127_danger_zone_download_and_manual_code(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        _open_project_settings(page)
        danger=page.locator('#modalBody .project-settings-danger')
        metadata=page.locator('#settingsMetadata')
        assert danger.is_visible() and metadata.is_visible()
        assert danger.bounding_box()['y'] > metadata.bounding_box()['y']

        page.locator('#settingsDeleteProject').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Delete Project'")
        assert page.locator('#dangerDownloadData').is_visible()
        download_link=page.locator('#dangerDownloadData')
        assert download_link.get_attribute('href')=='/api/data/export'
        assert download_link.get_attribute('download') is not None
        assert page.locator('#dialogConfirm').is_visible()

        page.locator('#dialogConfirm').click();page.wait_for_selector('#dangerConfirmInput')
        code=page.locator('.danger-confirm-code')
        assert code.evaluate("el=>getComputedStyle(el).userSelect")=='none'
        assert code.evaluate("el=>{const e=new Event('copy',{cancelable:true});el.dispatchEvent(e);return e.defaultPrevented}") is True
        input_box=page.locator('#dangerConfirmInput')
        paste_prevented=input_box.evaluate("el=>{const e=new Event('paste',{cancelable:true});el.dispatchEvent(e);return e.defaultPrevented}")
        assert paste_prevented is True
        challenge=code.inner_text().strip()
        assert len(challenge)==10 and challenge==challenge.lower()
        input_box.press_sequentially(challenge)
        assert page.locator('#dangerCodeConfirm').is_enabled()
        page.locator('#dangerCodeCancel').click();page.wait_for_function("document.querySelector('#modalTitle')?.textContent==='Project Settings'")
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4128_checkpoint_tooltip_and_move_to_cuts_is_direct_and_undoable(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        setup=page.evaluate("""
          async()=>{
            const tab=state.activeDocumentId;
            const chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Cuts UX 4.13.2',tab_id:tab})});
            await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{block_type:'paragraph',semantic_style:'body',content_html:'alpha beta gamma',text_content:'alpha beta gamma'}]})});
            await api('/api/ui-state/cuts-onboarding-seen',{method:'PUT',body:JSON.stringify({value:false})});
            state.cutsOnboardingSeen=false;
            await refresh(false);await selectChapter(chapter.id,false);
            const cuts=await api('/api/cuts');
            return {chapter:chapter.id,baseline:cuts.length};
          }
        """)

        # Checkpoints remain a first-class Manuscript tool; Reviews now sits between
        # Checkpoints and Cuts. The tooltip contract remains deterministic.
        order=page.evaluate("""()=>{
          const c=document.querySelector('#checkpointBtn'),e=document.querySelector('#editorsEyeBtn');
          return {hasLegacyFocus:!!document.querySelector('#focusBtn'),checkpointNext:c.nextElementSibling?.id,viewFocus:!!document.querySelector('#viewFocusBtn')};
        }""")
        assert order=={'hasLegacyFocus':False,'checkpointNext':'manuscriptReviewsBtn','viewFocus':True}
        checkpoint=page.locator('#checkpointBtn'); tip=page.locator('#checkpointTooltip')
        checkpoint.hover();page.wait_for_timeout(430)
        assert tip.is_visible()
        assert 'Deliberately save a version' in tip.inner_text()
        page.locator('#editor').hover();page.wait_for_timeout(30)
        assert not tip.is_visible()

        def select_beta():
            page.evaluate("""()=>{
              const prose=document.querySelector('.manuscript-block'),text=prose.textContent,start=text.indexOf('beta');
              const walker=document.createTreeWalker(prose,NodeFilter.SHOW_TEXT);let node,at=0,sn=null,so=0,en=null,eo=0;
              while((node=walker.nextNode())){const next=at+node.nodeValue.length;if(sn===null&&start>=at&&start<=next){sn=node;so=start-at}if(start+4>=at&&start+4<=next){en=node;eo=start+4-at;break}at=next}
              const r=document.createRange();r.setStart(sn,so);r.setEnd(en,eo);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);state.lastManuscriptRange=r.cloneRange();positionSelectionToolbar(r);
            }""")
            page.wait_for_selector('#selectionToolbar:not(.hidden)')

        select_beta()
        toolbar=page.locator('#selectionToolbar')
        assert toolbar.locator('[data-selection-action="cuts"]').is_visible()
        assert toolbar.locator('[data-selection-action="task"]').count()==0
        assert toolbar.locator('[data-selection-action="map"]').count()==0

        # Task and Map moved under the quiet overflow menu instead of competing with Cuts.
        toolbar.locator('[data-selection-action="more"]').click()
        page.wait_for_selector('#contextMenu:not(.hidden)')
        menu_text=page.locator('#contextMenu').inner_text()
        assert 'Task' in menu_text and 'Map' in menu_text
        page.locator('#editor').click(position={'x':4,'y':4})

        # Move to Cuts is immediate. The only first-use UI is an informational
        # acknowledgement *after* the move has safely completed.
        select_beta()
        toolbar.locator('[data-selection-action="cuts"]').click()
        page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text()=='Safely moved to Cuts'
        assert 'Cuts button in the Manuscript toolbar' in page.locator('#modalBody').inner_text()
        first=page.evaluate("""async()=>({text:document.querySelector('.manuscript-block').innerText,cuts:(await api('/api/cuts')).length})""")
        assert 'beta' not in first['text'] and first['cuts']==setup['baseline']+1
        page.locator('#cutsOnboardingDone').click()

        # Button Undo reverses the complete action: prose returns and the Cut disappears.
        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        undone=page.evaluate("""async()=>({text:document.querySelector('.manuscript-block').innerText,cuts:(await api('/api/cuts')).length,seen:(await api('/api/ui-state/cuts-onboarding-seen')).value})""")
        assert 'beta' in undone['text'] and undone['cuts']==setup['baseline'] and undone['seen'] is True
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')") is True

        # The onboarding is not Undo state. A second Move is silent, and Cmd+Z also
        # reverses it completely without resurrecting the onboarding prompt.
        select_beta();toolbar.locator('[data-selection-action="cuts"]').click()
        page.wait_for_timeout(250);page.evaluate("()=>state.historyChain.then(()=>true)")
        second=page.evaluate("""async()=>({text:document.querySelector('.manuscript-block').innerText,cuts:(await api('/api/cuts')).length})""")
        assert 'beta' not in second['text'] and second['cuts']==setup['baseline']+1
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')") is True
        page.keyboard.press('Meta+z');page.evaluate("()=>state.historyChain.then(()=>true)")
        shortcut=page.evaluate("""async()=>({text:document.querySelector('.manuscript-block').innerText,cuts:(await api('/api/cuts')).length})""")
        assert 'beta' in shortcut['text'] and shortcut['cuts']==setup['baseline']
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)



def _seed_v41210_cut_reliability(page):
    return page.evaluate("""
      async()=>{
        const tab=state.activeDocumentId;
        const chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Undo Reliability 4.13.2',tab_id:tab})});
        await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{block_type:'paragraph',semantic_style:'body',content_html:'alpha beta gamma',text_content:'alpha beta gamma'}]})});
        await api('/api/ui-state/cuts-onboarding-seen',{method:'PUT',body:JSON.stringify({value:true})});
        state.cutsOnboardingSeen=true;
        await refresh(false);await selectChapter(chapter.id,false);
        return {chapter:chapter.id,baseline:(await api('/api/cuts')).length};
      }
    """)


def _select_v41210_beta(page):
    page.evaluate("""()=>{
      const prose=document.querySelector('.manuscript-block'),text=prose.textContent,start=text.indexOf('beta');
      const node=prose.firstChild||prose,r=document.createRange();r.setStart(node,start);r.setEnd(node,start+4);
      const sel=getSelection();sel.removeAllRanges();sel.addRange(r);
    }""")
    page.wait_for_selector('#selectionToolbar:not(.hidden)')


def test_browser_v41210_immediate_cut_undo_waits_for_structural_action(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        setup=_seed_v41210_cut_reliability(page)
        _select_v41210_beta(page);page.locator('[data-selection-action="cuts"]').click();page.keyboard.press('Meta+z')
        page.evaluate("()=>Promise.all([state.structuralChain,state.historyChain]).then(()=>true)")
        immediate=page.evaluate("""async()=>({text:document.querySelector('.manuscript-block').innerText,cuts:(await api('/api/cuts')).length})""")
        assert 'beta' in immediate['text'] and immediate['cuts']==setup['baseline']
        assert not errors,errors
    finally:
        _shutdown_page(page)


def test_browser_v41210_failed_undo_redo_remain_retryable(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        setup=_seed_v41210_cut_reliability(page)
        _select_v41210_beta(page);page.locator('[data-selection-action="cuts"]').click();page.evaluate("()=>state.structuralChain.then(()=>true)")
        before_len=page.evaluate('state.undoStack.length')
        assert before_len>0 and page.evaluate("state.undoStack[state.undoStack.length-1]?.kind")=='structural'
        page.evaluate("""()=>{
          const original=window.fetch;window.__breadmakerOriginalFetch=original;window.__undoFailureInjected=false;
          window.fetch=(url,options)=>{
            if(!window.__undoFailureInjected&&String(url).includes('/api/undo/apply/')){
              window.__undoFailureInjected=true;
              return Promise.resolve(new Response(JSON.stringify({detail:'forced undo failure'}),{status:503,headers:{'Content-Type':'application/json'}}));
            }
            return original(url,options);
          };
        }""")
        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        assert page.evaluate('window.__undoFailureInjected') is True
        assert page.evaluate('state.undoStack.length')==before_len and page.locator('#undoBtn').is_enabled()
        failed_state=page.evaluate("""async()=>({text:document.querySelector('.manuscript-block').innerText,cuts:(await api('/api/cuts')).length})""")
        assert 'beta' not in failed_state['text'] and failed_state['cuts']==setup['baseline']+1
        page.evaluate("()=>{window.fetch=window.__breadmakerOriginalFetch;delete window.__breadmakerOriginalFetch}")

        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        retried=page.evaluate("""async()=>({text:document.querySelector('.manuscript-block').innerText,cuts:(await api('/api/cuts')).length})""")
        assert 'beta' in retried['text'] and retried['cuts']==setup['baseline']

        redo_len=page.evaluate('state.redoStack.length');assert redo_len>0
        page.evaluate("""()=>{
          const original=window.fetch;window.__breadmakerOriginalFetch=original;window.__redoFailureInjected=false;
          window.fetch=(url,options)=>{
            if(!window.__redoFailureInjected&&String(url).includes('/api/undo/apply/')){
              window.__redoFailureInjected=true;
              return Promise.resolve(new Response(JSON.stringify({detail:'forced redo failure'}),{status:503,headers:{'Content-Type':'application/json'}}));
            }
            return original(url,options);
          };
        }""")
        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        assert page.evaluate('window.__redoFailureInjected') is True
        assert page.evaluate('state.redoStack.length')==redo_len and page.locator('#redoBtn').is_enabled()
        page.evaluate("()=>{window.fetch=window.__breadmakerOriginalFetch;delete window.__breadmakerOriginalFetch}")
        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        redone=page.evaluate("""async()=>({text:document.querySelector('.manuscript-block').innerText,cuts:(await api('/api/cuts')).length})""")
        assert 'beta' not in redone['text'] and redone['cuts']==setup['baseline']+1
        assert not errors,errors
        unexpected=[text for typ,text in console if typ=='error' and 'HISTORY_COMMAND_001' not in text and 'favicon' not in text.lower()]
        assert not unexpected,unexpected
    finally:
        _shutdown_page(page)

def test_browser_v4129_view_menu_global_history_and_persistent_text_size(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        # Undo/Redo are global controls immediately after the save indicator, not manuscript-only controls.
        assert page.locator('.top-actions #globalSaveState + .global-history-controls #undoBtn').count()==1
        assert page.locator('.global-history-controls #redoBtn').count()==1
        assert page.locator('.document-toolbar #undoBtn').count()==0
        assert page.locator('.document-toolbar #redoBtn').count()==0

        # Split View is progressively disclosed inside View rather than taking permanent toolbar space.
        assert page.locator('#splitViewBtn').count()==0
        view_button=page.locator('#viewBtn');view_button.click()
        page.wait_for_selector('#viewPopover:not(.hidden)')
        assert page.locator('#viewFocusBtn').is_visible()
        assert page.locator('#viewSplitBtn').is_visible()
        assert page.locator('#manuscriptTextSize').is_visible()
        centers=page.evaluate("""()=>{const b=document.querySelector('#viewBtn').getBoundingClientRect(),c=document.querySelector('.view-menu-chevron').getBoundingClientRect();return{button:b.top+b.height/2,chevron:c.top+c.height/2}}""")
        assert abs(centers['button']-centers['chevron'])<1.25

        # Focus Mode is now a View option rather than another permanent toolbar button.
        page.locator('#viewFocusBtn').click()
        assert page.locator('body').evaluate("el=>el.classList.contains('focus')") is True
        page.locator('#viewBtn').click();assert page.locator('#viewFocusBtn').get_attribute('aria-checked')=='true'
        page.keyboard.press('Escape')
        assert page.locator('body').evaluate("el=>el.classList.contains('focus')") is False
        page.locator('#viewBtn').click() if page.locator('#viewPopover').evaluate("el=>el.classList.contains('hidden')") else None

        # The accessibility size updates immediately and is persisted independently of manuscript data.
        page.locator('#manuscriptTextSize').fill('22.5')
        page.locator('#manuscriptTextSize').dispatch_event('input')
        page.wait_for_function("getComputedStyle(document.querySelector('#editor')).fontSize==='22.5px'")
        assert page.locator('#viewTextSizeValue').inner_text()=='22.5 px'
        assert page.evaluate("localStorage.getItem('breadmaker-manuscript-text-size')")=='22.5'

        # The global recovery controls remain reachable in another workspace.
        page.locator('.main-tab[data-view="canvas"]').click()
        assert page.locator('.top-actions #undoBtn').is_visible()
        assert page.locator('.top-actions #redoBtn').is_visible()
        page.locator('.main-tab[data-view="manuscript"]').click()

        # Boot restoration reads the durable local preference back into presentation state.
        page.evaluate("()=>{document.documentElement.style.setProperty('--manuscript-font-size','14px');state.manuscriptFontSize=14;restoreManuscriptTextSize()}")
        page.wait_for_function("getComputedStyle(document.querySelector('#editor')).fontSize==='22.5px'")
        page.locator('#viewBtn').click()
        assert page.locator('#manuscriptTextSize').input_value()=='22.5'
        assert page.locator('#viewTextSizeValue').inner_text()=='22.5 px'

        # Split View remains available from View after the preference round-trip.
        page.locator('#viewSplitBtn').click()
        page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text()=='Split View'
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v413_manuscript_toolbar_is_clean_icon_first_and_tooltipped(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        order=page.evaluate("""()=>[...document.querySelector('.document-toolbar').children].map(el=>el.id||el.className)""")
        view_index=next(i for i,x in enumerate(order) if 'manuscript-view-control' in x)
        assert view_index < order.index('commentsBtn') < order.index('markupBtn') < order.index('manuscriptImageBtn') < order.index('checkpointBtn') < order.index('manuscriptReviewsBtn') < order.index('cutsBtn') < order.index('editorsEyeBtn')
        assert page.locator('#blockStyleBtn').count()==0
        assert page.locator('#boldBtn').count()==0 and page.locator('#italicBtn').count()==0 and page.locator('#underlineBtn').count()==0

        page.locator('#viewBtn').click()
        assert page.locator('#viewFindReplaceBtn').is_visible()
        pop_order=page.locator('#viewPopover > *').evaluate_all("els=>els.map(el=>el.id||el.className)")
        assert pop_order.index('viewScenesBtn') < pop_order.index('viewFindReplaceBtn') < next(i for i,x in enumerate(pop_order) if 'view-popover-divider' in x)
        page.locator('#viewBtn').click()
        markup=page.locator('#markupBtn')
        assert page.locator('#manuscriptImageBtn svg').count()==1 and page.locator('#manuscriptReviewsBtn svg').count()==1 and markup.locator('svg').count()==1
        page.locator('#manuscriptImageBtn').hover();page.wait_for_timeout(430)
        assert page.locator('#manuscriptImageTooltip').is_visible()
        page.locator('#activeDocumentTitle').hover();page.wait_for_timeout(30)
        markup.hover();page.wait_for_timeout(430)
        assert page.locator('#markupTooltip').is_visible()
        assert 'Markers' in page.locator('#markupTooltip').inner_text()

        book=page.locator('#bookViewBtn')
        assert book.inner_text().strip()=='Full Text'
        assert book.is_visible()
        assert 'full-document-row' in (book.get_attribute('class') or '')

        prose=page.locator('.manuscript-block').first
        prose.click();page.keyboard.type('Toolbar style selection')
        page.evaluate("""()=>{const p=document.querySelector('.manuscript-block'),n=p.firstChild||p,r=document.createRange();r.setStart(n,0);r.setEnd(n,Math.min(7,n.textContent.length));const s=getSelection();s.removeAllRanges();s.addRange(r)}""")
        page.wait_for_selector('#selectionToolbar:not(.hidden)')
        assert page.locator('#selectionToolbar [data-selection-action="style"]').inner_text().strip()=='Aa'
        assert page.locator('#selectionToolbar [data-selection-command="bold"]').is_visible()
        assert not errors, errors
        assert not [entry for entry in console if entry[0]=='error'], console
    finally:
        _shutdown_page(page)

def test_browser_v4131_sidebar_chapter_creation_undo_redo(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        baseline=page.evaluate("state.chapters.length")
        _navigator_action(page,'Add Chapter');page.evaluate("()=>state.structuralChain.then(()=>true)")
        assert page.evaluate("state.chapters.length")==baseline+1
        created_id=page.evaluate("state.chapter?.id");assert created_id
        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        assert page.evaluate("state.chapters.length")==baseline
        assert not page.evaluate("id=>state.chapters.some(ch=>ch.id===id)",created_id)
        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        assert page.evaluate("state.chapters.length")==baseline+1
        assert page.evaluate("id=>state.chapters.some(ch=>ch.id===id)",created_id)
        assert not errors,errors
    finally:
        _shutdown_page(page)


def test_browser_v4131_h1_chapter_generation_undo_redo(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        setup=page.evaluate("""async()=>{
          const tab=state.activeDocumentId;
          const chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'H1 Undo Source',tab_id:tab})});
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[
            {block_type:'paragraph',semantic_style:'body',content_html:'alpha',text_content:'alpha'},
            {block_type:'paragraph',semantic_style:'body',content_html:'NEW CHAPTER',text_content:'NEW CHAPTER'},
            {block_type:'paragraph',semantic_style:'body',content_html:'beta',text_content:'beta'},
            {block_type:'paragraph',semantic_style:'body',content_html:'gamma',text_content:'gamma'}
          ]})});
          await refresh(false);await selectChapter(chapter.id,false);
          return {id:chapter.id,blockIds:[...document.querySelectorAll('.manuscript-block-wrap')].map(w=>w.dataset.blockId),chapterCount:state.chapters.length};
        }""")
        page.evaluate("""()=>{
          const wrap=[...document.querySelectorAll('.manuscript-block-wrap')].find(w=>w.querySelector('.manuscript-block')?.innerText==='NEW CHAPTER');
          const prose=wrap.querySelector('.manuscript-block'),r=document.createRange();r.selectNodeContents(prose);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);
          prose.dispatchEvent(new Event('mouseup',{bubbles:true}));
        }""")
        page.wait_for_selector('#selectionToolbar:not(.hidden)')
        page.locator('#selectionToolbar [data-selection-action="style"]').click();page.wait_for_selector('#contextMenu:not(.hidden)')
        page.locator('#contextMenu button').filter(has_text='H1  Chapter').click();page.evaluate("()=>state.structuralChain.then(()=>true)")
        assert page.evaluate("state.chapters.length")==setup['chapterCount']+1
        generated=page.evaluate("id=>state.chapters.find(ch=>ch.id!==id&&ch.title==='NEW CHAPTER')?.id",setup['id']);assert generated
        assert page.evaluate("()=>[...document.querySelectorAll('.manuscript-block')].map(el=>el.innerText)")==['beta','gamma']

        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        assert page.evaluate("state.chapters.length")==setup['chapterCount']
        restored=page.evaluate("""async id=>{await selectChapter(id,false);return {texts:[...document.querySelectorAll('.manuscript-block')].map(el=>el.innerText),ids:[...document.querySelectorAll('.manuscript-block-wrap')].map(w=>w.dataset.blockId)}}""",setup['id'])
        assert restored['texts']==['alpha','NEW CHAPTER','beta','gamma'] and restored['ids']==setup['blockIds']
        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        assert page.evaluate("state.chapters.length")==setup['chapterCount']+1
        assert page.evaluate("""async id=>(await api(`/api/chapters/${id}`)).blocks.map(b=>b.text_content)""",generated)==['beta','gamma']
        assert not errors,errors
    finally:
        _shutdown_page(page)


def test_browser_v4132_canvas_text_save_is_outside_unrelated_structural_undo(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        created=page.evaluate("""async()=>{
          const cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'4.13.2 Canvas journal boundary',canvas_type:'blank'})});
          const a=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Original A',x:100,y:100,w:200,h:100})});
          const b=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({title:'Node B',x:450,y:100,w:200,h:100,color:'#ffffff'})});
          await refresh(false);setView('canvas');await selectCanvas(cv.id);
          return {canvas:cv.id,a:a.id,b:b.id,bColor:b.color||'#ffffff'};
        }""")
        title=page.locator(f'.canvas-node[data-id="{created["a"]}"] .node-title')
        title.fill('Edited A must survive')
        # Do not wait for the old 350 ms autosave timer. Start a structural action
        # immediately; beginStructuralUndo() must flush the pending text first.
        page.evaluate("""async id=>{
          const node=state.canvasNodes.find(n=>n.id===id);
          await applyNodePatch(node,{color:'#ffeeaa'},'Change unrelated Node B');
        }""",created['b'])
        page.evaluate("()=>state.structuralChain.then(()=>true)")

        saved=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()
        saved_a=next(n for n in saved['nodes'] if n['id']==created['a'])
        saved_b=next(n for n in saved['nodes'] if n['id']==created['b'])
        assert saved_a['title']=='Edited A must survive'
        assert saved_b['color']=='#ffeeaa'

        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        undone=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()
        undone_a=next(n for n in undone['nodes'] if n['id']==created['a'])
        undone_b=next(n for n in undone['nodes'] if n['id']==created['b'])
        assert undone_a['title']=='Edited A must survive'
        assert undone_b['color']==created['bColor']

        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        redone=requests.get(live_server+f"/api/canvases/{created['canvas']}",timeout=5).json()
        redone_a=next(n for n in redone['nodes'] if n['id']==created['a'])
        redone_b=next(n for n in redone['nodes'] if n['id']==created['b'])
        assert redone_a['title']=='Edited A must survive'
        assert redone_b['color']=='#ffeeaa'
        assert not errors,errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected,unexpected
    finally:
        _shutdown_page(page)


def test_browser_v4140_cuts_panel_note_insert_and_undo_redo(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        setup=page.evaluate("""
          async()=>{
            const tab=state.activeDocumentId;
            const chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Cuts panel 4.14',tab_id:tab})});
            await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{block_type:'paragraph',semantic_style:'body',content_html:'alpha beta gamma',text_content:'alpha beta gamma'}]})});
            await api('/api/ui-state/cuts-onboarding-seen',{method:'PUT',body:JSON.stringify({value:true})});state.cutsOnboardingSeen=true;
            await refresh(false);await selectChapter(chapter.id,false);
            return {chapter:chapter.id,baseline:(await api('/api/cuts')).length};
          }
        """)
        page.evaluate("""()=>{
          const prose=document.querySelector('.manuscript-block'),text=prose.textContent,start=text.indexOf('beta');
          const r=document.createRange(),node=prose.firstChild;r.setStart(node,start);r.setEnd(node,start+4);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);state.lastManuscriptRange=r.cloneRange();positionSelectionToolbar(r);
        }""")
        page.wait_for_selector('#selectionToolbar:not(.hidden)')
        page.locator('#cutsBtn').click();page.wait_for_selector('#cutsPanel:not(.hidden)')
        assert page.locator('#cutSelectionBtn').is_enabled()
        page.locator('#cutSelectionBtn').click();page.evaluate("()=>state.structuralChain.then(()=>true)")
        state1=page.evaluate("""async()=>({text:document.querySelector('.manuscript-block').innerText,cuts:await api('/api/cuts')})""")
        assert 'beta' not in state1['text'] and len(state1['cuts'])==setup['baseline']+1
        cut_id=state1['cuts'][0]['id']

        page.locator('[data-cut-select]').first.click()
        assert page.locator('.cuts-list-row.active').count()==1
        page.locator('[data-cut-more]').first.click();page.wait_for_selector('#contextMenu:not(.hidden)')
        assert page.locator('#contextMenu').get_by_role('button',name='Details').count()==1
        assert page.locator('#contextMenu').get_by_role('button',name='Insert').count()==1
        assert page.locator('#contextMenu').get_by_role('button',name='Delete').count()==1
        assert page.locator('#contextMenu').get_by_role('button',name='Add Note').count()==0
        page.locator('#contextMenu').get_by_role('button',name='Details').click();page.wait_for_selector('#modal:not(.hidden)')
        page.locator('#cutInformationNote').fill('Maybe use this line in the ending.')
        page.locator('#cutInformationSave').click();page.wait_for_function("()=>document.querySelector('#modal').classList.contains('hidden')")
        page.wait_for_timeout(80)

        page.locator('[data-cut-more]').first.click();page.locator('#contextMenu').get_by_role('button',name='Details').click();page.wait_for_selector('#modal:not(.hidden)')
        assert 'Cuts panel 4.14' in page.locator('.cut-information-provenance').inner_text()
        assert page.locator('#cutInformationNote').input_value()=='Maybe use this line in the ending.'
        assert 'beta' in page.locator('.cut-information-content').inner_text()
        assert page.locator('#cutInformationDelete').count()==1 and page.locator('#cutInformationClose').count()==0
        page.locator('#modalClose').click();page.wait_for_function("()=>document.querySelector('#modal').classList.contains('hidden')")

        # Save a collapsed manuscript caret while the side panel remains open.
        page.evaluate("""()=>{const prose=document.querySelector('.manuscript-block');placeCaret(prose,6)}""")
        page.wait_for_timeout(40)
        assert page.locator('#cutInsertBtn').is_enabled()
        page.locator('#cutInsertBtn').click();page.evaluate("()=>state.structuralChain.then(()=>true)");page.wait_for_function("()=>document.querySelectorAll('.manuscript-block').length===3")
        inserted=page.evaluate("""async()=>({text:[...document.querySelectorAll('.manuscript-block')].map(x=>x.innerText),cuts:await api('/api/cuts')})""")
        assert inserted['text']==['alpha ','beta',' gamma']
        assert not any(x['id']==cut_id for x in inserted['cuts'])

        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        undone=page.evaluate("""async()=>({text:[...document.querySelectorAll('.manuscript-block')].map(x=>x.innerText),cuts:await api('/api/cuts')})""")
        assert undone['text']==['alpha  gamma']
        restored=next(x for x in undone['cuts'] if x['id']==cut_id)
        assert restored['note']=='Maybe use this line in the ending.'
        # The already-open side panel must refresh immediately; no close/reopen workaround.
        assert page.locator(f'[data-cut-select="{cut_id}"]').count()==1
        assert 'beta' in page.locator(f'[data-cut-select="{cut_id}"]').inner_text()

        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        redone=page.evaluate("""async()=>({text:[...document.querySelectorAll('.manuscript-block')].map(x=>x.innerText),cuts:await api('/api/cuts')})""")
        assert redone['text']==['alpha ','beta',' gamma']
        assert not any(x['id']==cut_id for x in redone['cuts'])
        assert page.locator(f'[data-cut-select="{cut_id}"]').count()==0
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4143_imported_cuts_refresh_open_panel(live_server,browser_runtime):
    # Ensure the exported package contains at least one Cut, then keep the Cuts
    # sidebar open while an additive import changes the server-side collection.
    created=requests.post(live_server+'/api/cuts',headers={'X-Breadmaker-Client':'e2e-tab'},json={
        'title':'4.14.3 import refresh source',
        'content_html':'<p>Imported Cut should appear without reopening the panel.</p>'
    },timeout=10)
    created.raise_for_status()
    package=requests.get(live_server+'/api/cuts/export',timeout=10)
    package.raise_for_status()

    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.locator('#cutsBtn').click();page.wait_for_selector('#cutsPanel:not(.hidden)')
        before=page.locator('[data-cut-select]').count()
        assert before>=1

        imported=requests.post(
            live_server+'/api/cuts/import',
            headers={'X-Breadmaker-Client':'e2e-tab'},
            files={'file':('refresh.breadcuts',package.content,'application/octet-stream')},
            timeout=20,
        )
        imported.raise_for_status()
        result=imported.json()
        assert result['additive'] is True and result['imported']>=1

        # importCutsPackage() uses this same synchronization boundary after its
        # successful structural import. Exercise the live-panel side of that
        # boundary against the externally changed collection.
        page.evaluate('()=>notifyCutsChanged()')
        page.wait_for_function(
            f"()=>document.querySelectorAll('[data-cut-select]').length==={before+result['imported']}"
        )
        assert page.locator('[data-cut-select]').count()==before+result['imported']
        assert page.locator('#cutsPanel').is_visible()
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4144_selection_toolbar_waits_for_pointer_release(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        prose=page.locator('.manuscript-block').first
        prose.click()
        page.evaluate("""()=>{
          const prose=document.querySelector('.manuscript-block');
          prose.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,isPrimary:true,pointerId:1,pointerType:'mouse'}));
          const node=prose.firstChild||prose;
          const text=prose.textContent||'';
          if(text.length<2) prose.textContent='selection toolbar regression text';
          const target=prose.firstChild;
          const range=document.createRange();range.setStart(target,0);range.setEnd(target,Math.min(12,target.nodeValue.length));
          const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
          document.dispatchEvent(new Event('selectionchange'));
        }""")
        assert page.locator('#selectionToolbar').evaluate("el=>el.classList.contains('hidden')") is True
        page.evaluate("()=>window.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,button:0,isPrimary:true,pointerId:1,pointerType:'mouse'}))")
        page.wait_for_selector('#selectionToolbar:not(.hidden)')
        assert page.locator('#selectionToolbar').evaluate("el=>getComputedStyle(el).userSelect")=='none'
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v415_comments_panel_thread_hover_and_span(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.evaluate("""async()=>{
          const tab=state.activeDocumentId;
          const chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Notes 4.15.7',tab_id:tab})});
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[
            {block_type:'paragraph',semantic_style:'body',content_html:'Alpha first paragraph for notes with enough words to establish a visible range.',text_content:'Alpha first paragraph for notes with enough words to establish a visible range.'},
            {block_type:'paragraph',semantic_style:'body',content_html:'Beta second paragraph continues the thought and gives the note another anchor.',text_content:'Beta second paragraph continues the thought and gives the note another anchor.'}
          ]})});
          await refresh(false);await selectChapter(chapter.id,false);
        }""")
        page.evaluate("""()=>{
          const blocks=[...document.querySelectorAll('.manuscript-block')],r=document.createRange();
          r.setStart(blocks[0].firstChild,6);r.setEnd(blocks[1].firstChild,32);
          const s=getSelection();s.removeAllRanges();s.addRange(r);document.dispatchEvent(new Event('selectionchange'));
        }""")
        page.wait_for_selector('#selectionToolbar:not(.hidden)')
        assert page.locator('[data-selection-action="comment"]').inner_text()=='Note'
        page.locator('[data-selection-action="comment"]').click()
        page.wait_for_selector('#commentComposer:not(.hidden)')
        assert not page.locator('#commentsPanel').is_visible()
        assert page.evaluate("()=>CSS.highlights?.has('breadmaker-comment-compose')") is True
        page.locator('#commentCreateText').fill('The transition needs a stronger emotional bridge.')
        page.locator('#commentCreateSubmit').click();page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_function("()=>document.querySelectorAll('.note-gutter-marker').length===2")
        assert not page.locator('#commentComposer').is_visible()
        assert page.locator('.note-gutter-count').all_inner_texts()==['1','1']

        # The block-relative markers sit well left of manuscript prose and need no range rail geometry.
        pos=page.evaluate("""()=>{const m=document.querySelector('.note-gutter-marker').getBoundingClientRect(),p=document.querySelector('.manuscript-block').getBoundingClientRect();return {markerRight:m.right,proseLeft:p.left}}""")
        assert pos['markerRight'] < pos['proseLeft']-20,pos

        marker=page.locator('.note-gutter-marker').first
        marker.hover();page.wait_for_timeout(550)
        page.wait_for_selector('#commentHoverCard:not(.hidden)')
        assert 'stronger emotional bridge' in page.locator('#commentHoverCard').inner_text()
        assert page.evaluate("()=>CSS.highlights?.has('breadmaker-comment-focus')") is True
        marker.click()
        page.wait_for_selector('#commentsPanel:not(.hidden)')
        assert page.locator('#commentsPanel .panel-head b').inner_text()=='Notes'
        assert 'stronger emotional bridge' in page.locator('.comment-message textarea').first.input_value()

        page.locator('#commentReplyText').fill('Try tying it back to the bakery.')
        page.locator('#commentReplySend').click();page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_function("()=>document.querySelectorAll('.comment-message').length===2")
        original=page.locator('.comment-message textarea').first
        original.fill('The transition needs a quieter emotional bridge.')
        original.blur();page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_timeout(50)
        assert 'quieter emotional bridge' in page.locator('[data-comment-row]').first.inner_text()

        page.locator('#commentResolve').click();page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.wait_for_function("()=>document.querySelectorAll('.note-gutter-marker').length===0")
        assert page.locator('[data-comment-row]').count()==0
        page.locator('#commentsStatus').select_option('resolved');page.wait_for_function("()=>document.querySelectorAll('[data-comment-row]').length===1")
        page.locator('[data-comment-row]').first.click()
        page.locator('#commentResolve').click();page.evaluate("()=>state.structuralChain.then(()=>true)")
        page.locator('#commentsStatus').select_option('open');page.wait_for_function("()=>document.querySelectorAll('[data-comment-row]').length===1")
        page.wait_for_function("()=>document.querySelectorAll('.note-gutter-marker').length===2")
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4151_overlapping_comment_rail_picker(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        result=page.evaluate("""async()=>{
          const tab=state.activeDocumentId;
          const chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Overlapping notes',tab_id:tab})});
          const text=Array.from({length:75},(_,i)=>`word${i}`).join(' ');
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text}]})});
          await refresh(false);await selectChapter(chapter.id,false);const block=state.chapterData.blocks[0];
          const make=(body,start,end)=>api('/api/comments',{method:'POST',body:JSON.stringify({body,items:[{block_id:block.id,start_offset:start,end_offset:end,selected_text:text.slice(start,end),prefix_context:text.slice(Math.max(0,start-20),start),suffix_context:text.slice(end,end+20)}]})});
          const a=await make('Outer note',0,280),b=await make('Middle note',120,390),c=await make('Inner note',170,330);
          await reloadManuscriptView();return {ids:[a.id,b.id,c.id]};
        }""")
        page.wait_for_function("()=>document.querySelectorAll('.note-gutter-marker').length===1")
        marker=page.locator('.note-gutter-marker').first
        assert marker.locator('.note-gutter-count').inner_text()=='3'
        marker.hover();page.wait_for_timeout(550)
        page.wait_for_selector('#commentHoverCard:not(.hidden)')
        assert page.locator('#commentHoverCard [data-hover-comment]').count()==3
        assert page.locator('#commentHoverCard').evaluate("el=>getComputedStyle(el).overflowY")=='auto'
        second=page.locator('#commentHoverCard [data-hover-comment]').nth(1)
        second.hover()
        assert page.evaluate("()=>CSS.highlights?.has('breadmaker-comment-focus')") is True
        chosen=int(second.get_attribute('data-hover-comment'))
        second.click();page.wait_for_selector('#commentsPanel:not(.hidden)')
        assert page.evaluate("()=>commentsWorkspace.selectedId")==chosen
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4152_compact_comments_keyboard_and_hover_transition(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        ids=page.evaluate("""async()=>{
          const tab=state.activeDocumentId;
          const chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Compact notes',tab_id:tab})});
          const firstText='Alpha passage for first note with enough room to make a clear first marker.';
          const secondText='Beta passage for second note lives in another paragraph so its marker is clearly separate.';
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[
            {block_type:'paragraph',semantic_style:'body',content_html:firstText,text_content:firstText},
            {block_type:'paragraph',semantic_style:'body',content_html:secondText,text_content:secondText}
          ]})});
          await refresh(false);await selectChapter(chapter.id,false);const blocks=state.chapterData.blocks;
          const make=(body,block,text,start,end)=>api('/api/comments',{method:'POST',body:JSON.stringify({body,items:[{block_id:block.id,start_offset:start,end_offset:end,selected_text:text.slice(start,end),prefix_context:text.slice(Math.max(0,start-10),start),suffix_context:text.slice(end,end+10)}]})});
          const a=await make('First compact note that intentionally runs longer than two visual lines in the side panel.',blocks[0],firstText,0,28);
          const b=await make('Second compact note',blocks[1],secondText,0,25);
          await api(`/api/comments/${a.id}/messages`,{method:'POST',body:JSON.stringify({body:'One response'})});
          await reloadManuscriptView();return [a.id,b.id];
        }""")
        page.locator('#commentsBtn').click();page.wait_for_selector('#commentsPanel:not(.hidden)')
        page.locator('#commentsSearch').fill('compact note')
        page.wait_for_function("()=>document.querySelectorAll('[data-comment-row]').length===2")
        assert page.locator('#commentsList').evaluate("el=>getComputedStyle(el).overflowX")=='hidden'
        first=page.locator(f'[data-comment-row="{ids[0]}"]');second=page.locator(f'[data-comment-row="{ids[1]}"]')
        first_text=first.inner_text()
        assert 'First compact note' in first_text
        assert 'Alpha passage' not in first_text
        assert 'Compact notes' not in first_text
        assert 'response' not in first_text.lower()
        assert first.locator('.comments-row-replies').inner_text()=='1'
        assert second.locator('.comments-row-replies').count()==0
        assert first.evaluate("el=>el.scrollWidth<=el.clientWidth+1") is True

        first.click();page.wait_for_function(f"()=>commentsWorkspace.selectedId==={ids[0]}")
        first=page.locator(f'[data-comment-row="{ids[0]}"]');first.focus();first.press('ArrowDown')
        page.wait_for_function(f"()=>commentsWorkspace.selectedId==={ids[1]}")
        page.locator(f'[data-comment-row="{ids[1]}"]').press('ArrowUp')
        page.wait_for_function(f"()=>commentsWorkspace.selectedId==={ids[0]}")

        page.locator('#closeCommentsPanel').click();page.wait_for_timeout(120)
        page.wait_for_function("()=>document.querySelectorAll('.note-gutter-marker').length===2")
        markers=page.locator('.note-gutter-marker')
        first_marker=markers.first;second_marker=markers.nth(1)
        first_marker.hover();page.wait_for_timeout(550);page.wait_for_selector('#commentHoverCard:not(.hidden)')
        assert 'First compact note' in page.locator('#commentHoverCard').inner_text()
        pos=page.evaluate("""()=>{const c=document.querySelector('#commentHoverCard').getBoundingClientRect(),m=document.querySelector('.note-gutter-marker'),p=m.closest('.manuscript-block-wrap').querySelector('.manuscript-block').getBoundingClientRect();return {cardRight:c.right,proseLeft:p.left,cardLeft:c.left}}""")
        assert pos['cardRight'] <= pos['proseLeft']+2,pos
        assert pos['cardLeft'] >= 7,pos
        second_marker.hover();page.wait_for_timeout(550);page.wait_for_selector('#commentHoverCard:not(.hidden)')
        assert 'Second compact note' in page.locator('#commentHoverCard').inner_text()
        assert 'First compact note' not in page.locator('#commentHoverCard').inner_text()
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)



def test_browser_v4153_note_delete_undo_redo(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        tid=page.evaluate("""async()=>{
          if(!state.chapterData){const chapter=visibleChapters(state.activeDocumentId)[0];if(chapter)await selectChapter(chapter.id,false)}
          const chapter=state.chapter;const block=state.chapterData.blocks[0];
          const text=(block.text_content||'')||'Start writing here';
          if(!block.text_content){await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:block.id,block_type:'paragraph',semantic_style:'body',content_html:'Delete note anchor',text_content:'Delete note anchor'}]})});await selectChapter(chapter.id,false)}
          const b=state.chapterData.blocks[0],t=b.text_content||'Delete note anchor';
          const note=await api('/api/comments',{method:'POST',body:JSON.stringify({body:'Temporary private note',items:[{block_id:b.id,start_offset:0,end_offset:Math.min(6,t.length),selected_text:t.slice(0,Math.min(6,t.length))}]})});
          await api(`/api/comments/${note.id}/messages`,{method:'POST',body:JSON.stringify({body:'Preserve this response'})});
          await reloadManuscriptView();return note.id;
        }""")
        page.locator('#commentsBtn').click();page.wait_for_selector('#commentsPanel:not(.hidden)')
        page.wait_for_function(f"()=>commentsWorkspace.items.some(x=>x.id==={tid})")
        page.locator(f'[data-comment-row="{tid}"]').click()
        assert page.locator('#commentDelete').is_visible()
        assert page.locator('#commentDelete').evaluate("el=>getComputedStyle(el).color") != page.locator('#commentResolve').evaluate("el=>getComputedStyle(el).color")
        page.locator('#commentDelete').click();page.evaluate("()=>state.structuralChain.then(()=>true)")
        assert 'method not allowed' not in page.locator('#toast').inner_text().lower()
        page.wait_for_function(f"()=>!commentsWorkspace.items.some(x=>x.id==={tid})")
        assert page.evaluate("id=>![...document.querySelectorAll('.note-gutter-marker')].some(el=>(el.dataset.commentThreads||'').split(',').includes(String(id)))",tid)
        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        page.wait_for_function(f"()=>commentsWorkspace.items.some(x=>x.id==={tid})")
        page.wait_for_function("id=>[...document.querySelectorAll('.note-gutter-marker')].some(el=>(el.dataset.commentThreads||'').split(',').includes(String(id)))",arg=tid)
        page.locator(f'[data-comment-row="{tid}"]').click()
        assert page.locator('.comment-message').count()==2
        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)")
        page.wait_for_function(f"()=>!commentsWorkspace.items.some(x=>x.id==={tid})")
        assert page.evaluate("id=>![...document.querySelectorAll('.note-gutter-marker')].some(el=>(el.dataset.commentThreads||'').split(',').includes(String(id)))",tid)
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)



def test_browser_v4154_sticky_note_keyboard_reply_gate_and_left_preview(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        ids=page.evaluate("""async()=>{
          const tab=state.activeDocumentId;
          const chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Sticky Notes',tab_id:tab})});
          const texts=['First sticky note paragraph has enough words to make the hover flyer taller rather than tiny.','Second sticky note paragraph.','Third sticky note paragraph.'];
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:texts.map(text=>({block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text}))})});
          await refresh(false);await selectChapter(chapter.id,false);const blocks=state.chapterData.blocks,ids=[];
          for(let i=0;i<3;i++){const text=texts[i],note=await api('/api/comments',{method:'POST',body:JSON.stringify({body:`Sticky note ${i+1} with more than two words`,items:[{block_id:blocks[i].id,start_offset:0,end_offset:Math.min(24,text.length),selected_text:text.slice(0,Math.min(24,text.length))}]})});ids.push(note.id)}
          await reloadManuscriptView();return ids;
        }""")
        page.locator('#commentsBtn').click();page.wait_for_selector('#commentsPanel:not(.hidden)')
        first=page.locator(f'[data-comment-row="{ids[0]}"]');first.click()
        page.wait_for_function(f"()=>commentsWorkspace.selectedId==={ids[0]}")
        page.keyboard.press('ArrowDown');page.wait_for_function(f"()=>commentsWorkspace.selectedId==={ids[1]}")
        # The row was rerendered, but navigation mode must remain sticky without refocusing manually.
        page.keyboard.press('ArrowDown');page.wait_for_function(f"()=>commentsWorkspace.selectedId==={ids[2]}")
        page.keyboard.press('ArrowUp');page.wait_for_function(f"()=>commentsWorkspace.selectedId==={ids[1]}")
        assert page.evaluate("()=>commentsKeyboardNavActive") is True

        reply=page.locator('#commentReplyText');send=page.locator('#commentReplySend')
        assert send.is_disabled()
        reply.click();reply.fill('   ');assert send.is_disabled()
        reply.fill('A real response');assert send.is_enabled()
        # Clicking the response field ends sticky list navigation.
        assert page.evaluate("()=>commentsKeyboardNavActive") is False

        page.locator('#closeCommentsPanel').click();page.wait_for_timeout(100)
        marker=page.locator('.note-gutter-marker').nth(1);marker.hover();page.wait_for_timeout(550);page.wait_for_selector('#commentHoverCard:not(.hidden)')
        pos=page.evaluate("""()=>{const card=document.querySelector('#commentHoverCard').getBoundingClientRect(),marker=document.querySelectorAll('.note-gutter-marker')[1],prose=marker.closest('.manuscript-block-wrap').querySelector('.manuscript-block').getBoundingClientRect();return {cardRight:card.right,proseLeft:prose.left,cardLeft:card.left,width:card.width}}""")
        assert pos['cardRight'] <= pos['proseLeft']+2,pos
        assert pos['cardLeft'] >= 7,pos
        assert pos['width'] <= 240,pos
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4155_note_flyer_left_scrollable_and_five_line_editor(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        tid=page.evaluate("""async()=>{
          const chapter=state.chapter,block=state.chapterData.blocks[0];
          const anchor='A note anchor paragraph with enough manuscript text for a stable marker position.';
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:block.id,block_type:'paragraph',semantic_style:'body',content_html:anchor,text_content:anchor}]})});
          await selectChapter(chapter.id,false);
          const b=state.chapterData.blocks[0];
          const longNote=Array.from({length:18},(_,i)=>`Line ${i+1}: this deliberately long Note verifies flyer scrolling.`).join('\\n');
          const note=await api('/api/comments',{method:'POST',body:JSON.stringify({body:longNote,items:[{block_id:b.id,start_offset:0,end_offset:20,selected_text:anchor.slice(0,20)}]})});
          await reloadManuscriptView();return note.id;
        }""")
        marker=page.locator('.note-gutter-marker').first
        marker.hover();page.wait_for_timeout(340);page.wait_for_selector('#commentHoverCard:not(.hidden)')
        pos=page.evaluate("""()=>{const c=document.querySelector('#commentHoverCard'),cb=c.getBoundingClientRect(),m=document.querySelector('.note-gutter-marker').getBoundingClientRect();return {cardRight:cb.right,markerLeft:m.left,clientHeight:c.clientHeight,scrollHeight:c.scrollHeight,overflowY:getComputedStyle(c).overflowY}}""")
        assert pos['cardRight'] <= pos['markerLeft']+2,pos
        assert pos['overflowY']=='auto',pos
        assert pos['scrollHeight'] > pos['clientHeight'],pos
        before=page.locator('#commentHoverCard').evaluate('el=>el.scrollTop')
        page.locator('#commentHoverCard').hover();page.mouse.wheel(0,300);page.wait_for_timeout(100)
        after=page.locator('#commentHoverCard').evaluate('el=>el.scrollTop')
        assert after>before,(before,after)

        page.locator('#commentsBtn').click();page.wait_for_selector('#commentsPanel:not(.hidden)')
        page.locator(f'[data-comment-row="{tid}"]').click()
        area=page.locator('[data-comment-message]').first
        metrics=area.evaluate("el=>({height:el.getBoundingClientRect().height,lineHeight:parseFloat(getComputedStyle(el).lineHeight),resize:getComputedStyle(el).resize,rows:el.rows})")
        assert metrics['rows']==5,metrics
        assert metrics['resize']=='vertical',metrics
        assert metrics['height'] >= metrics['lineHeight']*4.7,metrics
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4156_note_responses_autosize_and_explorer_space(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        tid=page.evaluate("""async()=>{
          const chapter=state.chapter,block=state.chapterData.blocks[0];
          const text='A manuscript paragraph used to verify compact note responses in the Notes panel.';
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:block.id,block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text}]})});
          await selectChapter(chapter.id,false);
          const b=state.chapterData.blocks[0];
          const note=await api('/api/comments',{method:'POST',body:JSON.stringify({body:'Original note remains a comfortable five-line editor.',items:[{block_id:b.id,start_offset:0,end_offset:20,selected_text:text.slice(0,20)}]})});
          await api(`/api/comments/${note.id}/messages`,{method:'POST',body:JSON.stringify({body:'Short response'})});
          await api(`/api/comments/${note.id}/messages`,{method:'POST',body:JSON.stringify({body:'one\\ntwo\\nthree\\nfour\\nfive\\nsix\\nseven'})});
          await reloadManuscriptView();
          return note.id;
        }""")
        page.locator('#commentsBtn').click();page.wait_for_selector('#commentsPanel:not(.hidden)')
        page.locator(f'[data-comment-row="{tid}"]').click()
        page.wait_for_selector('[data-comment-message]')
        metrics=page.evaluate("""()=>{
          const areas=[...document.querySelectorAll('[data-comment-message]')];
          const m=el=>{const s=getComputedStyle(el);return {rows:el.rows,height:el.getBoundingClientRect().height,line:parseFloat(s.lineHeight),client:el.clientHeight,scroll:el.scrollHeight,overflow:s.overflowY}};
          const list=document.querySelector('#commentsList');
          return {note:m(areas[0]),short:m(areas[1]),long:m(areas[2]),reply:m(document.querySelector('#commentReplyText')),basis:getComputedStyle(list).flexBasis};
        }""")
        assert metrics['note']['rows']==5,metrics
        assert metrics['short']['rows']==1,metrics
        assert metrics['short']['height'] < metrics['short']['line']*2.2,metrics
        assert metrics['long']['rows']==1,metrics
        assert metrics['long']['height'] <= metrics['long']['line']*5+20,metrics
        assert metrics['long']['scroll'] > metrics['long']['client'],metrics
        assert metrics['long']['overflow']=='auto',metrics
        assert metrics['reply']['rows']==1,metrics
        assert metrics['basis']=='48%',metrics

        reply=page.locator('#commentReplyText')
        reply.fill('first line\nsecond line\nthird line')
        three=reply.evaluate("el=>({height:el.getBoundingClientRect().height,line:parseFloat(getComputedStyle(el).lineHeight)})")
        assert three['height'] >= three['line']*2.7,three
        reply.fill('1\n2\n3\n4\n5\n6\n7\n8')
        capped=reply.evaluate("el=>({height:el.getBoundingClientRect().height,line:parseFloat(getComputedStyle(el).lineHeight),client:el.clientHeight,scroll:el.scrollHeight,overflow:getComputedStyle(el).overflowY})")
        assert capped['height'] <= capped['line']*5+20,capped
        assert capped['scroll'] > capped['client'],capped
        assert capped['overflow']=='auto',capped
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4157_follow_up_vocabulary_and_cuts_label(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        assert page.locator('#selectionToolbar [data-selection-action="cuts"]').inner_text().strip()=='Cut'
        tid=page.evaluate("""async()=>{
          const chapter=state.chapter,block=state.chapterData.blocks[0];
          const text='A paragraph used to verify the Notes follow-up vocabulary.';
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:block.id,block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text}]})});
          await selectChapter(chapter.id,false);
          const b=state.chapterData.blocks[0];
          const note=await api('/api/comments',{method:'POST',body:JSON.stringify({body:'Original note',items:[{block_id:b.id,start_offset:0,end_offset:12,selected_text:text.slice(0,12)}]})});
          await api(`/api/comments/${note.id}/messages`,{method:'POST',body:JSON.stringify({body:'Later thought'})});
          await reloadManuscriptView();
          return note.id;
        }""")
        page.locator('#commentsBtn').click();page.wait_for_selector('#commentsPanel:not(.hidden)')
        row=page.locator(f'[data-comment-row="{tid}"]')
        row.click()
        assert page.locator('#commentReplyText').get_attribute('placeholder')=='Add a follow-up…'
        assert page.locator('#commentReplyText').get_attribute('aria-label')=='Follow-up'
        assert page.locator('#commentReplySend').inner_text().strip()=='Add follow-up'
        assert page.locator('[data-comment-message]').nth(1).get_attribute('aria-label')=='Follow-up'
        badge=row.locator('.comments-row-replies')
        assert badge.inner_text().strip()=='1'
        assert badge.get_attribute('title')=='1 follow-up'
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v416_markup_one_click_active_highlighter_and_create_highlight(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        page.evaluate("""async()=>{
          const chapter=state.chapter,block=state.chapterData.blocks[0];
          const text='Alpha beta gamma delta';
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:block.id,block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text}]})});
          await selectChapter(chapter.id,false);
        }""")
        page.locator('#markupBtn').click();page.wait_for_selector('#markupPanel:not(.hidden)')
        page.locator('#markerEmptyNew').click();page.wait_for_selector('#markerName')
        page.locator('#markerName').fill('Continuity 416')
        assert page.locator('#createMarkerConfirm').inner_text().strip()=='Create'
        page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>state.markers.some(m=>m.name==='Continuity 416')")
        marker_id=page.evaluate("()=>state.markers.find(m=>m.name==='Continuity 416').id")

        def select_offsets(start,end):
            page.evaluate("""([start,end])=>{
              const prose=document.querySelector('.manuscript-block');
              const walker=document.createTreeWalker(prose,NodeFilter.SHOW_TEXT);
              let node=walker.nextNode();
              if(!node)throw new Error('No manuscript text node');
              const range=document.createRange();range.setStart(node,start);range.setEnd(node,end);
              const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
              document.dispatchEvent(new Event('selectionchange'));
            }""",[start,end])
            page.wait_for_timeout(50)

        # One-off workflow: select prose, click the marker once, no modal/dropdown.
        select_offsets(6,10)
        page.locator(f'[data-marker-use="{marker_id}"]').click()
        page.wait_for_function("""id=>fetch('/api/annotations?status=open&label_id='+id).then(r=>r.json()).then(rows=>rows.filter(x=>x.annotation_type==='highlight').length===1)""",arg=marker_id)

        # No selection: clicking the row turns it into a persistent highlighter.
        page.evaluate("""()=>{const prose=document.querySelector('#editor .manuscript-block');placeCaret(prose,0);state.lastManuscriptRange=getSelection().getRangeAt(0).cloneRange()}""")
        page.locator(f'[data-marker-use="{marker_id}"]').click()
        page.wait_for_function(f"()=>state.activeMarkupMarkerId==={marker_id}")
        assert page.locator(f'[data-marker-row="{marker_id}"]').evaluate("el=>el.classList.contains('active')")

        # The next pointer-finalized selection is highlighted automatically.
        select_offsets(11,16)
        page.evaluate("showSelectionToolbarAfterPointer()")
        page.wait_for_function("""id=>fetch('/api/annotations?status=open&label_id='+id).then(r=>r.json()).then(rows=>rows.filter(x=>x.annotation_type==='highlight').length===2)""",arg=marker_id)

        # Escape from the manuscript itself exits highlighter mode.
        page.locator('#editor .manuscript-block').first.focus();page.locator('#editor .manuscript-block').first.press('Escape')
        page.wait_for_function("()=>state.activeMarkupMarkerId===null")

        # Creating a marker while prose is selected applies it immediately.
        select_offsets(17,22)
        page.locator('#newLabelBtn').click();page.wait_for_selector('#markerName')
        assert page.locator('#createMarkerConfirm').inner_text().strip()=='Create & highlight'
        page.locator('#markerName').fill('Research 416')
        page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>state.markers.some(m=>m.name==='Research 416')")
        second_id=page.evaluate("()=>state.markers.find(m=>m.name==='Research 416').id")
        page.wait_for_function("""id=>fetch('/api/annotations?status=open&label_id='+id).then(r=>r.json()).then(rows=>rows.some(x=>x.annotation_type==='highlight'&&x.selected_text==='delta'))""",arg=second_id)

        assert page.locator('#selectionToolbar [data-selection-action="highlight"]').count()==1
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4161_marker_validation_order_color_and_panel_layout(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.locator('#markupBtn').click();page.wait_for_selector('#markupPanel:not(.hidden)')
        page.locator('#markerEmptyNew').click();page.wait_for_selector('#markerName')
        first_color=page.locator('#markerColorText').input_value().lower()
        assert first_color.startswith('#') and len(first_color)==7

        # Empty names explain the problem instead of silently doing nothing.
        page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent==='Marker needs a name.'")
        assert page.locator('#markerName').is_visible()
        page.locator('#markerName').fill('First 4161')
        page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>state.markers.some(m=>m.name==='First 4161')")
        first_id=page.evaluate("()=>state.markers.find(m=>m.name==='First 4161').id")
        assert page.locator('#labelList [data-marker-row]').first.get_attribute('data-marker-row')==str(first_id)

        # A new dialog gets a new algorithmically selected color.
        page.locator('#newLabelBtn').click();page.wait_for_selector('#markerName')
        second_color=page.locator('#markerColorText').input_value().lower()
        assert second_color!=first_color
        page.locator('#markerName').fill('Second 4161')
        page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>state.markers.some(m=>m.name==='Second 4161')")
        second_id=page.evaluate("()=>state.markers.find(m=>m.name==='Second 4161').id")
        assert page.locator('#labelList [data-marker-row]').first.get_attribute('data-marker-row')==str(second_id)

        # Duplicate name and duplicate color are both explicit toasts and keep the form open.
        page.locator('#newLabelBtn').click();page.wait_for_selector('#markerName')
        page.locator('#markerName').fill('first 4161')
        page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent.includes('already exists')")
        assert page.locator('#markerName').is_visible()
        page.locator('#markerName').fill('Third 4161')
        page.locator('#markerColorText').fill(first_color)
        page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent==='That color is already used by another marker.'")
        assert page.locator('#markerName').is_visible()
        page.locator('#modalClose').click()

        # The panel shell stays fixed; only the marker rack owns scrolling. Marker + menu are seamless.
        layout=page.evaluate("""()=>{
          const panel=document.querySelector('#markupPanel'),list=document.querySelector('#labelList');
          const row=list.querySelector('[data-marker-row]'),use=row.querySelector('.marker-use'),more=row.querySelector('.marker-more');
          const ps=getComputedStyle(panel),ls=getComputedStyle(list),rs=getComputedStyle(row),us=getComputedStyle(use),ms=getComputedStyle(more);
          return {panelOverflowY:ps.overflowY,listOverflowY:ls.overflowY,rowGap:rs.gap,useRight:us.borderRightWidth,moreLeft:ms.borderLeftWidth};
        }""")
        assert layout['panelOverflowY']=='hidden'
        assert layout['listOverflowY']=='auto'
        assert layout['rowGap'] in {'0px','normal'}
        assert layout['useRight']=='0px' and layout['moreLeft']=='0px'

        # Suggested colors are visible on both manuscript surfaces and choose readable text.
        contrast=page.evaluate("""color=>({light:contrastRatio(color,'#ffffff'),dark:contrastRatio(color,'#222322'),text:contrastRatio(color,markerTextColor(color)),textColor:markerTextColor(color)})""",second_color)
        assert contrast['light']>=1.32 and contrast['dark']>=1.32
        assert contrast['text']>=4.5
        assert contrast['textColor'] in {'#202020','#ececea'}

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4162_markup_margin_swatches_hover_and_navigation(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        setup=page.evaluate("""async()=>{
          const chapter=state.chapter,block=state.chapterData.blocks[0];
          const text='Alpha beta gamma delta epsilon';
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:block.id,block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text}]})});
          const first=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Continuity 4162',color:'#4f8fd8'})});
          const second=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Rewrite 4162',color:'#d8874f'})});
          await api('/api/annotations/batch',{method:'POST',body:JSON.stringify({annotation_type:'highlight',label_id:first.id,body:'',items:[{block_id:block.id,start_offset:6,end_offset:10,selected_text:'beta',prefix_context:'Alpha ',suffix_context:' gamma'}]})});
          await api('/api/annotations/batch',{method:'POST',body:JSON.stringify({annotation_type:'highlight',label_id:second.id,body:'',items:[{block_id:block.id,start_offset:11,end_offset:16,selected_text:'gamma',prefix_context:'Alpha beta ',suffix_context:' delta'}]})});
          await refresh(false);await selectChapter(chapter.id,false);
          return {first:first.id,second:second.id,firstColor:first.color};
        }""")
        page.wait_for_selector('[aria-label="Continuity 4162 highlight"]')
        first=page.locator('[aria-label="Continuity 4162 highlight"]')
        second=page.locator('[aria-label="Rewrite 4162 highlight"]')
        assert first.count()==1 and second.count()==1
        assert page.locator('.legacy-annotation-gutter').count()==0

        first.hover()
        flyer=page.locator('#markupHoverCard')
        flyer.wait_for(state='visible',timeout=1500)
        assert flyer.is_visible()
        assert flyer.inner_text().strip()=='Continuity 4162'
        assert 'beta' not in flyer.inner_text()
        dot=flyer.locator('.markup-hover-dot')
        assert dot.count()==1
        dot_color=dot.evaluate("el=>getComputedStyle(el).backgroundColor")
        assert dot_color
        geometry=page.evaluate("""()=>{
          const sw=document.querySelector('[aria-label="Continuity 4162 highlight"]'),fly=document.querySelector('#markupHoverCard');
          const s=sw.getBoundingClientRect(),f=fly.getBoundingClientRect();
          return {swLeft:s.left,flyRight:f.right};
        }""")
        assert geometry['flyRight'] < geometry['swLeft']

        # Clicking a swatch uses Marker navigation instead of the old annotation modal.
        first.click()
        page.wait_for_selector('#findPopover:not(.hidden)')
        assert page.locator('#findInput').input_value()=='Marker matches'
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')")
        assert page.locator('#findCounter').inner_text().strip()=='1 / 1'

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)



def test_browser_v4163_empty_markup_and_selection_highlight_picker(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        page.evaluate("""async()=>{
          const chapter=state.chapter,block=state.chapterData.blocks[0],text='Alpha beta gamma delta epsilon zeta';
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:block.id,block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text}]})});
          await selectChapter(chapter.id,false);
        }""")
        page.get_by_test_id('markup-panel').wait_for(state='hidden')
        page.locator('#markupBtn').click();page.get_by_test_id('markup-panel').wait_for(state='visible')
        empty_new=page.get_by_test_id('marker-empty-new')
        empty_new.wait_for(state='visible')
        assert page.get_by_test_id('marker-new').evaluate("el=>el.classList.contains('hidden')")
        empty_layout=page.evaluate("""()=>{const list=document.querySelector('#labelList'),box=document.querySelector('.marker-empty-state');const l=list.getBoundingClientRect(),b=box.getBoundingClientRect();return {delta:Math.abs((l.left+l.width/2)-(b.left+b.width/2))}}""")
        assert empty_layout['delta'] < 3

        empty_new.click();page.wait_for_selector('#markerName')
        page.locator('#markerName').fill('First 4163');page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>state.markers.some(m=>m.name==='First 4163')")
        first_id=page.evaluate("()=>state.markers.find(m=>m.name==='First 4163').id")
        page.get_by_test_id('marker-new').click();page.wait_for_selector('#markerName')
        page.locator('#markerName').fill('Second 4163');page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>state.markers.some(m=>m.name==='Second 4163')")
        second_id=page.evaluate("()=>state.markers.find(m=>m.name==='Second 4163').id")

        def set_selection(start,end):
            page.evaluate("""([start,end])=>{const prose=document.querySelector('[data-testid=\"manuscript-block\"]'),node=prose.firstChild;const range=document.createRange();range.setStart(node,start);range.setEnd(node,end);const sel=getSelection();sel.removeAllRanges();sel.addRange(range);state.manuscriptPointerSelecting=false;state.lastManuscriptRange=range.cloneRange();document.dispatchEvent(new Event('selectionchange'));}""",[start,end])

        def select_for_toolbar(start,end):
            set_selection(start,end)
            toolbar=page.get_by_test_id('manuscript-selection-toolbar')
            toolbar.wait_for(state='visible')
            page.get_by_test_id('manuscript-selection-highlight').wait_for(state='visible')

        # Active Marker mode is a one-click highlighter: selecting prose applies the
        # marker directly and intentionally suppresses the ordinary selection toolbar.
        page.locator(f'[data-marker-use="{second_id}"]').click()
        page.wait_for_function('(id)=>+state.activeMarkupMarkerId===+id',arg=second_id)
        set_selection(6,10)
        page.evaluate('showSelectionToolbarAfterPointer()')
        page.wait_for_function("([id])=>fetch('/api/annotations?status=open&label_id='+id).then(r=>r.json()).then(rows=>rows.some(x=>x.annotation_type==='highlight'&&x.selected_text==='beta'))",arg=[second_id])
        page.wait_for_function('(id)=>+state.lastUsedMarkupMarkerId===+id',arg=second_id)
        assert page.get_by_test_id('manuscript-selection-toolbar').evaluate("el=>el.classList.contains('hidden')")

        # Escape turns the highlighter off. A normal selection then exposes the
        # compact Highlight picker and keeps recent-marker ordering deterministic.
        page.keyboard.press('Escape')
        page.wait_for_function('()=>state.activeMarkupMarkerId===null')
        select_for_toolbar(11,16)
        page.get_by_test_id('manuscript-selection-highlight').click()
        picker=page.get_by_test_id('manuscript-highlight-picker');picker.wait_for(state='visible')
        picker_rows=page.locator('[data-selection-highlight-marker]')
        assert picker_rows.first.get_attribute('data-selection-highlight-marker')==str(second_id)
        overflow=page.locator('#selectionHighlightMarkerList').evaluate("el=>getComputedStyle(el).overflowY")
        assert overflow=='auto'

        # Pick First; it applies immediately and becomes the next recent marker.
        page.locator(f'[data-selection-highlight-marker="{first_id}"]').click()
        page.wait_for_function("""id=>fetch('/api/annotations?status=open&label_id='+id).then(r=>r.json()).then(rows=>rows.some(x=>x.annotation_type==='highlight'&&x.selected_text==='gamma'))""",arg=first_id)
        assert page.evaluate("()=>state.lastUsedMarkupMarkerId")==first_id
        assert page.evaluate("()=>localStorage.getItem('breadmaker-last-markup-marker')")==str(first_id)

        # A later selection opens with First at the top now.
        select_for_toolbar(17,22)
        page.get_by_test_id('manuscript-selection-highlight').click();picker.wait_for(state='visible')
        assert page.locator('[data-selection-highlight-marker]').first.get_attribute('data-selection-highlight-marker')==str(first_id)

        # Markup gutter occupies a lane close to prose instead of the Notes lane.
        page.locator(f'[data-selection-highlight-marker="{second_id}"]').click()
        page.wait_for_selector('[aria-label="Second 4163 highlight"]')
        gutter=page.evaluate("""()=>{const sw=document.querySelector('[aria-label="Second 4163 highlight"]'),prose=sw.closest('.manuscript-block-wrap').querySelector('.manuscript-block');const s=sw.getBoundingClientRect(),p=prose.getBoundingClientRect();return {gap:p.left-s.right,left:getComputedStyle(sw.parentElement).left}}""")
        assert 0 <= gutter['gap'] < 32
        assert gutter['left']=='-27px'

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)

def test_browser_v4164_in_place_edits_keep_writer_position(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        page.evaluate("""async()=>{
          const chapter=state.chapter,first=state.chapterData.blocks[0];
          const blocks=[];
          for(let i=0;i<48;i++){
            const text=`Paragraph ${i} has enough manuscript prose to occupy some vertical room and verify position stability after editorial changes.`;
            blocks.push({id:i===0?first.id:null,block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text});
          }
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks})});
          await selectChapter(chapter.id,false);
          const marker=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Position 4164',color:'#6f93bf'})});
          await refresh(false);renderMarkers();
          return marker.id;
        }""")
        marker_id=page.evaluate("()=>state.markers.find(m=>m.name==='Position 4164').id")
        page.locator('.manuscript-block-wrap').nth(34).scroll_into_view_if_needed()
        page.evaluate("document.querySelectorAll('.manuscript-block-wrap')[34].scrollIntoView({block:'center'})")
        page.wait_for_timeout(80)

        def scroll_top():
            return page.locator('#documentScroller').evaluate('el=>el.scrollTop')

        # Highlighting rerenders the manuscript, but the visible manuscript anchor must stay put.
        before=scroll_top(); assert before>100
        page.evaluate("""async id=>{
          const prose=document.querySelectorAll('.manuscript-block')[34],node=prose.firstChild;
          const range=document.createRange();range.setStart(node,0);range.setEnd(node,12);
          const sel=getSelection();sel.removeAllRanges();sel.addRange(range);state.lastManuscriptRange=range.cloneRange();
          await applyAnnotation('highlight',selectionItems(),id,'');
        }""",marker_id)
        after=scroll_top(); assert abs(after-before)<8,(before,after)

        # Creating a Note uses the same in-place reload contract.
        page.evaluate("""async()=>{
          const prose=document.querySelectorAll('.manuscript-block')[35],node=prose.firstChild;
          const range=document.createRange();range.setStart(node,0);range.setEnd(node,14);
          const sel=getSelection();sel.removeAllRanges();sel.addRange(range);state.lastManuscriptRange=range.cloneRange();
          await beginCommentComposer(selectionItems());
        }""")
        page.locator('#commentCreateText').fill('Keep the writer here.')
        note_before=scroll_top();page.locator('#commentCreateSubmit').click()
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent==='Note added'")
        note_after=scroll_top();assert abs(note_after-note_before)<8,(note_before,note_after)

        # Creating a Checkpoint must not disturb the manuscript position either.
        page.locator('#checkpointBtn').click();page.wait_for_selector('#checkpointPanel:not(.hidden)')
        checkpoint_before=scroll_top();page.locator('#checkpointCreateBtn').click();page.wait_for_selector('#checkpointCreateConfirm')
        page.locator('#checkpointCreateName').fill('Position checkpoint')
        page.locator('#checkpointCreateConfirm').click();page.wait_for_function("()=>document.querySelector('#toast')?.textContent.startsWith('Checkpoint created')")
        checkpoint_after=scroll_top();assert abs(checkpoint_after-checkpoint_before)<8,(checkpoint_before,checkpoint_after)

        # Marker deletion hides the Configure Marker frame immediately and preserves the viewport through its reload.
        page.locator('#checkpointBtn').click();page.wait_for_function("()=>document.querySelector('#checkpointPanel')?.classList.contains('hidden')")
        page.locator('#markupBtn').click();page.wait_for_selector('#markupPanel:not(.hidden)')
        page.locator(f'[data-marker-edit="{marker_id}"]').click();page.get_by_text('Details',exact=True).click();page.wait_for_selector('#deleteMarkerConfirm')
        marker_before=scroll_top();page.locator('#deleteMarkerConfirm').click();page.wait_for_selector('#dialogConfirm')
        page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>document.querySelector('#modal')?.classList.contains('hidden')")
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent==='Marker deleted'")
        marker_after=scroll_top();assert abs(marker_after-marker_before)<8,(marker_before,marker_after)

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4165_undo_redo_keep_writer_position_and_cut_label(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        page.evaluate("""async()=>{
          const chapter=state.chapter,first=state.chapterData.blocks[0],blocks=[];
          for(let i=0;i<52;i++){
            const text=`Undo position paragraph ${i} has enough prose to make the chapter tall and keep the viewport meaningful.`;
            blocks.push({id:i===0?first.id:null,block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text});
          }
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks})});
          await selectChapter(chapter.id,false);
          const marker=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Undo position 4165',color:'#7ea989'})});
          await refresh(false);renderMarkers();
          return marker.id;
        }""")
        marker_id=page.evaluate("()=>state.markers.find(m=>m.name==='Undo position 4165').id")
        page.evaluate("document.querySelectorAll('.manuscript-block-wrap')[38].scrollIntoView({block:'center'})")
        page.wait_for_timeout(80)
        scroller=page.locator('#documentScroller')
        def top(): return scroller.evaluate('el=>el.scrollTop')
        baseline=top();assert baseline>100

        # Selection toolbar terminology is singular for the direct action.
        page.evaluate("""()=>{
          const prose=document.querySelectorAll('.manuscript-block')[38],node=prose.firstChild;
          const range=document.createRange();range.setStart(node,0);range.setEnd(node,12);
          const sel=getSelection();sel.removeAllRanges();sel.addRange(range);
          state.lastManuscriptRange=range.cloneRange();positionSelectionToolbar(range);
        }""")
        assert page.locator('#selectionToolbar [data-selection-action="cuts"]').inner_text().strip()=='Cut'

        # Create one structural highlight without changing the visible location.
        page.evaluate("""async id=>{
          const prose=document.querySelectorAll('.manuscript-block')[38],node=prose.firstChild;
          const range=document.createRange();range.setStart(node,0);range.setEnd(node,12);
          const sel=getSelection();sel.removeAllRanges();sel.addRange(range);state.lastManuscriptRange=range.cloneRange();
          await applyAnnotation('highlight',selectionItems(),id,'');
        }""",marker_id)
        after_highlight=top();assert abs(after_highlight-baseline)<8,(baseline,after_highlight)

        # Structural Undo/Redo refresh/reopen the manuscript; neither is navigation.
        await_before=top();page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)");assert page.locator('#toast').inner_text().splitlines()[0]=='Undone'
        undo_top=top();assert abs(undo_top-await_before)<8,(await_before,undo_top)
        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)");assert page.locator('#toast').inner_text().splitlines()[0]=='Redone'
        redo_top=top();assert abs(redo_top-undo_top)<8,(undo_top,redo_top)

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4166_marker_description_search_details_and_navigation_only(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        setup=page.evaluate("""async()=>{
          const chapter=state.chapter,block=state.chapterData.blocks[0],text='Alpha beta gamma delta';
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:block.id,block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text}]})});
          const continuity=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Continuity 4166',description:'Timeline contradictions and callbacks',color:'#4f8fd8'})});
          await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Rewrite 4166',description:'Prose that needs another pass',color:'#d8874f'})});
          await api('/api/annotations/batch',{method:'POST',body:JSON.stringify({annotation_type:'highlight',label_id:continuity.id,body:'',items:[{block_id:block.id,start_offset:6,end_offset:10,selected_text:'beta',prefix_context:'Alpha ',suffix_context:' gamma'}]})});
          await refresh(false);await selectChapter(chapter.id,false);return {id:continuity.id};
        }""")
        page.locator('#markupBtn').click();page.wait_for_selector('#markupPanel:not(.hidden)')
        page.locator('#markerSearch').fill('timeline')
        rows=page.locator('#labelList [data-marker-row]')
        assert rows.count()==1
        assert 'Continuity 4166' in rows.first.inner_text()
        assert 'Timeline contradictions' not in rows.first.inner_text()

        page.locator(f'[data-marker-row="{setup["id"]}"] .marker-more').click()
        page.locator('#contextMenu button',has_text='Details').click()
        page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text().strip()=='Marker Details'
        assert page.locator('#markerDescription').input_value()=='Timeline contradictions and callbacks'
        page.locator('#modalClose').click()

        page.locator('#markerSearch').fill('')
        page.locator(f'[data-marker-row="{setup["id"]}"] .marker-more').click()
        page.locator('#contextMenu button',has_text='Navigate').click()
        page.wait_for_selector('#findPopover:not(.hidden)')
        assert page.locator('#findInput').input_value()=='Marker matches'
        assert page.locator('#findExpand').evaluate("el=>el.classList.contains('hidden')")
        assert page.locator('#replaceRow').evaluate("el=>el.classList.contains('hidden')")

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4167_marker_description_hover_only_when_present(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        setup=page.evaluate("""async()=>{
          const described=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Continuity 4167',description:'Track timeline contradictions without cluttering the marker rack.',color:'#527fa8'})});
          const plain=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Plain 4167',description:'',color:'#a86652'})});
          await refresh(false);return {described:described.id,plain:plain.id};
        }""")
        page.locator('#markupBtn').click();page.wait_for_selector('#markupPanel:not(.hidden)')
        described=page.locator(f'[data-marker-row="{setup["described"]}"]')
        described.hover()
        flyer=page.locator('#markerPanelHoverCard')
        flyer.wait_for(state='visible',timeout=1500)
        assert flyer.inner_text().strip()=='Track timeline contradictions without cluttering the marker rack.'

        plain=page.locator(f'[data-marker-row="{setup["plain"]}"]')
        plain.hover()
        flyer.wait_for(state='hidden',timeout=1500)
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v417_unified_manuscript_panels_and_view_font(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        widths=[]
        for button,panel in [('#commentsBtn','#commentsPanel'),('#markupBtn','#markupPanel'),('#checkpointBtn','#checkpointPanel'),('#cutsBtn','#cutsPanel')]:
            page.locator(button).click();page.wait_for_selector(panel+':not(.hidden)')
            widths.append(round(page.locator(panel).evaluate('el=>el.getBoundingClientRect().width')))
            page.locator(button).click()
        assert len(set(widths))==1,widths

        page.locator('#viewBtn').hover();page.wait_for_timeout(420)
        assert page.locator('#viewTooltip').is_visible()
        page.locator('#viewBtn').click();page.wait_for_selector('#viewPopover:not(.hidden)')
        assert not page.locator('#viewTooltip').is_visible()
        page.locator('#manuscriptFont').select_option('verdana')
        assert 'Verdana' in page.locator('#editor').evaluate("el=>getComputedStyle(el).fontFamily")
        assert page.evaluate("()=>localStorage.getItem('breadmaker-manuscript-font')")=='verdana'
        assert not errors,errors
    finally:
        _shutdown_page(page)


def test_browser_v417_checkpoint_references_deleted_current_and_book_scope(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        setup=page.evaluate("""async()=>{
          const first=state.chapter;
          const cp1=await api('/api/checkpoints',{method:'POST',body:JSON.stringify({scope:'chapter',chapter_id:first.id,name:'Chapter one guide'})});
          const second=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Reference Chapter',tab_id:state.activeDocumentId})});
          await api('/api/checkpoints',{method:'POST',body:JSON.stringify({scope:'chapter',chapter_id:second.id,name:'Current chapter guide'})});
          const book=await api('/api/checkpoints',{method:'POST',body:JSON.stringify({scope:'book',name:'Whole book guide'})});
          await api(`/api/chapters/${first.id}`,{method:'DELETE'});
          await refresh(false);await selectChapter(second.id,false,false);
          return {second:second.id,cp1:cp1.id,book:book.id};
        }""")
        page.locator('#checkpointBtn').click();page.wait_for_selector('#checkpointPanel:not(.hidden)')
        page.locator('#checkpointScopeFilter').select_option('chapters')
        names=page.locator('#checkpointList .checkpoint-list-main b').all_inner_texts()
        assert 'Chapter one guide' in names and 'Current chapter guide' in names
        page.locator('#checkpointScopeFilter').select_option('current')
        current_names=page.locator('#checkpointList .checkpoint-list-main b').all_inner_texts()
        assert 'Current chapter guide' in current_names and 'Chapter one guide' not in current_names

        page.locator('#checkpointScopeFilter').select_option('chapters')
        page.locator(f'[data-checkpoint-row="{setup["cp1"]}"] [data-checkpoint-compare]').click();page.wait_for_selector('#checkpointCompareToolbar:not(.hidden)')
        assert page.evaluate('()=>state.chapter.id')==setup['second']
        assert page.locator('#checkpointCompareToolbar .checkpoint-context-label').inner_text().strip()=='Reference'
        assert page.locator('#checkpointSplitShowDiff').is_disabled()
        options=page.locator('#checkpointSplitSelect option').all_inner_texts()
        assert any(x.startswith('Chapter · Chapter one guide') for x in options)
        assert any(x.startswith('Book · Whole book guide') for x in options)
        page.locator('#checkpointSplitSelect').select_option(str(setup['book']))
        page.wait_for_function("()=>document.querySelector('#splitBody')?.innerText.includes('Reference Chapter')")
        split_text=page.locator('#splitBody').inner_text()
        assert 'Chapter 1' in split_text and 'Reference Chapter' in split_text
        page.evaluate("()=>openWholeBook(false,false)");page.wait_for_function("()=>state.viewMode==='book'")
        page.wait_for_function("()=>{const el=document.querySelector('#checkpointSplitShowDiff');return !!el&&!el.disabled}",timeout=2500)
        assert not page.locator('#checkpointSplitShowDiff').is_disabled()
        assert not errors,errors
    finally:
        _shutdown_page(page)


def test_browser_v4171_manuscript_tool_cards_share_visible_borders(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        setup=page.evaluate("""async()=>{
          const chapter=await api(`/api/chapters/${state.chapter.id}`),block=chapter.blocks[0];
          const marker=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Border marker 4171',color:'#6f8fb5'})});
          const note=await api('/api/comments',{method:'POST',body:JSON.stringify({body:'Border note 4171',items:[{block_id:block.id,start_offset:0,end_offset:0,selected_text:''}]})});
          const checkpoint=await api('/api/checkpoints',{method:'POST',body:JSON.stringify({scope:'chapter',chapter_id:state.chapter.id,name:'Border checkpoint 4171'})});
          const cut=await api('/api/cuts',{method:'POST',body:JSON.stringify({title:'Border cut 4171',content_html:'<p>Saved prose for border test.</p>',source_chapter_id:state.chapter.id})});
          await refresh(false);
          return {marker:marker.id,note:note.id,checkpoint:checkpoint.id,cut:cut.id};
        }""")

        targets=[]
        page.locator('#markupBtn').click();page.wait_for_selector('#markupPanel:not(.hidden)')
        targets.append(page.locator(f'[data-marker-row="{setup["marker"]}"]'))
        page.locator('#commentsBtn').click();page.wait_for_selector('#commentsPanel:not(.hidden)')
        targets.append(page.locator(f'[data-comment-row="{setup["note"]}"]'))
        page.locator('#checkpointBtn').click();page.wait_for_selector('#checkpointPanel:not(.hidden)')
        targets.append(page.locator(f'[data-checkpoint-row="{setup["checkpoint"]}"]'))
        page.locator('#cutsBtn').click();page.wait_for_selector('#cutsPanel:not(.hidden)')
        targets.append(page.locator(f'[data-cut-row="{setup["cut"]}"]'))

        # Re-open each owner panel before measuring its row; every card reserves the
        # same real 1px solid border instead of relying on a transparent placeholder.
        pairs=[('#markupBtn','#markupPanel',f'[data-marker-row="{setup["marker"]}"]'),('#commentsBtn','#commentsPanel',f'[data-comment-row="{setup["note"]}"]'),('#checkpointBtn','#checkpointPanel',f'[data-checkpoint-row="{setup["checkpoint"]}"]'),('#cutsBtn','#cutsPanel',f'[data-cut-row="{setup["cut"]}"]')]
        seen=[]
        for button,panel,row in pairs:
            if not page.locator(panel).is_visible():
                page.locator(button).click();page.wait_for_selector(panel+':not(.hidden)')
            seen.append(page.locator(row).evaluate("el=>{const s=getComputedStyle(el);return {width:s.borderTopWidth,style:s.borderTopStyle,color:s.borderTopColor}}"))
        assert all(x['width']=='1px' and x['style']=='solid' for x in seen),seen
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4173_find_scope_full_task_and_manuscript_range_navigation(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        # Find must update its scope label even with an empty query.
        page.locator('#viewBtn').click(); page.locator('#viewFindReplaceBtn').click()
        assert page.locator('#findScopeLabel').inner_text()=='Chapter'
        page.locator('#bookViewBtn').click(); page.wait_for_timeout(250)
        assert page.locator('#findScopeLabel').inner_text()=='Book'
        page.locator('#findClose').click()

        # Return to a chapter and create stable prose for a linked Task.
        page.locator('.chapter-row').first.click(); page.wait_for_timeout(180)
        prose=page.locator('.manuscript-block').first
        page.evaluate("""() => {const p=document.querySelector('.manuscript-block');p.innerText='The linked manuscript passage should be easy to find again.';p.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:null}));}""")
        page.evaluate("saveNow()"); page.wait_for_timeout(120)

        selected=page.evaluate("""
          () => {const p=document.querySelector('.manuscript-block');const text=p.firstChild||p;const start=p.innerText.indexOf('linked'),end=p.innerText.indexOf('passage')+'passage'.length;const r=document.createRange();r.setStart(text,start);r.setEnd(text,end);const s=getSelection();s.removeAllRanges();s.addRange(r);state.lastManuscriptRange=r.cloneRange();positionSelectionToolbar(r);return selectionItems().map(x=>x.selected_text).join(' ')}
        """)
        assert 'linked manuscript passage' in selected
        page.locator('[data-selection-action="more"]').click()
        menu=page.locator('#contextMenu')
        assert menu.get_by_text('Suggest rewrite…',exact=True).count()==0
        assert menu.get_by_text('Add editorial note…',exact=True).count()==0
        menu.get_by_text('Task',exact=True).click()

        # Selection-created Tasks use the full editor, not the old title prompt.
        assert page.locator('#taskName').is_visible()
        assert page.locator('#taskDescription').is_visible()
        assert page.locator('#taskMilestoneNew').is_visible()
        assert page.locator('#taskStatus').is_visible()
        assert page.locator('#taskMarker').is_visible()
        assert 'linked manuscript passage' in page.locator('.task-linked-selection-text').inner_text().lower()
        page.locator('#taskName').fill('Rewrite linked passage')
        page.locator('#taskDescription').fill('Use the full task editor from manuscript context.')
        page.locator('#taskMilestoneNew').fill('Check the dialogue')
        page.locator('#taskMilestoneAdd').click()
        page.locator('#saveTask').click(); page.wait_for_timeout(350)

        def tasks_named():
            return [row for row in requests.get(live_server+'/api/tasks',timeout=5).json() if row['title']=='Rewrite linked passage']
        assert len(tasks_named())==1
        task_id=tasks_named()[0]['id']
        refs=requests.get(live_server+f'/api/references?source_type=task&source_id={task_id}',timeout=5).json()
        assert len(refs)==1 and refs[0]['target_type']=='manuscript_range'

        # Task + selection link are one structural Undo/Redo unit.
        page.keyboard.press('Control+z'); page.wait_for_timeout(300)
        assert not tasks_named()
        page.keyboard.press('Control+y'); page.wait_for_timeout(350)
        assert len(tasks_named())==1
        task_id=tasks_named()[0]['id']
        refs=requests.get(live_server+f'/api/references?source_type=task&source_id={task_id}',timeout=5).json()
        assert len(refs)==1

        # Following the Task link returns to and visibly identifies the manuscript range.
        page.locator('[data-view="tasks"]').click(); page.wait_for_timeout(180)
        page.locator('.task-row').filter(has_text='Rewrite linked passage').click(); page.wait_for_timeout(180)
        page.locator('.reference-target').click(); page.wait_for_timeout(250)
        assert page.locator('#manuscriptView').get_attribute('class').find('active')>=0
        assert page.evaluate("CSS.highlights && CSS.highlights.has('breadmaker-linked-range')") is True
        assert 'linked manuscript passage' in page.locator('.manuscript-block').first.inner_text().lower()

        assert not errors, errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected, unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v4174_linker_only_shows_available_concrete_targets_and_open_tasks(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    headers={'X-Breadmaker-Client':'e2e-tab'}
    try:
        # Create all three link families plus tasks in several workflow states.
        open_task=requests.post(live_server+'/api/tasks',headers=headers,json={'title':'Open target','status':'open'},timeout=5).json()
        requests.post(live_server+'/api/tasks',headers=headers,json={'title':'Later target','status':'later'},timeout=5).raise_for_status()
        requests.post(live_server+'/api/tasks',headers=headers,json={'title':'Done target','status':'done'},timeout=5).raise_for_status()
        canvas=requests.post(live_server+'/api/canvases',headers=headers,json={'name':'Reference Canvas'},timeout=5).json()
        requests.post(live_server+f'/api/canvases/{canvas["id"]}/nodes',headers=headers,json={'title':'Reference Node'},timeout=5).raise_for_status()
        story_map=requests.post(live_server+'/api/maps',headers=headers,json={'name':'Reference Map'},timeout=5).json()
        line=requests.post(live_server+f'/api/maps/{story_map["id"]}/lines',headers=headers,json={'name':'Story'},timeout=5).json()
        requests.post(live_server+f'/api/map-lines/{line["id"]}/items',headers=headers,json={'title':'Reference Beat','planned_start':.4},timeout=5).raise_for_status()
        page.evaluate('refresh(true)');page.wait_for_timeout(220)

        # Make a stable manuscript selection and open the universal linker.
        page.evaluate("""() => {const p=document.querySelector('.manuscript-block');if(!p.innerText.trim()){p.innerText='Link this manuscript passage.';p.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:null}))}const text=p.firstChild||p;const r=document.createRange();r.setStart(text,0);r.setEnd(text,Math.min(4,p.innerText.length));const s=getSelection();s.removeAllRanges();s.addRange(r);state.lastManuscriptRange=r.cloneRange();positionSelectionToolbar(r)}""")
        page.locator('[data-selection-action="more"]').click()
        page.locator('#contextMenu').get_by_text('Link…',exact=True).click()
        page.wait_for_selector('#selectionLinkType')
        assert page.locator('#selectionLinkType option').all_text_contents()==['Canvas','Map','Task']
        assert 'Scene' not in page.locator('#modal').inner_text()

        # Task choices are Open only.
        page.locator('#selectionLinkType').select_option('task');page.wait_for_timeout(120)
        task_choices=page.locator('#simpleLinkTarget option').all_text_contents()
        assert 'Open target' in task_choices,task_choices
        assert 'Later target' not in task_choices and 'Done target' not in task_choices,task_choices

        # Canvas is a family: the whole Canvas and content Nodes are both valid targets.
        page.locator('#selectionLinkType').select_option('canvas_family');page.wait_for_timeout(150)
        canvas_choices=page.locator('#compoundLinkTarget option').all_text_contents()
        assert 'Canvas · Reference Canvas' in canvas_choices
        assert 'Card · Reference Node' in canvas_choices
        page.locator('#compoundLinkTarget').select_option(f'canvas:{canvas["id"]}')
        page.locator('#saveSelectionLink').click();page.wait_for_timeout(180)
        refs=requests.get(live_server+f'/api/references?source_type=canvas&source_id={canvas["id"]}',timeout=5).json()
        assert any(ref['target_type']=='manuscript_range' for ref in refs)

        assert not errors,errors
        unexpected=[text for typ,text in console if typ=='error' and 'favicon' not in text.lower()]
        assert not unexpected,unexpected
    finally:
        try: requests.post(live_server+'/api/lease/release',headers={'X-Breadmaker-Client':'e2e-tab'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_browser_v4175_checkpoint_cards_hover_details_and_apply_to_live_chapter(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        setup=page.evaluate("""async()=>{
          const source=state.chapter,tab=state.activeDocumentId;
          const sourceBlock=document.querySelector('.manuscript-block');
          sourceBlock.innerText='Saved source words for apply.';
          sourceBlock.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
          await flushSave();
          const cp=await api('/api/checkpoints',{method:'POST',body:JSON.stringify({scope:'chapter',chapter_id:source.id,description:'Use this quieter dialogue as a reference.'})});
          const target=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:'Target Chapter',tab_id:tab})});
          await api(`/api/chapters/${target.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{block_type:'paragraph',semantic_style:'body',content_html:'Target draft words.',text_content:'Target draft words.'}]})});
          await refresh(false);await selectChapter(target.id,false,false);
          return {source:source.id,target:target.id,cp:cp.id,sourceTitle:source.title};
        }""")
        page.locator('#checkpointBtn').click();page.wait_for_selector('#checkpointPanel:not(.hidden)')
        page.locator('#checkpointScopeFilter').select_option('chapters')
        row=page.locator(f'[data-checkpoint-row="{setup["cp"]}"]')
        assert row.is_visible()
        assert row.locator('b').inner_text()==setup['sourceTitle'], 'Blank custom name should display the saved Chapter name.'
        assert page.locator('#checkpointPanelDetail').count()==0
        assert page.locator('#checkpointList').evaluate("el=>getComputedStyle(el).overflowY") in {'auto','scroll'}

        # Description is quiet metadata: hidden in the card, revealed after 300 ms hover.
        assert 'Use this quieter dialogue' not in row.inner_text()
        row.hover()
        flyer=page.locator('#checkpointPanelHoverCard')
        flyer.wait_for(state='visible',timeout=1500)
        assert flyer.inner_text().strip()=='Use this quieter dialogue as a reference.'

        # Integrated overflow menu exposes Details/Edit/Compare/Delete, but never Apply.
        row.locator('[data-checkpoint-more]').click()
        labels=page.locator('#contextMenu button').all_inner_texts()
        assert labels==['Compare','Details','Delete']
        page.locator('#contextMenu button').filter(has_text='Details').click();page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text()=='Checkpoint Details'
        body=page.locator('#modalBody').inner_text()
        assert setup['sourceTitle'] in body and page.locator('#checkpointEditDescription').input_value()=='Use this quieter dialogue as a reference.'
        assert 'Tag' not in body
        page.locator('#modalClose').click()

        # Compare is the gateway to Apply. Cross-Chapter Apply targets the live Chapter and keeps its title.
        row.locator('[data-checkpoint-compare]').click();page.wait_for_selector('#checkpointCompareToolbar:not(.hidden)')
        assert page.locator('#checkpointSplitApply').inner_text()=='Apply…'
        assert page.locator('#checkpointSplitRestore').count()==0
        page.locator('#checkpointSplitApply').click();page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#modalTitle').inner_text()=='Apply Checkpoint'
        assert 'Target Chapter' in page.locator('#modalBody').inner_text()
        assert page.locator('#dialogConfirm').inner_text()=='Replace Chapter'
        page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>document.querySelector('.manuscript-block')?.innerText.includes('Saved source words for apply.')")
        assert page.locator('#chapterTitle').inner_text()=='Target Chapter'
        assert 'Saved source words for apply.' in page.locator('.manuscript-block').first.inner_text()

        # Apply is a normal structural action: Undo restores the live target, Redo reapplies the saved state.
        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)");assert page.locator('#toast').inner_text().splitlines()[0]=='Undone'
        page.wait_for_function("()=>document.querySelector('.manuscript-block')?.innerText.includes('Target draft words.')")
        assert page.locator('#chapterTitle').inner_text()=='Target Chapter'
        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)");assert page.locator('#toast').inner_text().splitlines()[0]=='Redone'
        page.wait_for_function("()=>document.querySelector('.manuscript-block')?.innerText.includes('Saved source words for apply.')")
        assert page.locator('#chapterTitle').inner_text()=='Target Chapter'
        cps=page.evaluate("async()=> (await api('/api/checkpoints')).checkpoints")
        applied=next(x for x in cps if +x['id']==+setup['cp'])
        assert +applied['source_chapter_id']==+setup['source'], 'Cross-Chapter Apply must not rewrite immutable checkpoint provenance.'

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)



def test_browser_v4177_split_defaults_half_resizes_and_checkpoint_more_is_compact(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.set_viewport_size({'width':1200,'height':820})
        page.evaluate("""()=>{closeCheckpointPanel();closeCutsPanel();closeCommentsPanel();document.querySelector('#markupPanel').classList.add('hidden');showSplit('Reference','<article class="split-paper"><p>'+('Reference words '.repeat(120))+'</p></article>','chapter',999)}""")
        page.wait_for_function("()=>!document.querySelector('#splitPane').classList.contains('hidden')&&!document.querySelector('#splitResizer').classList.contains('hidden')")
        page.wait_for_timeout(80)
        initial=page.evaluate("""()=>{
          const view=document.querySelector('#manuscriptView').getBoundingClientRect(),nav=document.querySelector('.book-nav').getBoundingClientRect(),live=document.querySelector('#manuscriptPrimaryPane').getBoundingClientRect(),split=document.querySelector('#splitPane').getBoundingClientRect(),grip=document.querySelector('#splitResizer').getBoundingClientRect();
          const usable=view.width-nav.width-grip.width;
          return {usable,calc:splitUsableWidth(),live:live.width,split:split.width,grip:grip.width,nav:nav.width,view:view.width,flex:document.querySelector('#splitPane').style.flex,gripX:grip.x+grip.width/2,gripY:grip.y+100,toolbarOverflow:document.querySelector('.document-toolbar').scrollWidth>=document.querySelector('.document-toolbar').clientWidth};
        }""")
        assert abs(initial['live']-initial['split']) <= 3, initial
        assert abs(initial['split']-(initial['usable']/2)) <= 3

        # Dragging the divider changes the pane ratio and keeps both panes usable.
        page.mouse.move(initial['gripX'],initial['gripY'])
        page.mouse.down()
        page.mouse.move(initial['gripX']+130,initial['gripY'],steps=5)
        page.mouse.up();page.wait_for_timeout(40)
        moved=page.evaluate("""()=>({live:document.querySelector('#manuscriptPrimaryPane').getBoundingClientRect().width,split:document.querySelector('#splitPane').getBoundingClientRect().width,saved:Number(localStorage.getItem('breadmaker.splitRatio.v1'))})""")
        assert moved['live'] > initial['live']+80
        assert moved['split'] < initial['split']-80
        assert .15 < moved['saved'] < .85 and abs(moved['saved']-.5)>.05

        # Double-click is a quick reset to the intended 50/50 default.
        grip=page.locator('#splitResizer')
        grip.dblclick();page.wait_for_timeout(40)
        reset=page.evaluate("""()=>({live:document.querySelector('#manuscriptPrimaryPane').getBoundingClientRect().width,split:document.querySelector('#splitPane').getBoundingClientRect().width,saved:Number(localStorage.getItem('breadmaker.splitRatio.v1'))})""")
        assert abs(reset['live']-reset['split']) <= 3
        assert abs(reset['saved']-.5)<.001

        # Comparison toolbars remain horizontally reachable as panes are narrowed.
        page.evaluate("""()=>{document.querySelector('#checkpointCompareToolbar').classList.remove('hidden');applySplitRatio(.72)}""")
        toolbar=page.evaluate("""()=>{const el=document.querySelector('#checkpointCompareToolbar'),style=getComputedStyle(el);return {overflow:style.overflowX,client:el.clientWidth,scroll:el.scrollWidth}}""")
        assert toolbar['overflow']=='auto'
        assert toolbar['scroll'] >= toolbar['client']

        # Checkpoint overflow hover target matches the compact Marker-sized control.
        page.evaluate("""()=>{const box=document.querySelector('#checkpointList');box.innerHTML='<div class="checkpoint-list-row"><button class="checkpoint-use"><span class="checkpoint-list-main"><b>Reference</b><small>Chapter · now · 10 words</small></span></button><button class="checkpoint-more" id="v4177More">⋮</button></div>';document.querySelector('#checkpointPanel').classList.remove('hidden')}""")
        more=page.evaluate("""()=>{const el=document.querySelector('#v4177More'),s=getComputedStyle(el),r=el.getBoundingClientRect();return {w:r.width,h:r.height,radius:s.borderRadius}}""")
        assert 31 <= more['w'] <= 33
        assert 33 <= more['h'] <= 35
        assert more['radius']=='4px'

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4178_find_toggle_and_selection_toolbar_skips_non_actionable_selection(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.locator('#viewBtn').click(); page.locator('#viewFindReplaceBtn').click(); page.wait_for_selector('#findPopover:not(.hidden)')
        page.locator('#findClose').click();page.wait_for_function("()=>document.querySelector('#findPopover').classList.contains('hidden')")
        page.locator('#viewBtn').click(); page.locator('#viewFindReplaceBtn').click(); page.wait_for_selector('#findPopover:not(.hidden)')

        # Chapter headings are actionable editorial selections now. A selection in
        # toolbar chrome is genuinely non-manuscript and must not summon the toolbar.
        page.evaluate("""()=>{
          const chrome=document.querySelector('#activeDocumentTitle');
          const r=document.createRange();r.selectNodeContents(chrome);
          const sel=getSelection();sel.removeAllRanges();sel.addRange(r);
          document.dispatchEvent(new Event('selectionchange'));
        }""")
        page.wait_for_timeout(80)
        assert page.locator('#selectionToolbar').evaluate("el=>el.classList.contains('hidden')") is True
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v4179_clear_markers_row_is_first_and_works_like_eraser(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        assert _focus_first_chapter(page) is not None
        page.evaluate("""async()=>{
          const chapter=state.chapter,block=state.chapterData.blocks[0],text='Alpha beta gamma delta epsilon';
          await api(`/api/chapters/${chapter.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{id:block.id,block_type:'paragraph',semantic_style:'body',content_html:text,text_content:text}]})});
          await selectChapter(chapter.id,false);
        }""")
        page.locator('#markupBtn').click();page.wait_for_selector('#markupPanel:not(.hidden)')
        page.locator('#markerEmptyNew').click();page.locator('#markerName').fill('Erase test');page.locator('#createMarkerConfirm').click()
        page.wait_for_function("()=>state.markers.some(m=>m.name==='Erase test')")
        marker_id=page.evaluate("()=>state.markers.find(m=>m.name==='Erase test').id")
        assert page.locator('#labelList .marker-row').first.locator('.marker-name').inner_text().strip()=='Clear markers'

        def select_offsets(start,end):
            page.evaluate("""([start,end])=>{
              const prose=document.querySelector('#editor .manuscript-block'),node=document.createTreeWalker(prose,NodeFilter.SHOW_TEXT).nextNode();
              const range=document.createRange();range.setStart(node,start);range.setEnd(node,end);
              const selection=getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));
            }""",[start,end]);page.wait_for_timeout(40)

        # Highlight the first phrase, then erase only its middle.
        select_offsets(0,16);page.locator(f'[data-marker-use="{marker_id}"]').click()
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent==='Highlighted with Erase test'")
        select_offsets(6,10);page.locator('[data-marker-clear]').click()
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent==='Markers cleared'")
        spans=page.evaluate("""id=>fetch('/api/annotations?status=all&label_id='+id).then(r=>r.json()).then(rows=>rows.filter(a=>a.annotation_type==='highlight').map(a=>[a.start_offset,a.end_offset]).sort((a,b)=>a[0]-b[0]))""",marker_id)
        assert spans==[[0,6],[10,16]]

        # With no selection it becomes an active eraser for repeated selections.
        page.evaluate("()=>{const prose=document.querySelector('#editor .manuscript-block');placeCaret(prose,0)}")
        page.locator('[data-marker-clear]').click();page.wait_for_function("()=>state.activeMarkupClear===true")
        select_offsets(10,16);page.evaluate('showSelectionToolbarAfterPointer()')
        page.wait_for_function("""id=>fetch('/api/annotations?status=all&label_id='+id).then(r=>r.json()).then(rows=>rows.filter(a=>a.annotation_type==='highlight').length===1)""",arg=marker_id)
        page.wait_for_timeout(150)
        page.locator('#editor .manuscript-block').first.focus();page.locator('#editor .manuscript-block').first.press('Escape')
        page.wait_for_function("()=>state.activeMarkupClear===false")
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_browser_v418_scene_outline_fast_creation_and_blank_scene_start(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.evaluate("""async()=>{const ch=state.chapters[0];await api(`/api/chapters/${ch.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[{text_content:'Cinna argues with Eugene in the bakery.',content_html:'Cinna argues with Eugene in the bakery.'}]})});await refresh(false);await selectChapter(ch.id,false)}""")
        page.evaluate("""()=>{const p=document.querySelector('.manuscript-block'),n=p.firstChild||p,r=document.createRange();r.setStart(n,0);r.setEnd(n,p.innerText.length);const s=getSelection();s.removeAllRanges();s.addRange(r);positionSelectionToolbar(r)}""")
        before=page.evaluate('state.scenes.length')
        page.locator('[data-selection-action="scene"]').click()
        page.wait_for_function('(n)=>state.scenes.length===n+1',arg=before)
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')") is True
        written=page.evaluate('state.scenes[state.scenes.length-1]')
        assert written['title'].startswith('Scene ') and written['block_count']==1

        page.locator('#scenesNavMode').click()
        page.wait_for_selector('.scene-outline-row')
        assert written['title'] in page.locator('#chapterTree').inner_text()
        assert 'Written scene' in page.locator('#chapterTree').inner_text() or 'Chapter' in page.locator('#chapterTree').inner_text()

        _navigator_action(page,'Add Scene')
        page.wait_for_function('(n)=>state.scenes.length===n+1&&state.viewMode==="scene"',arg=before+1)
        created=page.evaluate('state.scenes[state.scenes.length-1]')
        assert created['block_count']==1
        assert page.locator('#editor .manuscript-block').count()==1
        assert page.locator('#editor .manuscript-block').first.inner_text()==''
        page.keyboard.type('New scene starts here')
        page.evaluate('flushSave()');page.wait_for_function("()=>document.querySelector('#saveState')?.textContent==='Saved'")

        page.evaluate('openWholeBook(false)');page.wait_for_timeout(180)
        assert page.locator('.ghost-scene-title').count()>=1
        assert float(page.locator('.ghost-scene-title').first.evaluate("el=>parseFloat(getComputedStyle(el).fontSize)"))<=10.5
        assert not errors, errors
    finally:
        _shutdown_page(page)


def test_browser_v4182_scene_view_gaps_reorder_and_full_book_boundaries(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        ids=page.evaluate("""async()=>{
          const ch=state.chapters[0],first=(await api(`/api/chapters/${ch.id}`)).blocks[0];
          const saved=await api(`/api/chapters/${ch.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:[
            {id:first.id,content_html:'Scene A prose',text_content:'Scene A prose'},
            {content_html:'Loose bridge words here',text_content:'Loose bridge words here'},
            {content_html:'Scene B prose',text_content:'Scene B prose'}]})});
          const a=await api('/api/scenes',{method:'POST',body:JSON.stringify({title:'Scene A',block_ids:[saved.ids[0]]})});
          const b=await api('/api/scenes',{method:'POST',body:JSON.stringify({title:'Scene B',block_ids:[saved.ids[2]]})});
          await refresh(false);await selectChapter(ch.id,false);return {a:a.id,b:b.id,ch:ch.id};
        }""")
        page.locator('#manuscriptPaper').click(position={'x':20,'y':20})
        assert page.locator('#manuscriptPaper').evaluate("el=>getComputedStyle(el).outlineStyle")=='none'
        page.locator('#scenesNavMode').click();page.wait_for_selector('.scene-gap-row')
        gap=page.locator('.scene-gap-row').first
        gap.hover();page.wait_for_timeout(350)
        assert 'words outside Scenes' in page.locator('#sceneGapHover').inner_text()
        # 4.18.10 keeps the compact rail collapse and exposes navigator options through ⋮.
        assert page.locator('#toggleBookNavBtn').is_visible()
        assert page.locator('#navigatorMenuBtn').is_visible()
        assert page.locator('.scene-outline-row').count()==2
        assert page.locator('.scene-outline-row .scene-outline-title').nth(0).is_visible()
        assert page.locator('.scene-outline-row .scene-outline-title').nth(1).is_visible()
        assert page.locator('.scene-gap-row').count()>=1

        # Double-click opens only the Scene prose. Scene identity stays in quiet start/end boundaries, while the toolbar keeps the document title.
        page.locator(f'.scene-outline-row[data-scene-id="{ids["a"]}"]').dblclick()
        page.wait_for_function("()=>state.viewMode==='scene'&&state.scene?.title==='Scene A'")
        assert page.locator('#chapterTitle').is_hidden()
        assert page.locator('#editor .manuscript-block').count()==1
        assert 'Scene A prose' in page.locator('#editor').inner_text()
        assert page.locator('#editor .ghost-scene-title').count()==1
        assert page.locator('#editor .ghost-scene-end').count()==1
        assert page.locator('#activeDocumentTitle').inner_text()==page.evaluate("activeDocument()?.title||'Manuscript'")
        prose=page.locator('#editor .manuscript-block').first
        page.evaluate("()=>{const p=document.querySelector('#editor .manuscript-block');placeCaret(p,p.innerText.length)}")
        page.keyboard.type(' edited')
        page.evaluate('flushSave()');page.wait_for_function("()=>document.querySelector('#saveState')?.textContent==='Saved'")
        persisted=page.evaluate("cid=>fetch('/api/chapters/'+cid).then(r=>r.json()).then(d=>d.blocks.map(b=>b.text_content))",ids['ch'])
        assert persisted==['Scene A prose edited','Loose bridge words here','Scene B prose']

        # Full Book only marks a Scene at its actual start; no `continued` marker is rendered.
        page.locator('#bookViewBtn').click();page.wait_for_function("()=>state.viewMode==='book'")
        assert page.locator('.ghost-scene-title.continued').count()==0
        assert 'continued' not in page.locator('#editor').inner_text().lower()

        # Reorder with the same drag affordance as Chapters. The gap prose stays put.
        page.locator('#scenesNavMode').click()
        source=page.locator(f'.scene-outline-row[data-scene-id="{ids["b"]}"]')
        target=page.locator(f'.scene-outline-row[data-scene-id="{ids["a"]}"]')
        source.drag_to(target)
        page.wait_for_function("([a,b])=>state.scenes.findIndex(s=>s.id===b)<state.scenes.findIndex(s=>s.id===a)",arg=[ids['a'],ids['b']])
        texts=page.evaluate("cid=>fetch('/api/chapters/'+cid).then(r=>r.json()).then(d=>d.blocks.map(b=>b.text_content))",ids['ch'])
        assert texts==['Scene B prose','Scene A prose edited','Loose bridge words here']
        assert page.locator('.scene-gap-row').count()>=1
        page.locator('#undoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)");assert page.locator('#toast').inner_text().splitlines()[0]=='Undone'
        undone=page.evaluate("cid=>fetch('/api/chapters/'+cid).then(r=>r.json()).then(d=>d.blocks.map(b=>b.text_content))",ids['ch'])
        assert undone==['Scene A prose edited','Loose bridge words here','Scene B prose']
        page.locator('#redoBtn').click();page.evaluate("()=>state.historyChain.then(()=>true)");assert page.locator('#toast').inner_text().splitlines()[0]=='Redone'
        redone=page.evaluate("cid=>fetch('/api/chapters/'+cid).then(r=>r.json()).then(d=>d.blocks.map(b=>b.text_content))",ids['ch'])
        assert redone==['Scene B prose','Scene A prose edited','Loose bridge words here']
        # The derived dotted gap is also an intentional drop target; its prose stays put.
        page.locator('#scenesNavMode').click() if page.locator('#scenesNavMode').get_attribute('class') and 'active' not in page.locator('#scenesNavMode').get_attribute('class') else None
        page.wait_for_selector('.scene-gap-row')
        page.locator(f'.scene-outline-row[data-scene-id="{ids["b"]}"]').drag_to(page.locator('.scene-gap-row').first)
        page.wait_for_timeout(180)
        gap_drop=page.evaluate("cid=>fetch('/api/chapters/'+cid).then(r=>r.json()).then(d=>d.blocks.map(b=>b.text_content))",ids['ch'])
        assert gap_drop==['Scene A prose edited','Scene B prose','Loose bridge words here']
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_v4185_per_tab_navigation_visibility_and_scene_markers(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.evaluate("""async()=>{await api('/api/projects',{method:'POST',body:JSON.stringify({name:'Per Tab Navigator'})});await refresh(false);const primary=state.documents.find(d=>d.is_manuscript)||state.documents[0];state.activeDocumentId=primary.id;await selectChapter(state.chapters.find(c=>c.tab_id===primary.id).id,false)}""")
        # Primary manuscript has meaningful Chapter structure but no Scenes: one list, no redundant mode switch.
        initial=page.evaluate("""()=>({modeHidden:document.querySelector('#manuscriptNavMode').classList.contains('hidden'),chapters:document.querySelectorAll('#chapterTree .chapter-row').length,scenes:document.querySelectorAll('#chapterTree .scene-outline-row').length})""")
        assert initial['modeHidden'] and initial['chapters']>=1 and initial['scenes']==0

        # A secondary freeform tab begins with neither Chapter nor Scene navigation exposed.
        ids=page.evaluate("""async()=>{const notes=await api('/api/documents',{method:'POST',body:JSON.stringify({title:'Notes'})});await refresh(false);state.activeDocumentId=notes.id;await selectChapter(notes.chapter_id,false);return {notes:notes.id,primary:(state.documents.find(d=>d.is_manuscript)||state.documents[0]).id}}""")
        empty=page.evaluate("""()=>({modeHidden:document.querySelector('#manuscriptNavMode').classList.contains('hidden'),tree:document.querySelector('#chapterTree').innerText.trim(),menuVisible:!!document.querySelector('#navigatorMenuBtn')})""")
        assert empty['modeHidden'] and empty['tree']=='' and empty['menuVisible']

        # The visible navigator ⋮ is the discovery surface; showing Scenes is local to this Tab.
        page.get_by_test_id('manuscript-navigator-menu').click();page.get_by_test_id('navigator-toggle-scenes').click();page.wait_for_function("()=>state.manuscriptNavMode==='scenes'")
        shown=page.evaluate("""()=>({empty:document.querySelector('#chapterTree').innerText.includes('No Scenes yet'),mode:state.manuscriptNavMode})""")
        assert shown['empty'] and shown['mode']=='scenes'

        # Creating a blank Scene binds its real manuscript block to Notes, not every project Tab.
        page.get_by_test_id('manuscript-navigator-menu').click()
        page.get_by_test_id('navigator-add-scene').click()
        page.wait_for_function("()=>state.scenes.some(s=>+s.tab_id===+state.activeDocumentId)",timeout=5000)
        note_scene=page.evaluate("""()=>state.scenes.find(s=>+s.tab_id===+state.activeDocumentId)?.id"""); assert note_scene
        page.evaluate(f"""async()=>{{state.activeDocumentId={ids['primary']};await selectChapter(state.chapters.find(c=>+c.tab_id===+state.activeDocumentId).id,false)}}""")
        primary_scene_count=page.evaluate("""()=>state.scenes.filter(s=>+s.tab_id===+state.activeDocumentId).length""")
        assert primary_scene_count==0
        page.evaluate(f"""async()=>{{state.activeDocumentId={ids['notes']};await selectChapter(state.chapters.find(c=>+c.tab_id===+state.activeDocumentId).id,false)}}""")

        # Type directly into the blank Scene and verify View -> Show Scenes is a per-tab manuscript display preference.
        page.evaluate(f"()=>selectScene({note_scene},false)")
        page.wait_for_function("()=>state.viewMode==='scene'")
        page.locator('#editor .manuscript-block').first.click()
        page.keyboard.type('Notes scene prose')
        page.evaluate('flushSave()');page.wait_for_function("()=>document.querySelector('#saveState')?.textContent==='Saved'")
        page.evaluate("()=>selectChapter(state.chapters.find(c=>+c.tab_id===+state.activeDocumentId).id,false)")
        page.wait_for_function("()=>state.viewMode==='chapter'")
        assert page.locator('.ghost-scene-title').count()==1
        page.locator('#viewBtn').click(); page.get_by_test_id('manuscript-view-scenes').click(); page.wait_for_function("()=>document.querySelector('#viewScenesBtn')?.getAttribute('aria-checked')==='false'&&document.querySelectorAll('.ghost-scene-title').length===0")
        assert page.locator('.ghost-scene-title').count()==0
        assert page.get_by_test_id('manuscript-view-scenes').get_attribute('aria-checked')=='false'
        page.evaluate(f"""async()=>{{state.activeDocumentId={ids['primary']};await selectChapter(state.chapters.find(c=>+c.tab_id===+state.activeDocumentId).id,false)}}""")
        assert page.evaluate("()=>sceneMarkersVisible()") is True
        page.evaluate(f"""async()=>{{state.activeDocumentId={ids['notes']};await selectChapter(state.chapters.find(c=>+c.tab_id===+state.activeDocumentId).id,false)}}""")
        assert page.evaluate("()=>sceneMarkersVisible()") is False

        # Focused Scene View always identifies itself even if normal Scene markers are hidden.
        page.evaluate(f"()=>selectScene({note_scene},false)")
        page.wait_for_function("()=>state.viewMode==='scene'")
        assert page.locator('#editor .ghost-scene-title').count()==1
        assert page.locator('#editor .ghost-scene-end').count()==1
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)

def test_browser_v4188_manuscript_overflow_standard_and_details_footers(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        # Tabs: visible ellipsis and right-click must expose the exact same actions.
        tab_more=page.locator('.document-more').first
        assert tab_more.is_visible()
        tab_more.click(); page.wait_for_selector('#contextMenu:not(.hidden)')
        tab_click=page.locator('#contextMenu button').all_inner_texts()
        page.mouse.click(500,500); page.wait_for_timeout(30)
        page.locator('.document-row').first.click(button='right'); page.wait_for_selector('#contextMenu:not(.hidden)')
        tab_right=page.locator('#contextMenu button').all_inner_texts()
        assert tab_click==tab_right
        page.mouse.click(500,500)

        # Chapters use the same explicit affordance and action list as right-click.
        chapter=page.locator('.chapter-row').first
        assert chapter.locator('.chapter-more').is_visible()
        chapter.locator('.chapter-more').click(); page.wait_for_selector('#contextMenu:not(.hidden)')
        chapter_click=page.locator('#contextMenu button').all_inner_texts()
        page.mouse.click(500,500)
        chapter.click(button='right'); page.wait_for_selector('#contextMenu:not(.hidden)')
        chapter_right=page.locator('#contextMenu button').all_inner_texts()
        assert chapter_click==chapter_right==['Bookmark','Split View','Add Chapter Below','Details','Cut','Delete']
        page.mouse.click(500,500)

        # Scenes can be rendered from client state for interaction-contract testing without
        # changing the shared test project's manuscript.
        page.evaluate("""()=>{
          const doc=state.activeDocumentId,ch=state.chapters.find(x=>+x.tab_id===+doc)||state.chapters[0];
          state.scenes=[{id:9918188,tab_id:doc,title:'Contract Scene',description:'',position:0,first_chapter_id:ch.id,chapter_count:1,word_count:3}];
          state.sceneGaps=[];state.manuscriptNavMode='scenes';updateManuscriptTabPrefs({scenes:true,mode:'scenes'},{persist:false});renderChapterTree();
        }""")
        scene=page.locator('.scene-outline-row').first
        assert scene.locator('.scene-more').is_visible()
        scene.locator('.scene-more').click();page.wait_for_selector('#contextMenu:not(.hidden)')
        scene_click=page.locator('#contextMenu button').all_inner_texts();page.mouse.click(500,500)
        scene.click(button='right');page.wait_for_selector('#contextMenu:not(.hidden)')
        scene_right=page.locator('#contextMenu button').all_inner_texts()
        assert scene_click==scene_right==['Add Scene Below','Bookmark','Details','Cut','Delete']
        page.mouse.click(500,500)

        # Cuts use the same 32x34 glyph as the rest, no custom separator/Add Note,
        # and Details owns Note editing plus Delete/Save.
        page.evaluate("""()=>{
          cutsWorkspace.items=[{id:8818188,title:'Contract Cut',note:'Keep this',content_text:'Removed prose',content_html:'<p>Removed prose</p>',source_project:'Test',source_chapter:'Chapter 1',source_scene:'',created_at:new Date().toISOString()}];
          cutsWorkspace.selectedId=8818188;document.querySelector('#cutsPanel').classList.remove('hidden');renderCutsList();
        }""")
        cut_more=page.locator('.cuts-more').first
        size=cut_more.evaluate("el=>({w:el.getBoundingClientRect().width,h:el.getBoundingClientRect().height,text:el.innerText.trim()})")
        assert size=={'w':32,'h':34,'text':'⋮'}
        cut_more.click();page.wait_for_selector('#contextMenu:not(.hidden)')
        cut_click=page.locator('#contextMenu button').all_inner_texts()
        assert cut_click==['Insert','Details','Delete']
        assert page.locator('#contextMenu .context-sep').count()==0
        page.mouse.click(500,500)
        page.locator('.cuts-list-row').first.click(button='right');page.wait_for_selector('#contextMenu:not(.hidden)')
        assert page.locator('#contextMenu button').all_inner_texts()==cut_click
        page.locator('#contextMenu').get_by_role('button',name='Details').click();page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#cutInformationDelete').is_visible() and page.locator('#cutInformationSave').is_visible()
        assert page.locator('#cutInformationClose').count()==0
        page.locator('#modalClose').click()

        # Checkpoint Details follows Marker Details: Delete + Save, dismissal stays in modal chrome.
        page.evaluate("""()=>openCheckpointDetails({id:7718188,scope:'chapter',source_chapter_id:null,source_chapter_title:'Chapter 1',source_exists:true,name:'Contract checkpoint',default_name:'Chapter 1',description:'',created_at:new Date().toISOString(),word_count:10})""")
        page.wait_for_selector('#modal:not(.hidden)')
        assert page.locator('#checkpointEditDelete').is_visible() and page.locator('#checkpointEditSave').is_visible()
        page.locator('#modalClose').click()

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_v4189_tab_selection_delete_and_section_menus(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        original=page.evaluate("()=>({id:state.activeDocumentId,title:activeDocument().title,count:state.documents.length})")

        # TABS overflow owns creation and list visibility.
        page.locator('#tabsMenuBtn').click()
        assert page.locator('#contextMenu button').filter(has_text='New Tab').count()==1
        assert page.locator('#contextMenu button').filter(has_text='Import Tabs').count()==1
        page.locator('#contextMenu button').filter(has_text='New Tab').click()
        page.locator('#newDocumentName').fill('Research')
        page.locator('#newDocumentDescription').fill('Secondary manuscript tab')
        page.locator('#createDocumentConfirm').click()
        page.wait_for_function('(n)=>state.documents.length===n+1',arg=original['count'])
        created=page.evaluate("()=>({id:state.activeDocumentId,title:activeDocument().title})")
        assert created['title']=='Research'
        assert page.locator('#activeDocumentTitle').inner_text()=='Research'
        assert page.locator('#bookViewBtn').inner_text().strip()=='Full Text'
        assert 'active' in (page.locator(f'.document-row[data-document-id="{created["id"]}"]').get_attribute('class') or '')

        # Tab rows are selectable; selection changes the manuscript context.
        page.locator(f'.document-row[data-document-id="{original["id"]}"] .document-row-main').click()
        page.wait_for_function('(id)=>state.activeDocumentId===id',arg=original['id'])
        assert page.locator('#activeDocumentTitle').inner_text()==original['title']
        page.locator(f'.document-row[data-document-id="{created["id"]}"] .document-row-main').click()
        page.wait_for_function('(id)=>state.activeDocumentId===id',arg=created['id'])

        # Per-Tab menus do not own collection import; Main Manuscript is an assignable role.
        page.locator(f'.document-row[data-document-id="{created["id"]}"] .document-more').click()
        assert page.locator('#contextMenu button').all_inner_texts()==['Create canvas from tab…','Import JSON…','Set as Main Manuscript','Details','Delete']
        page.locator('#contextMenu button').filter(has_text='Set as Main Manuscript').click()
        page.wait_for_function('(id)=>state.documents.find(tab=>tab.id===id)?.is_manuscript===1',arg=created['id'])
        assert page.evaluate('(id)=>state.documents.find(tab=>tab.id===id)?.is_manuscript',original['id'])==0
        page.locator(f'.document-row[data-document-id="{original["id"]}"] .document-more').click()
        assert page.locator('#contextMenu button').all_inner_texts()==['Create canvas from tab…','Import JSON…','Set as Main Manuscript','Details','Delete']
        page.keyboard.press('Escape')
        page.locator(f'.document-row[data-document-id="{created["id"]}"] .document-more').click()
        assert page.locator('#contextMenu button').all_inner_texts()==['Create canvas from tab…','Import JSON…','Details','Delete']
        page.keyboard.press('Escape')

        # The visible navigator overflow replaces standalone + / Show controls.
        page.locator('#navigatorMenuBtn').click()
        menu_text=page.locator('#contextMenu').inner_text()
        assert ('Add Chapter' in menu_text or 'Add Scene' in menu_text)
        assert 'Show Chapters' in menu_text and 'Show Scenes' in menu_text
        page.keyboard.press('Escape')

        # Hiding Tabs does not hide the selected Tab's navigator; Show Tabs restores them.
        page.locator('#tabsToggleBtn').click()
        assert page.locator('#documentList').is_hidden()
        assert page.locator('#bookViewBtn').is_visible() and page.locator('#navigatorMenuBtn').is_visible()
        page.locator('#tabsToggleBtn').click()
        assert page.locator('#documentList').is_visible()

        # Collapsed Scene mode with no Scenes shows F, not explanatory empty-state copy.
        page.evaluate("()=>{updateManuscriptTabPrefs({chapters:false,scenes:true,mode:'scenes'});state.manuscriptNavMode='scenes';state.bookNavCollapsed=true;renderChapterTree()}")
        assert page.locator('#collapsedFullTextBtn').is_visible()
        assert page.locator('#collapsedFullTextBtn').inner_text().strip()=='F'
        assert page.locator('.scene-nav-empty:visible').count()==0
        page.locator('#collapsedFullTextBtn').click();page.wait_for_timeout(80)
        assert page.evaluate('state.viewMode')=='book'
        page.evaluate("()=>{state.bookNavCollapsed=false;renderChapterTree()}")

        # Delete through the row menu, including the destructive confirmation.
        page.locator(f'.document-row[data-document-id="{created["id"]}"] .document-more').click()
        page.locator('#contextMenu button').filter(has_text='Delete').click()
        assert page.locator('#dialogConfirm').inner_text().strip()=='Delete Tab'
        page.locator('#dialogConfirm').click()
        page.wait_for_function('(id)=>!state.documents.some(tab=>tab.id===id)',arg=created['id'])
        assert page.evaluate('state.documents.length')==original['count']
        assert page.evaluate('state.activeDocumentId')==original['id']
        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)


def test_v41811_full_text_embedded_navigator_and_show_chapters(live_server,browser_runtime):
    page,console,errors=_browser_page(live_server,browser_runtime)
    try:
        page.locator('#bookViewBtn').click()
        page.wait_for_function("state.viewMode==='book'")
        chapter_count=page.evaluate('state.bookData.chapters.length')
        assert chapter_count>=1
        assert page.locator('.book-chapter-heading').count()==chapter_count

        # The navigator menu is physically contained by the Full Text control.
        box=page.evaluate("""()=>{const c=document.querySelector('#fullDocumentControl').getBoundingClientRect(),m=document.querySelector('#navigatorMenuBtn').getBoundingClientRect();return {cl:c.left,cr:c.right,ct:c.top,cb:c.bottom,ml:m.left,mr:m.right,mt:m.top,mb:m.bottom}}""")
        assert box['ml']>=box['cl'] and box['mr']<=box['cr'] and box['mt']>=box['ct'] and box['mb']<=box['cb']

        # With one navigator type visible the mode row disappears, but the embedded overflow remains discoverable.
        page.evaluate("()=>{updateManuscriptTabPrefs({chapters:true,scenes:false,mode:'chapters'});state.manuscriptNavMode='chapters';renderChapterTree()}")
        assert page.locator('#manuscriptNavToolbar').is_hidden()
        assert page.locator('#navigatorMenuBtn').is_visible()
        page.evaluate("()=>{updateManuscriptTabPrefs({chapters:true,scenes:true,mode:'chapters'});state.manuscriptNavMode='chapters';renderChapterTree()}")
        assert page.locator('#manuscriptNavToolbar').is_visible()

        # View -> Show Chapters is on by default and removes only Full Text presentation boundaries.
        page.locator('#viewBtn').click()
        chapters_toggle=page.locator('#viewChaptersBtn')
        assert chapters_toggle.get_attribute('aria-checked')=='true'
        chapters_toggle.click()
        page.wait_for_function("document.querySelectorAll('.book-chapter-heading').length===0")
        assert page.evaluate('manuscriptTabPrefs().chapterMarkers') is False
        assert page.evaluate('state.bookData.chapters.length')==chapter_count
        assert page.locator('.manuscript-block-wrap').count()>=1

        # Re-enable without touching Scene presentation state or manuscript structure.
        scene_pref_before=page.evaluate('manuscriptTabPrefs().sceneMarkers')
        page.locator('#viewChaptersBtn').click()
        page.wait_for_function('(n)=>document.querySelectorAll(\'.book-chapter-heading\').length===n',arg=chapter_count)
        assert page.evaluate('manuscriptTabPrefs().chapterMarkers') is True
        assert page.evaluate('manuscriptTabPrefs().sceneMarkers')==scene_pref_before
        assert page.evaluate('state.bookData.chapters.length')==chapter_count

        assert not errors,errors
        assert not [entry for entry in console if entry[0]=='error'],console
    finally:
        _shutdown_page(page)
