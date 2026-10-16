from __future__ import annotations

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _menu_button(page,text):
    return page.locator('#contextMenu button').filter(has_text=text).first


def test_v810_review_create_open_rename_reload_and_delete(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'8.1.0 persistent Review skeleton')
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click()
        page.wait_for_function("document.querySelector('#reviewsView').classList.contains('active')")
        page.get_by_text('No reviews yet.').wait_for()
        page.locator('#emptyCreateReview').click(); page.locator('#reviewNameInput').fill('Beta Reading'); page.locator('#reviewModalConfirm').click()
        page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Beta Reading'")
        page.locator('#editReviewDetailsBtn').click(); page.locator('#reviewNameInput').fill('Beta Reading Round 1'); page.locator('#reviewModalConfirm').click()
        page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Beta Reading Round 1'")
        page.locator('[data-view="tasks"]').click(); page.locator('[data-view="reviews"]').click()
        page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Beta Reading Round 1'")
        assert page.locator('.review-item.active .review-item-name').inner_text()=='Beta Reading Round 1'
        page.locator('.review-item-more').click(); _menu_button(page,'Delete').click(); page.locator('#dialogConfirm').click(); page.get_by_text('No reviews yet.').wait_for()
        assert not errors,errors
    finally:_shutdown_page(page)
