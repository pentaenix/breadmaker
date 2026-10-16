from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page


def _menu_button(page,text):
    return page.locator('#contextMenu button').filter(has_text=text).first


def test_v8160_review_ui_templates_and_reviewer_mode_follow_breadmaker_patterns(scene_live_server,scene_browser):
    base=scene_live_server; _tab,chapter=_new_project(base,'8.16 Review UI'); _set_blocks(base,chapter['id'],['A selectable manuscript paragraph for a reusable Review round.']); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click(); page.locator('#emptyCreateReview').click()
        assert page.locator('#reviewNameInput').get_attribute('placeholder')=='Review name'
        page.locator('#reviewNameInput').fill('Reader Pass'); page.locator('#reviewDescriptionInput').fill('A reusable reader-facing pass.')
        assert page.locator('#reviewCategoryInput').count()==0 and page.locator('#reviewInlineVisibility').is_visible()
        page.locator('#permissionComment').check(); page.locator('#permissionSuggest').check(); page.locator('#reviewModalConfirm').click(); page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Reader Pass'")

        # Four direct tools; Categories are gone and Review Markers are their own library.
        assert page.locator('#reviewCategoriesBtn').count()==0
        for selector in ('#reviewParticipantsBtn','#reviewMarkersBtn','#reviewQuestionsBtn','#reviewStateBtn'):
            assert page.locator(selector).is_visible()

        # Participant split-button keeps the ellipsis inside the control and no visibility setting lives here.
        page.locator('#reviewParticipantsBtn').click(); panel=page.locator('#reviewParticipantsPanel:not(.hidden)'); panel.wait_for()
        assert 'Reviewers can see each other' not in panel.inner_text()
        add=page.locator('#reviewParticipantsPanelAdd'); more=page.locator('#reviewParticipantsPanelMore')
        assert add.is_visible() and more.is_visible() and 'review-split-button' in (add.locator('xpath=..').get_attribute('class') or '')
        add.click(); page.locator('#reviewParticipantNameInput').fill('Rebeca'); page.locator('#reviewParticipantConfirm').click(); page.wait_for_function("()=>state.reviews.find(r=>r.name==='Reader Pass')?.participants?.length===1")
        page.locator('#closeReviewParticipantsPanel').click()

        # Questionnaire library attaches reusable prompts.
        page.locator('#reviewQuestionsBtn').click(); page.locator('#reviewQuestionsPanel:not(.hidden)').wait_for(); page.locator('#reviewQuestionnaireNew').click()
        page.locator('#questionnaireName').fill('Reader Questions'); page.locator('#questionnaireAddQuestion').click(); page.locator('#questionnaireQuestionRows textarea').fill('Where did your attention drift?'); page.locator('#questionnaireSave').click()
        page.locator('[data-questionnaire-attach]').first.click(); page.wait_for_function("()=>state.reviews.find(r=>r.name==='Reader Pass')?.questionnaires?.length===1"); page.locator('#closeReviewQuestionsPanel').click()

        # Review Activity owns lifecycle.
        page.locator('#reviewStateBtn').click(); page.locator('#reviewStateStart').click(); page.locator('#dialogConfirm').click(); page.wait_for_function("()=>state.reviews.find(r=>r.name==='Reader Pass')?.status==='open'"); page.locator('#closeReviewStatePanel').click()

        # Populate creates real editable Reviews, not Category metadata.
        page.locator('#reviewCollectionMore').click(); _menu_button(page,'Populate').click(); assert page.locator('.review-populate-preset').count()>=4
        page.locator('[data-review-preset=full]').click(); page.wait_for_function("()=>state.reviews.length>=7")
        populated=page.evaluate("()=>state.reviews.filter(r=>r.name!=='Reader Pass').map(r=>({name:r.name,description:r.description,scope:r.scope_type,hasCategory:Object.prototype.hasOwnProperty.call(r,'category_id')}))")
        assert len(populated)>=6 and all(row['description'] and row['scope']=='manuscript' and not row['hasCategory'] for row in populated)

        # Existing Review description hover and reviewer mode remain functional after Populate.
        reader_pass_id=page.evaluate("()=>state.reviews.find(r=>r.name==='Reader Pass').id"); row=page.locator(f'.review-item[data-review-id="{reader_pass_id}"]'); row.hover(); page.wait_for_timeout(330); page.locator('#reviewItemHoverCard:not(.hidden)').wait_for(); assert 'reusable reader-facing pass' in page.locator('#reviewItemHoverCard').inner_text().lower(); row.click()
        page.locator('#reviewParticipantsBtn').click(); page.locator('[data-review-participant-open]').filter(has_text='Rebeca').click(); page.locator('#reviewerMode:not(.hidden)').wait_for()
        page.evaluate("""()=>{const b=document.querySelector('.reviewer-block');const n=b.firstChild;const r=document.createRange();r.setStart(n,0);r.setEnd(n,Math.min(10,n.textContent.length));const s=getSelection();s.removeAllRanges();s.addRange(r);showReviewSelectionToolbar()}""")
        toolbar=page.locator('#reviewSelectionToolbar'); toolbar.wait_for(state='visible'); assert 'selection-toolbar' in (toolbar.get_attribute('class') or '')
        assert not errors,errors
    finally:
        _shutdown_page(page)
