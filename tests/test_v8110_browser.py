from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v8110_feedback_only_review_questions_and_response(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.11 feedback'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click(); page.locator('#emptyCreateReview').click(); page.locator('#reviewNameInput').fill('Beta form')
        page.locator('#permissionRead').uncheck(); page.locator('#permissionFeedback').check(); page.locator('#reviewModalConfirm').click()

        # Current Reviews use the reusable Questionnaire library. Create one, then
        # explicitly attach it to this Review; Populate/Create must never auto-attach.
        page.wait_for_selector('#reviewQuestionsBtn:not(.hidden)'); page.locator('#reviewQuestionsBtn').click()
        page.locator('#reviewQuestionnaireNew').click(); page.locator('#questionnaireName').fill('Beta Questions')
        page.locator('#questionnaireAddQuestion').click(); page.locator('#questionnaireQuestionRows textarea').fill('Do you think they will find the killer?')
        page.locator('#questionnaireSave').click(); page.wait_for_selector('[data-questionnaire-attach]')
        page.locator('[data-questionnaire-attach]').first.click()
        page.wait_for_function("()=>state.reviews[0]?.questionnaires?.length===1")

        page.locator('#reviewParticipantsBtn').click(); page.locator('#reviewParticipantsPanelAdd').click(); page.locator('#reviewParticipantNameInput').fill('Rebeca'); page.locator('#reviewParticipantConfirm').click()
        page.locator('#reviewStateBtn').click(); page.locator('#reviewStateStart').click(); page.locator('#dialogConfirm').click(); page.wait_for_function("()=>state.reviews[0]?.status==='open'")
        page.locator('#reviewParticipantsBtn').click(); page.locator('[data-review-participant-open]').first.click(); page.wait_for_selector('#reviewerMode:not(.hidden)')
        assert page.locator('#reviewerPaper').is_hidden()
        form=page.locator('#reviewerFeedbackForm'); assert form.is_visible()
        page.locator('[data-review-feedback-question]').fill('Yes, but only after the midpoint reveal.')
        page.locator('#reviewGeneralFeedback').fill('The mystery is easy to follow.')
        # Feedback autosaves; Finish flushes the final debounce and simply exits.
        page.locator('#reviewFeedbackFinish').click(); page.wait_for_function("()=>document.querySelector('#reviewerMode').classList.contains('hidden')")
        page.wait_for_selector('.review-feedback-form-card')
        text=page.locator('.review-feedback-form-card').inner_text()
        assert 'Rebeca' in text and 'midpoint reveal' in text and 'easy to follow' in text
        assert not errors,errors
    finally:_shutdown_page(page)
