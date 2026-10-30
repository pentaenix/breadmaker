"""Isolated local narration scope/settings regression; deterministic inference fixture."""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]
STUB="""window.BreadmakerKokoroEngine=class {
 constructor(options={}){this.preferredBackend=options.backend;this.backend=options.backend==='wasm'?'wasm':'webgpu';this.timing=new window.BreadmakerNarrationTiming(this.backend);this.generated=false;this.ready=false}
 async load(){this.ready=true;return this}
 dispose(){this.disposed=true;this.context?.close();window.testDisposed=(window.testDisposed||0)+1}
 async generate(text,voice,onprogress,onchunk,pace=1,punctuationPauses=false){
  window.testPunctuation=punctuationPauses;
  window.testPace=pace;
  window.testGenerations=(window.testGenerations||0)+1;
  (window.testInputs??=[]).push(text);
  const context=new AudioContext({sampleRate:24000}),buffer=context.createBuffer(1,24000,24000);
  onprogress({phase:'synthesis',completed:0,total:1});await new Promise(resolve=>setTimeout(resolve,window.testDelay||200));
  onchunk({buffer,context,timings:[],duration:1});await new Promise(resolve=>setTimeout(resolve,window.testDelay||200));
  const bytes=new Uint8Array(48044),view=new DataView(bytes.buffer),write=(at,s)=>{for(let i=0;i<s.length;i++)view.setUint8(at+i,s.charCodeAt(i))};
  write(0,'RIFF');view.setUint32(4,48036,true);write(8,'WAVE');write(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,24000,true);view.setUint32(28,48000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);write(36,'data');view.setUint32(40,48000,true);
  return {blob:new Blob([bytes],{type:'audio/wav'}),timings:[],metrics:{backend:this.backend}};
 }
};"""

