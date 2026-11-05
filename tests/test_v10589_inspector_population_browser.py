"""Real disposable stores with delayed requests; no audio."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10581_canvas_navigation_browser import _board


def exercise(home,local,engine="chromium",width=1280):
    with browser(home,local=local,engine=engine,page_options={"viewport":{"width":width,"height":844}}) as (_,page,_):
        ids,errors=_board(page)
        held=[]
        page.route('**/api/references?source_type=node&source_id=*',lambda route:held.append(route))
        page.evaluate('(id)=>{state.selectedNodes=new Set([id]);void openNodeInspector(id)}',ids['a'])
        expect(page.locator('#nodeShape')).to_be_visible()
        expect(page.locator('#nodeInspectorReferences')).to_have_attribute('aria-busy','true')
        page.screenshot(path=str(home/'immediate-inspector.png'))
        page.wait_for_function('()=>document.querySelector("#nodeShape")!==null')
        page.evaluate('(id)=>{state.selectedNodes=new Set([id]);void openNodeInspector(id)}',ids['b'])
        expect(page.locator('#nodeShape')).to_be_visible()
        # Deliver the old selection after the new inspector has opened.
        assert len(held)==2
        held[0].continue_()
        held[1].fulfill(status=503,content_type='application/json',body='{"detail":"synthetic failure"}')
        expect(page.locator('#nodeInspectorReferences')).to_contain_text('Could not load.')
        assert page.locator('#nodeShape').is_enabled()
        page.unroute('**/api/references?source_type=node&source_id=*')
        page.locator('#nodeInspectorReferences').get_by_role('button',name='Retry').click()
        expect(page.locator('#nodeInspectorReferences')).not_to_have_attribute('aria-busy','true')
        assert not errors,errors


def test_immediate_canvas_inspector_local(tmp_path):exercise(tmp_path,True)
def test_immediate_canvas_inspector_hosted(tmp_path):exercise(tmp_path,False)
def test_immediate_canvas_inspector_hosted_phone_webkit(tmp_path):exercise(tmp_path,False,"webkit",390)


def test_map_population_has_busy_feedback_and_no_duplicate_submission(tmp_path):
    with browser(tmp_path,local=True) as (_,page,_):
        info=page.evaluate('''async()=>{const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Population feedback'})});await refresh(false);setView('maps');await selectMap(map.id);const catalog=await loadMapPopulateCatalog();return {id:map.id,template:catalog.categories.flatMap(c=>c.templates).find(t=>t.item_count>0).id}}''')
        held=[];page.route('**/api/maps/*/populate',lambda route:held.append(route))
        with page.expect_request('**/api/maps/*/populate'):
            page.evaluate('(info)=>{window.populationDone=false;void populateMap(info.id,info.template,"Map structure").finally(()=>populationDone=true)}',info)
        expect(page.locator('#mapPopulationProgress')).to_be_visible()
        assert page.locator('#modalClose').is_disabled()
        page.keyboard.press('Escape');expect(page.locator('#mapPopulationProgress')).to_be_visible()
        page.screenshot(path=str(tmp_path/'maps-creating.png'))
        page.evaluate('(info)=>void populateMap(info.id,info.template,"Map structure")',info)
        assert len(held)==1
        held[0].continue_()
        page.wait_for_function('()=>populationDone')
        expect(page.locator('#modal')).to_be_hidden()
        assert page.evaluate('()=>state.mapData.lines.length')>0

        page.unroute('**/api/maps/*/populate')
        before=page.evaluate('()=>JSON.stringify(state.mapData.lines)')
        page.route('**/api/maps/*/populate',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"synthetic population failure"}'))
        page.evaluate('(info)=>{window.populationFailed=false;void populateMap(info.id,info.template,"Map structure").catch(()=>populationFailed=true)}',info)
        page.wait_for_function('()=>populationFailed')
        expect(page.locator('#modal')).to_be_hidden()
        assert page.evaluate('()=>mapPopulationBusy') is False
        assert page.evaluate('()=>JSON.stringify(state.mapData.lines)')==before
