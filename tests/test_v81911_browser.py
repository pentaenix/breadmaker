from __future__ import annotations

import io
import requests
from PIL import Image

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page,
)

_png=io.BytesIO();Image.new('RGBA',(40,20),(40,120,200,96)).save(_png,format='PNG');PNG=_png.getvalue()


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client':'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v81911_readonly_hover_and_end_of_manuscript_drop(scene_live_server, scene_browser):
    base=scene_live_server
    _tab,chapter=_new_project(base,'8.19.11 image completion')
    _set_blocks(base,chapter['id'],['First paragraph','Last paragraph'])
    upload=requests.post(base+'/api/assets',files={'file':('readonly.png',PNG,'image/png')},timeout=10)
    upload.raise_for_status();asset=upload.json()['path']
    detail=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()
    target=detail['blocks'][0]
    inserted=requests.post(base+'/api/manuscript/images/insert',json={
        'image_path':asset,'target_block_id':target['id'],'placement':'after','image_width':33,
        'image_footer':'Hover footer','image_description':'Hover description from read-only view.',
    },timeout=10)
    inserted.raise_for_status()

    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('(id)=>openChapterSplit(id)',chapter['id'])
        image=page.locator('#splitBody .split-image')
        image.wait_for(state='visible')
        image.hover();page.wait_for_timeout(560)
        tip=page.locator('.manuscript-image-description-tooltip')
        tip.wait_for(state='visible')
        assert tip.text_content()=='Hover description from read-only view.'
        tip_box=tip.bounding_box();image_box=image.bounding_box()
        assert tip_box and image_box and tip_box['y']+tip_box['height'] <= image_box['y']+1
        page.evaluate('closeSplitView()')

        result=page.evaluate("""() => {
          const image=document.querySelector('#editor > .manuscript-image-wrap');
          const last=[...document.querySelectorAll('#editor > .manuscript-block-wrap')].at(-1);
          const paper=document.querySelector('#manuscriptPaper');
          const rect=last.getBoundingClientRect();
          const dt=new DataTransfer();
          dt.setData('application/x-breadmaker-manuscript-image',image.dataset.blockId);
          paper.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:rect.left+20,clientY:rect.bottom+40}));
          const line=document.querySelector('.manuscript-image-end-drop-indicator');
          const info=line?{background:getComputedStyle(line).backgroundColor,top:line.getBoundingClientRect().top,lastBottom:rect.bottom}:null;
          paper.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:rect.left+20,clientY:rect.bottom+40}));
          return info;
        }""")
        assert result and result['background']=='rgb(17, 17, 17)'
        assert result['top'] >= result['lastBottom']
        page.wait_for_function("()=>document.querySelector('#editor > .manuscript-block-wrap:last-of-type')?.classList.contains('manuscript-image-wrap')")
        assert page.locator('#editor > .manuscript-block-wrap').last.get_attribute('data-image-path')
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