with tempfile.TemporaryDirectory(prefix='breadmaker-narration-ui-') as home:
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
        project=requests.post(base+'/api/projects',json={'name':'Narration UI fixture'}).json()['project']
        boot=requests.get(base+'/api/bootstrap').json();first=boot['chapters'][0]
        second=requests.post(base+'/api/chapters',json={'title':'Following chapter','tab_id':first['tab_id']}).json()
        def blocks(chapter,texts,headers=None):
            rows=[{'id':None,'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None} for text in texts]
            requests.put(base+f"/api/chapters/{chapter['id']}/blocks",json={'blocks':rows},headers=headers).raise_for_status()
            return requests.get(base+f"/api/chapters/{chapter['id']}").json()['blocks']
        first_blocks=blocks(first,['First one.','Second one.','Missing audio.']);night=blocks(second,['Last one.','Night two.','Night three.']);last=night[0]
        audio=io.BytesIO()
        with wave.open(audio,'wb') as out:out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(b'\0\0'*24000*3)
        for block in [*first_blocks[:2],last]:
            text=block['text_content'];metadata={'block_id':block['id'],'voice':'af_heart','source_text':text,'text':text,'start':0,'end':len(text),'timings':[]}
            requests.post(base+'/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('test.wav',audio.getvalue(),'audio/wav')}).raise_for_status()
        partial=first_blocks[2];metadata={'block_id':partial['id'],'voice':'af_heart','source_text':partial['text_content'],'text':'Missing','start':0,'end':7,'timings':[]}
        requests.post(base+'/api/narrations',data={'metadata':json.dumps(metadata)},files={'file':('test.wav',audio.getvalue(),'audio/wav')}).raise_for_status()
        # Chapter order must follow positions, not numeric creation IDs.
        requests.post(base+'/api/chapters/reorder',json={'ids':[second['id'],first['id']]}).raise_for_status()
        with sync_playwright() as pw:
            browser_name=os.environ.get('BREADMAKER_NARRATION_BROWSER','chromium')
            assert browser_name in ('chromium','firefox','webkit')
            browser=getattr(pw,browser_name).launch(headless=True,**({'args':['--autoplay-policy=no-user-gesture-required']} if browser_name=='chromium' else {}))
            page=browser.new_page();errors=[];saves=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:saves.append(request.url) if request.method=='POST' and request.url==base+'/api/narrations' else None)
            page.route('**/narration-engine.js?*',lambda route:route.fulfill(status=200,content_type='text/javascript',body=STUB))
            page.goto(base+'/p/'+project['id']);page.locator('#app:not([inert])').wait_for(state='visible')
            held_catalog=[]
            page.route('**/api/narration-voices',lambda route:held_catalog.append(route))
            with page.expect_request(base+'/api/narration-voices'):
                page.locator('#narrationBtn').click()
            expect(page.locator('#narrationVoice')).to_be_disabled()
            expect(page.locator('#narrationChapter')).to_be_disabled()
            held_catalog[0].continue_();page.unroute('**/api/narration-voices')
            expect(page.locator('#narrationChapter')).to_have_text('Narrate book')
            expect(page.locator('#narrationVoice')).to_be_enabled()
            expect(page.locator('#narrationVoice option')).to_have_count(4)
            assert page.locator('#narrationVoice').evaluate('(select)=>[...select.options].every(option=>/^[ab]/.test(option.value))')
            assert page.locator('#narrationBackend').count()==0
            assert page.get_by_text('Uses saved audio and generates missing passages.',exact=True).count()==0
            gutter=page.locator('.narration-preview-gutter').first
            expect(gutter).to_have_attribute('aria-label','Play narration')
            assert gutter.locator('svg').evaluate('(icon)=>getComputedStyle(icon).width')=='11px'
            assert gutter.locator('path').get_attribute('d')=='m8 4 12 8-12 8Z'
            assert page.locator('#narrationBtn').inner_html()!=gutter.inner_html(),'Keep headphones in toolbar, not in manuscript gutters'
            assert page.locator('#narrationSpeed option').last.get_attribute('value')=='2'
            for options in ['narrationSelectionOptions','narrationScopeOptions']:
                expect(page.locator('#'+options)).to_have_text('⋮')
                assert page.locator('#'+options).evaluate('(button)=>getComputedStyle(button).borderInlineStartWidth')=='0px'
            assert page.locator('#narrationPlayBook,#narrationPlayChapter').count()==0
            assert page.locator('#narrationContinue').count()==0
            # Hidden/collapsed prose still belongs to the complete-book job.
            page.evaluate('(id)=>document.querySelector(`[data-block-id="${id}"]`).style.display="none"',night[1]['id'])
            page.locator('#narrationVoice').select_option('bm_george')
            page.locator('#narrationScopeOptions').click()
            expect(page.get_by_test_id('narration-generate-only')).to_have_text('Generate book')
            expect(page.get_by_test_id('narration-continue-chapters')).to_be_disabled()
            expect(page.get_by_test_id('narration-continue-chapters')).to_have_attribute('aria-checked','true')
            page.get_by_test_id('narration-generate-only').click();expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert page.evaluate('()=>narration.audio.paused&&narration.audio.stream===null&&narration.selected===null')
            expected=[second['title'],'Last one.','Night two.','Night three.',first['title'],'First one.','Second one.','Missing audio.']
            assert page.evaluate('()=>window.testInputs')==[second['title'],'Night two.','Night three.',first['title'],'Missing audio.']
            assert len(saves)==5,'Titles are separate recordings; existing full paragraphs must be reused across voices'
            assert 'computer awake' in page.locator('#toast').inner_text()
            page.locator('#narrationChapter').click();expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert page.evaluate('()=>narration.queue.map(record=>record.text)')==expected
            assert len(saves)==5 and page.evaluate('()=>window.testGenerations')==5
            page.locator('#narrationPlay').click()
            # A chapter-only queue must not include the next chapter by default.
            page.evaluate('(id)=>selectChapter(id)',second['id']);expect(page.locator('#narrationChapter')).to_have_text('Narrate chapter')
            page.locator('#narrationChapter').click();expect(page.locator('#narrationPlay')).to_have_attribute('aria-label','Pause narration')
            assert page.evaluate('()=>narration.queue.map(record=>record.text)')==expected[:4]
            page.locator('#narrationPlay').click()
            page.locator('#narrationChapter').click(button='right')
            expect(page.get_by_test_id('narration-generate-only')).to_have_text('Generate chapter')
            expect(page.get_by_test_id('narration-continue-chapters')).to_have_attribute('aria-checked','false')
            page.get_by_test_id('narration-continue-chapters').click();page.evaluate('()=>narrationPreferenceWrite')
            page.locator('#narrationScopeOptions').click();expect(page.get_by_test_id('narration-continue-chapters')).to_have_attribute('aria-checked','true')
            page.locator('#narrationChapter').click(button='right');page.get_by_test_id('narration-generate-only').click()
            expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert page.evaluate('()=>narration.audio.paused'),'Cached Generate only must never start playback'
            page.locator('#narrationChapter').click();expect(page.locator('#narrationPlay')).to_have_attribute('aria-label','Pause narration')
            assert page.evaluate('()=>narration.queue.map(record=>record.text)')==expected;assert len(saves)==5
            page.locator('#narrationNext').click();expect(page.locator('#narrationPreviewExcerpt')).to_have_text('Last one.')
            page.locator('#narrationPlay').click()
            # A completed early stream still shares the inference AudioContext.
            # GPU changes must detach it before that context is retired.
            page.evaluate('()=>{const context=new AudioContext({sampleRate:24000});narration.engine.context=context;narration.audio.startStream(context);narration.audio.appendStream(context.createBuffer(1,24000,24000));narration.audio.finishStream()}')
            def settings():
                page.locator('#brandButton').click();page.locator('#projectMenuSettings').click();page.locator('#settingsNarration').click()
            settings();expect(page.locator('[data-narration-setting="gpu"]')).to_have_attribute('aria-checked','true')
            expect(page.locator('#narrationSettingsSave')).to_be_in_viewport()
            expect(page.locator('#narrationSettingsCancel')).to_be_visible()
            def check_selector_insets(selector):
                geometry=page.locator(selector).evaluate('''select=>{
                    const arrow=select.parentElement.querySelector('.view-menu-chevron'),box=select.getBoundingClientRect(),icon=arrow.getBoundingClientRect(),style=getComputedStyle(select);
                    return {inset:box.right-icon.right,padding:parseFloat(style.paddingInlineEnd),appearance:style.appearance,path:arrow.querySelector('path').getAttribute('d')};
                }''')
                assert 11.5<=geometry['inset']<=12.5,geometry
                assert geometry['padding']>=36 and geometry['appearance']=='none',geometry
                assert geometry['path']==page.locator('#viewBtn .view-menu-chevron path').get_attribute('d')
            for identifier in ['narrationSettingsVoice','narrationSettingsSpeed','narrationSettingsPitch','narrationSettingsPace']:check_selector_insets('#'+identifier)
            export_spacing=page.locator('#narrationSettingsExport').evaluate('''button=>{const box=button.getBoundingClientRect();return {above:box.top-button.previousElementSibling.getBoundingClientRect().bottom,below:button.closest('section').getBoundingClientRect().bottom-box.bottom}}''')
            # Engines represent fractional CSS pixels differently; tolerate <.05px.
            assert export_spacing['above']>=11.95 and export_spacing['below']>=17.95,export_spacing
            expect(page.locator('[data-narration-setting="punctuationPauses"]')).to_have_attribute('aria-checked','true')
            for key in ['gpu','earlyPlayback','autoplay','word','punctuationPauses']:
                page.locator(f'[data-narration-setting="{key}"]').click()
            page.locator('#narrationSettingsPace').select_option('0.95');page.locator('#narrationSettingsPitch').select_option('2')
            page.locator('#narrationSettingsSpeed').select_option('1.05');page.locator('#narrationSettingsVoice').select_option('bm_george');page.locator('#narrationSettingsSave').click()
            expect(page.locator('#settingsNarration')).to_be_visible();page.locator('#modalClose').click()
            assert page.evaluate('()=>window.testDisposed')==1
            assert page.evaluate('()=>narration.audio.stream===null')
            page.reload();page.locator('#app:not([inert])').wait_for(state='visible');page.locator('#narrationBtn').click()
            expect(page.locator('#narrationVoice')).to_have_value('bm_george');expect(page.locator('#narrationSpeed')).to_have_value('1.05')
            page.wait_for_function('()=>narration.audio.pitch===2&&!!narration.audio.nativePitch')
            assert page.evaluate('()=>narrationPreferences.value.generationPace')==.95
            assert page.evaluate('()=>narrationPreferences.value.punctuationPauses') is False
            expect(page.locator('#narrationProgressBar')).to_have_attribute('data-cpu-threads','4')
            assert page.evaluate('()=>narration.engine.preferredBackend')=='wasm'
            page.locator('#viewBtn').click();expect(page.locator('#viewNarrationWord')).to_be_hidden()
            page.locator('#viewNarrationBtn').click();expect(page.locator('#viewNarrationWord')).to_have_attribute('aria-checked','false')
            expect(page.locator('#viewNarrationWord')).to_be_visible();page.locator('#viewBtn').click()
            settings();expect(page.locator('[data-narration-setting="gpu"]')).to_have_attribute('aria-checked','false')
            assert 'Nothing is generated' not in page.locator('#modalBody').inner_text()
            page.locator('#modalClose').click();expect(page.locator('#settingsNarration')).to_be_visible();page.locator('#modalClose').click()
            # Generation with both early preview and autoplay disabled stays paused.
            page.evaluate('(id)=>selectChapter(id)',first['id']);page.locator('.narration-recording .bare-button').first.click()
            page.get_by_role('button',name='Recreate recording',exact=True).click()
            expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert page.evaluate('()=>narration.audio.paused')
            assert page.evaluate('()=>narration.audio.stream===null')
            assert page.evaluate('()=>narration.selected.voice')=='bm_george'
            assert len(saves)==6
            assert page.evaluate('()=>window.testPace')==.95
            assert page.evaluate('()=>window.testPunctuation') is False
            assert any(record.get('generation_pace')==.95 for record in requests.get(base+'/api/narrations').json()['records'])
            # Resume a longer unattended job outside the displayed chapter.
            # Audio stays paused: generation must not depend on audio ending.
            headers={'X-Breadmaker-Project':project['id'],'X-Breadmaker-Client':page.evaluate('()=>CLIENT_ID')}
            response=requests.post(base+'/api/chapters',json={'title':'Overnight','tab_id':first['tab_id']},headers=headers);response.raise_for_status();overnight=response.json()
            blocks(overnight,[f'Night passage {i}.' for i in range(1,9)],headers)
            # A persisted Continue playback setting must not generate later
            # chapters from either the main chapter button or its menu.
            page.locator('#narrationChapter').click();expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert len(saves)==6 and page.evaluate('()=>window.testGenerations')==1
            assert page.evaluate('()=>narration.queue.map(record=>record.text)')==[first['title'],'First one.','Second one.','Missing audio.']
            page.evaluate('()=>openWholeBook(false,true,{force:true})');expect(page.locator('#narrationChapter')).to_have_text('Narrate book')
            page.evaluate('()=>window.testDelay=350');page.locator('#narrationChapter').click()
            expect(page.locator('#narrationProgressLabel')).to_have_text('Generating 9 of 17…',timeout=10000)
            page.locator('#narrationStop').click();expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert len(saves)==7,'Stop must finish/save the current title and leave later passages untouched'
            assert page.evaluate('()=>narration.audio.paused')
            page.evaluate('()=>window.testDelay=50');page.locator('#narrationChapter').click();expect(page.locator('#narrationChapter')).to_be_enabled(timeout=20000)
            assert len(saves)==15,'Resume must generate only the eight remaining paragraphs'
            assert page.evaluate('()=>narration.queue.map(record=>record.text)')==expected+['Overnight']+[f'Night passage {i}.' for i in range(1,9)]
            assert page.evaluate('()=>narration.audio.paused')
            page.evaluate('(id)=>selectChapter(id)',first['id']);expect(page.locator('#narrationChapter')).to_have_text('Narrate chapter')
            # Both entry points preserve an exact text selection; Generate only
            # must override enabled autoplay/early-preview without changing them.
            settings()
            for key in ['autoplay','earlyPlayback','punctuationPauses']:
                page.locator(f'[data-narration-setting="{key}"]').click()
            page.locator('#narrationSettingsSave').click();expect(page.locator('#settingsNarration')).to_be_visible();page.locator('#modalClose').click()
            page.evaluate('(id)=>{const prose=document.querySelector(`[data-block-id="${id}"] .manuscript-block`),range=document.createRange();range.setStart(prose.firstChild,0);range.setEnd(prose.firstChild,5);const selection=getSelection();selection.removeAllRanges();selection.addRange(range)}',first_blocks[0]['id'])
            expect(page.locator('#narrationSelectionOptions')).to_be_enabled()
            page.locator('#narrationSelectionOptions').click();expect(page.get_by_test_id('narration-generate-only')).to_be_visible()
            assert page.get_by_test_id('narration-continue-chapters').count()==0
            page.locator('#narrationSelection').click(button='right');page.get_by_test_id('narration-generate-only').click()
            expect(page.locator('#narrationSelection')).to_be_enabled(timeout=10000)
            assert len(saves)==16 and page.evaluate('()=>window.testInputs.at(-1)')=='First'
            assert page.evaluate('()=>window.testPunctuation') is True
            assert page.evaluate('()=>narration.audio.paused&&narration.audio.stream===null')
            assert page.evaluate('()=>narrationPreferences.value.autoplay&&narrationPreferences.value.earlyPlayback')
            # Regression: Generate chapter must ignore persisted continuation,
            # even with a wholly unrecorded chapter after the focused chapter.
            response=requests.post(base+'/api/chapters',json={'title':'Do not generate','tab_id':first['tab_id']},headers=headers);response.raise_for_status();excluded=response.json()
            blocks(excluded,['Outside requested chapter.'],headers)
            page.locator('#narrationChapter').click();expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert len(saves)==16 and page.evaluate('()=>!window.testInputs.includes("Do not generate")&&!window.testInputs.includes("Outside requested chapter.")')
            page.locator('#narrationPlay').click()
            page.locator('#narrationChapter').click(button='right')
            expect(page.get_by_test_id('narration-continue-chapters')).to_have_attribute('aria-checked','true')
            expect(page.get_by_test_id('narration-generate-only')).to_have_text('Generate chapter')
            page.get_by_test_id('narration-generate-only').click();expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert len(saves)==16 and page.evaluate('()=>!window.testInputs.includes("Do not generate")&&!window.testInputs.includes("Outside requested chapter.")')
            # Regenerate chapter replaces complete recordings in this chapter,
            # including its title, without duplicating audio or continuing onward.
            before=requests.get(base+'/api/narrations',headers=headers).json()['records']
            page.locator('#narrationScopeOptions').click()
            expect(page.get_by_test_id('narration-regenerate')).to_have_text('Regenerate chapter')
            expect(page.get_by_test_id('narration-delete-all')).to_have_class('danger')
            page.get_by_test_id('narration-regenerate').click();expect(page.locator('#narrationChapter')).to_be_enabled(timeout=15000)
            assert len(saves)==20 and page.evaluate('()=>window.testInputs.slice(-4)')==[first['title'],'First one.','Second one.','Missing audio.']
            assert len(requests.get(base+'/api/narrations',headers=headers).json()['records'])==len(before)
            page.evaluate('(id)=>{const prose=document.querySelector(`[data-block-id="${id}"] .manuscript-block`),range=document.createRange();range.setStart(prose.firstChild,0);range.setEnd(prose.firstChild,5);getSelection().removeAllRanges();getSelection().addRange(range)}',first_blocks[0]['id'])
            expect(page.locator('#narrationSelectionOptions')).to_be_enabled();page.locator('#narrationSelection').click(button='right')
            expect(page.get_by_test_id('narration-regenerate')).to_have_text('Regenerate section')
            page.get_by_test_id('narration-regenerate').click();expect(page.locator('#narrationSelection')).to_be_enabled(timeout=10000)
            assert len(saves)==21 and page.evaluate('()=>window.testInputs.at(-1)')=='First'
            after=requests.get(base+'/api/narrations',headers=headers).json()['records'];assert len(after)==len(before)
            # Show only the focused chapter's six recordings, but every active
            # document recording in Full Text. Menus must track the same context.
            expect(page.locator('.narration-recording')).to_have_count(6)
            page.evaluate('()=>openWholeBook(false,true,{force:true})');expect(page.locator('.narration-recording')).to_have_count(len(after))
            page.locator('#narrationScopeOptions').click();expect(page.get_by_test_id('narration-regenerate')).to_have_text('Regenerate book')
            page.keyboard.press('Escape');expect(page.locator('#contextMenu')).to_be_hidden()
            page.evaluate('(id)=>selectChapter(id)',first['id']);expect(page.locator('.narration-recording')).to_have_count(6)
            page.locator('.narration-recording .bare-button').first.click()
            expect(page.get_by_role('button',name='Delete recording',exact=True)).to_have_class('danger')
            page.locator('#narrationScopeOptions').click();page.get_by_test_id('narration-delete-all').click()
            expect(page.locator('.narration-recording')).to_have_count(0,timeout=10000)
            remaining=requests.get(base+'/api/narrations',headers=headers).json()['records']
            assert len(remaining)==len(after)-6
            assert any(record.get('chapter_id')==str(second['id']) for record in remaining),'Other chapter title must survive Delete all'
            page.locator('#toast').get_by_role('button',name='Undo',exact=True).click()
            expect(page.locator('.narration-recording')).to_have_count(6,timeout=10000)
            assert len(requests.get(base+'/api/narrations',headers=headers).json()['records'])==len(after)
            # Settings-only export: retain the exact selection, export full chapter
            # losslessly including title, and reject incomplete book silently (no inference).
            # Establish a current selection rather than relying on browser-specific
            # retention of a range from earlier chapter switches and Delete/Undo.
            page.evaluate('''()=>{const prose=document.querySelector('.manuscript-block'),range=document.createRange();range.selectNodeContents(prose);getSelection().removeAllRanges();getSelection().addRange(range);captureNarrationSelection()}''')
            assert page.evaluate('()=>narration.selection.length')==1
            settings();expect(page.locator('#narrationSettingsPitch')).to_have_value('2');expect(page.locator('#narrationSettingsPace')).to_have_value('0.95')
            page.locator('#narrationSettingsExport').click();expect(page.locator('#narrationExportScope')).to_have_value('chapter')
            check_selector_insets('#narrationExportScope')
            generated_before=page.evaluate('()=>window.testGenerations')
            with page.expect_download() as download_info:page.locator('#narrationExportDownload').click()
            download=download_info.value;path=Path(home)/'chapter.wav';download.save_as(path)
            with wave.open(str(path),'rb') as out:assert out.getnchannels()==1 and out.getframerate()==24000 and out.getnframes()==4*24000
            page.locator('#narrationExportScope').select_option('selection')
            with page.expect_download() as download_info:page.locator('#narrationExportDownload').click()
            download_info.value.save_as(Path(home)/'selection.wav')
            with wave.open(str(Path(home)/'selection.wav'),'rb') as out:assert out.getnframes()==24000
            page.locator('#narrationExportScope').select_option('book');page.locator('#narrationExportDownload').click()
            expect(page.locator('#narrationExportStatus')).to_contain_text('no current recording')
            assert page.evaluate('()=>window.testGenerations')==generated_before and len(saves)==21
            # Supply just the intentionally missing chapter's cached fixture audio;
            # the same export now downloads one full-book WAV, still without inference.
            excluded_blocks=requests.get(base+f"/api/chapters/{excluded['id']}",headers=headers).json()['blocks']
            fixture_sources=[{'block_id':f"chapter-title:{excluded['id']}",'chapter_id':str(excluded['id']),'target_type':'chapter_title','text':excluded['title']},*({'block_id':block['id'],'text':block['text_content']} for block in excluded_blocks)]
            for source in fixture_sources:
                text=source['text'];metadata={**source,'source_text':text,'start':0,'end':len(text),'voice':'bm_george','timings':[]}
                requests.post(base+'/api/narrations',headers=headers,data={'metadata':json.dumps(metadata)},files={'file':('test.wav',audio.getvalue(),'audio/wav')}).raise_for_status()
            with page.expect_download() as download_info:page.locator('#narrationExportDownload').click()
            download_info.value.save_as(Path(home)/'book.wav')
            with wave.open(str(Path(home)/'book.wav'),'rb') as out:assert out.getnframes()>4*24000
            assert page.evaluate('()=>window.testGenerations')==generated_before and len(saves)==21
            page.locator('#modalClose').click();expect(page.locator('#narrationSettingsPace')).to_have_value('0.95')
            page.locator('#modalClose').click();page.locator('#modalClose').click()
            # Main Narrate chapter must also stay scoped when the current
            # chapter needs real generation, not just when it is fully cached.
            response=requests.post(base+'/api/chapters',json={'title':'Never synthesize from chapter view','tab_id':first['tab_id']},headers=headers);response.raise_for_status();untouched=response.json()
            blocks(untouched,['Do not synthesize this later paragraph.'],headers)
            records=requests.get(base+'/api/narrations',headers=headers).json()['records']
            current=next(record for record in records if record['block_id']==first_blocks[1]['id'] and record['start']==0 and record['end']==len(record['source_text']))
            requests.delete(base+f"/api/narrations/{current['id']}",headers=headers).raise_for_status()
            count=page.evaluate('()=>window.testGenerations');page.locator('#narrationChapter').click();expect(page.locator('#narrationChapter')).to_be_enabled(timeout=10000)
            assert page.evaluate('()=>window.testGenerations')==count+1
            assert page.evaluate('()=>window.testInputs.at(-1)')=='Second one.'
            assert page.evaluate('()=>!window.testInputs.includes("Never synthesize from chapter view")&&!window.testInputs.includes("Do not synthesize this later paragraph.")')
            settings();page.locator('#narrationSettingsSpeed').select_option('2');page.locator('#narrationSettingsSave').click()
            expect(page.locator('#settingsNarration')).to_be_visible();page.locator('#modalClose').click()
            page.reload();page.locator('#app:not([inert])').wait_for(state='visible');page.locator('#narrationBtn').click()
            expect(page.locator('#narrationSpeed')).to_have_value('2');assert page.evaluate('()=>narration.audio.playbackRate')==2
            # The Settings library uses searchable rows, real preview controls,
            # individual downloads and language packs, not a long dropdown.
            voice_catalog=requests.get(base+'/api/narration-voices',headers=headers).json()
            voice_data={voice['id']:voice for voice in voice_catalog['voices']}
            for voice in voice_data.values():
                if voice['language']=='en-us':voice['installed']=voice['id'] not in ('af_nicole','af_sky','am_echo')
            page.route(base+'/api/narration-voices',lambda route:route.fulfill(json=voice_catalog))
            download_calls=[];held=[];hold_downloads=False;fail_echo=True
            def download_voice(route):
                identifier=route.request.url.split('/')[-2];download_calls.append(identifier)
                if hold_downloads:held.append(route);return
                if identifier=='am_echo' and fail_echo:route.fulfill(status=502,json={'detail':'NARRATION_VOICE_DOWNLOAD_FAILED'});return
                voice_data[identifier]['installed']=True;route.fulfill(json=voice_data[identifier])
            page.route(base+'/api/narration-voices/*/download',download_voice)
            settings();page.locator('#narrationSettingsSpeed').select_option('1.75')
            page.locator('#narrationSettingsMoreVoices').click()
            expect(page.locator('[data-voice-id]')).to_have_count(28)
            check_selector_insets('#narrationVoiceLanguage')
            page.set_viewport_size({'width':390,'height':700})
            expect(page.locator('#narrationVoiceDone')).to_be_in_viewport()
            check_selector_insets('#narrationVoiceLanguage')
            assert page.locator('.narration-voice-library').evaluate('(element)=>element.scrollWidth<=element.clientWidth')
            page.set_viewport_size({'width':1280,'height':720})
            expect(page.locator('#narrationVoiceLanguage')).to_be_disabled()
            row=lambda identifier:page.locator(f'[data-voice-id="{identifier}"]')
            page.locator('#narrationVoiceSearch').fill('not a voice');expect(page.locator('[data-voice-id]')).to_have_count(0)
            expect(page.locator('#narrationVoiceList')).to_contain_text('No matching voices.')
            page.locator('#narrationVoiceSearch').fill('Bella');expect(page.locator('[data-voice-id]')).to_have_count(2) # Bella and Isabella
            expect(row('af_bella').locator('input')).not_to_be_checked()
            row('af_bella').locator('input').check();page.evaluate('()=>narrationPreferenceWrite')
            expect(row('af_bella').locator('input')).to_be_checked()
            count=page.evaluate('()=>window.testGenerations||0');saved=len(saves)
            row('af_bella').locator('[data-voice-preview]').click();expect(row('af_bella').locator('[data-voice-preview]')).to_be_enabled(timeout=10000)
            assert page.evaluate('()=>window.testGenerations')==count+1
            assert page.evaluate('()=>window.testInputs.at(-1)')=='The bakery is open. Please come inside.'
            assert len(saves)==saved
            page.locator('#narrationVoiceStop').click();row('af_bella').locator('[data-voice-preview]').click()
            expect(row('af_bella').locator('[data-voice-preview]')).to_be_enabled()
            assert page.evaluate('()=>window.testGenerations')==count+1,'Repeat preview reuses the sample'
            page.locator('#narrationVoiceStop').click()
            page.locator('#narrationVoiceSearch').fill('Nicole')
            expect(row('af_nicole').locator('[data-voice-preview]')).to_be_disabled()
            row('af_nicole').locator('[data-voice-download]').click()
            expect(row('af_nicole').locator('[data-voice-use]')).to_be_enabled()
            expect(row('af_nicole').locator('input')).to_be_checked()
            # A pack only downloads missing files. Failure preserves completed
            # downloads; retry does not download them again or switch narrator.
            page.locator('#narrationVoiceSearch').fill('')
            page.locator('#narrationVoicePack').click()
            expect(page.get_by_role('button',name='Default voices (4) · 0 MB',exact=True)).to_be_disabled()
            page.get_by_role('button',name='All voices (28) · 1 MB',exact=True).click()
            expect(page.locator('#narrationVoiceStatus')).to_contain_text('Could not download')
            assert voice_data['af_sky']['installed'] and not voice_data['am_echo']['installed']
            fail_echo=False
            page.locator('#narrationVoicePack').click();page.get_by_role('button',name='All voices (28) · 0.5 MB',exact=True).click()
            expect(page.locator('#narrationVoiceStatus')).to_have_text('Download complete.')
            assert download_calls==['af_nicole','af_sky','am_echo','am_echo']
            expect(row('af_sky').locator('input')).not_to_be_checked()
            # Stop finishes the current file, then leaves the rest untouched.
            page.locator('#narrationVoiceDone').click();expect(page.locator('#narrationSettingsMoreVoices')).to_be_visible()
            voice_data['af_river']['installed']=False;voice_data['am_puck']['installed']=False;hold_downloads=True
            page.locator('#narrationSettingsMoreVoices').click();page.locator('#narrationVoicePack').click()
            page.get_by_role('button',name='All voices (28) · 1 MB',exact=True).click()
            expect(page.locator('#narrationVoicePackProgress')).to_be_visible();assert len(held)==1
            page.locator('#narrationVoiceStop').click()
            held_voice=held[0].request.url.split('/')[-2];voice_data[held_voice]['installed']=True;held[0].fulfill(json=voice_data[held_voice])
            expect(page.locator('#narrationVoiceStatus')).to_have_text('Downloads stopped.')
            assert len(held)==1 and not voice_data['am_puck']['installed'];hold_downloads=False
            # Use selects in the parent draft; Save applies it, preserving other edits.
            page.locator('#narrationVoiceSearch').fill('Bella');row('af_bella').locator('[data-voice-use]').click()
            expect(page.locator('#narrationSettingsVoice')).to_have_value('af_bella')
            expect(page.locator('#narrationSettingsSpeed')).to_have_value('1.75')
            expect(page.locator('#narrationVoice')).to_have_value('bm_george')
            page.locator('#narrationSettingsSave').click();expect(page.locator('#settingsNarration')).to_be_visible();page.locator('#modalClose').click()
            expect(page.locator('#narrationVoice')).to_have_value('af_bella')
            assert len(saves)==saved,'Settings and previews never publish recordings'
            # Live locale filtering applies to both selectors and the voice browser.
            for locale,count,prefix in [('es',3,'e'),('fr',1,'f'),('it',2,'i'),('ja',3,'j'),('pt-BR',3,'p'),('zh-CN',4,'z')]:
                page.evaluate('(locale)=>switchLocale(locale)',locale)
                expect(page.locator('#narrationVoice option')).to_have_count(count)
                assert all(value.startswith(prefix) for value in page.locator('#narrationVoice').evaluate('(select)=>[...select.options].map(option=>option.value)'))
                if locale=='it':
                    page.locator('#narrationVoice').select_option('im_nicola');page.evaluate('()=>narrationPreferenceWrite')
            page.evaluate('()=>switchLocale("it")');expect(page.locator('#narrationVoice')).to_have_value('im_nicola')
            settings();page.locator('#narrationSettingsMoreVoices').click();expect(page.locator('[data-voice-id]')).to_have_count(2)
            page.locator('#modalClose').click();page.locator('#modalClose').click();page.locator('#modalClose').click()
            page.evaluate('()=>switchLocale("en")');settings()
            page.locator('[data-narration-setting="showAllLanguages"]').click()
            assert page.locator('#narrationSettingsVoice option[value="if_sara"]').count()==1
            page.locator('#narrationSettingsMoreVoices').click();expect(page.locator('#narrationVoiceLanguage option')).to_have_count(7);page.locator('#narrationVoiceLanguage').select_option('fr');expect(page.locator('[data-voice-id]')).to_have_count(1)
            page.locator('#modalClose').click();page.locator('#narrationSettingsSave').click();expect(page.locator('#settingsNarration')).to_be_visible();page.locator('#modalClose').click()
            page.reload();page.locator('#app:not([inert])').wait_for(state='visible');page.locator('#narrationBtn').click()
            expect(page.locator('#narrationVoice option[value="if_sara"]')).to_have_count(1)
            settings();expect(page.locator('[data-narration-setting="showAllLanguages"]')).to_have_attribute('aria-checked','true')
            page.locator('[data-narration-setting="showAllLanguages"]').click();page.locator('#narrationSettingsSave').click();expect(page.locator('#settingsNarration')).to_be_visible();page.locator('#modalClose').click()
            assert page.locator('#narrationVoice').evaluate('(select)=>[...select.options].every(option=>/^[ab]/.test(option.value))')
            assert len(saves)==saved,'Language filtering and settings must not create/regenerate recordings'
            assert not errors,errors
            print('PASS: ellipsis/right-click menus on both actions, exact Generate-only selection with autoplay enabled, silent cached scopes, persisted continuation, grouped View, settings/GPU disposal, ordered whole-book generation, stop/save/resume and unattended paused-audio generation.',flush=True)
            browser.close()
    finally:
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
