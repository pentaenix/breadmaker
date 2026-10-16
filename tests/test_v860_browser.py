from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v860_start_review_freezes_workspace(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.6 start review'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click(); page.locator('#emptyCreateReview').click(); page.locator('#reviewNameInput').fill('Beta Start'); page.locator('#reviewModalConfirm').click()
        page.wait_for_selector('#reviewStateBtn:not(.hidden)'); assert 'Draft' in page.locator('#reviewOverview').inner_text()
        page.locator('#reviewStateBtn').click(); page.locator('#reviewStateStart').click(); page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.reviews[0]?.status==='open'")
        text=page.locator('#reviewOverview').inner_text(); assert 'Open' in text
        assert page.evaluate("state.reviews[0]?.snapshot?.source_generation || 0") >= 1

        # An open Review can intentionally begin another round. The Activity control
        # therefore remains present, but its command changes from Start Review to
        # Start New Round instead of disappearing.
        if not page.locator('#reviewStateStart').is_visible():
            page.locator('#reviewStateBtn').click()
        assert page.locator('#reviewStateStart').is_visible()
        assert 'Start' in page.locator('#reviewStateStart').inner_text()
        page.locator('#reviewStateBtn').click()

        # Review configuration remains editable for future rounds. The currently open
        # round keeps its own frozen policy/scope snapshot (covered by the API contract).
        page.locator('#editReviewDetailsBtn').click()
        assert not page.locator('#reviewScopeType').is_disabled()
        assert not page.locator('#permissionFeedback').is_disabled()
        page.locator('#reviewModalCancel').click()
        assert not errors,errors
    finally:_shutdown_page(page)
