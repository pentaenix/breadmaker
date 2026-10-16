from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v870_open_participant_read_only_reviewer_mode(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.7 reviewer mode'); page,errors=_page(base,scene_browser)
    try:
        # Put unmistakable prose into the first chapter through the live editor so the writer lease stays valid.
        prose=page.locator('.manuscript-block').first; prose.fill('Frozen words for Rebeca')
        page.evaluate('flushSave()'); page.wait_for_timeout(180)

        page.locator('[data-view="reviews"]').click(); page.locator('#emptyCreateReview').click(); page.locator('#reviewNameInput').fill('Cafe Read'); page.locator('#reviewModalConfirm').click()
        page.wait_for_selector('#reviewParticipantsBtn:not(.hidden)'); page.locator('#reviewParticipantsBtn').click(); page.locator('#reviewParticipantsPanelAdd').click(); page.locator('#reviewParticipantNameInput').fill('Rebeca'); page.locator('#reviewParticipantConfirm').click()
        page.wait_for_selector('#reviewStateBtn:not(.hidden)'); page.locator('#reviewStateBtn').click(); page.locator('#reviewStateStart').click(); page.locator('#dialogConfirm').click(); page.wait_for_function("()=>state.reviews[0]?.status==='open'")
        page.locator('#reviewParticipantsBtn').click(); page.locator('[data-review-participant-open]').first.click()

        page.wait_for_selector('#reviewerMode:not(.hidden)')
        assert page.locator('body').evaluate("el=>el.classList.contains('reviewer-mode-active')")
        assert page.locator('#app').evaluate("el=>getComputedStyle(el).display")=='none'
        assert page.locator('#reviewerModeReviewName').inner_text()=='Cafe Read'
        assert 'Rebeca' in page.locator('#reviewerModeParticipant').inner_text()
        assert 'Frozen words for Rebeca' in page.locator('#reviewerPaper').inner_text()
        assert page.locator('#reviewerPaper [contenteditable="true"]').count()==0
        page.keyboard.press('Control+K'); assert page.locator('#quickOpen').evaluate("el=>el.classList.contains('hidden')")

        page.locator('#reviewerViewBtn').click(); page.locator('#reviewerTextSize').evaluate("el=>{el.value='20';el.dispatchEvent(new Event('input',{bubbles:true}))}")
        assert page.locator('#reviewerPaper').evaluate("el=>getComputedStyle(el).fontSize")=='20px'
        page.locator('#reviewerFont').select_option('courier')
        assert 'Courier' in page.locator('#reviewerPaper').evaluate("el=>getComputedStyle(el).fontFamily")

        page.locator('#exitReviewerMode').click(); page.wait_for_function("()=>document.querySelector('#reviewerMode').classList.contains('hidden')")
        assert page.locator('#app').evaluate("el=>getComputedStyle(el).display")!='none'
        assert page.locator('#reviewsView').evaluate("el=>el.classList.contains('active')")
        assert not errors,errors
    finally:_shutdown_page(page)
