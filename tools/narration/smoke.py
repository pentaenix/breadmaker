"""Real, offline Kokoro smoke in an isolated project. Run with the Editor venv."""
import os
import hashlib
import io
import wave
from array import array
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]

with tempfile.TemporaryDirectory(prefix='breadmaker-narration-smoke-') as home:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','src.server:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'BREADMAKER_HOME':home},stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if requests.get(base+'/api/health',timeout=.2).ok:break
            except requests.RequestException:pass
            time.sleep(.1)
        project=requests.post(base+'/api/projects',json={'name':'Isolated narration smoke'}).json()['project']
        chapter=requests.get(base+'/api/bootstrap').json()['chapters'][0]
        block=requests.get(base+f"/api/chapters/{chapter['id']}").json()['blocks'][0]
        text='The bakery is open. I don’t know.'
        response=requests.put(base+f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{**block,'text_content':text,'content_html':text},{'id':None,'block_type':'paragraph','semantic_style':'body','content_html':'Come inside.','text_content':'Come inside.','image_path':'','scene_id':None}]});response.raise_for_status()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=['--autoplay-policy=no-user-gesture-required'])
            page=browser.new_page();errors=[];external=[];saves=[]
            page.on('pageerror',lambda err:errors.append(str(err)))
            page.on('console',lambda msg:print(msg.type,msg.text[:300],flush=True) if msg.type in ('error','warning') else None)
            page.on('request',lambda req:external.append(req.url) if not req.url.startswith((base,'blob:','data:')) else None)
            page.on('request',lambda req:saves.append(req.url) if req.method=='POST' and req.url==base+'/api/narrations' else None)
            page.goto(base+'/p/'+project['id']);page.locator('#app:not([inert])').wait_for(state='visible',timeout=30000)
            page.locator('#narrationBtn').click()
            assert page.evaluate('()=>crossOriginIsolated'),'Local threaded WASM requires isolation'
            page.evaluate("""()=>{const prose=document.querySelector('.manuscript-block'),node=prose.firstChild,range=document.createRange();range.setStart(node,4);range.setEnd(node,10);const selection=getSelection();selection.removeAllRanges();selection.addRange(range)}""")
            expect(page.locator('#narrationSelection')).to_be_enabled(timeout=5000)
            print('Generating selection with real Kokoro...',flush=True)
            page.locator('#narrationSelection').click()
            expect(page.locator('#narrationProgressBar')).to_be_visible()
            early=float(page.locator('#narrationProgressBar').get_attribute('value'))
            assert early>0
            page.wait_for_timeout(1000)
            assert float(page.locator('#narrationProgressBar').get_attribute('value'))>early,'Progress must move between model callbacks'
            assert 'Estimating time' not in page.locator('#narrationProgressEstimate').inner_text()
            page.locator('.narration-recording').wait_for(state='visible',timeout=110000)
            expect(page.locator('#narrationSelection')).to_be_enabled(timeout=10000)
            records=requests.get(base+'/api/narrations',headers={'X-Breadmaker-Project':project['id']}).json()['records']
            assert len(records)==1 and records[0]['text']=='bakery',records
            assert records[0]['duration']>0 and records[0]['timings'],records
            print('Saved real audio:',records[0]['duration'],'seconds;',records[0]['timings'],flush=True)
            calibration=page.evaluate('()=>({threads:narration.engine.timing.threads,loadMs:narration.engine.timing.data.loadMs,samples:narration.engine.timing.data.samples})')
            assert calibration['loadMs']>0 and calibration['samples'],calibration
            print('Measured startup/calibration:',calibration,flush=True)
            page.locator('#narrationPlay').click()
            page.locator('#narrationSelection').click()
            expect(page.locator('#narrationSelection')).to_be_enabled(timeout=10000)
            assert len(saves)==1,saves
            page.evaluate('(length)=>{const prose=document.querySelector(".manuscript-block"),range=document.createRange();range.setStart(prose.firstChild,0);range.setEnd(prose.firstChild,length);getSelection().removeAllRanges();getSelection().addRange(range)}',len(text))
            page.locator('#narrationSelection').click()
            expect(page.locator('#narrationPreviewExcerpt')).to_have_text(text,timeout=110000)
            expect(page.locator('#narrationPlay')).to_have_attribute('aria-label','Pause narration')
            assert len(saves)==1,'First chapter chunk must play before the full block is uploaded'
            assert page.locator('#narrationProgressBar').is_visible()
            page.locator('#narrationPlay').click()
            expect(page.locator('.narration-recording')).to_have_count(2,timeout=110000)
            expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            expect(page.locator('#narrationPlay')).to_have_attribute('aria-label','Play narration')
            page.locator('#narrationChapter').click();expect(page.locator('.narration-recording')).to_have_count(4,timeout=110000)
            expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            records=requests.get(base+'/api/narrations',headers={'X-Breadmaker-Project':project['id']}).json()['records']
            assert {record['text'] for record in records}=={'bakery',text,'Come inside.',chapter['title']},records
            title_record=next(record for record in records if record.get('target_type')=='chapter_title');assert title_record['timings'] and title_record['duration']>0
            assert len(saves)==4,saves
            assert page.evaluate("()=>narration.queue.map(record=>record.text)")==[chapter['title'],text,'Come inside.']
            page.locator('#narrationChapter').click()
            expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert len(saves)==4,saves
            page.reload();page.locator('#app:not([inert])').wait_for(state='visible');page.locator('#narrationBtn').click();expect(page.locator('.narration-recording')).to_have_count(4)
            page.locator('.narration-recording-main').first.click();expect(page.locator('#narrationPlay')).to_have_attribute('aria-label','Pause narration')
            page.locator('.narration-recording .bare-button').first.click()
            page.get_by_role('button',name='Delete recording',exact=True).click()
            expect(page.locator('.narration-recording')).to_have_count(3)
            expect(page.locator('#narrationPanel .narration-player')).to_be_hidden()
            page.locator('#toast').get_by_role('button',name='Undo',exact=True).click()
            expect(page.locator('.narration-recording')).to_have_count(4)
            old=page.evaluate('()=>narrationVisibleRecords()[0]');old_id=old['id']
            page.locator('#narrationVoice').select_option('bf_emma')
            page.locator('.narration-recording .bare-button').first.click()
            page.get_by_role('button',name='Recreate recording',exact=True).click()
            expect(page.locator('#narrationChapter')).to_be_enabled(timeout=110000)
            expect(page.locator('.narration-recording')).to_have_count(4)
            records=requests.get(base+'/api/narrations',headers={'X-Breadmaker-Project':project['id']}).json()['records']
            assert old_id not in [record['id'] for record in records]
            assert any(record['text']==old['text'] and record['voice']=='bf_emma' for record in records)
            full=next(record for record in records if record['text']==text)
            assert any(text[word['start']:word['end']]=='don’t' for word in full['timings']),full['timings']
            # Exercise all Spanish embeddings through the same real worker/model.
            spanish='¡Hola, niño! Mañana comeremos 12 panes. ¿Dónde está el pingüino?'
            blocks=requests.get(base+f"/api/chapters/{chapter['id']}").json()['blocks']
            blocks[0].update(text_content=spanish,content_html=spanish)
            lease_headers={'X-Breadmaker-Project':project['id'],'X-Breadmaker-Client':page.evaluate('()=>CLIENT_ID')}
            requests.put(base+f"/api/chapters/{chapter['id']}/blocks",json={'blocks':blocks},headers=lease_headers).raise_for_status()
            page.reload();page.locator('#app:not([inert])').wait_for(state='visible');page.locator('#narrationBtn').click()
            expect(page.locator('#narrationVoice')).to_be_enabled()
            # This is a cross-language voice matrix, not the interface-language
            # filter test. Opt in explicitly, just as a user must in settings.
            page.evaluate('()=>saveNarrationPreferences({showAllLanguages:true})')
            spanish_audio_hashes=set()
            for voice in ('ef_dora','em_alex','em_santa'):
                page.locator('#narrationVoice').select_option(voice)
                page.locator('.narration-recording').filter(has_text=text if voice=='ef_dora' else spanish).locator('.bare-button').click()
                page.get_by_role('button',name='Recreate recording',exact=True).click()
                expect(page.locator('#narrationChapter')).to_be_enabled(timeout=110000)
                expect(page.locator('.narration-recording')).to_have_count(4)
                records=requests.get(base+'/api/narrations',headers={'X-Breadmaker-Project':project['id']}).json()['records']
                recording=next(record for record in records if record['voice']==voice)
                assert recording['language']=='es' and recording['text']==spanish and recording['duration']>0,recording
                assert all(any(spanish[word['start']:word['end']]==expected for word in recording['timings']) for expected in ('niño','Mañana','12','pingüino')),recording['timings']
                audio=requests.get(base+f"/api/narrations/{recording['id']}/audio",headers=lease_headers);audio.raise_for_status()
                spanish_audio_hashes.add(hashlib.sha256(audio.content).hexdigest())
                with wave.open(io.BytesIO(audio.content),'rb') as wav:
                    assert max(abs(sample) for sample in array('h',wav.readframes(wav.getnframes())))>100
                print('PASS Spanish voice:',voice,'duration:',recording['duration'],flush=True)
            assert len(spanish_audio_hashes)==3,'Spanish voice waveforms must differ'
            english_audio_hashes=set()
            for voice in ('af_heart','am_michael','bf_emma','bm_george'):
                page.locator('#narrationVoice').select_option(voice)
                page.locator('.narration-recording .bare-button').last.click()
                page.get_by_role('button',name='Recreate recording',exact=True).click()
                expect(page.locator('#narrationChapter')).to_be_enabled(timeout=110000)
                expect(page.locator('.narration-recording')).to_have_count(4)
                records=requests.get(base+'/api/narrations',headers=lease_headers).json()['records']
                recording=next(record for record in records if record['voice']==voice and record['text']=='Come inside.')
                assert recording['language']=='en-us' and recording['timings'] and recording['duration']>0
                audio=requests.get(base+f"/api/narrations/{recording['id']}/audio",headers=lease_headers);audio.raise_for_status()
                english_audio_hashes.add(hashlib.sha256(audio.content).hexdigest())
                with wave.open(io.BytesIO(audio.content),'rb') as wav:assert max(abs(sample) for sample in array('h',wav.readframes(wav.getnframes())))>100
                print('PASS English voice:',voice,flush=True)
            assert len(english_audio_hashes)==4,'English voice waveforms must differ'
            assert not external,external
            assert not errors,errors
            print('PASS: four English + three Spanish voices, distinct audio, original timestamps, replay, Delete/Undo, replacement, zero external requests.',flush=True)
            browser.close()
    finally:
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
