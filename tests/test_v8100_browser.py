from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v8100_reviewer_suggestion_and_writer_accept(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.10 suggestions'); page,errors=_page(base,scene_browser)
    try:
        prose=page.locator('.manuscript-block').first; prose.fill('Frozen words for suggested rewrite')
        page.evaluate('flushSave()'); page.wait_for_timeout(180)

        page.locator('[data-view="reviews"]').click(); page.locator('#emptyCreateReview').click(); page.locator('#reviewNameInput').fill('Editor suggestions')
        page.locator('#permissionSuggest').check(); page.locator('#reviewModalConfirm').click()
        page.wait_for_selector('#reviewParticipantsBtn:not(.hidden)'); page.locator('#reviewParticipantsBtn').click(); page.locator('#reviewParticipantsPanelAdd').click(); page.locator('#reviewParticipantNameInput').fill('Carlos'); page.locator('#reviewParticipantConfirm').click()
        page.locator('#reviewStateBtn').click(); page.locator('#reviewStateStart').click(); page.locator('#dialogConfirm').click(); page.wait_for_function("()=>state.reviews[0]?.status==='open'")
        page.locator('#reviewParticipantsBtn').click(); page.locator('[data-review-participant-open]').first.click(); page.wait_for_selector('#reviewerMode:not(.hidden)')
        page.wait_for_selector('.reviewer-block[data-review-block-id]')

        page.evaluate("""()=>{const block=document.querySelector('.reviewer-block[data-review-block-id]');const node=block.firstChild;const range=document.createRange();range.setStart(node,0);range.setEnd(node,6);const sel=getSelection();sel.removeAllRanges();sel.addRange(range);block.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,clientX:200,clientY:200}))}""")
        page.wait_for_selector('#reviewSelectionToolbar:not(.hidden)')
        assert page.locator('#reviewSuggestSelection').is_visible()
        page.locator('#reviewSuggestSelection').click()
        page.wait_for_selector('#reviewSuggestionOperation')
        page.locator('#reviewSuggestionOperation').select_option('replace')
        page.locator('#reviewSuggestionText').fill('Revised')
        page.locator('#reviewSuggestionSave').click(); page.wait_for_timeout(180)
        assert page.evaluate("()=>CSS.highlights.has('review-suggestions')")

        page.locator('#exitReviewerMode').click(); page.wait_for_function("()=>document.querySelector('#reviewerMode').classList.contains('hidden')")
        suggestion=page.locator('.review-feedback-item[data-review-feedback-kind="suggestion"]').first
        suggestion.wait_for(); feedback=suggestion.inner_text()
        assert 'Carlos' in feedback and 'Frozen' in feedback and 'Revised' in feedback

        # Accept/Reject intentionally lives in Review Feedback beside the frozen text,
        # not in the overview card. The reviewer name is the contextual jump target.
        suggestion.locator('[data-review-feedback-locate-kind="suggestion"]').click()
        page.wait_for_selector('#reviewerMode:not(.hidden)')
        page.wait_for_selector('[data-writer-suggestion-accept]')
        page.locator('[data-writer-suggestion-accept]').click()
        page.wait_for_function("()=>document.querySelector('#toast')?.textContent.includes('accepted') || document.querySelector('#toast')?.textContent.includes('Accepted')")
        page.locator('#exitReviewerMode').click(); page.wait_for_function("()=>document.querySelector('#reviewerMode').classList.contains('hidden')")

        page.locator('[data-view="manuscript"]').click(); page.wait_for_timeout(180)
        assert 'Revised words for suggested rewrite' in page.locator('.manuscript-block').first.inner_text()
        assert not errors,errors
    finally:_shutdown_page(page)
