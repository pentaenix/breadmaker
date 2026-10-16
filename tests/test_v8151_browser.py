from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def _menu_button(page,text): return page.locator('#contextMenu button').filter(has_text=text).first

def test_v8151_reviews_reuse_manuscript_toolbar_without_category_metadata(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.15.1 Manuscript-first Reviews'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click(); page.get_by_text('No reviews yet.').wait_for()
        assert page.locator('#newReviewBtn').count()==0 and page.locator('#reviewCategoryFilterBtn').count()==0
        assert page.locator('#reviewCategoriesBtn').count()==0 and page.locator('#reviewCategoriesPanel').count()==0
        page.locator('#reviewCollectionMore').click(); _menu_button(page,'Populate').click()
        page.locator('.review-populate-preset').first.wait_for(); assert page.locator('.review-populate-preset').count()>=4
        page.locator('[data-review-preset=full]').click(); page.wait_for_function('()=>state.reviews.length>=6')
        row=page.locator('.review-item').filter(has_text='Beta Reading'); row.hover(); page.wait_for_timeout(360)
        page.locator('#reviewItemHoverCard:not(.hidden)').wait_for(); assert page.locator('#reviewItemHoverCard').inner_text().strip()
        assert not errors,errors
    finally:_shutdown_page(page)
