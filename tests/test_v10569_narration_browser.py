"""Actual hosted UI/store/HTTP/encrypted memory providers; inference alone stubbed."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from playwright.sync_api import expect
import requests

from scripts.browser_runtime import start_browser, close_browser
from hosted.narration.models import encode_manifest
from tests.narration_hosted_fixture import public_manifest

ROOT=Path(__file__).resolve().parents[1]
STUB="""window.BreadmakerKokoroEngine=class {
 constructor(){this.timing=new window.BreadmakerNarrationTiming();this.ready=false;this.generated=false}
 async load(){this.ready=true;return this}
 dispose(){this.disposed=true;window.syntheticDisposed=(window.syntheticDisposed||0)+1}
 async generate(){window.syntheticGenerations=(window.syntheticGenerations||0)+1;
 const wav=new ArrayBuffer(524),v=new DataView(wav),s=(a,t)=>{for(let i=0;i<t.length;i++)v.setUint8(a+i,t.charCodeAt(i))};
 s(0,'RIFF');v.setUint32(4,516,true);s(8,'WAVE');s(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,24000,true);v.setUint32(28,48000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);s(36,'data');v.setUint32(40,480,true);
 return {blob:new Blob([wav],{type:'audio/wav'}),timings:[],metrics:{backend:'wasm'}}}
};"""


@contextmanager
def browser(home,*,local=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    env=dict(os.environ,BREADMAKER_HOME=str(home),PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT),
        BREADMAKER_RUNTIME='local' if local else 'hosted_dev',BREADMAKER_ENV='test',BREADMAKER_AUTH_MODE='none' if local else 'developer',
        BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED='1',BREADMAKER_NARRATION_ENABLED='false')
    application=['app:app'] if local else ['tests.narration_hosted_fixture:create_app','--factory']
    proc=subprocess.Popen([sys.executable,'-m','uvicorn',*application,
        '--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env=env,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    driver=instance=None
    try:
        for _ in range(80):
            assert proc.poll() is None,'Synthetic hosted narration server exited'
            try:
                if requests.get(base+'/api/runtime',timeout=.2).ok:break
            except requests.RequestException:pass
            time.sleep(.05)
        else:raise AssertionError('Synthetic server startup failed')
        driver,instance,_=start_browser();page=instance.new_page()
        def script(name):
            body=(ROOT/'tools/narration'/f'{name}.mjs').read_text()
            return lambda route:route.fulfill(status=200,content_type='text/javascript',body=body)
        for name in ('hosted-store','hosted-assets','hosted-device'):
            page.route('**/static/vendor/narration/'+name+'.mjs',script(name))
        digest=hashlib.sha256(encode_manifest(public_manifest())).hexdigest()
        page.route('**/static/vendor/narration/installation.json',lambda route:route.fulfill(json={'hosted':True,'manifest':digest}))
        page.route('**/narration-engine.js?*',lambda route:route.fulfill(status=200,content_type='text/javascript',body=STUB))
        if local:
            voices=json.loads((ROOT/'static/narration-voices.json').read_text())
            page.route('**/api/narration-voices',lambda route:route.fulfill(json={**voices,'voices':[{**voice,'installed':True} for voice in voices['voices']]}))
        made=page.request.post(base+'/api/projects',data={'name':'Disposable hosted narration'},headers={'Origin':base})
        assert made.ok;pid=made.json()['project']['id']
        page.goto(base+'/p/'+pid,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.locator('.manuscript-block').first.fill('Synthetic narration passage.')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        yield base,page,pid
    finally:
        if driver is not None:close_browser(driver,instance)
        proc.terminate()
        try:proc.wait(timeout=3)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)


def test_hosted_narration_publish_thin_reuse_delete_undo_without_manuscript_mutation(tmp_path):
    with browser(tmp_path) as (base,page,pid):
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        writes=[];page.on('request',lambda request:writes.append(request) if request.method=='POST' and '/api/narrations' in request.url else None)
        before=page.request.get(base+'/api/project/metadata',headers={'X-Breadmaker-Project':pid}).json()['generation']
        mismatch=page.request.put(base+'/api/ui-state/narration-preferences',data={'value':{'voice':'af_heart'}},
            headers={'Origin':base,'X-Breadmaker-Project':pid,'X-Breadmaker-Narration-Account':'usr_'+'9'*32})
        assert mismatch.status==409 and mismatch.json()['detail']=='NARRATION_ACCOUNT_CONTEXT_CHANGED'
        page.locator('#narrationBtn').click()
        page.wait_for_function('()=>!narration.loading&&narration.project===currentProjectIdentity()')
        page.evaluate("async()=>{hostedStructuralActionToken='synthetic-action';await generateNarrations([narrationSource(document.querySelector('.manuscript-block'))],false,{generateOnly:true})}")
        expect(page.locator('.narration-recording')).to_have_count(1)
        assert page.evaluate('()=>narration.records[0].thin===true&&!narration.records[0].timings&&!narration.records[0].source_text')
        assert len(writes)==2
        assert all('x-breadmaker-action' not in request.headers for request in writes)
        page.evaluate('async()=>await generateNarrations([narrationSource(document.querySelector(".manuscript-block"))],false,{generateOnly:true})')
        assert page.evaluate('()=>window.syntheticGenerations')==1
        assert len(writes)==2,'verified thin source reuse must not publish again'
        page.evaluate('async()=>await deleteNarration(narration.records[0])')
        expect(page.locator('.narration-recording')).to_have_count(0)
        page.get_by_role('button',name='Undo',exact=True).last.click()
        expect(page.locator('.narration-recording')).to_have_count(1)
        page.locator('.narration-recording-main').click()
        page.wait_for_function('()=>narration.selected?.timings&&narration.url')
        assert page.evaluate('()=>narration.selected.source_text')=='Synthetic narration passage.'
        assert page.request.get(base+'/api/project/metadata',headers={'X-Breadmaker-Project':pid}).json()['generation']==before
        expect(page.locator('.manuscript-block').first).to_have_text('Synthetic narration passage.')
        assert not errors,errors


def test_hosted_new_paragraph_narration_uses_normal_editor_save(tmp_path):
    with browser(tmp_path) as (base,page,pid):
        page.evaluate('()=>{const prose=document.querySelector(".manuscript-block");placeCaret(prose,prose.innerText.length)}')
        page.keyboard.press('Enter')
        expect(page.locator('.manuscript-block')).to_have_count(2)
        page.keyboard.insert_text('A new disposable paragraph.')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        expect(page.locator('.manuscript-block').last).to_have_text('A new disposable paragraph.')
        before=page.request.get(base+'/api/project/metadata',headers={'X-Breadmaker-Project':pid}).json()['generation']
        page.locator('#narrationBtn').click()
        page.wait_for_function('()=>!narration.loading&&narration.project===currentProjectIdentity()')
        page.evaluate('async()=>await generateNarrations([narrationSource([...document.querySelectorAll(".manuscript-block")].at(-1))],false,{generateOnly:true})')
        expect(page.locator('.narration-recording')).to_have_count(1)
        page.locator('.narration-recording-main').click()
        page.wait_for_function('()=>narration.selected?.source_text&&narration.url')
        assert page.evaluate('()=>narration.selected.source_text')=='A new disposable paragraph.'
        assert page.request.get(base+'/api/project/metadata',headers={'X-Breadmaker-Project':pid}).json()['generation']==before
        expect(page.locator('.manuscript-block').last).to_have_text('A new disposable paragraph.')


def test_hosted_narration_denial_clears_private_audio_worker_and_late_resume(tmp_path):
    with browser(tmp_path) as (base,page,pid):
        page.locator('#narrationBtn').click()
        page.wait_for_function('()=>!narration.loading&&narration.project===currentProjectIdentity()')
        page.evaluate('async()=>await generateNarrations([narrationSource(document.querySelector(".manuscript-block"))],false,{generateOnly:true})')
        page.locator('.narration-recording-main').click()
        page.wait_for_function('()=>narration.selected?.timings&&narration.url')
        page.route('**/api/narrations/*/manifest',lambda route:route.fulfill(status=403,json={'detail':'NARRATION_ACCESS_DENIED'}))
        page.evaluate('async()=>{try{await playNarration(narration.records[0])}catch(error){window.syntheticDenied=error.status===403}}')
        assert page.evaluate('()=>window.syntheticDenied&&narration.records.length===0&&!narration.selected&&!narration.url&&narration.audio.paused&&!narration.engine')
        assert page.evaluate('()=>narrationStore.memory().audioBytes===0&&narrationStore.memory().detailsBytes===0')
        assert page.evaluate('()=>window.syntheticDisposed')>=1
        expect(page.locator('#narrationPanel')).to_have_class(__import__('re').compile('hidden'))
        expect(page.locator('.manuscript-block').first).to_have_text('Synthetic narration passage.')


def test_native_narration_stays_accountless_and_preserves_full_metadata_and_reuse(tmp_path):
    with browser(tmp_path,local=True) as (base,page,pid):
        external=[];page.on('request',lambda request:external.append(request.url) if not request.url.startswith(base) else None)
        page.locator('#narrationBtn').click()
        page.wait_for_function('()=>!narration.loading&&narration.project===currentProjectIdentity()')
        page.evaluate('async()=>await generateNarrations([narrationSource(document.querySelector(".manuscript-block"))],false,{generateOnly:true})')
        expect(page.locator('.narration-recording')).to_have_count(1)
        assert page.evaluate('()=>!narration.records[0].thin&&narration.records[0].source_text==="Synthetic narration passage."&&Array.isArray(narration.records[0].timings)')
        page.evaluate('async()=>await generateNarrations([narrationSource(document.querySelector(".manuscript-block"))],false,{generateOnly:true})')
        assert page.evaluate('()=>window.syntheticGenerations')==1
        page.evaluate('async()=>await deleteNarration(narration.records[0])')
        expect(page.locator('.narration-recording')).to_have_count(0)
        page.get_by_role('button',name='Undo',exact=True).last.click()
        expect(page.locator('.narration-recording')).to_have_count(1)
        expect(page.locator('.manuscript-block').first).to_have_text('Synthetic narration passage.')
        assert not external,external
