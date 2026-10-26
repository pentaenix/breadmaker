"""Workspace return preserves the verified manuscript instead of a loading modal."""
from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser
from tests.test_v10556_browser import _project


def _book(page):
    page.locator('#bookViewBtn').click();page.wait_for_function('()=>state.viewMode==="book"&&!fullTextLoading')
    page.evaluate('''()=>{window.keptBook=state.bookData;window.keptNode=document.querySelector('#editor .manuscript-block');
        document.querySelector('#documentScroller').scrollTop=400;window.keptScroll=document.querySelector('#documentScroller').scrollTop}''')


def test_canvas_return_keeps_book_dom_scroll_and_cache_during_validation_and_network_failure(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        _project(base,page);_book(page);held=[]
        def hold(route):
            if route.request.headers.get('if-none-match'):held.append(route)
            else:route.continue_()
        page.route('**/api/book?**',hold)
        page.locator('.main-tab[data-view="canvas"]').click()
        expect(page.locator('#canvasView')).to_be_visible()
        with page.expect_request(lambda r:'/api/book?' in r.url and bool(r.headers.get('if-none-match'))):
            page.locator('.main-tab[data-view="manuscript"]').click()
        expect(page.locator('#editor')).to_be_visible()
        assert page.evaluate('()=>document.querySelector("#editor .manuscript-block")===window.keptNode&&state.bookData===window.keptBook')
        assert page.locator('.full-text-preview').count()==0 and page.locator('dialog[open]').count()==0
        assert page.evaluate('()=>document.querySelector("#manuscriptPrimaryPane").inert')
        assert page.evaluate('()=>document.querySelector("#documentScroller").scrollTop===window.keptScroll')
        # Real input must not edit retained text before current authority is
        # checked, even though the workspace is already visible.
        before=page.locator('.manuscript-block').first.text_content()
        page.locator('.manuscript-block').first.evaluate('(el)=>el.focus()')
        page.keyboard.insert_text('SYNTHETIC blocked while validating')
        expect(page.locator('.manuscript-block').first).to_have_text(before)
        assert page.evaluate('()=>!state.dirty')
        held[0].continue_();page.wait_for_function('()=>!document.querySelector("#manuscriptPrimaryPane").inert')
        assert page.evaluate('()=>state.bookCache.get(String(state.activeDocumentId))===window.keptBook&&document.querySelector("#editor .manuscript-block")===window.keptNode')
        # A failed check keeps the retained text, but cannot enable stale edits.
        held.clear();page.locator('.main-tab[data-view="canvas"]').click()
        with page.expect_request(lambda r:'/api/book?' in r.url and bool(r.headers.get('if-none-match'))):
            page.locator('.main-tab[data-view="manuscript"]').click()
        expect(page.locator('#editor')).to_be_visible();held[0].fulfill(status=503,json={'detail':'Synthetic unavailable'})
        page.wait_for_function('()=>document.querySelector("#manuscriptPrimaryPane").getAttribute("aria-busy")==="false"')
        assert page.evaluate('()=>state.manuscriptNeedsValidation&&document.querySelector("#manuscriptPrimaryPane").inert&&state.bookData===window.keptBook')
        page.unroute('**/api/book?**',hold)
        page.locator('.main-tab[data-view="manuscript"]').click()
        page.wait_for_function('()=>!state.manuscriptNeedsValidation&&!document.querySelector("#manuscriptPrimaryPane").inert')
        assert page.evaluate('()=>document.querySelector("#editor .manuscript-block")===window.keptNode')
        # Choosing a chapter during validation must not let the late book check
        # overwrite that navigation or replace the retained book cache with null.
        held.clear();page.route('**/api/book?**',hold)
        page.locator('.main-tab[data-view="canvas"]').click()
        with page.expect_request(lambda r:'/api/book?' in r.url and bool(r.headers.get('if-none-match'))):
            page.locator('.main-tab[data-view="manuscript"]').click()
        page.get_by_text('Synthetic56 chapter 0',exact=True).first.dblclick()
        expect(page.locator('#chapterTitle')).to_be_visible()
        held[0].continue_();page.wait_for_function('()=>!document.querySelector("#manuscriptPrimaryPane").inert')
        assert page.evaluate('()=>state.viewMode==="chapter"&&state.bookCache.get(String(state.activeDocumentId))===window.keptBook')


def test_canvas_revision_change_retains_loaded_book_until_complete_validated_replacement(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        pid=_project(base,page);_book(page)
        response=page.request.post(base+'/api/canvases',data={'name':'Synthetic57 revision advance'},
            headers={'Origin':base,'X-Breadmaker-Project':pid})
        assert response.ok
        held=[]
        def hold_first(route):
            if not held:held.append(route)
            else:route.continue_()
        page.route('**/api/chapters/*?**',hold_first)
        page.locator('.main-tab[data-view="canvas"]').click()
        with page.expect_request(lambda r:'/api/chapters/' in r.url and r.method=='GET'):
            page.locator('.main-tab[data-view="manuscript"]').click()
        expect(page.locator('#editor')).to_be_visible()
        assert page.locator('.full-text-preview').count()==0 and page.locator('dialog[open]').count()==0
        assert page.evaluate('()=>state.bookCache.get(String(state.activeDocumentId))===window.keptBook&&document.querySelector("#editor .manuscript-block")===window.keptNode')
        held[0].continue_();page.wait_for_function('()=>!document.querySelector("#manuscriptPrimaryPane").inert')
        assert page.locator('.manuscript-block').count()==520
        assert page.evaluate('()=>state.bookData!==window.keptBook&&!state.dirty&&state.bookData.project.generation>window.keptBook.project.generation')


def test_leaving_inline_full_text_cancels_without_modal_or_late_navigation_takeover(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        _project(base,page);held=[]
        def hold_final(route):
            if route.request.headers.get('if-none-match'):held.append(route)
            else:route.continue_()
        page.route('**/api/book?**',hold_final)
        page.locator('#bookViewBtn').click()
        expect(page.locator('.full-text-preview-body')).to_contain_text('Synthetic56 chapter 1 paragraph 259')
        assert page.locator('dialog[open]').count()==0
        page.locator('.main-tab[data-view="canvas"]').click()
        expect(page.locator('#canvasView')).to_be_visible()
        expect(page.locator('.full-text-preview')).to_have_count(0)
        # Re-entry waits for the cancelled load to settle, without freezing
        # other navigation or accepting the cancelled snapshot as validation.
        page.locator('.main-tab[data-view="manuscript"]').click()
        expect(page.locator('#manuscriptView')).to_be_visible()
        page.locator('.main-tab[data-view="tasks"]').click()
        expect(page.locator('#tasksView')).to_be_visible()
        held[0].continue_();page.wait_for_function('()=>!fullTextLoading')
        page.wait_for_function('()=>document.querySelector("#manuscriptPrimaryPane").getAttribute("aria-busy")==="false"')
        expect(page.locator('#tasksView')).to_be_visible()
        assert page.evaluate('()=>state.bookCache.size')==0
