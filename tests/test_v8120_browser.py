from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _menu_button(page,text):
    return page.locator('#contextMenu button').filter(has_text=text).first


def test_v8120_review_processing_lifecycle_and_task_link_stays_in_reviews(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.12 review processing'); page,errors=_page(base,scene_browser)
    try:
        prose=page.locator('.manuscript-block').first
        prose.fill('Frozen review text for lifecycle processing')
        page.evaluate('flushSave()'); page.wait_for_timeout(180)

        page.locator('[data-view="reviews"]').click()
        page.locator('#emptyCreateReview').click()
        page.locator('#reviewNameInput').fill('Processing Review')
        page.locator('#permissionComment').check()
        page.locator('#reviewModalConfirm').click()
        page.wait_for_selector('#reviewParticipantsBtn:not(.hidden)')
        page.locator('#reviewParticipantsBtn').click()
        page.locator('#reviewParticipantsPanelAdd').click()
        page.locator('#reviewParticipantNameInput').fill('Rebeca')
        page.locator('#reviewParticipantConfirm').click()
        page.locator('#reviewStateBtn').click(); page.locator('#reviewStateStart').click(); page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.reviews[0]?.status==='open'")

        page.locator('#reviewParticipantsBtn').click(); page.locator('[data-review-participant-open]').first.click()
        page.wait_for_selector('#reviewerMode:not(.hidden)')
        page.wait_for_selector('.reviewer-block[data-review-block-id]')
        page.wait_for_function("()=>state.reviewerSession!=null")

        page.evaluate("""()=>{const block=document.querySelector('.reviewer-block[data-review-block-id]');const node=block.firstChild;const range=document.createRange();range.setStart(node,0);range.setEnd(node,13);const sel=getSelection();sel.removeAllRanges();sel.addRange(range);block.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,clientX:200,clientY:200}))}""")
        page.wait_for_selector('#reviewSelectionToolbar:not(.hidden)')
        page.locator('#reviewCommentSelection').click()
        page.locator('#reviewCommentBody').fill('This needs another pass.')
        page.locator('#reviewCommentSave').click()
        page.wait_for_timeout(150)

        # Reviewer completion is deliberately non-destructive: Exit just leaves the
        # round and it may be reopened later.
        page.locator('#exitReviewerMode').click()
        page.wait_for_function("()=>document.querySelector('#reviewerMode').classList.contains('hidden')")
        item=page.locator('.review-feedback-item[data-review-feedback-kind="comment"]').first
        item.wait_for()

        # Overview actions live in one quiet ellipsis menu. Creating a Task must keep
        # the writer inside Reviews and use the generic editable Feedback title.
        item.locator('[data-review-feedback-more]').click(); _menu_button(page,'Create Task').click()
        page.wait_for_selector('#taskName')
        assert page.locator('#taskName').input_value().startswith('[Feedback]:')
        assert 'This needs another pass.' in page.locator('#taskDescription').input_value()
        page.locator('#saveTask').click()
        page.wait_for_function("()=>document.querySelector('#modal').classList.contains('hidden')")
        assert page.evaluate("()=>currentView()")== 'reviews'

        # Resolve also lives in the same menu. The default Open filter should then
        # hide the resolved item rather than leaving a noisy action strip behind.
        item=page.locator('.review-feedback-item[data-review-feedback-kind="comment"]').first
        item.locator('[data-review-feedback-more]').click(); _menu_button(page,'Resolve').click()
        page.wait_for_function("()=>document.querySelectorAll('.review-feedback-item[data-review-feedback-kind=\"comment\"]').length===0")

        page.locator('#reviewStateBtn').click(); page.locator('#reviewStateClose').click(); page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.reviews[0]?.status==='closed'")
        assert 'Closed' in page.locator('#reviewOverview').inner_text()
        assert not errors,errors
    finally:
        _shutdown_page(page)
