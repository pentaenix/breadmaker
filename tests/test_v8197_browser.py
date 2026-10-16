from __future__ import annotations

import base64
import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _page, _shutdown_page,
)

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X7xLAAAAAElFTkSuQmCC'
)


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': 'v41812-browser'}, timeout=2)
    except Exception:
        pass


def test_v8197_checkpoint_split_renders_real_image_metadata(scene_live_server, scene_browser):
    base=scene_live_server
    _tab,chapter=_new_project(base,'8.19.7 checkpoint image comparison')
    detail=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()
    asset=requests.post(base+'/api/assets',files={'file':('compare.png',PNG,'image/png')},timeout=10).json()['path']
    inserted=requests.post(base+'/api/manuscript/images/insert',json={
        'image_path':asset,'target_block_id':detail['blocks'][0]['id'],'placement':'after',
        'image_width':41,'image_footer':'Checkpoint footer','image_description':'Checkpoint description',
    },timeout=10).json()
    cp=requests.post(base+'/api/checkpoints',json={
        'scope':'chapter','chapter_id':chapter['id'],'name':'Image baseline'
    },timeout=10).json()

    current=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()['blocks']
    payload=[]
    for block in current:
        row={
            'id':block['id'],'block_type':block['block_type'],'semantic_style':block['semantic_style'],
            'content_html':block['content_html'],'text_content':block['text_content'],
            'image_path':block['image_path'],'scene_id':block['scene_id'],
        }
        if str(block['id'])==str(inserted['block_id']):
            row.update(image_width=76,image_footer='Current footer',image_description='Current description')
        elif block['block_type']=='image':
            row.update(image_width=block['image_width'],image_footer=block['image_footer'],image_description=block['image_description'])
        payload.append(row)
    response=requests.put(base+f'/api/chapters/{chapter["id"]}/blocks',json={'blocks':payload},timeout=10)
    response.raise_for_status()

    page,errors=_page(base,scene_browser)
    try:
        page.evaluate("id => selectChapter(id,false)",chapter['id'])
        page.wait_for_function("id => state.viewMode==='chapter' && Number(state.chapter?.id)===Number(id)",arg=chapter['id'])
        page.evaluate("id => openCheckpointSplit(id)",cp['id'])
        page.wait_for_selector('#splitBody .checkpoint-image-figure')
        figure=page.locator('#splitBody .checkpoint-image-figure').first
        assert '41%' in (figure.get_attribute('style') or '')
        assert figure.locator('.checkpoint-image').get_attribute('alt')=='Checkpoint description'
        assert figure.locator('figcaption').text_content()=='Checkpoint footer'
        assert page.locator('#splitBody .checkpoint-compare-row.changed .checkpoint-image-figure').count()==1
        assert not errors,errors
    finally:
        _release(base)
        _shutdown_page(page)
