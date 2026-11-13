"""Real hosted UI keeps the last clicked tab through a pending Full Text read."""
from tests.test_v10548_browser import hosted_browser
from tests.test_v10556_browser import _project


def test_tab_clicked_during_full_text_load_eventually_renders_latest_target(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        _project(base, page)
        ids = page.evaluate('''async()=>{
          const ids=[];for(const title of ['Guide','License'])ids.push((await api('/api/documents',{method:'POST',body:JSON.stringify({title})})).id);
          await refresh(false);return ids;
        }''')
        held=[]
        def hold(route):
            if not held: held.append(route)
            else: route.continue_()
        page.route('**/api/book?**',hold)
        with page.expect_request(lambda r:'/api/book?' in r.url):
            page.locator('#bookViewBtn').click()
        page.locator(f'[data-document-id="{ids[0]}"] .document-row-main').click()
        page.locator(f'[data-document-id="{ids[1]}"] .document-row-main').click()
        held[0].continue_()
        page.wait_for_function('(id)=>!fullTextLoading&&state.activeDocumentId===id&&state.bookData?.chapters.every(c=>c.tab_id===id)',arg=ids[1])
        assert page.locator('#activeDocumentTitle').inner_text()=='License'
        assert page.locator('.full-text-preview,.manuscript-loading-indicator').count()==0
        assert not page.evaluate('()=>state.dirty')
