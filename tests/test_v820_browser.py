from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _menu_button(page,text):
    return page.locator('#contextMenu button').filter(has_text=text).first


def test_v820_review_templates_replace_category_metadata(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.2.0 Review templates'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click(); page.get_by_text('No reviews yet.').wait_for()
        assert page.locator('#reviewCategoriesBtn').count()==0
        assert page.locator('#reviewCategoriesPanel').count()==0
        page.locator('#reviewCollectionMore').click(); _menu_button(page,'Populate').click()
        options=page.locator('.review-populate-preset'); assert options.count()>=4
        assert 'Reader Feedback' in options.first.inner_text() and 'Alpha Reading' in options.first.inner_text() and 'Beta Reading' in options.first.inner_text()
        page.locator('[data-review-preset=full]').click(); page.wait_for_function('()=>state.reviews.length>=6')
        names=page.evaluate('()=>state.reviews.map(r=>r.name)')
        assert names[0]=='Alpha Reading' and 'Beta Reading' in names and 'Proofreading' in names and 'Copy Edit' in names
        assert all(row['description'] for row in page.evaluate('()=>state.reviews.map(r=>({description:r.description}))'))
        assert page.locator('.review-item-grip').count()==len(names)
        assert page.locator('.review-item-description').count()==0
        size=page.locator('.brand-mark').evaluate("el=>getComputedStyle(el).width")
        assert float(size.replace('px',''))>=29.7
        assert not errors,errors
    finally:
        _shutdown_page(page)
