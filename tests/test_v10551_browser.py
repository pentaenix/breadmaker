"""Real hosted import UI resumes a failed batch and opens the NEW project."""
import re

from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def test_new_project_import_resumes_batches_and_preserves_source(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        made=page.request.post(base+'/api/projects',data={'name':'Synthetic source'},headers={'Origin':base})
        assert made.ok
        original=made.json()['project']['id']
        page.goto(base+'/p/'+original,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.locator('.manuscript-block').first.fill('Original must survive')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        failed=[]
        def fail_one_batch(route):
            if 'batch=1' in route.request.url and not failed:
                failed.append(True)
                route.fulfill(status=503,json={'detail':'Synthetic interruption; retry import'})
            else:route.continue_()
        page.route('**/api/import/jobs/*/step?*',fail_one_batch)
        payload={'name':'Synthetic book.txt','mimeType':'text/plain',
            'buffer':'\n'.join(f'Synthetic imported {i}' for i in range(100)).encode()}
        page.locator('#importInput').set_input_files(payload)
        page.locator('#importDestinationNew').click()
        expect(page.locator('#manuscriptImportProgress')).to_have_class(re.compile('failed'))
        assert page.url.endswith(original)
        pending=page.evaluate('()=>JSON.parse(sessionStorage.getItem("breadmaker-pending-document-import"))')
        assert pending['job']
        page.locator('#closeFailedImport').click()
        page.locator('#importInput').set_input_files(payload)
        page.locator('#importDestinationNew').click()
        page.wait_for_url(lambda url:bool(re.search(r'/p/[0-9a-f]{32}$',url)) and not url.endswith(original))
        expect(page.locator('.manuscript-block').first).to_have_text('Synthetic imported 0')
        expect(page.locator('.manuscript-block').last).to_have_text('Synthetic imported 99')
        assert page.evaluate('()=>sessionStorage.getItem("breadmaker-pending-document-import")') is None
        page.goto(base+'/p/'+original,wait_until='domcontentloaded')
        expect(page.locator('.manuscript-block').first).to_have_text('Original must survive')


def test_book_revalidation_reuses_unchanged_text_but_never_hides_concurrent_edit(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        made=page.request.post(base+'/api/projects',data={'name':'Synthetic revalidation'},headers={'Origin':base})
        pid=made.json()['project']['id']
        page.goto(base+'/p/'+pid,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        statuses=[]
        page.on('response',lambda response:statuses.append(response.status) if '/api/book?' in response.url else None)
        assert page.evaluate('async()=>{const before=state.bookData;await reloadManuscriptView();return state.bookData===before}')
        assert statuses==[304]
        data=page.evaluate('()=>({chapter:state.bookData.chapters[0].id,block:state.bookData.blocks[0].id,revision:state.bookData.project.generation})')
        changed=page.request.put(base+'/api/chapters/'+data['chapter']+'/blocks',
            headers={'Origin':base,'X-Breadmaker-Project':pid},
            data={'expected_revision':data['revision'],'blocks':[{'id':data['block'],
                'content_html':'Concurrent synthetic edit','text_content':'Concurrent synthetic edit'}]})
        assert changed.ok
        page.evaluate('async()=>await reloadManuscriptView()')
        expect(page.locator('.manuscript-block').first).to_have_text('Concurrent synthetic edit')
        assert statuses==[304,200]
