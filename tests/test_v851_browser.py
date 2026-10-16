from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def test_v851_selected_review_uses_main_workspace(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.5.1 Review workspace'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click()
        page.locator('#emptyCreateReview').click()
        page.locator('#reviewNameInput').fill('Beta Workspace')
        page.locator('#permissionComment').check()
        page.locator('#permissionFeedback').check()
        page.locator('#reviewModalConfirm').click()
        page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Beta Workspace'")

        overview=page.locator('#reviewOverview'); overview.wait_for(); assert overview.is_visible()
        assert 'Draft' in overview.inner_text()
        for selector in ('#reviewParticipantsBtn','#reviewQuestionsBtn','#reviewStateBtn'):
            assert page.locator(selector).is_visible()
        assert page.locator('#reviewCategoriesBtn').count()==0
        assert page.locator('.review-overview-section').count()==0
        assert page.locator('.review-control-strip').count()==0

        # Participants are managed in the canonical right panel.
        page.locator('#reviewParticipantsBtn').click()
        page.locator('#reviewParticipantsPanelAdd').click()
        page.locator('#reviewParticipantNameInput').fill('Rebeca')
        page.locator('#reviewParticipantConfirm').click()
        page.wait_for_function("()=>state.reviews[0]?.participants?.some(p=>p.display_name==='Rebeca')")

        # The panel remains open and becomes the compact manager.
        assert 'Rebeca' in page.locator('#reviewParticipantsPanel').inner_text()
        page.locator('[data-review-participant-more]').click()
        page.locator('#contextMenu button').filter(has_text='Rename').click()
        page.locator('#reviewParticipantNameInput').fill('Rebecca')
        page.locator('#reviewParticipantConfirm').click()
        page.wait_for_function("()=>state.reviews[0]?.participants?.some(p=>p.display_name==='Rebecca')")
        page.locator('#closeReviewParticipantsPanel').click()

        page.locator('#editReviewDetailsBtn').click()
        page.locator('#reviewNameInput').wait_for(); assert page.locator('#reviewScopeType').is_visible()
        page.locator('#reviewModalCancel').click()
        page.locator('#editReviewDetailsBtn').click()
        assert page.locator('#permissionComment').is_visible()
        page.locator('#reviewModalCancel').click()
        assert not errors,errors
    finally:_shutdown_page(page)
