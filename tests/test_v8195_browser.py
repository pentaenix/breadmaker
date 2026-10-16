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


def test_v8195_image_copy_cut_paste_preserves_occurrence_metadata(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, '8.19.5 image clipboard')
    _set_blocks(base, chapter['id'], ['Before image', 'After image'])
    detail = requests.get(base + f'/api/chapters/{chapter["id"]}', timeout=10).json()
    target = detail['blocks'][0]
    upload = requests.post(base + '/api/assets', files={'file': ('clip.png', b'clipboard-browser-image', 'image/png')}, timeout=10)
    upload.raise_for_status()
    insert = requests.post(base + '/api/manuscript/images/insert', json={
        'image_path': upload.json()['path'], 'target_block_id': target['id'], 'placement': 'after',
        'image_width': 41, 'image_footer': 'Source: archive', 'image_description': 'An archival image.',
    }, timeout=10)
    insert.raise_for_status()

    page, errors = _page(base, scene_browser)
    try:
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===1")
        image = page.locator('.manuscript-image-figure').first
        image.click()
        page.keyboard.press('Control+c')

        page.evaluate("""() => {
          const prose=document.querySelectorAll('.manuscript-block')[1];
          placeCaret(prose, prose.innerText.length);
        }""")
        page.keyboard.press('Control+v')
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===2")
        copied = page.locator('.manuscript-image-wrap').last
        assert copied.get_attribute('data-image-width') == '41'
        assert copied.get_attribute('data-image-footer') == 'Source: archive'
        assert copied.get_attribute('data-image-description') == 'An archival image.'

        copied.locator('.manuscript-image-figure').click()
        page.keyboard.press('Control+x')
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===1")

        page.evaluate("""() => {
          const prose=document.querySelector('.manuscript-block');
          placeCaret(prose, prose.innerText.length);
        }""")
        page.keyboard.press('Control+v')
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===2")
        cut_pasted = page.locator('.manuscript-image-wrap').last
        assert cut_pasted.get_attribute('data-image-width') == '41'
        assert cut_pasted.get_attribute('data-image-footer') == 'Source: archive'
        assert cut_pasted.get_attribute('data-image-description') == 'An archival image.'
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
