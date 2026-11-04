"""Real file input -> asset upload -> manuscript insertion and reopen."""
import struct
import zlib
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser


def png():
    def chunk(kind,payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\x10\x80\x20\x10\x80\x20'*2))+chunk(b'IEND',b'')


def roundtrip(tmp_path,local):
    with browser(tmp_path,local=local) as (base,page,pid):
        page.get_by_role('button',name='Images',exact=True).click()
        with page.expect_response(lambda r:r.url.endswith('/api/assets') and r.request.method=='POST') as upload:
            page.locator('#manuscriptImageFileInput').set_input_files({'name':'synthetic.png','mimeType':'image/png','buffer':png()})
        assert upload.value.ok
        data=upload.value.json();assert data['path'].startswith('/api/assets/')
        image=page.locator('#editor img.manuscript-image')
        expect(image).to_have_count(1)
        url=urlsplit(image.get_attribute('src'));assert url.path==data['path'];assert parse_qs(url.query)['project_id']==[pid]
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(image).to_have_count(1);assert urlsplit(image.get_attribute('src')).path==data['path']
        page.wait_for_function('()=>{const img=document.querySelector("#editor img.manuscript-image");return img?.complete&&img.naturalWidth===2}')
        fetched=page.request.get(base+data['path'],headers={'X-Breadmaker-Project':pid})
        assert fetched.ok and fetched.body()==png()


def test_hosted_image_upload_insert_and_reopen(tmp_path):roundtrip(tmp_path,False)
def test_local_image_upload_insert_and_reopen(tmp_path):roundtrip(tmp_path,True)
