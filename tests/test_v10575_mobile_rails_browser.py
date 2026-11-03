"""Shared phone rail lifecycle and unchanged desktop push/scroll controls."""
from playwright.sync_api import expect

from tests.test_v10569_narration_browser import browser


RAILS=(
    ('canvas','#canvasRail','#phoneCanvasNavBtn','#canvasStage','.canvas-item'),
    ('maps','#mapRail','#phoneMapNavBtn','#mapStage','.map-tree-map-row'),
    ('reviews','#reviewRail','#phoneReviewNavBtn','#reviewStage','.review-item'),
)


def setup(page):
    return page.evaluate('''async()=>{
      const canvas=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Synthetic canvas',canvas_type:'developmental'})});
      const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Synthetic map'})});
      const review=await api('/api/reviews',{method:'POST',body:JSON.stringify({name:'Synthetic review'})});
      await refresh(false);return {canvas:canvas.id,map:map.id,review:review.id};
    }''')


def navigate(page,view):
    tab=page.locator(f'.main-tab[data-view="{view}"]');tab.scroll_into_view_if_needed();tab.click()
    page.wait_for_function('(view)=>currentView()===view',arg=view)


def mobile_rails(home,local):
    with browser(home,local=local) as (_,page,_):
        setup(page);errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_viewport_size({'width':1400,'height':950})
        for view,rail,_,_,_ in RAILS:
            navigate(page,view)
            page.locator(rail+' .nav-collapse-button').click()
            expect(page.locator(rail)).to_have_class(__import__('re').compile('collapsed'))
        preferences=page.evaluate('()=>[state.canvasRailExpanded,state.mapRailExpanded,state.reviewRailExpanded]')
        for width in (360,390):
            page.set_viewport_size({'width':width,'height':844})
            for view,rail,opener,stage,row in RAILS:
                navigate(page,view)
                expect(page.locator(rail)).to_be_hidden()
                assert page.locator(stage).bounding_box()['width']>=width-2
                page.locator(opener).scroll_into_view_if_needed();page.locator(opener).click()
                expect(page.locator(rail)).to_be_visible()
                expect(page.locator(opener)).to_have_attribute('aria-expanded','true')
                box=page.locator(rail).bounding_box()
                assert box['x']>=0 and box['x']+box['width']<=width
                page.keyboard.press('Shift+Tab')
                assert page.locator(rail).evaluate('(el)=>el.contains(document.activeElement)')
                more=page.locator(rail+' .nav-section-more').first
                more.press('Enter')
                actions=page.locator('#contextMenu button:not(:disabled)')
                actions.first.focus();page.keyboard.press('Tab')
                expect(actions.nth(1)).to_be_focused()
                page.keyboard.press('Shift+Tab');expect(actions.first).to_be_focused()
                page.keyboard.press('Escape')
                expect(page.locator('#contextMenu')).to_be_hidden()
                expect(more).to_be_focused();expect(page.locator(rail)).to_be_visible()
                page.keyboard.press('Escape')
                expect(page.locator(rail)).to_be_hidden();expect(page.locator(opener)).to_be_focused()
                page.locator(opener).click()
                page.locator(rail+' .collection-quick-add').click()
                expect(page.locator('#modal')).to_be_visible()
                page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_hidden()
                expect(page.locator(rail)).to_be_visible()
                page.locator(rail+' '+row).first.click()
                expect(page.locator(rail)).to_be_hidden()
                # View switches close the drawer and release only its own inert state.
                page.locator(opener).scroll_into_view_if_needed();page.locator(opener).click()
                navigate(page,'tasks')
                navigate(page,view)
                expect(page.locator(rail)).to_be_hidden()
                assert not page.locator(stage).evaluate('(el)=>!!el.closest("[inert]")')
                assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
        assert page.evaluate('()=>[state.canvasRailExpanded,state.mapRailExpanded,state.reviewRailExpanded]')==preferences
        page.set_viewport_size({'width':1400,'height':950})
        for view,rail,_,_,_ in RAILS:
            navigate(page,view)
            expect(page.locator(rail)).to_have_class(__import__('re').compile('collapsed'))
        assert not errors,errors


def desktop_rails(home,local):
    with browser(home,local=local) as (_,page,_):
        ids=setup(page)
        for width,height in ((844,390),(900,800),(1400,950)):
            page.set_viewport_size({'width':width,'height':height})
            for view,rail,opener,stage,_ in RAILS:
                navigate(page,view)
                expect(page.locator(opener)).to_be_hidden();expect(page.locator(rail)).to_be_visible()
                assert page.locator(stage).bounding_box()['width']>=width-240
            navigate(page,'canvas')
            page.evaluate('(id)=>selectCanvas(id)',ids['canvas'])
            page.locator('#canvasStructureBtn').scroll_into_view_if_needed()
            before=page.locator('#canvasStructureBtn').bounding_box()['width']
            page.locator('#canvasStructureBtn').click()
            expect(page.locator('#canvasToolPanel')).to_be_visible()
            assert page.locator('#canvasStructureBtn').bounding_box()['width']==before
            page.locator('[data-close-canvas-panel]').click()
            navigate(page,'maps')
            before=page.locator('#mapViewBtn').bounding_box()['width']
            page.evaluate('()=>openMapInspector("Synthetic inspector","<div class=canvas-panel-field><label for=syntheticMapField>Name</label><input id=syntheticMapField></div>")')
            expect(page.locator('#mapInspector')).to_be_visible()
            assert page.locator('#mapViewBtn').bounding_box()['width']==before
            page.locator('[data-close-map-inspector]').click()
            navigate(page,'reviews')
            page.evaluate('(id)=>selectReview(id)',ids['review'])
            page.locator('#reviewParticipantsBtn').scroll_into_view_if_needed()
            before=page.locator('#reviewParticipantsBtn').bounding_box()['width']
            page.locator('#reviewParticipantsBtn').click()
            expect(page.locator('#reviewParticipantsPanel')).to_be_visible()
            assert page.locator('#reviewParticipantsBtn').bounding_box()['width']==before
            page.locator('#closeReviewParticipantsPanel').click()


def test_local_phone_rails_creation_selection_focus_and_view_switch(tmp_path):
    mobile_rails(tmp_path,True)


def test_hosted_phone_rails_creation_selection_focus_and_view_switch(tmp_path):
    mobile_rails(tmp_path,False)


def test_local_landscape_narrow_and_normal_desktop_panels(tmp_path):
    desktop_rails(tmp_path,True)


def test_hosted_landscape_narrow_and_normal_desktop_panels(tmp_path):
    desktop_rails(tmp_path,False)
