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


def test_v8196_cut_paste_uses_latest_image_inspector_metadata(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, '8.19.6 image cut metadata')
    _set_blocks(base, chapter['id'], ['Before image', 'After image'])
    detail = requests.get(base + f'/api/chapters/{chapter["id"]}', timeout=10).json()
    upload = requests.post(base + '/api/assets', files={'file': ('clip.png', b'cut-metadata-image', 'image/png')}, timeout=10)
    upload.raise_for_status()
    insert = requests.post(base + '/api/manuscript/images/insert', json={
        'image_path': upload.json()['path'],
        'target_block_id': detail['blocks'][0]['id'],
        'placement': 'after',
        'image_width': 100,
        'image_footer': '',
        'image_description': '',
    }, timeout=10)
    insert.raise_for_status()

    page, errors = _page(base, scene_browser)
    try:
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===1")
        page.evaluate("() => document.querySelector('.manuscript-image-figure').click()")

        # Edit all occurrence metadata immediately before cutting the selected image.
        page.locator('#manuscriptImageFooter').fill('Source: restored archive')
        page.locator('#manuscriptImageDescription').fill('A restored archival photograph.')
        page.locator('#manuscriptImageSize').evaluate("(el)=>{el.value='37';el.dispatchEvent(new Event('input',{bubbles:true}))}")
        # Return focus to the image so Ctrl/Cmd+X means cut the image block, not text in an inspector field.
        page.evaluate("() => document.querySelector('.manuscript-image-figure').focus()")
        page.keyboard.press('Control+x')
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===0")

        page.evaluate("""() => {
          const prose=document.querySelector('.manuscript-block');
          placeCaret(prose, prose.innerText.length);
        }""")
        page.keyboard.press('Control+v')
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===1")
        pasted = page.locator('.manuscript-image-wrap').first
        assert pasted.get_attribute('data-image-width') == '37'
        assert pasted.get_attribute('data-image-footer') == 'Source: restored archive'
        assert pasted.get_attribute('data-image-description') == 'A restored archival photograph.'
        assert pasted.locator('.manuscript-image-footer').text_content() == 'Source: restored archive'
        assert pasted.locator('.manuscript-image').get_attribute('alt') == 'A restored archival photograph.'
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
