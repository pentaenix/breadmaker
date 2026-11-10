"""Bounded, disposable hosted-dev UI benchmark; never uses a user browser/profile.

This measures real UI actions and editable/saved completion against memory-backed
hosted APIs, not production Firestore performance. Run outside the release suite.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import os
import socket
import requests
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect
from scripts.browser_runtime import start_browser, close_browser

@contextmanager
def hosted_browser(home, *, setup_code):
    """Standalone large-fixture lifecycle; does not change test-suite deadlines."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, BREADMAKER_HOME=str(home), PYTHONPATH=str(ROOT/"src"),
        BREADMAKER_RUNTIME="hosted_dev", BREADMAKER_ENV="test",
        BREADMAKER_AUTH_MODE="developer", BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED="1")
    command = [sys.executable, "-c", "from app import app\n"+setup_code+
        f"\nimport uvicorn\nuvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')"]
    log = (home/"server.log").open("w+")
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
    driver = browser = None
    try:
        deadline = time.monotonic()+60
        while time.monotonic()<deadline:
            if proc.poll() is not None:
                log.seek(0); raise RuntimeError(log.read())
            try:
                if requests.get(base+"/api/runtime", timeout=.25).ok: break
            except requests.RequestException: pass
            time.sleep(.1)
        else:
            log.seek(0); raise RuntimeError("Large fixture startup exceeded 60s: "+log.read())
        driver, browser, _ = start_browser()
        yield base, browser.new_page()
    finally:
        if driver is not None: close_browser(driver, browser)
        proc.terminate()
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
        log.close()

SEED = '''
from backend.environment import runtime
uid=runtime.identity.active_profile_id()
pid=runtime.projects.create('Synthetic latency novel')['id']
domain=runtime.projects.domain
parents={'project_id':pid}
def make(kind,data,eid):
    return domain.create(kind,parents,data,user_id=uid,entity_id=eid)
make('document_tab',{'title':'Synthetic guide','position':1},'tab_guide')
make('chapter',{'title':'Guide','tab_id':'tab_guide','position':0},'chapter_guide')
make('manuscript_block',{'chapter_id':'chapter_guide','tab_id':'tab_guide','position':0,'block_type':'paragraph','semantic_style':'body','content_html':'Synthetic guide paragraph','text_content':'Synthetic guide paragraph'},'block_guide')
for c in range(34):
    cid='chapter_1' if c==0 else f'chapter_{c+1}'
    if c:make('chapter',{'title':f'Synthetic chapter {c+1:02}','tab_id':'tab_manuscript','position':c},cid)
    for s in range(2):
        make('scene',{'title':f'Synthetic scene {c*2+s+1:02}','tab_id':'tab_manuscript','position':c*2+s},f'scene_{c}_{s}')
    for p in range(40):
        prose=f'Synthetic chapter {c+1} paragraph {p+1}. '+('The traveler studies the harbor and carefully records every changing detail. '*5)
        make('manuscript_block',{'chapter_id':cid,'tab_id':'tab_manuscript','scene_id':f'scene_{c}_{p//20}','position':p+1,'block_type':'paragraph','semantic_style':'body','content_html':prose,'text_content':prose},f'block_{c}_{p}')
'''


