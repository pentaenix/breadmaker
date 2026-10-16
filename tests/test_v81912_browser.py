from __future__ import annotations

import io
import requests
from PIL import Image

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page


def _png(seed):
    out=io.BytesIO();Image.new('RGBA',(40,28),(30*seed%255,80,160,190)).save(out,format='PNG');return out.getvalue()


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v81912_drag_beside_rows_snap_targets_and_paste_stays_vertical(scene_live_server,scene_browser):
    base=scene_live_server;_tab,chapter=_new_project(base,'8.19.12 image rows');_set_blocks(base,chapter['id'],['Above comparison','Below comparison'])
    assets=[]
    for seed in (1,2):
        response=requests.post(base+'/api/assets',files={'file':(f'row-{seed}.png',_png(seed),'image/png')},timeout=10);response.raise_for_status();assets.append(response.json()['path'])
    detail=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json();first_text=detail['blocks'][0]
    first=requests.post(base+'/api/manuscript/images/insert',json={'image_path':assets[0],'target_block_id':first_text['id'],'placement':'after','image_width':100,'image_footer':'Left'},timeout=10);first.raise_for_status();first_id=first.json()['block_id']
    second=requests.post(base+'/api/manuscript/images/insert',json={'image_path':assets[1],'target_block_id':first_id,'placement':'after','image_width':100,'image_footer':'Right'},timeout=10);second.raise_for_status();second_id=second.json()['block_id']

    page,errors=_page(base,scene_browser)
    try:
        # Drag the second image horizontally onto the first: that alone creates a row.
        info=page.evaluate("""([sourceId,targetId])=>{
          const source=manuscriptImageWrapById(sourceId),target=manuscriptImageWrapById(targetId),figure=target.querySelector('.manuscript-image-figure'),r=figure.getBoundingClientRect();
          selectManuscriptImage(source);const dt=new DataTransfer();dt.setData('application/x-breadmaker-manuscript-image',sourceId);
          target.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.right-3,clientY:r.top+r.height/2}));
          const line=document.querySelector('.manuscript-image-row-drop-indicator');const vertical=!!line&&line.getBoundingClientRect().height>line.getBoundingClientRect().width*4;
          target.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.right-3,clientY:r.top+r.height/2}));return {vertical};
        }""",[second_id,first_id])
        assert info['vertical'] is True
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-row').length===1&&document.querySelectorAll('.manuscript-image-row>.manuscript-image-wrap').length===2")

        # One slider controls the whole row.
        page.evaluate("""() => { const row=document.querySelector('.manuscript-image-row');selectManuscriptImageRow(row);const slider=document.querySelector('#manuscriptImageSize');slider.value='50';slider.dispatchEvent(new Event('input',{bubbles:true}));slider.dispatchEvent(new Event('change',{bubbles:true})); }""")
        widths=page.evaluate("()=>[...document.querySelectorAll('.manuscript-image-row>.manuscript-image-wrap')].map(x=>x.dataset.imageWidth)")
        assert widths==['50','50']
        row_box=page.locator('.manuscript-image-row').bounding_box();paper_box=page.locator('#editor').bounding_box();assert row_box and paper_box
        assert .40 <= row_box['width']/paper_box['width'] <= .60
        page.evaluate('flushSave()')
        page.wait_for_timeout(120)
        persisted=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()['blocks']
        persisted_row=[b for b in persisted if b.get('image_row_id')]
        assert len(persisted_row)==2 and {b['image_width'] for b in persisted_row}=={50}

        # Read-only manuscript surfaces reconstruct the same row instead of flattening it.
        page.evaluate('(id)=>openChapterSplit(id)',chapter['id'])
        page.wait_for_function("()=>document.querySelectorAll('#splitBody .readonly-image-row .split-image-figure').length===2")
        page.evaluate('closeSplitView()')

        # Over prose, even in its middle, drag targeting snaps to before/after only.
        snap=page.evaluate("""(sourceId)=>{const source=manuscriptImageWrapById(sourceId),target=document.querySelector('#editor>.manuscript-block-wrap'),r=target.getBoundingClientRect();const anchor=manuscriptImageDropAnchor({clientX:r.left+r.width/2,clientY:r.top+r.height*.55},target,sourceId);showManuscriptImageDropIndicator(null,target,anchor);const line=document.querySelector('.manuscript-image-drop-indicator');return{placement:anchor.placement,rowPlacement:anchor.placement.startsWith('row_'),horizontal:!!line&&line.getBoundingClientRect().width>line.getBoundingClientRect().height*4}}""",first_id)
        assert snap['placement'] in ('before','after') and snap['rowPlacement'] is False and snap['horizontal'] is True
        page.evaluate('hideManuscriptImageDropIndicator()')

        # Pasting while an image in the row is selected inserts below the whole row, never beside it.
        page.evaluate("""(id)=>{const wrap=manuscriptImageWrapById(id);selectManuscriptImage(wrap);state.manuscriptImageClipboard=manuscriptImageClipboardPayload(wrap);wrap.querySelector('.manuscript-image-figure').focus()}""",first_id)
        page.keyboard.press('Control+V')
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-wrap').length===3")
        structure=page.evaluate("""()=>[...document.querySelector('#editor').children].map(el=>({row:el.classList.contains('manuscript-image-row'),members:el.classList.contains('manuscript-image-row')?el.querySelectorAll(':scope>.manuscript-image-wrap').length:0,image:el.classList.contains('manuscript-image-wrap')}))""")
        assert any(item['row'] and item['members']==2 for item in structure)
        row_index=next(i for i,item in enumerate(structure) if item['row'])
        assert structure[row_index+1]['image'] is True

        # Drag one member out onto prose; the row collapses and the image becomes standalone.
        page.evaluate("""(sourceId)=>{const source=manuscriptImageWrapById(sourceId),target=document.querySelector('#editor>.manuscript-block-wrap'),r=target.getBoundingClientRect();selectManuscriptImage(source);const dt=new DataTransfer();dt.setData('application/x-breadmaker-manuscript-image',sourceId);target.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.left+20,clientY:r.top+1}))}""",second_id)
        page.wait_for_function("()=>document.querySelectorAll('.manuscript-image-row').length===0")
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
