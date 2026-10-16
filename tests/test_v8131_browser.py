from __future__ import annotations

import base64
import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page


def test_v8131_drop_opens_temporary_review_and_exit_restores_workspace(scene_live_server,scene_browser):
    base=scene_live_server
    _tab,chapter=_new_project(base,'8.13.1 temporary drop')
    _set_blocks(base,chapter['id'],['temporary review alpha'])
    review=requests.post(base+'/api/reviews',json={'name':'Drop Review','permission_read':True,'permission_comment':True},timeout=10).json()
    participant=requests.post(base+f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'},timeout=10).json()
    requests.post(base+f"/api/reviews/{review['id']}/start",timeout=10).raise_for_status()
    exported=requests.get(base+f"/api/reviews/{review['id']}/participants/{participant['id']}/breadreview",timeout=10);exported.raise_for_status()

    page,errors=_page(base,scene_browser)
    def upload_bridge(payload):
        data=base64.b64decode(payload['base64'])
        response=requests.post(base+payload['url'],headers={str(k):str(v) for k,v in (payload.get('headers') or {}).items()},files={'file':(payload['name'],data,'application/octet-stream')},timeout=20)
        return {'status':response.status_code,'body':response.text,'headers':{k.lower():v for k,v in response.headers.items()}}
    page.expose_function('__breadReviewUploadBridge',upload_bridge)
    def download_bridge(payload):
        response=requests.get(base+payload['url'],headers={str(k):str(v) for k,v in (payload.get('headers') or {}).items()},timeout=20)
        return {'status':response.status_code,'base64':base64.b64encode(response.content).decode('ascii'),'headers':{k.lower():v for k,v in response.headers.items()}}
    page.expose_function('__breadReviewDownloadBridge',download_bridge)
    page.evaluate("""() => {
      const priorFetch=window.fetch;
      window.fetch=async(url,opts={})=>{
        if(opts.body instanceof FormData){
          const file=opts.body.get('file'),bytes=new Uint8Array(await file.arrayBuffer());let binary='';
          for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode(...bytes.subarray(i,i+0x8000));
          const headers={};for(const [k,v] of Object.entries(opts.headers||{}))headers[k]=v;const result=await window.__breadReviewUploadBridge({url:String(url),name:file.name,base64:btoa(binary),headers});
          return {ok:result.status>=200&&result.status<300,status:result.status,headers:{get:n=>result.headers[String(n).toLowerCase()]||null},json:async()=>JSON.parse(result.body||'null'),text:async()=>result.body};
        }
        if(String(url)==='/api/portable-review/package'){
          const headers={};for(const [k,v] of Object.entries(opts.headers||{}))headers[k]=v;const result=await window.__breadReviewDownloadBridge({url:String(url),headers});const binary=atob(result.base64),bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);const blob=new Blob([bytes],{type:'application/octet-stream'});
          return {ok:result.status>=200&&result.status<300,status:result.status,headers:{get:n=>result.headers[String(n).toLowerCase()]||null},blob:async()=>blob,arrayBuffer:async()=>blob.arrayBuffer(),text:async()=>await blob.text()};
        }
        return priorFetch(url,opts);
      };
    }""")
    page.evaluate("setView('tasks',{historyMode:'none'})")
    assert page.locator('#tasksView').evaluate("el=>el.classList.contains('active')") is True
    encoded=base64.b64encode(exported.content).decode('ascii')
    page.evaluate("""encoded => {
      const binary=atob(encoded),bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
      const file=new File([bytes],'rebeca.breadreview',{type:'application/octet-stream'});
      const dt=new DataTransfer();dt.items.add(file);
      window.dispatchEvent(new DragEvent('dragenter',{dataTransfer:dt,bubbles:true,cancelable:true}));
      window.dispatchEvent(new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true}));
      window.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));
    }""",encoded)
    page.locator('#breadReviewDropOpen').wait_for(state='visible')
    page.locator('#breadReviewDropOpen').click()
    page.locator('#reviewerMode').wait_for(state='visible')
    assert 'temporary review alpha' in page.locator('#reviewerPaper').inner_text()
    assert page.locator('#app').is_hidden()
    assert requests.get(base+'/api/portable-review/status',timeout=10).json()['temporary'] is True
    page.locator('#exitReviewerMode').click()
    page.locator('#reviewerMode').wait_for(state='hidden')
    assert page.locator('#app').is_visible()
    assert page.locator('#tasksView').evaluate("el=>el.classList.contains('active')") is True
    assert requests.get(base+'/api/portable-review/status',timeout=10).json()['active'] is False
    assert not errors,errors
