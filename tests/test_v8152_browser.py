from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def _menu_button(page,text):
    return page.locator('#contextMenu button').filter(has_text=text).first


def test_v8152_reviews_use_manuscript_toolbar_panels_and_narrow_layout(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.15.2 Review UI'); page,errors=_page(base,scene_browser)
    try:
        page.set_viewport_size({'width':1180,'height':800})
        page.locator('[data-view="reviews"]').click(); page.get_by_text('No reviews yet.').wait_for()
        page.locator('#reviewCollectionMore').click(); _menu_button(page,'New Review').click()
        page.locator('#reviewNameInput').fill('Quiet Beta')
        page.locator('#reviewDescriptionInput').fill('Reader experience, pacing, and clarity.')
        assert page.locator('#reviewCategoryInput').count()==0
        page.locator('#permissionComment').check(); page.locator('#permissionFeedback').check()
        # Review-level note visibility lives in Details, not Participants.
        assert page.locator('#reviewInlineVisibility').is_visible()
        page.locator('#reviewModalConfirm').click(); page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Quiet Beta'")

        # Three direct Review tools; Scope and Permissions remain in Details.
        for selector in ('#reviewParticipantsBtn','#reviewQuestionsBtn','#reviewStateBtn'):
            assert page.locator(selector).is_visible()
            assert 'tool-icon-button' in (page.locator(selector).get_attribute('class') or '')
        assert page.locator('#reviewCategoriesBtn').count()==0
        for selector in ('#reviewScopeBtn','#reviewPermissionsBtn','#reviewOpenReviewerBtn'):
            assert page.locator(selector).count()==0

        row=page.locator('.review-item').filter(has_text='Quiet Beta'); row.hover(); page.wait_for_timeout(330)
        page.locator('#reviewItemHoverCard:not(.hidden)').wait_for(); assert 'Reader experience' in page.locator('#reviewItemHoverCard').inner_text()

        # Participants is a pure participant manager; note visibility is absent here.
        shell=page.locator('.review-workspace-shell'); before=shell.bounding_box()
        page.locator('#reviewParticipantsBtn').click(); panel=page.locator('#reviewParticipantsPanel:not(.hidden)'); panel.wait_for()
        after=shell.bounding_box(); assert before and after and after['width'] < before['width']
        assert page.locator('#reviewParticipantsPanel #reviewInlineVisibility').count()==0
        assert 'Reviewers can see each other' not in panel.inner_text()
        page.locator('#closeReviewParticipantsPanel').click()

        assert page.locator('#startReviewBtn').count()==0
        page.locator('#reviewStateBtn').click(); page.locator('#reviewStatePanel:not(.hidden)').wait_for(); page.locator('#reviewStateStart').wait_for(); page.locator('#closeReviewStatePanel').click()

        page.locator('.review-item-more').first.click(); menu=page.locator('#contextMenu').inner_text(); assert 'Participants' not in menu and 'Details' in menu; page.keyboard.press('Escape')

        # Narrow layout: participant panel overlays safely and close control remains reachable.
        page.set_viewport_size({'width':620,'height':760})
        page.locator('#reviewParticipantsBtn').click(); panel=page.locator('#reviewParticipantsPanel'); panel.wait_for(state='visible'); close=page.locator('#closeReviewParticipantsPanel'); box=close.bounding_box(); assert box and box['x']>=0 and box['x']+box['width']<=620 and close.is_visible(); close.click()

        for theme in ('light','dusk','dark'):
            page.evaluate("theme=>{if(theme==='light')document.documentElement.removeAttribute('data-theme');else document.documentElement.dataset.theme=theme}",theme)
            color=page.locator('#reviewStateBtn').evaluate("el=>getComputedStyle(el).color"); assert color and color!='rgba(0, 0, 0, 0)'
        assert not errors,errors
    finally:
        _shutdown_page(page)
