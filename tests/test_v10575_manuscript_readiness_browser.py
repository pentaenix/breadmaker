"""Synthetic local/hosted bookmark history and responsive Manuscript behavior."""
from playwright.sync_api import expect

from tests.test_v10569_narration_browser import browser


def settled(page):
    page.evaluate('async()=>{await state.structuralChain;await state.historyChain}')


def bookmark_visible_create_move_navigate_reload_delete_undo_redo(tmp_path, local):
    with browser(tmp_path, local=local) as (base,page,pid):
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_viewport_size({'width':1400,'height':950})
        page.evaluate('async()=>await api("/api/chapters",{method:"POST",body:JSON.stringify({title:"Second chapter",tab_id:state.activeDocumentId})})')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page.locator('#bookmarkBtn')).to_be_hidden()
        page.locator('.chapter-more').first.click()
        page.get_by_role('button',name='Bookmark',exact=True).click()
        settled(page)
        page.wait_for_function('()=>!!state.bookmark')
        first=page.evaluate('()=>state.bookmark.chapter_id')
        page.locator('#undoBtn').click();settled(page)
        page.wait_for_function('()=>!state.bookmark')
        page.locator('#redoBtn').click();settled(page)
        page.wait_for_function('()=>!!state.bookmark')
        page.locator('.chapter-more').nth(1).click()
        page.get_by_role('button',name='Bookmark',exact=True).click();settled(page)
        page.wait_for_function('(id)=>!!state.bookmark&&String(state.bookmark.chapter_id)!==String(id)',arg=first)
        second=page.evaluate('()=>state.bookmark.chapter_id')
        assert second!=first
        page.locator('.chapter-row').first.dblclick()
        page.locator('#bookmarkBtn').click()
        page.wait_for_function('(id)=>String(state.chapter?.id)===String(id)',arg=second)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"&&!!state.bookmark')
        assert page.evaluate('()=>state.bookmark.chapter_id')==second
        page.locator('#bookmarkBtn').click(button='right')
        page.get_by_role('button',name='Remove Bookmark',exact=True).click();settled(page)
        page.wait_for_function('()=>!state.bookmark')
        page.locator('#undoBtn').click();settled(page)
        page.wait_for_function('()=>!!state.bookmark')
        page.locator('#redoBtn').click();settled(page)
        page.wait_for_function('()=>!state.bookmark')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page.locator('#bookmarkBtn')).to_be_hidden()
        assert not errors,errors


def phone_navigation_writing_toolbar_and_panel_geometry(tmp_path, local):
    with browser(tmp_path, local=local) as (_,page,_):
        page.set_default_timeout(2000)
        for width in (360,390):
            page.set_viewport_size({'width':width,'height':844})
            expect(page.locator('.book-nav')).to_be_hidden()
            assert page.locator('#editor').bounding_box()['width']>=width-100
            page.locator('#phoneBookNavBtn').click()
            expect(page.locator('.book-nav')).to_be_visible()
            expect(page.locator('#phoneBookNavBtn')).to_have_attribute('aria-expanded','true')
            page.keyboard.press('Escape')
            expect(page.locator('.book-nav')).to_be_hidden()
            expect(page.locator('#phoneBookNavBtn')).to_be_focused()
            page.locator('#phoneBookNavBtn').click()
            page.locator('.chapter-row').first.click()
            expect(page.locator('.book-nav')).to_be_hidden()
            prose=page.locator('.manuscript-block').first
            prose.fill(f'Phone writing at {width}.')
            page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
            for button,panel in (('#markupBtn','#markupPanel'),('#narrationBtn','#narrationPanel')):
                opener=page.locator('#phoneManuscriptToolsBtn')
                before=opener.bounding_box()['width']
                opener.click()
                page.locator('#contextMenu').get_by_role('menuitem',name=page.locator(button).get_attribute('aria-label'),exact=True).click()
                expect(page.locator(panel)).to_be_visible()
                if panel=='#narrationPanel':
                    page.wait_for_function('()=>!narration.loading')
                    expect(page.locator('.narration-empty')).to_be_visible()
                    expect(page.locator('#narrationSelection')).to_be_disabled()
                box=page.locator(panel).bounding_box()
                assert box['x']>=0 and box['x']+box['width']<=width
                assert opener.bounding_box()['width']==before
                view=page.locator('#manuscriptView').bounding_box()
                assert box['height']<=view['height']*.51 and abs(box['y']+box['height']-view['y']-view['height'])<=1
                expect(page.locator(panel+' .panel-head button').last).to_be_visible()
                page.locator(panel+' .panel-head button').last.click()
                expect(page.locator(panel)).to_be_hidden()
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
            page.locator('#phoneBookNavBtn').scroll_into_view_if_needed()
        # The same controls keep their full dimensions on narrow/normal desktop.
        for width in (900,1400):
            page.set_viewport_size({'width':width,'height':950})
            expect(page.locator('#phoneBookNavBtn')).to_be_hidden()
            expect(page.locator('.book-nav')).to_be_visible()
            page.locator('#markupBtn').scroll_into_view_if_needed()
            before=page.locator('#markupBtn').bounding_box()['width']
            page.locator('#markupBtn').click()
            assert page.locator('#markupBtn').bounding_box()['width']==before
            assert page.locator('#markupPanel').bounding_box()['x']>=page.locator('#manuscriptPrimaryPane').bounding_box()['x']+page.locator('#manuscriptPrimaryPane').bounding_box()['width']-1
            page.locator('#markupPanel .panel-head button').last.click()
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page.locator('.manuscript-block').first).to_have_text('Phone writing at 390.')


def failed_bookmark_create_restores_empty_state_and_allows_retry(tmp_path, local):
    with browser(tmp_path, local=local) as (_,page,_):
        page.route('**/api/bookmark',lambda route:route.fulfill(status=503,json={'detail':'Synthetic bookmark failure'})
            if route.request.method=='PUT' else route.continue_())
        page.locator('.chapter-more').first.click()
        page.get_by_role('button',name='Bookmark',exact=True).click()
        settled(page)
        page.wait_for_function('()=>!document.body.classList.contains("manuscript-mutating")')
        expect(page.locator('#bookmarkBtn')).to_be_hidden()
        expect(page.locator('.manuscript-block').first).to_have_text('Synthetic narration passage.')
        page.unroute('**/api/bookmark')
        page.locator('.chapter-more').first.click()
        page.get_by_role('button',name='Bookmark',exact=True).click()
        page.wait_for_function('()=>!!state.bookmark')
        expect(page.locator('#bookmarkBtn')).to_be_visible()


# Browser verifier jobs own one exact pytest item/function (no parametrization).
def test_local_bookmark_lifecycle(tmp_path):
    bookmark_visible_create_move_navigate_reload_delete_undo_redo(tmp_path, True)


def test_hosted_bookmark_lifecycle(tmp_path):
    bookmark_visible_create_move_navigate_reload_delete_undo_redo(tmp_path, False)


def test_local_phone_and_desktop_layout(tmp_path):
    phone_navigation_writing_toolbar_and_panel_geometry(tmp_path, True)


def test_hosted_phone_and_desktop_layout(tmp_path):
    phone_navigation_writing_toolbar_and_panel_geometry(tmp_path, False)


def test_local_bookmark_failure_retry(tmp_path):
    failed_bookmark_create_restores_empty_state_and_allows_retry(tmp_path, True)


def test_hosted_bookmark_failure_retry(tmp_path):
    failed_bookmark_create_restores_empty_state_and_allows_retry(tmp_path, False)
