"""A pre-rollout import job can restart without stranding the writer."""
import re

from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def test_old_import_job_restarts_with_fresh_key_and_preserves_existing_book(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        made = page.request.post(base+'/api/projects', data={'name': 'Synthetic original'}, headers={'Origin': base})
        original = made.json()['project']['id']
        page.goto(base+'/p/'+original, wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.locator('.manuscript-block').first.fill('Original writing stays safe')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        payload = {'name': 'Synthetic restart.txt', 'mimeType': 'text/plain', 'buffer': b'Successful restarted import'}
        page.evaluate('''async ()=>{
            const file=new File(['Successful restarted import'],'Synthetic restart.txt');
            const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),b=>b.toString(16).padStart(2,'0')).join('');
            sessionStorage.setItem('breadmaker-pending-document-import',JSON.stringify({
                signature:JSON.stringify([digest,file.name,'heading1']),key:'synthetic-old-key',job:'import_old_job',created:Date.now()}));
        }''')
        page.route('**/api/import/jobs/import_old_job', lambda route: route.fulfill(status=409, json={'detail': 'HOSTED_IMPORT_RESTART_REQUIRED'}))
        new_keys = []
        page.on('request', lambda request: new_keys.append(request.headers.get('idempotency-key')) if '/api/import/new-project?' in request.url else None)
        page.locator('#importInput').set_input_files(payload)
        page.locator('#importDestinationNew').click()
        page.wait_for_url(lambda url: bool(re.search(r'/p/[0-9a-f]{32}$', url)) and not url.endswith(original))
        expect(page.locator('.manuscript-block').first).to_have_text('Successful restarted import')
        assert len(new_keys) == 1 and new_keys[0] and new_keys[0] != 'synthetic-old-key'
        assert page.evaluate('()=>sessionStorage.getItem("breadmaker-pending-document-import")') is None
        page.goto(base+'/p/'+original, wait_until='domcontentloaded')
        expect(page.locator('.manuscript-block').first).to_have_text('Original writing stays safe')
