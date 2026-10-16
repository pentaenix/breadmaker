from __future__ import annotations

import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

CLIENT='v770-browser'


def _release(base):
    try:requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception:pass


def test_v770_browser_bootstrap_reload_does_not_invent_canonical_generations(scene_live_server,scene_browser):
    base=scene_live_server;_new_project(base,'7.7.0 Generation Browser')
    before=requests.get(base+'/api/bootstrap',timeout=5).json()['project']['project_generation']
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_selector('#editor')
        for _ in range(2):
            page.evaluate("refresh(false)")
            page.wait_for_function("document.querySelectorAll('.manuscript-block').length >= 1")
        browser_generation=page.evaluate("fetch('/api/bootstrap').then(r=>r.json()).then(x=>x.project.project_generation)")
        after=requests.get(base+'/api/bootstrap',timeout=5).json()['project']['project_generation']
        assert int(before)==int(browser_generation)==int(after)
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
