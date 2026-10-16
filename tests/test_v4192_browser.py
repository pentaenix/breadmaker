from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page
import requests


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v4192_toolbar_icons_and_tooltips_are_clear(scene_live_server, scene_browser):
    base=scene_live_server
    tab,ch=_new_project(base,'Toolbar clarity 4192')
    _set_blocks(base,ch['id'],['Some manuscript prose'])
    page,errors=_page(base,scene_browser)
    try:
        # Checkpoint is a flag, not another ribbon/bookmark silhouette.
        paths=page.locator('#checkpointBtn svg path').evaluate_all("els=>els.map(el=>el.getAttribute('d'))")
        assert paths==['M6 21V4','M6 5h11l-2.5 3L17 11H6']

        bookmark=page.locator('#bookmarkBtn')
        bookmark.hover(); page.wait_for_timeout(100)
        assert page.locator('#bookmarkTooltip').is_visible()
        assert page.locator('#bookmarkTooltipText').inner_text().strip()==(
            'Mark one place in your text you want to come back to while writing or editing. '
            'It can be a passage, Scene, or Chapter. Click here to go there.'
        )

        page.locator('#viewBtn').hover(); page.wait_for_timeout(100)
        tip=page.locator('#viewTooltip')
        assert tip.is_visible()
        view_text=tip.inner_text()
        assert 'Show Chapters' in view_text
        assert 'Chapter headers and separators' in view_text
        assert 'Show Scenes' in view_text
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
