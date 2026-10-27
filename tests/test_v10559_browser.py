"""Verified snapshot reuse, gesture accounting, and latest-navigation authority."""
from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser
from tests.test_v10556_browser import _project
from tests.test_v10557_browser import _book


def test_verified_book_retains_nodes_selection_viewport_and_reuses_chapter_bodies(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        _project(base,page);_book(page);reads=[]
        page.on('request',lambda r:reads.append(r.url) if '/api/chapters/' in r.url and r.method=='GET' else None)
        page.evaluate('''()=>{const node=document.querySelector('#editor .manuscript-block').firstChild;
            getSelection().setBaseAndExtent(node,3,node,9);window.originalSelection=getSelection().toString()}''')
        page.evaluate('async()=>await selectChapter(state.bookData.chapters[1].id)')
        assert page.evaluate('()=>state.chapterData.blocks.length')==260
        assert page.evaluate('()=>state.bookDOMCache.fragment.contains(window.keptNode)')
        page.locator('#bookViewBtn').click();page.wait_for_function('()=>state.viewMode==="book"&&!fullTextLoading')
        assert page.evaluate('()=>document.querySelector("#editor .manuscript-block")===window.keptNode')
        assert page.evaluate('()=>getSelection().toString()===window.originalSelection')
        assert page.evaluate('()=>Math.abs(document.querySelector("#documentScroller").scrollTop-window.keptScroll)<2')
        assert not reads
        # Explicit presentation changes must still rebuild incompatible DOM.
        page.evaluate('()=>setChapterMarkersVisible(false)')
        assert page.locator('#editor .book-chapter-heading').count()==0
        page.evaluate('()=>setChapterMarkersVisible(true)')
        assert page.locator('#editor .book-chapter-heading').count()==2
        for _ in range(3):
            page.evaluate('async()=>{await selectChapter(state.bookData.chapters[0].id);await openWholeBook()}')
        assert page.evaluate('()=>state.bookCache.size<=2&&state.chapterCache.size<=16&&!state.bookDOMCache&&state.chapterReadFlights.size===0')


def test_chapter_double_click_is_one_read_and_book_single_click_only_scrolls(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        _project(base,page);reads=[]
        page.on('request',lambda r:reads.append(r.url) if '/api/chapters/' in r.url and r.method=='GET' else None)
        page.get_by_text('Synthetic56 chapter 1',exact=True).first.dblclick()
        page.wait_for_function('()=>state.chapter?.title==="Synthetic56 chapter 1"')
        assert len(reads)==1
        _book(page);reads.clear();authority=[]
        page.on('request',lambda r:authority.append(r.url) if '/api/book?' in r.url else None)
        page.get_by_text('Synthetic56 chapter 0',exact=True).first.click()
        assert page.evaluate('()=>state.viewMode')=='book'
        assert not reads and not authority
        page.get_by_text('Synthetic56 chapter 0',exact=True).first.dblclick()
        page.wait_for_function('()=>state.viewMode==="chapter"')
        assert len(authority)==1 and not reads


def test_equivalent_inflight_reads_coalesce_and_old_navigation_cannot_win(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        _project(base,page);held=[]
        def hold(route):held.append(route)
        page.route('**/api/chapters/*?**',hold)
        page.evaluate('''()=>{window.ids=state.chapters.map(c=>c.id);
            window.oldRead=selectChapter(ids[0]);window.sameRead=loadChapterData(ids[0])}''')
        page.wait_for_function('()=>state.chapterReadFlights.size===1')
        assert len(held)==1
        page.evaluate('()=>{window.newRead=selectChapter(ids[1])}')
        page.wait_for_function('()=>state.chapterReadFlights.size===2')
        assert len(held)==2
        held[1].continue_();page.wait_for_function('()=>state.chapter.id===ids[1]')
        held[0].continue_();page.evaluate('async()=>await Promise.all([oldRead,sameRead,newRead])')
        assert page.evaluate('()=>state.chapter.id===ids[1]&&state.chapterReadFlights.size===0')


def test_edit_during_book_projection_rejects_stale_install_and_clears_retained_dom(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        _project(base,page);_book(page);held=[]
        page.route('**/api/book?**',lambda route:held.append(route))
        page.evaluate('()=>{window.pendingChapter=selectChapter(state.bookData.chapters[1].id).catch(e=>window.readError=e.message)}')
        page.wait_for_function('()=>state.chapterReadFlights.size===1')
        page.evaluate('()=>{markDirty(state.bookData.chapters[0].id);clearTimeout(state.saveTimer)}')
        held[0].fulfill(status=304);page.evaluate('async()=>await pendingChapter')
        assert page.evaluate('()=>state.viewMode==="book"&&state.dirty&&!state.bookDOMActive&&!state.bookDOMCache&&readError.includes("changed while loading")')


def test_changed_manuscript_cannot_reuse_old_book_dom_or_rebase_old_bodies(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        pid=_project(base,page);_book(page)
        payload=page.evaluate('''()=>({id:state.bookData.chapters[0].id,expected_revision:String(state.bookData.project.generation),
            blocks:wrapsForChapter(state.bookData.chapters[0].id).map(serializeWrap).map((b,i)=>i?b:{...b,text_content:'Synthetic59 externally acknowledged edit',content_html:'Synthetic59 externally acknowledged edit'})})''')
        response=page.request.put(base+'/api/chapters/'+payload.pop('id')+'/blocks',data=payload,
            headers={'Origin':base,'X-Breadmaker-Project':pid})
        assert response.ok,response.text()
        page.locator('#bookViewBtn').click();page.wait_for_function('()=>!fullTextLoading&&state.bookData!==window.keptBook')
        expect(page.locator('#editor .manuscript-block').first).to_have_text('Synthetic59 externally acknowledged edit')
        assert page.evaluate('()=>document.querySelector("#editor .manuscript-block")!==window.keptNode&&state.bookData.project.generation>window.keptBook.project.generation')


def test_missing_membership_404_clears_book_and_locks_retained_text(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        _project(base,page);_book(page)
        page.route('**/api/book?**',lambda r:r.fulfill(status=404,json={'detail':'PROJECT_NOT_FOUND'}))
        page.evaluate('async()=>{try{await selectChapter(state.bookData.chapters[1].id)}catch(e){window.denial=e.status}}')
        assert page.evaluate('()=>state.bookCache.size===0&&state.chapterCache.size===0&&!state.bookDOMActive&&!state.bookDOMCache&&state.manuscriptNeedsValidation')
        assert page.locator('#manuscriptPrimaryPane').evaluate('(pane)=>pane.inert')
        before=page.locator('#editor .manuscript-block').first.text_content()
        page.locator('#editor .manuscript-block').first.evaluate('(el)=>el.focus()')
        page.keyboard.insert_text('Synthetic forbidden edit')
        expect(page.locator('#editor .manuscript-block').first).to_have_text(before)
