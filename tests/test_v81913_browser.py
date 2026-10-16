from __future__ import annotations

import io
import requests
from PIL import Image

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page


def _png():
    out=io.BytesIO();Image.new('RGBA',(48,32),(80,120,200,160)).save(out,format='PNG');return out.getvalue()


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v81913_image_resize_previews_without_saving_until_release(scene_live_server,scene_browser):
    base=scene_live_server;_tab,chapter=_new_project(base,'8.19.13 resize commit safety');_set_blocks(base,chapter['id'],['Resize below'])
    upload=requests.post(base+'/api/assets',files={'file':('resize.png',_png(),'image/png')},timeout=10);upload.raise_for_status();asset=upload.json()['path']
    target=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()['blocks'][0]
    inserted=requests.post(base+'/api/manuscript/images/insert',json={'image_path':asset,'target_block_id':target['id'],'placement':'after','image_width':100},timeout=10);inserted.raise_for_status();block_id=inserted.json()['block_id']

    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('(id)=>selectManuscriptImage(manuscriptImageWrapById(id))',block_id)
        # Begin a real resize interaction, move several times, then wait longer than the
        # normal 700 ms autosave debounce. The preview must move, but persistence must not.
        page.evaluate("""() => {
          const slider=document.querySelector('#manuscriptImageSize');
          slider.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:1,pointerType:'mouse'}));
          for(const value of ['90','70','42']){slider.value=value;slider.dispatchEvent(new Event('input',{bubbles:true}))}
        }""")
        page.wait_for_timeout(900)
        assert page.locator('#manuscriptImageSizeValue').inner_text()=='42%'
        persisted=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()['blocks']
        image=next(b for b in persisted if b['id']==block_id)
        assert image['image_width']==100
        dirty=page.evaluate("()=>({dirty:state.dirty,chapters:[...state.dirtyChapters]})")
        assert dirty=={'dirty':False,'chapters':[]}

        # Releasing/committing the range control creates exactly one dirty generation;
        # the ordinary autosave then persists the final preview value.
        before=page.evaluate('()=>state.editGeneration')
        page.evaluate("""() => {
          const slider=document.querySelector('#manuscriptImageSize');
          slider.dispatchEvent(new Event('change',{bubbles:true}));
          slider.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerId:1,pointerType:'mouse'}));
        }""")
        after=page.evaluate('()=>state.editGeneration')
        assert after==before+1
        page.wait_for_function("()=>!state.dirty&&!state.dirtyChapters.size",timeout=4000)
        persisted=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()['blocks']
        image=next(b for b in persisted if b['id']==block_id)
        assert image['image_width']==42
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
