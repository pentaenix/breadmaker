"""Full Text stays a verified in-memory cache, never an authorization cache."""
from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser
from tests.test_v10556_browser import _project
from tests.test_v10557_browser import _book


def test_cached_full_text_checks_current_authority_and_denial_clears_memory(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        _project(base,page);_book(page)
        page.get_by_text('Synthetic56 chapter 0',exact=True).first.dblclick()
        page.wait_for_function('()=>state.viewMode==="chapter"')
        held=[];chapter_reads=[];gate=[True]
        page.on('request',lambda request:chapter_reads.append(request.url) if '/api/chapters/' in request.url else None)
        def hold(route):
            if gate[0]:gate[0]=False;held.append(route)
            else:route.continue_()  # Chapter projection independently checks current authority.
        page.route('**/api/book?**',hold)
        with page.expect_request(lambda request:'/api/book?' in request.url):
            page.locator('#bookViewBtn').click()
        expect(page.locator('.manuscript-loading-indicator')).to_be_visible()
        assert len(held)==1 and held[0].request.headers.get('if-none-match')
        assert page.evaluate('()=>state.viewMode')=='chapter'
        held.pop().fulfill(status=304)
        page.wait_for_function('()=>state.viewMode==="book"&&!fullTextLoading')
        assert page.evaluate('()=>state.bookData===window.keptBook')
        assert not chapter_reads,'Warm Full Text unnecessarily downloaded chapter bodies'
        page.get_by_text('Synthetic56 chapter 0',exact=True).first.dblclick()
        page.wait_for_function('()=>state.viewMode==="chapter"')
        gate[0]=True
        with page.expect_request(lambda request:'/api/book?' in request.url):
            page.locator('#bookViewBtn').click()
        page.get_by_text('Synthetic56 chapter 1',exact=True).first.dblclick()
        page.wait_for_function('()=>state.chapter?.title==="Synthetic56 chapter 1"')
        held.pop().fulfill(status=304)
        page.wait_for_function('()=>!fullTextLoading')
        assert page.evaluate('()=>state.viewMode==="chapter"&&state.chapter.title==="Synthetic56 chapter 1"')
        gate[0]=True
        with page.expect_request(lambda request:'/api/book?' in request.url):
            page.locator('#bookViewBtn').click()
        held.pop().fulfill(status=403,json={'detail':'Synthetic revoked membership'})
        page.wait_for_function('()=>!fullTextLoading')
        assert page.evaluate('()=>state.viewMode==="chapter"&&state.bookCache.size===0&&state.chapterCache.size===0')
        assert page.evaluate('()=>document.querySelector("#manuscriptPrimaryPane").inert&&state.manuscriptNeedsValidation&&!state.bookDOMCache&&!state.bookDOMActive')
        expect(page.locator('.manuscript-loading-indicator')).to_have_count(0)


def test_logout_fences_detached_inflight_full_text_and_cannot_repopulate_cache(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        _project(base,page);held=[]
        def hold_final(route):
            if route.request.headers.get('if-none-match'):held.append(route)
            else:route.continue_()
        page.route('**/api/book?**',hold_final)
        page.locator('#bookViewBtn').click()
        expect(page.locator('.full-text-preview-body')).to_contain_text('Synthetic56 chapter 1 paragraph 259')
        page.locator('.main-tab[data-view="canvas"]').click()
        expect(page.locator('#canvasView')).to_be_visible()
        # Hosted-dev has no real login. Exercise the actual API logout handler
        # with a successful server response while the old book read is pending.
        page.route('**/api/auth/logout',lambda route:route.fulfill(status=200,json={'ok':True}))
        page.evaluate('async()=>await api("/api/auth/logout",{method:"POST"})')
        held[0].continue_();page.wait_for_function('()=>!fullTextLoading')
        assert page.evaluate('()=>state.bookCache.size===0&&state.chapterCache.size===0&&state.viewMode==="chapter"')
        expect(page.locator('#canvasView')).to_be_visible()
        expect(page.locator('.full-text-preview,.manuscript-loading-indicator')).to_have_count(0)
