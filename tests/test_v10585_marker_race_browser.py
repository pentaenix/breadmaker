"""Committed marker creation cannot overwrite the next action's UI state."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser


def setup(page):
    page.evaluate("""async()=>{
      const c=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:'Synthetic marker race',canvas_type:'blank'})});
      await refresh(false);setView('canvas');await selectCanvas(c.id);setLegendDrawerOpen(true,{persist:false});await openLegendPanel(false);
    }""")


def _exercise(tmp_path,local,engine,interrupt=False):
    width=1024 if local else 390
    with browser(tmp_path,local=local,engine=engine,page_options={'viewport':{'width':width,'height':844}}) as (_,page,_):
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        text=page.locator('.manuscript-block').first.inner_text();setup(page)
        page.evaluate("""interrupt=>{
          const fetch=window.fetch;let armed=false,delayed=false;
          window.markerRace={pending:0,label:false,icon:false,held:false};
          window.fetch=async(url,options={})=>{
            const response=await fetch(url,options);
            if(String(url).startsWith('/api/labels')&&options.method==='POST'){
              armed=true;window.markerRace.label=true;
              if(interrupt){window.markerRace.held=true;await new Promise(resolve=>window.releaseMarkerSave=resolve)}
            }
            if(String(url).startsWith('/api/icon-markers')&&options.method==='POST'){armed=false;window.markerRace.icon=true}
            if(armed&&!delayed&&String(url).startsWith('/api/bootstrap')){
              delayed=true;window.markerRace.pending++;await new Promise(resolve=>setTimeout(resolve,800));window.markerRace.pending--;
            }
            return response;
          };
        }""",interrupt)
        page.locator('#newCanvasColorMarker').click();page.locator('#markerName').fill('Synthetic color')
        page.locator('#markerColorText').fill('#6688aa')
        box=page.locator('#createMarkerConfirm').bounding_box();assert box and 0<=box['x'] and box['x']+box['width']<=width
        page.locator('#createMarkerConfirm').click()
        if interrupt:
            page.wait_for_function('()=>window.markerRace.held')
            expect(page.locator('#createMarkerConfirm')).to_be_disabled()
            page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_hidden()
            page.locator('#newCanvasIconMarker').click();page.locator('#iconMarkerName').fill('Synthetic icon')
            page.locator('#iconMarkerIcon').fill('★');page.evaluate('()=>window.releaseMarkerSave()')
            expect(page.locator('.legend-drawer-entry').filter(has_text='Synthetic color')).to_have_count(1)
            expect(page.locator('#iconMarkerName')).to_have_value('Synthetic icon')
        else:
            expect(page.locator('#modal')).to_be_hidden()
            page.locator('#newCanvasIconMarker').click();page.locator('#iconMarkerName').fill('Synthetic icon')
            page.locator('#iconMarkerIcon').fill('★')
        page.locator('#createIconMarkerConfirm').click();expect(page.locator('#modal')).to_be_hidden()
        page.wait_for_function('()=>window.markerRace.label&&window.markerRace.icon&&window.markerRace.pending===0')
        assert page.evaluate("()=>({colors:state.markers.map(m=>m.name),icons:state.iconMarkers.map(m=>m.name)})")=={'colors':['Synthetic color'],'icons':['Synthetic icon']}
        expect(page.locator('.legend-drawer-entry').filter(has_text='Synthetic color')).to_have_count(1)
        expect(page.locator('.legend-drawer-entry').filter(has_text='Synthetic icon')).to_have_count(1)
        page.evaluate('async()=>await appUndo()')
        assert page.evaluate("()=>state.markers.some(m=>m.name==='Synthetic color')&&!state.iconMarkers.some(m=>m.name==='Synthetic icon')")
        page.evaluate('async()=>await appRedo()')
        assert page.evaluate("()=>state.markers.some(m=>m.name==='Synthetic color')&&state.iconMarkers.some(m=>m.name==='Synthetic icon')")
        assert page.locator('.manuscript-block').first.inner_text()==text
        assert not errors,errors


def test_marker_then_icon_survives_delayed_bootstrap_local(tmp_path):_exercise(tmp_path,True,'chromium')
def test_marker_then_icon_survives_delayed_bootstrap_hosted_phone_webkit(tmp_path):_exercise(tmp_path,False,'webkit')
def test_marker_completion_does_not_close_next_modal_local(tmp_path):_exercise(tmp_path,True,'chromium',True)
def test_marker_completion_does_not_close_next_modal_hosted_phone_webkit(tmp_path):_exercise(tmp_path,False,'webkit',True)