def run(output: Path):
    rows=[]
    report={'environment':'localhost hosted_dev memory adapters, isolated Chromium',
            'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'worktree_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
            'fixture':{'chapters':34,'scenes':68,'paragraphs':1360,'additional_tabs':1},
            'measurements':rows,'errors':[]}
    with tempfile.TemporaryDirectory(prefix='breadmaker-action-benchmark-') as home:
        with hosted_browser(Path(home),setup_code=SEED) as (base,page):
            page.set_default_timeout(8000)
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.on('console',lambda msg:print('CONSOLE',msg.text,flush=True) if msg.type=='error' else None)
            page.on('response',lambda r:print('HTTP_ERROR',r.status,r.url,r.text(),flush=True) if r.status>=400 else None)
            requests=[]
            page.on('request',lambda r:requests.append({'method':r.method,'path':r.url.split(base)[-1]}) if r.url.startswith(base+'/api/') else None)
            page.add_init_script('''window.__inputPaint=[];document.addEventListener('input',e=>{if(!e.target.closest('#manuscriptPaper'))return;const t=performance.now();requestAnimationFrame(()=>requestAnimationFrame(()=>window.__inputPaint.push(performance.now()-t)))},true)''')
            pid=next(x['id'] for x in page.request.get(base+'/api/workspace').json()['projects'] if x['name']=='Synthetic latency novel')
            url=base+'/p/'+pid
            def measure(name,action,ready):
                start_index=len(requests);t=time.perf_counter()
                action();ready()
                row={'action':name,'completion_ms':round((time.perf_counter()-t)*1000,1),'api_requests':requests[start_index:]}
                rows.append(row);output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(row),flush=True)
            def editable():
                page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"&&!document.querySelector("#manuscriptPrimaryPane").inert&&document.querySelector("#editor .manuscript-block")')
            def book_ready():
                page.wait_for_function('()=>state.viewMode==="book"&&!fullTextLoading&&!document.querySelector("#manuscriptPrimaryPane").inert')
            def saved():
                try:page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
                except Exception:
                    print('SAVE_STATE',page.evaluate('()=>({dirty:state.dirty,inFlight:!!state.saveInFlight,notice:document.querySelector("#saveState")?.textContent,scene:state.scene?.id,revision:[...state.chapterRevisions]})'),flush=True)
                    raise
            def view(v):page.locator(f'.main-tab[data-view="{v}"]').click()
            measure('cold project chapter ready',lambda:page.goto(url,wait_until='domcontentloaded'),editable)
            measure('cold Full Text editable',lambda:page.locator('#bookViewBtn').click(),book_ready)
            report['fixture']['rendered_blocks']=page.locator('#editor .manuscript-block').count()
            for n in range(3):
                measure('warm Full Text to chapter',lambda:page.locator('#chapterTree').get_by_text('Synthetic chapter 02',exact=True).dblclick(),lambda:page.wait_for_function('()=>state.viewMode==="chapter"&&state.chapter?.id==="chapter_2"&&!document.querySelector("#manuscriptPrimaryPane").inert'))
                measure('warm chapter to Full Text',lambda:page.locator('#bookViewBtn').click(),book_ready)
                measure('first novel to guide tab' if n==0 else 'warm novel to guide tab',lambda:page.get_by_role('button',name='Synthetic guide',exact=True).click(),lambda:page.wait_for_function('()=>state.activeDocumentId==="tab_guide"&&!document.querySelector("#manuscriptPrimaryPane").inert&&document.querySelector("#editor")?.textContent.includes("Synthetic guide paragraph")'))
                measure('warm guide to novel tab',lambda:page.locator('#documentList button').filter(has_text='Manuscript').first.click(),book_ready)
            page.locator('#scenesNavMode').click()
            measure('cached Full Text to scene',lambda:page.locator('#chapterTree').get_by_text('Synthetic scene 01',exact=True).dblclick(),lambda:page.wait_for_function('()=>state.viewMode==="scene"&&state.scene?.id==="scene_0_0"&&!document.querySelector("#manuscriptPrimaryPane").inert'))
            report['unmeasured']=['scene save', 'production durable saves', 'paint/INP', 'network-delayed runs']
            page.locator('#chaptersNavMode').click()
            page.locator('#chapterTree').get_by_text('Synthetic chapter 02',exact=True).dblclick()
            page.wait_for_function('()=>state.viewMode==="chapter"&&state.chapter?.id==="chapter_2"&&!document.querySelector("#manuscriptPrimaryPane").inert')
            block=page.locator('#editor .manuscript-block').first
            block.fill(block.inner_text()+' Synthetic edit preparation.');saved()
            assert block.evaluate('(el)=>el.isContentEditable'), 'Chapter must be editable'
            block.evaluate('''el=>{const r=document.createRange();r.selectNodeContents(el);r.collapse(false);const s=getSelection();s.removeAllRanges();s.addRange(r);document.querySelector('#manuscriptPaper').focus()}''')
            for n in range(3):
                marker=f' Synthetic typing sample {n}.'
                measure('chapter typing visible',lambda m=marker:page.keyboard.type(m),lambda m=marker:expect(block).to_contain_text(m.strip()))
                measure('chapter save after typing',lambda:None,saved)
            report['input_event_to_two_animation_frames_ms']=page.evaluate('window.__inputPaint')
            before=block.inner_text()
            measure('reload saved chapter',lambda:page.reload(wait_until='domcontentloaded'),editable)
            assert 'Synthetic typing sample 2.' in before, 'Typing produced no text'
            assert before in page.locator('#editor').inner_text(),'Saved chapter text did not survive reload'
            measure('Tasks workspace',lambda:view('tasks'),lambda:expect(page.locator('#newTaskBtn')).to_be_visible())
            for n in range(3):
                title=f'Synthetic benchmark task {n}'
                page.locator('#newTaskBtn').click();page.locator('#taskName').fill(title)
                measure('create task and visible completion',lambda:page.locator('#saveTask').click(),lambda:expect(page.locator('#taskList')).to_contain_text(title))
                row=page.locator('#taskList .task-row').filter(has_text=title)
                row.locator('[data-task-more]').click()
                measure('delete disposable task',lambda:page.get_by_text('Delete',exact=True).click(),lambda:expect(page.locator('#taskList')).not_to_contain_text(title))
                measure('undo task deletion',lambda:page.locator('#undoBtn').click(),lambda:expect(page.locator('#taskList')).to_contain_text(title))
                measure('redo task deletion',lambda:page.locator('#redoBtn').click(),lambda:expect(page.locator('#taskList')).not_to_contain_text(title))
    output.write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    run(args.output)
