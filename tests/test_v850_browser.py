from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v850_review_participants_management(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.5.0 Review participants'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click()
        page.locator('#emptyCreateReview').click()
        page.locator('#reviewNameInput').fill('Beta Participants')
        page.locator('#reviewModalConfirm').click()
        page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Beta Participants'")

        # Participants now live in the canonical Review toolbar side panel.
        page.locator('#reviewParticipantsBtn').click()
        page.locator('#reviewParticipantsPanel:not(.hidden)').wait_for()
        page.locator('#reviewParticipantsPanelAdd').click()
        page.locator('#reviewParticipantNameInput').fill('Rebeca')
        page.locator('#reviewParticipantConfirm').click()
        page.wait_for_function("()=>state.reviews[0]?.participants?.some(p=>p.display_name==='Rebeca')")

        # Add a second participant and rename it without changing identity.
        page.locator('#reviewParticipantsPanelAdd').click()
        page.locator('#reviewParticipantNameInput').fill('Ana')
        page.locator('#reviewParticipantConfirm').click()
        page.wait_for_function("()=>state.reviews[0]?.participants?.length===2")
        before=page.evaluate("()=>state.reviews[0].participants.find(p=>p.display_name==='Ana').id")
        ana_row=page.locator('[data-review-participant]').filter(has_text='Ana')
        ana_row.locator('[data-review-participant-more]').click()
        page.locator('#contextMenu button').filter(has_text='Rename').click()
        page.locator('#reviewParticipantNameInput').fill('Anna')
        page.locator('#reviewParticipantConfirm').click()
        page.wait_for_function("()=>state.reviews[0]?.participants?.some(p=>p.display_name==='Anna')")
        after=page.evaluate("()=>state.reviews[0].participants.find(p=>p.display_name==='Anna').id")
        assert before==after

        # Delete Rebeca; the Review itself remains selected and intact.
        rebeca_row=page.locator('[data-review-participant]').filter(has_text='Rebeca')
        rebeca_row.locator('[data-review-participant-more]').click()
        page.locator('#contextMenu button').filter(has_text='Remove Participant').click()
        page.locator('.modal-actions .danger').click()
        page.wait_for_function("()=>state.reviews[0]?.participants?.length===1")
        assert page.evaluate("()=>state.reviews[0].participants[0].display_name")=='Anna'
        assert page.locator('#reviewCurrentName').inner_text()=='Beta Participants'
        assert not errors,errors
    finally:_shutdown_page(page)
