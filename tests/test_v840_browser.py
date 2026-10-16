from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v840_review_permissions_ui_and_dependencies(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.4.0 Review permissions'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click()
        page.locator('#emptyCreateReview').click()
        page.locator('#reviewNameInput').fill('Beta Feedback')

        # Default is Read only. Feedback is deliberately independent.
        assert page.locator('#permissionRead').is_checked()
        assert not page.locator('#permissionComment').is_checked()
        assert not page.locator('#permissionSuggest').is_checked()
        assert not page.locator('#permissionFeedback').is_checked()
        page.locator('#permissionRead').uncheck()
        page.locator('#permissionFeedback').check()
        assert not page.locator('#permissionRead').is_checked()
        page.locator('#reviewModalConfirm').click()
        page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Beta Feedback'")

        page.locator('#editReviewDetailsBtn').click()
        assert not page.locator('#permissionRead').is_checked()
        assert page.locator('#permissionFeedback').is_checked()

        # Suggest forces Read but does not turn Comment or Feedback on/off for us.
        page.locator('#permissionFeedback').uncheck()
        page.locator('#permissionSuggest').check()
        assert page.locator('#permissionRead').is_checked()
        assert page.locator('#permissionRead').is_disabled()
        assert not page.locator('#permissionComment').is_checked()
        assert not page.locator('#permissionFeedback').is_checked()
        page.locator('#reviewModalConfirm').click()

        page.locator('#editReviewDetailsBtn').click()
        assert page.locator('#permissionRead').is_checked()
        assert page.locator('#permissionRead').is_disabled()
        assert page.locator('#permissionSuggest').is_checked()
        assert not page.locator('#permissionComment').is_checked()
        assert not page.locator('#permissionFeedback').is_checked()
        assert not errors,errors
    finally:_shutdown_page(page)
