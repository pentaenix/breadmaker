"""Create from the committed empty Map without redundant hydration requests."""
import re
from playwright.sync_api import expect

from tests.test_v10569_narration_browser import browser
from tests.test_v10565_browser import first_workspace
from tests.test_v10548_browser import hosted_browser


def _creation_contract(page):
    page.evaluate('''()=>{
      const original=api;window.__mapCreationCalls=[];
      api=async(path,options={})=>{
        window.__mapCreationCalls.push({path,method:options.method||'GET'});
        return original(path,options);
      };
    }''')
    ids=[]
    for index in range(2):
        name=f'SYNTHETIC fast Map {index+1}'
        page.evaluate('()=>{window.__mapCreationCalls=[];void newMapDialog()}')
        page.locator('#dialogText').fill(name)
        page.locator('#dialogConfirm').click()
        expect(page.locator('#mapCurrentName')).to_have_text(name)
        expect(page.locator('#toast')).to_have_text('Map created')
        page.wait_for_function('()=>!state.mapViewportRestoring')
        current=page.evaluate('''()=>({id:state.currentMap.id,
          lines:state.mapData.lines,junctions:state.mapData.junctions,
          cached:state.mapTreeCache.get(state.currentMap.id).map.id})''')
        ids.append(current['id'])
        assert current=={'id':ids[-1],'lines':[],'junctions':[],'cached':ids[-1]}
        calls=page.evaluate('()=>window.__mapCreationCalls')
        reads=[row['path'] for row in calls if row['method']=='GET' and row['path'].startswith('/api/maps/')]
        hosted=page.evaluate('()=>runtimeSupports("hosted_persistence")')
        assert reads==([] if hosted else [f'/api/maps/{ids[-1]}']),calls
        assert not [row for row in calls if row['method']=='GET' and
            row['path'].startswith('/api/ui-state/map-viewport-')],calls
        writes=[row['path'] for row in calls if row['method']=='POST']
        assert len(writes)==3,writes
        assert writes[0].startswith('/api/undo/begin?')
        assert writes[1]=='/api/maps'
        assert writes[2].startswith('/api/undo/finish/')
        # A subsequent Map must still preserve the previous one's viewport.
        if index==0:
            page.evaluate('()=>{state.mapZoom=.73}')
    assert ids[0]!=ids[1]
    page.evaluate('''async id=>{
      state.mapZoom=1.2;window.__mapCreationCalls=[];
      await selectMap(id);
    }''',ids[0])
    page.wait_for_function('()=>!state.mapViewportRestoring')
    assert page.evaluate('()=>state.mapZoom')==.73
    calls=page.evaluate('()=>window.__mapCreationCalls')
    assert any(row['method']=='GET' and row['path']==f'/api/ui-state/map-viewport-{ids[0]}' for row in calls)
    # Keep the durable structural transaction: Undo removes only the second Map,
    # and Redo restores it with the same identity.
    page.evaluate('async()=>await appUndo()')
    assert page.evaluate('()=>state.maps.map(row=>row.id)')==[ids[0]]
    page.evaluate('async()=>await appRedo()')
    assert page.evaluate('()=>state.maps.map(row=>row.id)')==ids
    return ids


def _hosted_map_creation_reuses_commit_and_preserves_undo_and_viewport(tmp_path,width):
    with first_workspace(tmp_path,'maps') as (_base,page,_pid,_errors):
        page.set_viewport_size({'width':width,'height':900})
        ids=_creation_contract(page)
        expect(page.locator('#mapViewBtn')).to_be_visible()
        expect(page.locator('#editMapDetailsBtn')).to_be_visible()
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('#mapsView')).to_have_class(re.compile(r'\bactive\b'))
        page.wait_for_function('()=>state.maps.length===2')
        assert page.evaluate('()=>state.maps.map(row=>row.id)')==ids


def test_hosted_map_creation_reuses_commit_at_1400px(tmp_path):_hosted_map_creation_reuses_commit_and_preserves_undo_and_viewport(tmp_path,1400)
def test_hosted_map_creation_reuses_commit_at_900px(tmp_path):_hosted_map_creation_reuses_commit_and_preserves_undo_and_viewport(tmp_path,900)
def test_hosted_map_creation_reuses_commit_at_430px(tmp_path):_hosted_map_creation_reuses_commit_and_preserves_undo_and_viewport(tmp_path,430)


def test_local_map_creation_reuses_commit_and_preserves_undo_and_viewport(tmp_path):
    # Own the complete driver lifecycle: a module-scoped browser must not leave
    # a running Playwright event loop across later hosted test contexts.
    with browser(tmp_path,local=True) as (_base,page,_pid):
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.evaluate("""async()=>await api('/api/chapters',{method:'POST',body:JSON.stringify({
          title:'SYNTHETIC ruler chapter',tab_id:state.documents.find(doc=>doc.is_manuscript).id})})""")
        page.locator('.main-tab[data-view="maps"]').click()
        _creation_contract(page)
        expect(page.locator('#mapSvg')).to_contain_text('SYNTHETIC ruler chapter')
        assert not errors,errors


def test_map_creation_does_not_publish_unfinished_save(tmp_path):
    with hosted_browser(tmp_path) as (_base,page):
        page.goto(_base,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.locator('.main-tab[data-view="maps"]').click()
        # Fail after the create committed, so the real journal must roll it back.
        page.route('**/api/undo/finish/*',lambda route:route.fulfill(
            status=503,content_type='application/json',body='{"detail":"Synthetic finish failure"}'))
        page.evaluate('()=>{void newMapDialog()}')
        page.locator('#dialogText').fill('SYNTHETIC failed Map')
        page.locator('#dialogConfirm').click()
        page.evaluate('async()=>await state.structuralChain')
        expect(page.locator('#mapCurrentName')).to_have_text('Maps')
        assert page.evaluate('()=>state.maps.length')==0
        assert page.evaluate('()=>state.mapTreeCache.size')==0
        assert page.evaluate('async()=>await api("/api/maps")')=={'maps':[]}
