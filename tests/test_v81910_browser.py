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


def test_v81910_image_paste_anchor_footer_hover_and_backspace(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, '8.19.10 image interaction polish')
    _set_blocks(base, chapter['id'], ['First paragraph', '\tIndented text'])
    upload = requests.post(base + '/api/assets', files={'file': ('interaction.png', b'interaction-image', 'image/png')}, timeout=10)
    upload.raise_for_status()
    asset = upload.json()['path']

    page, errors = _page(base, scene_browser)
    try:
        # Reproduce the reported paste failure: create a dirty temporary paragraph,
        # keep the caret in it, then insert an image through the same helper used by paste.
        page.evaluate("""() => {
          const prose=document.querySelector('.manuscript-block');
          placeCaret(prose, prose.innerText.length);
        }""")
        page.keyboard.press('Enter')
        page.keyboard.type('Temporary paragraph')
        assert page.locator('.manuscript-block-wrap[data-block-id^="tmp_"]').count() == 1
        page.evaluate("""async (asset) => {
          const anchor=manuscriptImageInsertAnchor();
          await insertManuscriptImageReference({image_path:asset,image_width:5,image_footer:'A comfortably readable footer',image_description:'A tiny transparent image.'},anchor,'Paste manuscript image');
        }""", asset)
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===1")

        wrap = page.locator('.manuscript-image-wrap').first
        image = wrap.locator('.manuscript-image')
        footer = wrap.locator('.manuscript-image-footer')
        image_box = image.bounding_box()
        footer_box = footer.bounding_box()
        assert image_box and footer_box
        assert footer_box['width'] > image_box['width'] * 4

        # Description hover belongs above the actual image, not below it.
        image.hover()
        page.wait_for_timeout(560)
        tip = page.locator('.manuscript-image-description-tooltip')
        tip.wait_for(state='visible')
        tip_box = tip.bounding_box()
        image_box = image.bounding_box()
        assert tip_box and image_box
        assert tip_box['y'] + tip_box['height'] <= image_box['y'] + 1

        # Dragging over prose exposes a black insertion line.
        line_info = page.evaluate("""() => {
          const wrap=[...document.querySelectorAll('.manuscript-block-wrap')].find(row=>row.querySelector('.manuscript-block'));
          const rect=wrap.getBoundingClientRect();
          const dt=new DataTransfer();
          dt.items.add(new File(['x'],'drop.png',{type:'image/png'}));
          wrap.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:rect.left+20,clientY:rect.top+Math.min(12,rect.height/2)}));
          const line=document.querySelector('.manuscript-image-drop-indicator');
          return line?{background:getComputedStyle(line).backgroundColor,width:line.getBoundingClientRect().width}:null;
        }""")
        assert line_info and line_info['background'] == 'rgb(17, 17, 17)'
        assert line_info['width'] > 100
        page.evaluate("hideManuscriptImageDropIndicator()")

        # Backspace from the paragraph immediately after the image removes only the
        # image; the paragraph's leading tab/indentation remains intact.
        page.evaluate("""() => {
          const image=document.querySelector('.manuscript-image-wrap');
          let row=image.nextElementSibling;
          while(row && !row.querySelector?.('.manuscript-block')) row=row.nextElementSibling;
          const prose=row.querySelector('.manuscript-block');
          placeCaret(prose,0);
        }""")
        page.keyboard.press('Backspace')
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===0")
        texts = page.locator('.manuscript-block').all_inner_texts()
        assert any(text.startswith('\tIndented text') for text in texts)

        # A selected/focused image is also directly deletable with Backspace.
        page.evaluate("""async (asset) => {
          const prose=document.querySelector('.manuscript-block');
          placeCaret(prose, prose.innerText.length);
          await insertManuscriptImageReference({image_path:asset,image_width:40,image_footer:'Footer',image_description:'Description'},manuscriptImageInsertAnchor(),'Add manuscript image');
          document.querySelector('.manuscript-image-figure').focus();
        }""", asset)
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===1")
        page.keyboard.press('Backspace')
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===0")
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
