"""Real settings actions, CSS highlights and existing marker navigation."""
import pytest
import re
from playwright.sync_api import expect
from tests.test_v41812_browser import (scene_live_server,scene_browser,
    _new_project,_set_blocks,_page)


@pytest.fixture(autouse=True)
def release_test_pages(scene_browser):
    yield
    for context in scene_browser.contexts:
        for page in context.pages:
            page.evaluate("async()=>{await api('/api/lease/release',{method:'POST'});await window.__v41812Shutdown()}")
            page.close()


def test_settings_search_highlights_unicode_and_supports_undo(scene_live_server,scene_browser):
    base=scene_live_server
    _,chapter=_new_project(base,'English editing browser')
    _set_blocks(base,chapter['id'],['😀 She walked quietly.','A friendly family left hurriedly.'])
    page,errors=_page(base,scene_browser)
    page.evaluate('()=>openExperimentalFeatures()')
    expect(page.locator('[data-editing-search]')).to_have_count(0)
    page.locator('#experimentalEditorHighlights').click()
    expect(page.locator('#modalTitle')).to_have_text('Editor Highlights')
    page.locator('#modalClose').click()
    expect(page.locator('#modalTitle')).to_have_text('Experimental Features')
    page.locator('#experimentalEditorHighlights').click()
    expect(page.locator('[data-editing-search]')).to_have_count(7)
    recommended=page.evaluate('()=>suggestMarkerColor()')
    page.locator('[data-editing-search="adverbs"]').click()
    expect(page.locator('#editingSearchStatus')).to_have_text('2 matches marked with -ly adverbs.')
    expect(page.locator('[data-editing-search="adverbs"]')).to_be_visible()
    assert page.evaluate('()=>state.markers[0].color')==recommended
    assert page.evaluate("()=>[...CSS.highlights].filter(([name])=>name.startsWith('marker-')).flatMap(([,h])=>[...h].map(r=>r.toString()))")==['quietly','hurriedly']
    page.locator('#modalClose').click()
    expect(page.locator('#modalTitle')).to_have_text('Experimental Features')
    page.locator('#modalClose').click()
    page.evaluate('()=>navigateMarker(state.markers[0].id)')
    assert page.evaluate("()=>[...CSS.highlights.get('breadmaker-find-current')].map(r=>r.toString())")==['quietly']
    page.evaluate('()=>navigateFind(1)')
    assert page.evaluate('()=>state.find.index')==1
    assert page.evaluate("()=>[...CSS.highlights.get('breadmaker-find-current')].map(r=>r.toString())")==['hurriedly']
    page.evaluate('()=>appUndo()')
    page.wait_for_function('()=>state.markers.length===0')
    assert page.locator('.manuscript-block').all_text_contents()==['😀 She walked quietly.','A friendly family left hurriedly.']
    assert not errors


def test_settings_no_matches_and_rerun_reuse(scene_live_server,scene_browser):
    base=scene_live_server
    _,chapter=_new_project(base,'English editing rerun')
    _set_blocks(base,chapter['id'],['I realised she hurriedly left.'])
    page,errors=_page(base,scene_browser)
    page.evaluate('()=>openExperimentalFeatures()')
    page.locator('#experimentalEditorHighlights').click()
    page.locator('[data-editing-search="emotions"]').click()
    expect(page.locator('#editingSearchStatus')).to_have_text('No matches found.')
    page.locator('[data-editing-search="telling"]').click()
    expect(page.locator('#editingSearchStatus')).to_have_text('1 match marked with Telling words.')
    first=page.evaluate('()=>state.markers[0].id')
    page.locator('[data-editing-search="telling"]').click()
    page.get_by_role('button',name='Recheck',exact=True).click()
    expect(page.locator('[data-editing-search="telling"]')).to_be_enabled()
    assert page.evaluate('()=>state.markers.map(m=>m.id)')==[first]
    recommended=page.evaluate('()=>suggestMarkerColor()')
    page.locator('[data-editing-search="adverbs"]').click()
    expect(page.locator('#editingSearchStatus')).to_have_text('1 match marked with -ly adverbs.')
    assert page.evaluate("()=>state.markers.find(m=>m.name==='-ly adverbs').color")==recommended
    assert page.evaluate('()=>new Set(state.markers.map(m=>m.color)).size')==2
    expect(page.locator('#modalTitle')).to_have_text('Editor Highlights')
    assert not errors


