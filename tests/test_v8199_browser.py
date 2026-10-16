from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v8199_image_and_reviews_tooltips_explain_the_tools(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '8.19.9 tooltip language')
    page, errors = _page(base, scene_browser)
    try:
        page.locator('#manuscriptImageBtn').hover()
        page.wait_for_function("()=>!document.querySelector('#manuscriptImageTooltip').classList.contains('hidden')")
        assert page.locator('#manuscriptImageTooltip span').inner_text() == (
            'Add and arrange images between passages, then adjust their size, footer, and description.'
        )

        page.locator('#manuscriptReviewsBtn').hover()
        page.wait_for_function("()=>!document.querySelector('#manuscriptReviewsTooltip').classList.contains('hidden')")
        assert page.locator('#manuscriptReviewsTooltip span').inner_text() == (
            'View Review feedback in manuscript context, including highlights, comments, and suggestions.'
        )
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
