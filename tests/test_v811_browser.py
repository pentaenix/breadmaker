from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def _menu_button(page,text):return page.locator('#contextMenu button').filter(has_text=text).first

def test_v811_reviews_match_collection_workspace_language(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.1.1 Review workspace parity'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click(); page.wait_for_function("document.querySelector('#reviewsView').classList.contains('active')"); page.get_by_text('No reviews yet.').wait_for()
        rail=page.locator('#reviewRail').bounding_box(); toolbar=page.locator('.review-toolbar').bounding_box(); assert rail and 230<=rail['width']<=242; assert toolbar and 41<=toolbar['height']<=45
        assert page.locator('#editReviewDetailsBtn').is_hidden(); assert page.locator('#reviewCurrentName').inner_text()=='Reviews'
        page.locator('#emptyCreateReview').click(); page.locator('#reviewNameInput').fill('Beta Reading'); page.locator('#reviewModalConfirm').click(); page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Beta Reading'")
        assert page.locator('.review-item.active .review-item-name').inner_text()=='Beta Reading'; assert page.locator('#editReviewDetailsBtn').is_visible(); assert page.locator('#reviewOverview').is_visible(); assert page.locator('#editReviewDetailsBtn').is_visible()
        page.locator('#editReviewDetailsBtn').click(); page.locator('#reviewNameInput').fill('Beta Reading Round 1'); page.locator('#reviewModalConfirm').click(); page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Beta Reading Round 1'")
        page.locator('#reviewCollectionMore').click(); _menu_button(page,'New Review').wait_for(); page.keyboard.press('Escape'); page.locator('.review-item-more').click(); _menu_button(page,'Details…').wait_for(); _menu_button(page,'Delete').wait_for(); page.keyboard.press('Escape')
        page.locator('#toggleReviewRail').click(); page.wait_for_function("document.querySelector('#reviewRail').classList.contains('collapsed')"); page.wait_for_timeout(180); collapsed=page.locator('#reviewRail').bounding_box(); assert collapsed and 54<=collapsed['width']<=62
        assert page.locator('.review-item-short').is_visible(); assert page.locator('.review-item-name').is_hidden(); assert not errors,errors
    finally:_shutdown_page(page)
