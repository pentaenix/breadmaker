from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def _menu_button(page,text):return page.locator('#contextMenu button').filter(has_text=text).first

def test_v8150_review_cleanup_keeps_common_actions_direct(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.15 Review UI'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click(); page.get_by_text('No reviews yet.').wait_for()
        page.locator('#reviewCollectionMore').click(); _menu_button(page,'New Review').click()
        page.locator('#reviewNameInput').fill('Quiet Review'); page.locator('#permissionComment').check(); page.locator('#permissionFeedback').check(); page.locator('#reviewModalConfirm').click()
        page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Quiet Review'")
        assert page.locator('.review-control-strip').count()==0
        # Enduring 8.15 contract: common tools stay direct in the toolbar, not in a second in-page strip.
        for selector in ('#reviewParticipantsBtn','#reviewQuestionsBtn','#reviewStateBtn'):
            assert page.locator(selector).is_visible()
        page.locator('#reviewParticipantsBtn').click(); page.locator('#reviewParticipantsPanel:not(.hidden)').wait_for(); page.locator('#reviewParticipantsPanelAdd').click(); page.locator('#reviewParticipantNameInput').fill('Rebeca'); page.locator('#reviewParticipantConfirm').click(); page.wait_for_function("state.reviews[0]?.participants?.length===1")
        assert page.locator('#reviewParticipantsCount').count()==0
        assert not errors,errors
    finally:_shutdown_page(page)
