from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v812_brand_logo_remains_theme_sized(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.1.2 brand'); page,errors=_page(base,scene_browser)
    try:
        mark=page.locator('.brand-mark')
        box=mark.bounding_box(); assert box and box['width']>=29 and box['height']>=29
        mask=mark.evaluate("el=>getComputedStyle(el).webkitMaskImage||getComputedStyle(el).maskImage")
        assert 'breadmaker_logo.svg' in mask
        assert not errors,errors
    finally:_shutdown_page(page)
