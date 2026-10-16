from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v890_reviewer_highlight_comment_and_writer_feedback(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.9 highlights comments'); page,errors=_page(base,scene_browser)
    try:
        page.evaluate("""async()=>{await api('/api/review-markers',{method:'POST',body:JSON.stringify({name:'Review marker',description:'',color:'#6f93bf'})});await refresh(false)}""")
        prose=page.locator('.manuscript-block').first; prose.fill('Frozen words for review feedback')
        page.evaluate('flushSave()'); page.wait_for_timeout(180)

        page.locator('[data-view="reviews"]').click(); page.locator('#emptyCreateReview').click(); page.locator('#reviewNameInput').fill('Beta comments')
        page.locator('#permissionComment').check(); page.locator('#reviewModalConfirm').click()
        page.wait_for_selector('#reviewMarkersBtn:not(.hidden)'); page.locator('#reviewMarkersBtn').click(); page.wait_for_selector('#reviewMarkerList .marker-row'); page.locator('#reviewMarkerList [data-review-marker-use]').first.click(); page.wait_for_timeout(120); page.locator('#closeReviewMarkersPanel').click()
        page.wait_for_selector('#reviewParticipantsBtn:not(.hidden)'); page.locator('#reviewParticipantsBtn').click(); page.locator('#reviewParticipantsPanelAdd').click(); page.locator('#reviewParticipantNameInput').fill('Rebeca'); page.locator('#reviewParticipantConfirm').click()
        page.locator('#reviewStateBtn').click(); page.locator('#reviewStateStart').click(); page.locator('#dialogConfirm').click(); page.wait_for_function("()=>state.reviews[0]?.status==='open'")
        page.locator('#reviewParticipantsBtn').click(); page.locator('[data-review-participant-open]').first.click(); page.wait_for_selector('#reviewerMode:not(.hidden)')
        page.wait_for_selector('.reviewer-block[data-review-block-id]')

        # Select "Frozen words" inside the frozen snapshot.
        page.evaluate("""()=>{const block=document.querySelector('.reviewer-block[data-review-block-id]');const node=block.firstChild;const range=document.createRange();range.setStart(node,0);range.setEnd(node,12);const sel=getSelection();sel.removeAllRanges();sel.addRange(range);block.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,clientX:200,clientY:200}))}""")
        page.wait_for_selector('#reviewSelectionToolbar:not(.hidden)')
        page.locator('#reviewHighlightSelection').click(); page.wait_for_selector('#reviewMarkerPicker:not(.hidden)'); page.locator('#reviewMarkerPicker .selection-highlight-marker').first.click(); page.wait_for_timeout(150)
        assert page.evaluate("()=>CSS.highlights.has('review-marker-0')")

        page.evaluate("""()=>{const block=document.querySelector('.reviewer-block[data-review-block-id]');const node=block.firstChild;const range=document.createRange();range.setStart(node,13);range.setEnd(node,18);const sel=getSelection();sel.removeAllRanges();sel.addRange(range);block.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,clientX:220,clientY:200}))}""")
        page.wait_for_selector('#reviewSelectionToolbar:not(.hidden)'); page.locator('#reviewCommentSelection').click(); page.locator('#reviewCommentBody').fill('This wording caught my attention.'); page.locator('#reviewCommentSave').click(); page.wait_for_timeout(180)
        assert page.evaluate("()=>CSS.highlights.has('review-comments')")

        page.locator('#exitReviewerMode').click(); page.wait_for_function("()=>document.querySelector('#reviewerMode').classList.contains('hidden')"); page.wait_for_selector('.review-feedback-item')
        text=page.locator('.review-feedback-list').inner_text()
        assert 'Rebeca' in text and 'This wording caught my attention.' in text and 'Frozen words' in text
        assert not errors,errors
    finally:_shutdown_page(page)
