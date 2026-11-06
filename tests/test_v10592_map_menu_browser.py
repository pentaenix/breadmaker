"""Optional Populate reads cannot delay independent Maps commands."""
from time import perf_counter
from playwright.sync_api import expect
from tests.readable_export_browser_fixture import browser


def _map(page,with_line=False):
    return page.evaluate("""async(withLine)=>{
      const map=await api('/api/maps',{method:'POST',body:JSON.stringify({name:'Synthetic menu Map'})});
      let line=null;
      if(withLine)line=await api(`/api/maps/${map.id}/lines`,{method:'POST',body:JSON.stringify({name:'Manual line',color:'#557788',provenance:'manual',layout_locked:false})});
      await refresh(false);setView('maps');await selectMap(map.id);
      return {map:map.id,line:line?.id};
    }""",with_line)


def test_empty_and_rail_map_menus_show_commands_before_populate_and_fence_late_reply(tmp_path):
    with browser(tmp_path,local=False) as (_,page,_):
        _map(page);held=[];page.route('**/api/map-populate*',lambda route:held.append(route))
        start=perf_counter();page.locator('#mapEmpty').click(button='right')
        expect(page.locator('#contextMenu')).to_be_visible(timeout=500)
        expect(page.locator('#contextMenu').get_by_role('button',name='New Line',exact=True)).to_be_enabled()
        print(f'Empty Map menu before Populate acknowledgement: {(perf_counter()-start)*1000:.1f}ms')
        assert len(held)==1
        page.keyboard.press('Escape');page.evaluate("()=>openContextMenu(30,100,[{label:'Newer menu',action:()=>{}}])")
        held[0].continue_();page.wait_for_function('()=>!!state.mapPopulateCatalog')
        expect(page.locator('#contextMenu')).to_contain_text('Newer menu')
        page.keyboard.press('Escape');held.clear()
        page.locator('.map-tree-more').first.click()
        expect(page.locator('#contextMenu').get_by_role('button',name='New Line',exact=True)).to_be_visible(timeout=500)
        assert len(held)==1
        held[0].fulfill(status=503,json={'detail':'Synthetic unavailable catalog'})
        expect(page.locator('#contextMenu')).to_contain_text('Populate is unavailable')
        expect(page.locator('#contextMenu').get_by_role('button',name='New Line',exact=True)).to_be_enabled()


def test_map_route_and_background_commands_do_not_wait_for_catalog(tmp_path,local=False):
    with browser(tmp_path,local=local) as (_,page,_):
        _map(page,with_line=True);held=[];page.route('**/api/map-populate*',lambda route:held.append(route))
        route=page.locator('[data-map-line-route]').first
        point=route.evaluate('(el)=>{const p=el.getPointAtLength(el.getTotalLength()/2),s=new DOMPoint(p.x,p.y).matrixTransform(el.getScreenCTM());return{x:s.x,y:s.y}}')
        page.mouse.click(point['x'],point['y'],button='right')
        expect(page.locator('#contextMenu').get_by_role('button',name='Details…',exact=True)).to_be_visible(timeout=500)
        assert not held,'Line menu does not use Populate; it must not fetch that catalog'
        page.keyboard.press('Escape')
        svg=page.locator('#mapWorld svg').first
        box=svg.bounding_box()
        page.mouse.click(box['x']+20,box['y']+20,button='right')
        expect(page.locator('#contextMenu').get_by_role('button',name='New Line',exact=True)).to_be_visible(timeout=500)
        assert len(held)==1
        held[0].continue_()
        page.wait_for_function('()=>!!state.mapPopulateCatalog')


def test_local_map_route_and_background_commands_are_immediate(tmp_path):
    test_map_route_and_background_commands_do_not_wait_for_catalog(tmp_path,local=True)
