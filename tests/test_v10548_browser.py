"""Hosted first-write and workspace smoke: actual in-memory hosted API, opaque IDs.

Only the initial workspace read simulates an account with no project. Creation,
saving, navigation, reloads and all subsequent API calls use the real hosted app.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

from playwright.sync_api import expect
import requests

from scripts.browser_runtime import start_browser, close_browser

ROOT=Path(__file__).resolve().parents[1]


@contextmanager
def hosted_browser(home):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    env=dict(os.environ,BREADMAKER_HOME=str(home),PYTHONPATH=str(ROOT/'src'),
             BREADMAKER_RUNTIME='hosted_dev',BREADMAKER_ENV='test',
             BREADMAKER_AUTH_MODE='developer',BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED='1')
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1',
                           '--port',str(port),'--log-level','warning'],cwd=ROOT,env=env,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    driver=browser=None
    try:
        for _ in range(100):
            assert proc.poll() is None,'Hosted test server exited'
            try:
                if requests.get(base+'/api/runtime',timeout=.25).ok:break
            except requests.RequestException:pass
            time.sleep(.05)
        else:raise AssertionError('Hosted test server did not start')
        driver,browser,_=start_browser()
        page=browser.new_page()
        yield base,page
    finally:
        if driver is not None:close_browser(driver,browser)
        proc.terminate()
        try:proc.wait(timeout=3)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)


def test_hosted_first_write_and_all_workspaces_persist_with_opaque_ids(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        # Collect caught boot/render failures as well as uncaught exceptions.
        page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' else None)
        first=[True]
        def empty_workspace(route):
            response=route.fetch();body=response.json()
            if first[0]:first[0]=False;body.update(projects=[],active_project_id='')
            route.fulfill(response=response,json=body)
        page.route('**/api/workspace',empty_workspace)
        page.goto(base,wait_until='domcontentloaded',timeout=8000)
        expect(page.locator('body')).to_have_class(re.compile('hosted-zero-project'))
        marker='SYNTHETIC hosted first-write regression'
        page.locator('#editor').fill(marker)
        page.wait_for_url(re.compile(r'/p/[0-9a-f]{32}$'),timeout=6000)
        expect(page.locator('.manuscript-block').first).to_have_text(marker)
        page.wait_for_function('()=>!state.dirty && !state.saveInFlight',timeout=5000)
        assert page.evaluate('()=>sessionStorage.getItem("breadmaker-hosted-first-draft")') is None
        pid=page.url.rsplit('/',1)[-1]
        page.reload(wait_until='domcontentloaded',timeout=8000)
        expect(page.locator('.manuscript-block').first).to_have_text(marker)
        for view in ('canvas','maps','tasks','reviews'):
            button=page.locator(f'.main-tab[data-view="{view}"]')
            assert button.evaluate('(el)=>getComputedStyle(el).pointerEvents')!='none'
            button.click()
            expect(page.locator(f'#{view}View')).to_have_class(re.compile('active'))
            if view=='canvas':
                page.locator('#emptyCreateCanvas').click()
                page.locator('#newCanvasName').fill('Synthetic opaque canvas')
                page.locator('#createCanvasConfirm').click()
                page.wait_for_function('()=>state.currentCanvas?.name==="Synthetic opaque canvas"')
            elif view=='maps':
                page.evaluate('()=>{void newMapDialog()}')
                page.locator('#dialogText').fill('Synthetic opaque map')
                page.locator('#dialogConfirm').click()
                page.wait_for_function('()=>state.currentMap?.name==="Synthetic opaque map"')
            elif view=='tasks':
                page.locator('#newTaskBtn').click()
                page.locator('#taskName').fill('Synthetic opaque task')
                page.locator('#saveTask').click()
                expect(page.locator('#tasksView')).to_contain_text('Synthetic opaque task')
                page.get_by_text('Synthetic opaque task',exact=True).first.click()
                page.locator('#taskName').fill('Synthetic edited task')
                page.locator('#saveTask').click()
                expect(page.locator('#tasksView')).to_contain_text('Synthetic edited task')
            else:
                page.evaluate('()=>createReviewDialog()')
                page.locator('#reviewNameInput').fill('Synthetic opaque review')
                page.locator('#reviewModalConfirm').click()
                expect(page.locator('#reviewsView')).to_contain_text('Synthetic opaque review')
        page.locator('.main-tab[data-view="manuscript"]').click()
        expect(page.locator('#manuscriptView')).to_have_class(re.compile('active'))
        marker+=' after workspace edits'
        page.locator('.manuscript-block').first.fill(marker)
        try:page.wait_for_function('()=>!state.dirty && !state.saveInFlight',timeout=5000)
        except Exception as exc:
            diagnostic=page.evaluate('()=>({dirty:state.dirty,inFlight:!!state.saveInFlight,chapters:[...state.dirtyChapters],view:state.viewMode,chapter:state.chapter?.id,revision:[...state.chapterRevisions],generation:[...state.chapterDirtyGeneration],saved:[...state.chapterSavedGeneration],notice:document.querySelector("#saveState")?.textContent})')
            raise AssertionError((errors,diagnostic)) from exc
        page.reload(wait_until='domcontentloaded',timeout=8000)
        expect(page.locator('#manuscriptView')).to_have_class(re.compile('active'))
        headers={'X-Breadmaker-Project':pid}
        snapshot=requests.get(base+'/api/bootstrap',headers=headers,timeout=2).json()
        assert any(b.get('text_content')==marker for b in snapshot['blocks'])
        for key,name in [('canvases','canvas'),('maps','map'),('reviews','review')]:
            assert any(row.get('name')==f'Synthetic opaque {name}' for row in snapshot[key])
            assert all(isinstance(row['id'],str) for row in snapshot[key])
        tasks=requests.get(base+'/api/tasks',headers=headers,timeout=2).json()['tasks']
        assert any(row['title']=='Synthetic edited task' for row in tasks)
        assert not errors,errors
