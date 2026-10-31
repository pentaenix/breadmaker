"""Real hosted UI, source, canonical annotation packs and structural Undo."""
from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def test_hosted_highlights_unicode_selection_clear_and_undo_preserve_manuscript(tmp_path,monkeypatch):
    monkeypatch.setenv('BREADMAKER_EDITOR_HIGHLIGHTS_ENABLED','true')
    with hosted_browser(tmp_path) as (base,page):
        made=page.request.post(base+'/api/projects',data={'name':'Disposable hosted Highlights'},headers={'Origin':base})
        assert made.ok;pid=made.json()['project']['id']
        page.goto(base+'/p/'+pid,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        text='😀 She walked quietly and self‑consciously.'
        page.locator('.manuscript-block').first.fill(text)
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        page.evaluate('()=>openEditorHighlights()')
        page.locator('[data-editing-search="adverbs"]').click()
        expect(page.locator('#editingSearchStatus')).to_have_text('2 matches marked with -ly adverbs.')
        assert page.evaluate('()=>[...CSS.highlights].filter(([key])=>key.startsWith("marker-")).flatMap(([,h])=>[...h].map(range=>range.toString())).sort()')==['quietly','self‑consciously']
        page.locator('#modalClose').click()
        page.evaluate('()=>navigateMarker(state.markers[0].id)')
        assert page.evaluate('()=>state.find.hits.length')==2
        # Actual DOM UTF16 selection through the ordinary shared UI action.
        page.evaluate('''async()=>{const root=document.querySelector('.manuscript-block');
          const node=root.firstChild,start=node.textContent.indexOf('quietly'),range=document.createRange();
          range.setStart(node,start);range.setEnd(node,start+7);const selected=getSelection();selected.removeAllRanges();selected.addRange(range);
          await clearMarkerHighlights(markupSelectionItems())}''')
        assert page.evaluate('()=>visibleAnnotations().filter(a=>a.annotation_type==="highlight").map(a=>a.selected_text)')==['self‑consciously']
        page.evaluate('()=>appUndo()')
        page.wait_for_function('()=>visibleAnnotations().filter(a=>a.annotation_type==="highlight").length===2')
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('.manuscript-block').first).to_have_text(text)
        page.wait_for_function('()=>visibleAnnotations().filter(a=>a.annotation_type==="highlight").length===2')


def test_hosted_highlights_default_off_is_hidden_and_api_rejects(tmp_path,monkeypatch):
    monkeypatch.delenv('BREADMAKER_EDITOR_HIGHLIGHTS_ENABLED',raising=False)
    with hosted_browser(tmp_path) as (base,page):
        made=page.request.post(base+'/api/projects',data={'name':'Disabled hosted Highlights'},headers={'Origin':base})
        assert made.ok;pid=made.json()['project']['id']
        page.goto(base+'/p/'+pid,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.evaluate('()=>openExperimentalFeatures()')
        expect(page.locator('#experimentalEditorHighlights')).to_have_count(0)
        assert page.request.post(base+'/api/annotations/editing-search',data={'scan':'adverbs','tab_id':'foreign'},
            headers={'Origin':base,'X-Breadmaker-Project':pid}).status==404
