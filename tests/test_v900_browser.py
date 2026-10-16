from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client':'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v900_profile_menu_name_seeded_avatar_and_phase_one_logout(scene_live_server, scene_browser):
    base = scene_live_server
    page, errors = _page(base, scene_browser)
    try:
        button = page.locator('#userAvatarButton')
        button.wait_for()
        initial_svg = page.locator('#userAvatar svg').evaluate('(node)=>node.outerHTML')

        button.click()
        popover = page.locator('#userProfilePopover:not(.hidden)')
        popover.wait_for()
        assert 'Writer' in popover.inner_text()
        assert 'Edit profile' in popover.inner_text()
        assert 'Log out' in popover.inner_text()

        page.locator('#editLocalProfile').click()
        name = page.locator('#profileDisplayName')
        name.fill('Ada Lovelace')
        changed_svg = page.locator('#profileAvatarPreview svg').evaluate('(node)=>node.outerHTML')
        assert changed_svg != initial_svg
        page.locator('#profileSave').click()
        page.locator('#modal').wait_for(state='hidden')

        button.click()
        popover.wait_for()
        assert 'Ada Lovelace' in popover.inner_text()
        page.locator('#localProfileLogout').click()
        # Phase one exposes the future action but deliberately has no auth behavior.
        assert not popover.evaluate('(node)=>node.classList.contains("hidden")')

        # _page() is a synthetic set_content harness whose URL is about:blank, so
        # Playwright page.reload() reloads an empty document rather than Breadmaker.
        # Recreate the harness page to exercise a real application reboot/persistence.
        _shutdown_page(page)
        _release(base)
        page, reload_errors = _page(base, scene_browser)
        page.locator('#userAvatarButton').wait_for(state='visible')
        page.locator('#userAvatarButton').click()
        page.locator('#userProfilePopover:not(.hidden)').wait_for()
        assert 'Ada Lovelace' in page.locator('#userProfilePopover').inner_text()
        assert not errors + reload_errors, errors + reload_errors
    finally:
        _release(base)
        _shutdown_page(page)
