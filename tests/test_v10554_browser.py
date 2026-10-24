"""Actual hosted saves survive cache outages; reload resumes maintenance itself."""
from playwright.sync_api import expect

from tests.test_v10548_browser import hosted_browser


def test_hosted_cache_failure_does_not_fail_save_and_reload_automatically_resumes(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        headers={'Origin':base}
        raw='\n'.join(f'Synthetic automatic cache paragraph {i}' for i in range(130)).encode()
        progress=page.request.post(base+'/api/import/new-project?resumable=true&project_name=Synthetic54',
            multipart={'file':{'name':'Synthetic54.txt','mimeType':'text/plain','buffer':raw}},headers=headers).json()
        job=progress['job_id']
        while not progress['ready']:
            progress=page.request.post(base+f'/api/import/jobs/{job}/step?batch={progress["next_batch"]}',headers=headers).json()
        pid=page.request.post(base+f'/api/import/jobs/{job}/finish',headers=headers).json()['project']['id']
        headers['X-Breadmaker-Project']=pid
        boot=page.request.get(base+'/api/bootstrap?navigation_only=true',headers=headers).json();cid=boot['chapters'][0]['id']
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(base+'/p/'+pid,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.evaluate('async id=>await selectChapter(id)',cid)
        failed=[]
        def cache_outage(route):
            failed.append(True);route.fulfill(status=503,json={'detail':'SYNTHETIC_CACHE_OUTAGE'})
        page.route('**/read-cache',cache_outage)
        marker='Synthetic save remains durable during cache outage'
        page.locator('.manuscript-block').first.fill(marker)
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        page.wait_for_function('()=>!hostedReadCacheRunning&&hostedReadCacheQueue.size===0')
        # Ensure the idle callback actually ran (not merely an empty pre-save queue).
        page.wait_for_function('()=>hostedReadCacheScheduled===false')
        assert failed,'A successful save did not trigger automatic incremental maintenance'
        expect(page.locator('#saveState')).to_have_text('Saved')
        chapter=page.request.get(base+f'/api/chapters/{cid}',headers=headers).json()
        assert chapter['cache_refresh_needed'] and chapter['blocks'][0]['text_content']==marker
        page.unroute('**/read-cache',cache_outage)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.wait_for_function('()=>hostedReadCacheCompleted.size>0')
        chapter=page.request.get(base+f'/api/chapters/{cid}',headers=headers).json()
        assert not chapter['cache_refresh_needed'] and chapter['blocks'][0]['text_content']==marker
        assert len(chapter['blocks'])==130 and not errors,errors
