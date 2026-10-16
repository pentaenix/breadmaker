from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v830_review_scope_ui_and_32px_logo(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.3.0 Review scope'); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click()
        page.locator('#emptyCreateReview').click()
        page.locator('#reviewNameInput').fill('Opening Pass')
        page.locator('#reviewScopeType').select_option('chapters')
        page.locator('#reviewScopePicker input[type="checkbox"]').first.check()
        assert not page.locator('#reviewModalConfirm').is_disabled()
        selected_id=int(page.locator('#reviewScopePicker input[type="checkbox"]:checked').first.get_attribute('value'))
        page.locator('#reviewModalConfirm').click()
        page.wait_for_function("document.querySelector('#reviewCurrentName')?.textContent==='Opening Pass'")

        # Scope remains one Review-level setting in the existing Details surface.
        page.locator('#editReviewDetailsBtn').click()
        assert page.locator('#reviewScopeType').input_value()=='chapters'
        checked=[int(v) for v in page.locator('#reviewScopePicker input[type="checkbox"]:checked').evaluate_all("els=>els.map(el=>el.value)")]
        assert checked==[selected_id]
        page.locator('#reviewScopeType').select_option('manuscript')
        assert page.locator('#reviewScopePicker').evaluate("el=>el.classList.contains('hidden')")
        page.locator('#reviewModalConfirm').click()

        # The requested logo footprint is now 32px.
        size=page.locator('.brand-mark').evaluate("el=>getComputedStyle(el).width")
        assert abs(float(size.replace('px',''))-32.0)<0.2
        assert not errors,errors
    finally:
        _shutdown_page(page)