def test_marker_menu_hide_show_preserves_highlights_and_is_project_scoped(scene_live_server,scene_browser):
    import requests
    base=scene_live_server
    tab,chapter=_new_project(base,'Marker visibility browser')
    _set_blocks(base,chapter['id'],['quietly and softly'])
    result=requests.post(base+'/api/annotations/editing-search',json={'tab_id':tab['id'],'scan':'adverbs'},timeout=5).json()
    page,errors=_page(base,scene_browser)
    before=requests.get(base+'/api/annotations',timeout=5).json()
    page.evaluate('()=>navigateMarker(state.markers[0].id)')
    page.locator('#markupBtn').click()
    page.locator('[data-marker-edit]').click()
    page.get_by_role('button',name='Hide highlights',exact=True).click()
    expect(page.locator('[data-marker-row]')).to_have_class(re.compile('highlights-hidden'))
    assert page.locator('[data-marker-row] .color-chip').evaluate('(chip)=>getComputedStyle(chip).filter')=='grayscale(1)'
    expect(page.locator('[data-marker-edit]')).to_be_enabled()
    assert page.evaluate("()=>![...CSS.highlights.keys()].some(n=>n.startsWith('marker-')||n.startsWith('breadmaker-find-'))")
    expect(page.locator('.markup-gutter-swatch:visible')).to_have_count(0)
    page.locator('[data-marker-edit]').click()
    expect(page.get_by_role('button',name='Show highlights',exact=True)).to_be_visible()
    assert requests.get(base+'/api/annotations',timeout=5).json()==before
    assert page.evaluate('()=>{markerVisibilityKey=null;return markerIsHidden(state.markers[0].id)}')
    # The saved preference remains scoped to this project, not numeric marker IDs.
    assert page.evaluate("()=>{const id=state.markers[0].id,pid=state.workspace.active_project_id;state.workspace.active_project_id='another-project';const hidden=markerIsHidden(id);state.workspace.active_project_id=pid;return !hidden&&markerIsHidden(id)}")
    page.get_by_role('button',name='Show highlights',exact=True).click()
    assert 'highlights-hidden' not in page.locator('[data-marker-row]').get_attribute('class')
    assert page.locator('[data-marker-row] .color-chip').evaluate('(chip)=>getComputedStyle(chip).filter')=='none'
    expect(page.locator('.markup-gutter-swatch:visible')).to_have_count(1)
    assert page.evaluate("()=>[...CSS.highlights].filter(([n])=>n.startsWith('marker-')).flatMap(([,h])=>[...h].map(r=>r.toString()))")==['quietly','softly']
    assert requests.get(base+'/api/annotations',timeout=5).json()==before
    assert not errors


def test_experimental_actions_clear_recheck_remove_and_undo(scene_live_server,scene_browser):
    import requests
    base=scene_live_server
    tab,chapter=_new_project(base,'Editing actions browser')
    _set_blocks(base,chapter['id'],['quietly swiftly'])
    made=requests.post(base+'/api/annotations/editing-search',json={'tab_id':tab['id'],'scan':'adverbs'},timeout=5).json()
    marker=made['marker']
    response=requests.patch(base+f"/api/labels/{marker['id']}",json={'name':'My adverbs'},timeout=5)
    response.raise_for_status()
    page,errors=_page(base,scene_browser)
    page.evaluate('()=>openEditorHighlights()')
    button=page.locator('[data-editing-search="adverbs"]')
    button.click()
    for label in ('Clear','Remove marker','Recheck'):
        expect(page.get_by_role('button',name=label,exact=True)).to_be_visible()
    page.get_by_role('button',name='Clear',exact=True).click()
    expect(page.locator('#editingSearchStatus')).to_have_text('Search highlights cleared. The marker was kept.')
    assert page.evaluate('()=>state.markers[0].name')=='My adverbs'
    button.click();page.get_by_role('button',name='Recheck',exact=True).click()
    expect(page.locator('#editingSearchStatus')).to_have_text('2 matches marked with My adverbs.')
    assert len(requests.get(base+f"/api/annotations?label_id={marker['id']}",timeout=5).json())==2
    button.click();page.get_by_role('button',name='Remove marker',exact=True).click()
    expect(page.locator('#dialogConfirm')).to_have_text('Remove marker')
    page.locator('#dialogConfirm').click()
    expect(page.locator('#editingSearchStatus')).to_have_text('Marker removed.')
    assert page.evaluate('()=>state.markers.length')==0
    page.evaluate('()=>appUndo()')
    page.wait_for_function('()=>state.markers.length===1')
    button.click()
    expect(page.get_by_role('button',name='Recheck',exact=True)).to_be_visible()
    assert not errors
