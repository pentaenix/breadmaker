from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _assert_clean(errors):
    assert not errors, errors


def test_v8184_collection_creation_controls_and_permanent_tabs_are_live(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '8.18.4 Control Audit')
    page, errors = _page(base, scene_browser)
    try:
        tabs = page.locator('.main-tab').evaluate_all("els=>els.map(el=>el.dataset.view)")
        assert tabs == ['manuscript', 'canvas', 'maps', 'reviews', 'tasks']
        for view in tabs:
            page.locator(f'[data-view="{view}"]').click()
            page.wait_for_function("expected=>currentView()===expected",arg=view)

        # Canvas rail quick-add must open the real creation modal.
        page.locator('[data-view="canvas"]').click()
        canvas_add = page.locator('#canvasRailItems .collection-quick-add')
        canvas_add.wait_for(state='visible')
        canvas_add.click()
        page.locator('#newCanvasName').wait_for(state='visible')
        page.locator('#modalClose').click()

        # Maps use the same quiet end-of-list creation affordance.
        page.locator('[data-view="maps"]').click()
        map_add = page.locator('#mapTree .collection-quick-add')
        map_add.wait_for(state='visible')
        map_add.click()
        page.locator('#dialogText').wait_for(state='visible')
        page.locator('#dialogCancel').click()

        # Reviews use the same affordance and must open Review Details.
        page.locator('[data-view="reviews"]').click()
        review_add = page.locator('#reviewList .collection-quick-add')
        review_add.wait_for(state='visible')
        review_add.click()
        page.locator('#reviewNameInput').wait_for(state='visible')
        page.locator('#reviewModalCancel').click()

        # Tasks has a direct creation control rather than a collection rail.
        page.locator('[data-view="tasks"]').click()
        page.locator('#newTaskBtn').click()
        page.locator('#taskName').wait_for(state='visible')
        page.locator('#modalClose').click()
        _assert_clean(errors)
    finally:
        _shutdown_page(page)


def test_v8184_review_toolbar_panels_are_clickable_and_mutually_live(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '8.18.4 Review Toolbar Audit')
    page, errors = _page(base, scene_browser)
    try:
        page.locator('[data-view="reviews"]').click()
        page.locator('#emptyCreateReview').click()
        page.locator('#reviewNameInput').fill('Interaction Audit')
        page.locator('#reviewModalConfirm').click()
        page.wait_for_function("()=>state.reviews.some(r=>r.name==='Interaction Audit')")

        # The title pencil is a live interaction too; losing this binding makes all
        # Review Details controls exist in DOM snapshots but remain unreachable.
        page.locator('#editReviewDetailsBtn').click()
        page.locator('#reviewNameInput').wait_for(state='visible')
        page.locator('#reviewModalCancel').click()

        controls = [
            ('#reviewParticipantsBtn', '#reviewParticipantsPanel'),
            ('#reviewMarkersBtn', '#reviewMarkersPanel'),
            ('#reviewQuestionsBtn', '#reviewQuestionsPanel'),
            ('#reviewStateBtn', '#reviewStatePanel'),
        ]
        for button_selector, panel_selector in controls:
            button = page.locator(button_selector)
            assert button.is_visible()
            button.click()
            panel = page.locator(panel_selector)
            panel.wait_for(state='visible')
            assert button.get_attribute('aria-pressed') == 'true'
            # Clicking another tool must not strand the previous panel open.
            button.click()
            panel.wait_for(state='hidden')
            assert button.get_attribute('aria-pressed') == 'false'

        page.locator('#reviewCollectionMore').click()
        page.locator('#contextMenu:not(.hidden)').wait_for(state='visible')
        assert page.locator('#contextMenu button').count() >= 2
        page.keyboard.press('Escape')
        _assert_clean(errors)
    finally:
        _shutdown_page(page)


def test_v8184_manuscript_tool_panels_are_not_dead_buttons(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '8.18.4 Manuscript Tool Audit')
    page, errors = _page(base, scene_browser)
    try:
        controls = [
            ('#commentsBtn', '#commentsPanel', '#closeCommentsPanel'),
            ('#markupBtn', '#markupPanel', '#markupPanel [data-close-panel]'),
            ('#checkpointBtn', '#checkpointPanel', '#closeCheckpointPanel'),
            ('#cutsBtn', '#cutsPanel', '#closeCutsPanel'),
        ]
        for button_selector, panel_selector, close_selector in controls:
            button = page.locator(button_selector)
            button.wait_for(state='visible')
            button.click()
            page.locator(panel_selector).wait_for(state='visible')
            page.locator(close_selector).click()
            page.locator(panel_selector).wait_for(state='hidden')
        _assert_clean(errors)
    finally:
        _shutdown_page(page)
