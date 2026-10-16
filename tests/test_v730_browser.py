from __future__ import annotations

import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

CLIENT='v41812-browser'


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':CLIENT},timeout=2)
    except Exception: pass


def _install_indexeddb_compatible_store(page):
    # The CI browser is not allowed to navigate directly to localhost and the shared
    # Breadmaker browser harness therefore uses set_content(). That has an opaque
    # origin where native IndexedDB is unavailable. Install the minimal IndexedDB
    # transaction contract Breadmaker uses so we can still exercise the complete
    # application journal/save/recovery behavior deterministically.
    page.evaluate("""()=>{
      const rows=new Map(); window.__v730CrashRows=rows;
      const makeTx=()=>{
        const tx={oncomplete:null,onerror:null,error:null};
        tx.objectStore=()=>({
          put(entry){rows.set(String(entry.key),structuredClone(entry));queueMicrotask(()=>tx.oncomplete?.());},
          delete(key){rows.delete(String(key));queueMicrotask(()=>tx.oncomplete?.());},
          getAll(){const req={result:null,error:null,onsuccess:null,onerror:null};queueMicrotask(()=>{req.result=[...rows.values()].map(x=>structuredClone(x));req.onsuccess?.()});return req;}
        });
        return tx;
      };
      const shim={open(){const req={result:null,error:null,onsuccess:null,onerror:null,onupgradeneeded:null};queueMicrotask(()=>{const db={objectStoreNames:{contains:()=>true},createObjectStore(){},transaction(){return makeTx()},close(){}};req.result=db;req.onsuccess?.()});return req;}};
      Object.defineProperty(window,'indexedDB',{value:shim,configurable:true});
    }""")


def test_v730_unsaved_browser_text_is_journaled_then_cleared_after_confirmed_sqlite_save(scene_live_server,scene_browser):
    base=scene_live_server; _tab,chapter=_new_project(base,'7.3 crash journal')
    page,errors=_page(base,scene_browser)
    try:
        _install_indexeddb_compatible_store(page)
        prose=page.locator('.manuscript-block').first
        prose.fill('words that exist in the crash journal first')
        page.wait_for_timeout(140)
        history=page.evaluate("async()=>await api('/api/history/v2')")
        assert any(r.get('label')=='Before editing session' and r.get('cause')=='safety' for r in history.get('revisions',[]))
        rows=page.evaluate("async()=>await crashJournalForProject(currentProjectIdentity())")
        assert rows and 'crash journal first' in rows[0]['blocks'][0]['text_content']
        page.evaluate('flushSave()')
        page.wait_for_function("async()=>!(await crashJournalForProject(currentProjectIdentity())).length")
        committed=requests.get(base+f"/api/chapters/{chapter['id']}",timeout=10).json()
        assert 'crash journal first' in committed['blocks'][0]['text_content']
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v730_stale_browser_save_preserves_draft_and_never_overwrites_newer_committed_text(scene_live_server,scene_browser):
    base=scene_live_server; _tab,chapter=_new_project(base,'7.3 stale browser')
    page,errors=_page(base,scene_browser)
    try:
        _install_indexeddb_compatible_store(page)
        loaded=requests.get(base+f"/api/chapters/{chapter['id']}",timeout=10).json()
        newer=requests.put(base+f"/api/chapters/{chapter['id']}/blocks",headers={'X-Breadmaker-Client':CLIENT},json={
            'expected_revision':loaded['revision'],
            'blocks':[{'text_content':'newer committed server copy','content_html':'newer committed server copy'}],
        },timeout=10)
        assert newer.status_code==200,newer.text
        # Browser still holds the prior revision. Its next autosave must fail closed.
        page.locator('.manuscript-block').first.fill('older browser draft must survive')
        page.wait_for_function("()=>!document.querySelector('#modal').classList.contains('hidden')",timeout=5000)
        assert page.locator('#modalTitle').inner_text()=='Newer manuscript version exists'
        committed=requests.get(base+f"/api/chapters/{chapter['id']}",timeout=10).json()
        assert committed['blocks'][0]['text_content']=='newer committed server copy'
        rows=page.evaluate("async()=>await crashJournalForProject(currentProjectIdentity())")
        assert rows and 'older browser draft must survive' in rows[0]['blocks'][0]['text_content']
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
