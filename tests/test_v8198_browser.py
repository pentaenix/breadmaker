from __future__ import annotations

import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page,
)


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v8198_split_view_can_choose_other_tabs_scenes_and_full_tab(scene_live_server, scene_browser):
    base = scene_live_server
    primary_tab, primary_chapter = _new_project(base, '8.19.8 split tab chooser')
    _set_blocks(base, primary_chapter['id'], ['Primary tab text'])

    second_tab = requests.post(base + '/api/documents', json={'title': 'Research Tab'}, timeout=10).json()
    second_chapter = requests.post(base + '/api/chapters', json={'title': 'Remote Chapter', 'tab_id': second_tab['id']}, timeout=10).json()
    blocks = _set_blocks(base, second_chapter['id'], ['Remote scene text', 'Remote chapter tail'])
    second_scene = requests.post(base + '/api/scenes', json={'title': 'Remote Scene', 'tab_id': second_tab['id'], 'block_ids': [blocks[0]['id']]}, timeout=10).json()

    page, errors = _page(base, scene_browser)
    try:
        active_before = page.evaluate('state.activeDocumentId')
        assert int(active_before) == int(primary_tab['id'])

        page.evaluate('openSplitViewChooser()')
        page.wait_for_selector('#modal:not(.hidden)')
        page.wait_for_selector('#splitTabSelect')

        # Chapter and Scene targets carry their own Tab selector instead of being
        # silently limited to the live Manuscript tab.
        tab_labels = page.locator('#splitTabSelect option').all_inner_texts()
        assert primary_tab['title'] in tab_labels
        assert second_tab['title'] in tab_labels
        page.locator('#splitTabSelect').select_option(str(second_tab['id']))
        page.wait_for_function("title => document.querySelector('#splitTargetSelect option')?.textContent===title", arg=second_chapter['title'])
        assert page.locator('#splitTargetSelect option').all_inner_texts() == [second_chapter['title']]

        page.locator('#splitType').select_option('scene')
        page.wait_for_function("title => document.querySelector('#splitTargetSelect option')?.textContent===title", arg=second_scene['title'])
        assert page.locator('#splitTabSelect').input_value() == str(second_tab['id'])

        # Full Tab is a first-class split target and renders the complete chosen tab.
        page.locator('#splitType').select_option('tab')
        page.wait_for_selector('#splitTargetSelect')
        page.locator('#splitTargetSelect').select_option(str(second_tab['id']))
        page.locator('#openSplitConfirm').click()
        page.wait_for_selector('#splitPane:not(.hidden)')
        page.wait_for_function("()=>document.querySelector('#splitTitle').textContent==='Research Tab'")
        body = page.locator('#splitBody').inner_text()
        assert 'Remote Chapter' in body
        assert 'Remote scene text' in body
        assert 'Remote chapter tail' in body
        assert 'Primary tab text' not in body
        assert page.locator('#splitBody .split-tab-paper').count() == 1

        # Opening another Tab in Split View must not navigate the editable Manuscript.
        assert int(page.evaluate('state.activeDocumentId')) == int(primary_tab['id'])
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
