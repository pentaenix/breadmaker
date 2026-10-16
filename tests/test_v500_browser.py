from __future__ import annotations

import requests
import time

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _replace_visible_text(page,text):
    page.evaluate("""text => {const prose=document.querySelector('.manuscript-block-wrap:not(.scene-gap-hidden) .manuscript-block');prose.textContent=text;placeCaret(prose,text.length);prose.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:text}))}""",text)


def test_v500_autosave_old_response_cannot_clear_newer_edit(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0 autosave generation race')
    _set_blocks(base,ch['id'],['Initial'])
    page,errors=_page(base,scene_browser)
    reopened=None
    try:
        page.evaluate("""chapterId => {
          const original=window.fetch; window.__v500SaveCount=0; window.__v500ReleaseFirst=null;
          window.fetch=(url,opts={})=>{
            const isSave=String(url)===`/api/chapters/${chapterId}/blocks`&&String(opts.method||'GET').toUpperCase()==='PUT';
            if(isSave){window.__v500SaveCount++;if(window.__v500SaveCount===1){return new Promise((resolve,reject)=>{window.__v500ReleaseFirst=()=>original(url,opts).then(resolve,reject)})}}
            return original(url,opts);
          };
        }""",ch['id'])
        _replace_visible_text(page,'First save payload')
        page.wait_for_function("window.__v500SaveCount===1 && !!window.__v500ReleaseFirst",timeout=5000)
        # This edit occurs after save generation 1 has already been serialized but before
        # its response returns. It must remain dirty when the old response completes.
        _replace_visible_text(page,'First save payload + NEWER EDIT')
        assert page.evaluate('state.dirty===true && state.dirtyChapters.size>0')
        page.evaluate('window.__v500ReleaseFirst()')
        page.wait_for_function("window.__v500SaveCount>=2",timeout=7000)
        page.wait_for_function("!state.saveInFlight && !state.dirty && state.dirtyChapters.size===0",timeout=7000)
        persisted=requests.get(base+f"/api/chapters/{ch['id']}",timeout=10).json()['blocks'][0]['text_content']
        assert persisted=='First save payload + NEWER EDIT'
        # Reopen a fresh browser surface to prove the newest generation came from disk,
        # not merely from the old DOM that still held the unsaved string.
        _shutdown_page(page); page=None
        reopened,reopen_errors=_page(base,scene_browser)
        assert reopened.locator('.manuscript-block').first.inner_text()=='First save payload + NEWER EDIT'
        assert not errors and not reopen_errors,(errors,reopen_errors)
    finally:
        _release(base)
        if page is not None and not page.is_closed(): _shutdown_page(page)
        if reopened is not None and not reopened.is_closed(): _shutdown_page(reopened)


def test_v500_structural_mutation_barrier_blocks_input_only_while_operation_is_open(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0 structural input barrier')
    _set_blocks(base,ch['id'],['Editable'])
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate("""() => {
          const original=window.fetch;window.__v500BeginHeld=false;window.__v500ReleaseBegin=null;
          window.fetch=(url,opts={})=>{
            if(String(url).startsWith('/api/undo/begin')&&!window.__v500BeginHeld){window.__v500BeginHeld=true;return new Promise((resolve,reject)=>{window.__v500ReleaseBegin=()=>original(url,opts).then(resolve,reject)})}
            return original(url,opts);
          };
        }""")
        # Use a real undoable project mutation and deliberately stall its before-snapshot.
        page.evaluate("""chapterId => {window.__v500StructuralDone=false;window.__v500StructuralError='';withStructuralUndo('Barrier browser test',()=>api('/api/bookmark',{method:'PUT',body:JSON.stringify({target_type:'chapter',chapter_id:chapterId})}),{view:'manuscript',chapterId}).then(()=>window.__v500StructuralDone=true).catch(e=>{window.__v500StructuralError=String(e);window.__v500StructuralDone=true})}""",ch['id'])
        page.wait_for_function("window.__v500BeginHeld && !!window.__v500ReleaseBegin && state.mutationBarrierDepth>0")
        prose=page.locator('.manuscript-block').first; prose.click(); page.keyboard.press('End')
        before=prose.inner_text(); page.keyboard.type(' BLOCKED')
        assert prose.inner_text()==before
        page.evaluate('window.__v500ReleaseBegin()')
        page.wait_for_function("window.__v500StructuralDone && state.mutationBarrierDepth===0",timeout=7000)
        assert page.evaluate('window.__v500StructuralError')==''
        prose.click(); page.keyboard.press('End'); page.keyboard.type(' allowed')
        assert prose.inner_text().endswith(' allowed')
        page.evaluate('flushSave()'); page.wait_for_timeout(120)
        assert requests.get(base+f"/api/chapters/{ch['id']}",timeout=10).json()['blocks'][0]['text_content'].endswith(' allowed')
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v500_chapter_drag_refuses_scene_split_and_safe_drag_still_works(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0 Scene-safe Chapter drag')
    _set_blocks(base,ch1['id'],['Scene first'])
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json(); _set_blocks(base,ch2['id'],['Scene second'])
    ch3=requests.post(base+'/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']},timeout=10).json(); _set_blocks(base,ch3['id'],['Unrelated'])
    b1=requests.get(base+f"/api/chapters/{ch1['id']}",timeout=10).json()['blocks'][0]
    b2=requests.get(base+f"/api/chapters/{ch2['id']}",timeout=10).json()['blocks'][0]
    requests.post(base+'/api/scenes',json={'title':'Cross chapter Scene','block_ids':[b1['id'],b2['id']]},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#chaptersNavMode').click(); page.wait_for_function("document.querySelectorAll('.chapter-row').length===3")
        rows=page.locator('.chapter-row')
        # Attempt to insert Chapter 3 between the two chunks of one Scene.
        rows.nth(2).drag_to(rows.nth(1),target_position={'x':40,'y':4}); page.wait_for_timeout(650)
        order=[c['id'] for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if not c.get('is_implicit') and c['tab_id']==tab['id']]
        assert order==[ch1['id'],ch2['id'],ch3['id']]
        page.wait_for_function("document.querySelector('#toast').textContent.includes('split Scene')",timeout=4000)
        # A safe move that keeps the Scene's Chapter chunks adjacent remains available.
        rows=page.locator('.chapter-row'); rows.nth(2).drag_to(rows.nth(0),target_position={'x':40,'y':4}); page.wait_for_timeout(700)
        order=[c['id'] for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if not c.get('is_implicit') and c['tab_id']==tab['id']]
        assert order==[ch3['id'],ch1['id'],ch2['id']]
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v500_checkpoint_create_exposes_explicit_context_defaults(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0 checkpoint options')
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#checkpointBtn').click(); page.wait_for_timeout(150)
        # The toolbar opens the panel first; creation remains an explicit panel action.
        page.locator('#checkpointCreateBtn').click()
        page.wait_for_selector('#checkpointIncludeScenes')
        assert page.locator('#checkpointIncludeScenes').is_checked()
        assert page.locator('#checkpointIncludeMarkup').is_checked()
        assert page.locator('#checkpointIncludeNotes').is_checked()
        assert 'Include manuscript context' in page.locator('#modalBody').inner_text()
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v502_focused_ctrl_a_delete_removes_chapter_and_contents_without_destroying_editor_shell(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0.2 focused whole-Chapter delete')
    _set_blocks(base,ch1['id'],['Above'])
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json(); _set_blocks(base,ch2['id'],['Focused prose'])
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch2['id']); page.wait_for_function('(id)=>state.viewMode===\'chapter\'&&state.chapter?.id===id',arg=ch2['id'])
        prose=page.locator('.manuscript-block').first; prose.click(); page.keyboard.press('Control+A'); page.keyboard.press('Delete')
        page.wait_for_function('(id)=>!state.chapters.some(ch=>ch.id===id)',arg=ch2['id'],timeout=7000)
        page.wait_for_function('(id)=>state.viewMode===\'chapter\'&&state.chapter?.id===id',arg=ch1['id'],timeout=7000)
        assert page.locator('#manuscriptPaper').count()==1
        assert page.locator('#chapterTitle').count()==1
        assert page.locator('#editor').count()==1
        persisted=requests.get(base+f"/api/chapters/{ch1['id']}",timeout=10).json()['blocks']
        assert [b['text_content'] for b in persisted]==['Above']
        page.locator('#undoBtn').click(); page.wait_for_function('(id)=>state.chapters.some(ch=>ch.id===id)',arg=ch2['id'],timeout=7000)
        restored=requests.get(base+f"/api/chapters/{ch2['id']}",timeout=10).json()['blocks']
        assert [b['text_content'] for b in restored]==['Focused prose']
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v501_full_text_header_delete_merges_chapter_up_and_stays_in_full_text(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0 Full Text boundary delete')
    _set_blocks(base,ch1['id'],['Above'])
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json(); _set_blocks(base,ch2['id'],['Below'])
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_function("state.viewMode==='book'")
        heading=page.locator(f'.book-chapter-heading[data-chapter-id="{ch2["id"]}"]')
        heading.click(); heading.press('Delete')
        page.wait_for_function('(id)=>!state.chapters.some(ch=>ch.id===id)',arg=ch2['id'],timeout=7000)
        page.wait_for_function("state.viewMode==='book'",timeout=7000)
        assert page.locator('.book-chapter-heading').count()==1
        assert page.locator('#editor').inner_text().find('Above')>=0 and page.locator('#editor').inner_text().find('Below')>=0
        persisted=requests.get(base+f"/api/chapters/{ch1['id']}",timeout=10).json()['blocks']
        assert [b['text_content'] for b in persisted]==['Above','Below']
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v501_full_text_backspace_at_chapter_start_removes_boundary(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0 Full Text Backspace boundary')
    _set_blocks(base,ch1['id'],['Above'])
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json(); _set_blocks(base,ch2['id'],['Below'])
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_function("state.viewMode==='book'")
        prose=page.locator(f'.manuscript-block-wrap[data-chapter-id="{ch2["id"]}"] .manuscript-block').first
        prose.click(); page.keyboard.press('Home'); page.keyboard.press('Backspace')
        page.wait_for_function('(id)=>!state.chapters.some(ch=>ch.id===id)',arg=ch2['id'],timeout=7000)
        persisted=requests.get(base+f"/api/chapters/{ch1['id']}",timeout=10).json()['blocks']
        assert [b['text_content'] for b in persisted]==['Above','Below']
        assert page.locator('#editor').count()==1 and page.locator('.book-chapter-heading').count()==1
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)


def test_v503_chapter_menu_add_below_inserts_and_opens_new_chapter(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0.3 add Chapter below')
    ch2=requests.post(base+'/api/chapters',json={'title':'Existing Chapter 2','tab_id':tab['id']},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_function("document.querySelectorAll('.chapter-row').length===2")
        page.locator(f'.chapter-row[data-id="{ch1["id"]}"] .chapter-more').click()
        page.locator('#contextMenu button',has_text='Add Chapter Below').click()
        page.wait_for_function('(oldId)=>state.chapters.filter(ch=>!ch.is_implicit&&ch.tab_id===state.activeDocumentId).length===3&&state.chapter?.id!==oldId',arg=ch1['id'],timeout=7000)
        rows=[c for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if not c.get('is_implicit') and c['tab_id']==tab['id']]
        assert rows[0]['id']==ch1['id'] and rows[2]['id']==ch2['id']
        created=rows[1]
        data=requests.get(base+f"/api/chapters/{created['id']}",timeout=10).json()
        assert len(data['blocks'])==1 and data['blocks'][0]['text_content']==''
        assert page.evaluate('id=>state.viewMode===\'chapter\'&&state.chapter?.id===id',created['id'])
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v503_scene_menu_add_below_creates_single_blank_scene_line(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.3 add Scene below')
    _set_blocks(base,ch['id'],['Anchor prose','Following prose'])
    blocks=requests.get(base+f"/api/chapters/{ch['id']}",timeout=10).json()['blocks']
    anchor=requests.post(base+'/api/scenes',json={'title':'Anchor Scene','block_ids':[blocks[0]['id']]},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#scenesNavMode').click();page.wait_for_function("document.querySelectorAll('.scene-outline-row').length===1")
        page.locator(f'.scene-outline-row[data-scene-id="{anchor["id"]}"] .scene-more').click()
        page.locator('#contextMenu button',has_text='Add Scene Below').click()
        page.wait_for_function('(anchorId)=>state.scenes.length>=2&&state.viewMode===\'scene\'&&state.scene?.id!==anchorId',arg=anchor['id'],timeout=7000)
        new_id=page.evaluate('state.scene.id')
        data=requests.get(base+f'/api/scenes/{new_id}/manuscript',timeout=10).json()
        assert len(data['blocks'])==1 and data['blocks'][0]['text_content']==''
        order=[s['id'] for s in requests.get(base+'/api/bootstrap',timeout=10).json()['scenes'] if s['tab_id']==tab['id']]
        assert order[:2]==[anchor['id'],new_id]
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v503_focused_delete_navigates_to_chapter_immediately_above_and_clears_selection(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0.3 delete navigation')
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    ch3=requests.post(base+'/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']},timeout=10).json();_set_blocks(base,ch3['id'],['Delete me'])
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch3['id']);page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch3['id'])
        page.locator('.manuscript-block').first.click();page.keyboard.press('Control+A');page.keyboard.press('Delete')
        page.wait_for_function('(id)=>state.viewMode===\'chapter\'&&state.chapter?.id===id',arg=ch2['id'],timeout=7000)
        assert page.evaluate("()=>{const s=getSelection();return !s.rangeCount||s.isCollapsed}")
        assert page.locator('#selectionToolbar').evaluate("el=>el.classList.contains('hidden')")
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v503_selecting_chapter_header_shows_contextual_toolbar_and_body_merges_up(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0.3 header selection toolbar')
    _set_blocks(base,ch1['id'],['Above prose'])
    ch2=requests.post(base+'/api/chapters',json={'title':'Selected Header','tab_id':tab['id']},timeout=10).json();_set_blocks(base,ch2['id'],['Below prose'])
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch2['id']);page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch2['id'])
        page.evaluate("""()=>{const title=document.querySelector('#chapterTitle'),range=document.createRange(),sel=getSelection();range.selectNodeContents(title);sel.removeAllRanges();sel.addRange(range);state.manuscriptPointerSelecting=false;document.dispatchEvent(new Event('selectionchange'))}""")
        page.wait_for_function("!document.querySelector('#selectionToolbar').classList.contains('hidden')")
        assert page.locator('#selectionToolbar [data-selection-action="style"]').is_visible()
        assert page.locator('#selectionToolbar [data-selection-action="scene"]').evaluate("el=>el.classList.contains('hidden')")
        page.locator('#selectionToolbar [data-selection-action="style"]').click()
        page.locator('#contextMenu button',has_text='Body').click()
        page.wait_for_function('(id)=>!state.chapters.some(ch=>ch.id===id)',arg=ch2['id'],timeout=7000)
        page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch1['id'],timeout=7000)
        data=requests.get(base+f"/api/chapters/{ch1['id']}",timeout=10).json()['blocks']
        assert [b['text_content'] for b in data]==['Above prose','Selected Header','Below prose']
        assert data[1]['semantic_style']=='body'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v503_checkpoint_panel_click_outside_row_clears_row_highlight(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.3 checkpoint deselect')
    cp=requests.post(base+'/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Selected checkpoint'},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#checkpointBtn').click();page.wait_for_function("!document.querySelector('#checkpointPanel').classList.contains('hidden')")
        page.evaluate('id=>{checkpointWorkspace.selectedId=id;renderCheckpointList()}',cp['id'])
        assert page.locator(f'[data-checkpoint-row="{cp["id"]}"]').evaluate("el=>el.classList.contains('active')")
        page.locator('#checkpointSearch').click()
        assert page.evaluate('checkpointWorkspace.selectedId===null')
        assert not page.locator(f'[data-checkpoint-row="{cp["id"]}"]').evaluate("el=>el.classList.contains('active')")
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v503_chapter_menu_delete_navigates_to_immediately_above_chapter(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0.3 menu delete navigation')
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    ch3=requests.post(base+'/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']},timeout=10).json();_set_blocks(base,ch3['id'],['Delete me'])
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch3['id']);page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch3['id'])
        page.locator(f'.chapter-row[data-id="{ch3["id"]}"] .chapter-more').click()
        page.locator('#contextMenu button',has_text='Delete').click()
        page.wait_for_selector('#modal:not(.hidden) #dialogConfirm')
        page.locator('#dialogConfirm').click()
        page.wait_for_function('(id)=>state.viewMode===\'chapter\'&&state.chapter?.id===id',arg=ch2['id'],timeout=7000)
        assert page.evaluate("()=>{const s=getSelection();return !s.rangeCount||s.isCollapsed}")
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def _select_focused_chapter_title(page):
    page.evaluate("""()=>{const title=document.querySelector('#chapterTitle'),range=document.createRange(),sel=getSelection();range.selectNodeContents(title);sel.removeAllRanges();sel.addRange(range);state.manuscriptPointerSelecting=false;document.dispatchEvent(new Event('selectionchange'))}""")
    page.wait_for_function("!document.querySelector('#selectionToolbar').classList.contains('hidden')")


def test_v504_chapter_header_selection_supports_highlight_note_and_more_under_toolbar(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.4 Chapter header editorial toolbar')
    requests.patch(base+f"/api/chapters/{ch['id']}/title",json={'title':'Header Editorial Target'},timeout=10).raise_for_status()
    marker=requests.post(base+'/api/labels',json={'name':'Header highlight','color':'#b98252','description':''},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch['id']);page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch['id'])
        _select_focused_chapter_title(page)
        toolbar=page.locator('#selectionToolbar')
        assert toolbar.locator('[data-selection-action="style"]').is_visible()
        assert toolbar.locator('[data-selection-action="highlight"]').is_visible()
        assert toolbar.locator('[data-selection-action="comment"]').is_visible()
        assert toolbar.locator('[data-selection-action="more"]').is_visible()
        assert toolbar.locator('[data-selection-command="bold"]').evaluate("el=>el.classList.contains('hidden')")

        toolbar.locator('[data-selection-action="highlight"]').click()
        page.locator(f'[data-selection-highlight-marker="{marker["id"]}"]').click()
        page.wait_for_function("()=>state.chapterData?.annotations?.some(a=>!a.block_id&&a.chapter_id===state.chapter.id&&a.annotation_type==='highlight')",timeout=7000)
        highlights=requests.get(base+f"/api/annotations?label_id={marker['id']}&status=all",timeout=10).json()
        assert any(a['block_id'] is None and a['chapter_id']==ch['id'] and a['selected_text']=='Header Editorial Target' for a in highlights)

        _select_focused_chapter_title(page)
        toolbar.locator('[data-selection-action="comment"]').click()
        page.wait_for_function("!document.querySelector('#commentComposer').classList.contains('hidden')")
        page.locator('#commentCreateText').fill('This note belongs to the Chapter title.')
        page.locator('#commentCreateSubmit').click()
        page.wait_for_function("document.querySelector('#commentComposer').classList.contains('hidden')",timeout=7000)
        comments=requests.get(base+'/api/comments?status=all',timeout=10).json()
        thread=next(row for row in comments if row['first_message']=='This note belongs to the Chapter title.')
        assert thread['anchors'][0]['chapter_title_anchor']==1 and thread['anchors'][0]['chapter_id']==ch['id']

        _select_focused_chapter_title(page)
        more=toolbar.locator('[data-selection-action="more"]'); button_box=more.bounding_box(); assert button_box
        more.click();page.wait_for_function("!document.querySelector('#contextMenu').classList.contains('hidden')")
        menu_box=page.locator('#contextMenu').bounding_box(); assert menu_box
        assert menu_box['y'] >= button_box['y']+button_box['height']-2
        assert abs(menu_box['x']-button_box['x']) < 12
        labels=page.locator('#contextMenu button').all_text_contents()
        assert 'Link…' in labels and 'Task' in labels and 'Map' in labels
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v504_changing_chapters_dismisses_selection_toolbar_and_selection(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0.4 Chapter change clears selection')
    ch2=requests.post(base+'/api/chapters',json={'title':'Second Chapter','tab_id':tab['id']},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch1['id']);page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch1['id'])
        _select_focused_chapter_title(page)
        page.locator(f'.chapter-row[data-id="{ch2["id"]}"]').click()
        page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch2['id'],timeout=7000)
        assert page.locator('#selectionToolbar').evaluate("el=>el.classList.contains('hidden')")
        assert page.evaluate("()=>{const s=getSelection();return !s.rangeCount||s.isCollapsed}")
        assert page.evaluate('state.selectionToolbarHeaderChapterId===null&&state.selectionToolbarItems.length===0')
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v504_chapter_menu_places_add_below_after_split_view_before_details(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.4 Chapter menu order')
    page,errors=_page(base,scene_browser)
    try:
        page.locator(f'.chapter-row[data-id="{ch["id"]}"] .chapter-more').click()
        labels=[text.strip() for text in page.locator('#contextMenu button').all_text_contents()]
        assert labels.index('Split View') < labels.index('Add Chapter Below') < labels.index('Details')
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v504_chapter_header_more_task_and_map_actions_link_to_chapter_entity(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.4 Chapter header more actions')
    requests.patch(base+f"/api/chapters/{ch['id']}/title",json={'title':'Entity Header'},timeout=10).raise_for_status()
    story_map=requests.post(base+'/api/maps',json={'name':'Header Map'},timeout=10).json()
    line=requests.post(base+f"/api/maps/{story_map['id']}/lines",json={'name':'Header Line'},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch['id']);page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch['id'])
        _select_focused_chapter_title(page)
        page.locator('#selectionToolbar [data-selection-action="more"]').click()
        page.locator('#contextMenu button',has_text='Task').click()
        page.wait_for_selector('#modal:not(.hidden) #taskName')
        page.locator('#taskName').fill('Task from Chapter header')
        page.locator('#saveTask').click()
        page.wait_for_function("document.querySelector('#modal').classList.contains('hidden')",timeout=7000)
        tasks=requests.get(base+'/api/tasks',timeout=10).json(); task=next(t for t in tasks if t['title']=='Task from Chapter header')
        refs=requests.get(base+f"/api/references?source_type=task&source_id={task['id']}",timeout=10).json()
        assert any(r['target_type']=='chapter' and str(r['target_id'])==str(ch['id']) for r in refs)

        _select_focused_chapter_title(page)
        page.locator('#selectionToolbar [data-selection-action="more"]').click()
        page.locator('#contextMenu button',has_text='Map').click()
        page.wait_for_selector('#modal:not(.hidden) #selectionMapSelect')
        page.locator('#addSelectionMapConfirm').click()
        page.wait_for_function("document.querySelector('#modal').classList.contains('hidden')",timeout=7000)
        detail=requests.get(base+f"/api/maps/{story_map['id']}",timeout=10).json()
        created=next(item for ln in detail['lines'] if ln['id']==line['id'] for item in ln['items'] if item['title']=='Entity Header')
        assert created['start_anchor_type']=='chapter' and str(created['start_anchor_id'])==str(ch['id'])
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v506_chapter_header_bookmark_targets_chapter_not_text_range(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.6 Chapter header bookmark')
    requests.patch(base+f"/api/chapters/{ch['id']}/title",json={'title':'Bookmark This Chapter'},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch['id']);page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch['id'])
        _select_focused_chapter_title(page)
        bookmark=page.locator('#selectionToolbar [data-selection-action="bookmark"]')
        assert bookmark.is_visible()
        assert bookmark.get_attribute('aria-label')=='Bookmark Chapter'
        bookmark.click()
        page.wait_for_function('(id)=>state.bookmark?.target_type===\'chapter\'&&state.bookmark?.chapter_id===id',arg=ch['id'],timeout=7000)
        saved=requests.get(base+'/api/bookmark',timeout=10).json()['bookmark']
        assert saved['target_type']=='chapter' and saved['chapter_id']==ch['id']
        assert saved.get('start_block_id') is None and saved.get('end_block_id') is None
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v506_chapter_header_note_hover_highlights_selected_title_text(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.6 Chapter header note hover')
    title='The Purple Cupcake Chapter'
    requests.patch(base+f"/api/chapters/{ch['id']}/title",json={'title':title},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch['id']);page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch['id'])
        start=title.index('Purple Cupcake'); end=start+len('Purple Cupcake')
        page.evaluate("""([start,end])=>{const root=document.querySelector('#chapterTitle'),node=root.firstChild,range=document.createRange(),sel=getSelection();range.setStart(node,start);range.setEnd(node,end);sel.removeAllRanges();sel.addRange(range);state.manuscriptPointerSelecting=false;document.dispatchEvent(new Event('selectionchange'))}""",[start,end])
        page.wait_for_function("!document.querySelector('#selectionToolbar').classList.contains('hidden')")
        page.locator('#selectionToolbar [data-selection-action="comment"]').click()
        page.wait_for_function("!document.querySelector('#commentComposer').classList.contains('hidden')")
        page.locator('#commentCreateText').fill('Header hover note')
        page.locator('#commentCreateSubmit').click()
        page.wait_for_function("document.querySelector('#commentComposer').classList.contains('hidden')",timeout=7000)
        marker=page.locator('.chapter-title-row .note-gutter-marker')
        marker.wait_for(state='visible',timeout=7000)
        marker.hover();page.wait_for_timeout(450)
        page.wait_for_function("()=>CSS.highlights?.get('breadmaker-comment-focus')?.size===1",timeout=5000)
        highlighted=page.evaluate("()=>Array.from(CSS.highlights.get('breadmaker-comment-focus')).map(r=>r.toString())")
        assert highlighted==['Purple Cupcake']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v507_chapter_drag_drop_uses_visible_before_target_line(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0.7 Chapter drag insertion line')
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    ch3=requests.post(base+'/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']},timeout=10).json()
    ch4=requests.post(base+'/api/chapters',json={'title':'Chapter 4','tab_id':tab['id']},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_function("document.querySelectorAll('.chapter-row').length===4")
        rows=page.locator('.chapter-row')
        # The drop indicator is the top edge of Chapter 4. Moving Chapter 2 onto
        # Chapter 4 must therefore place it between Chapters 3 and 4, not after 4.
        rows.nth(1).drag_to(rows.nth(3),target_position={'x':40,'y':4});page.wait_for_timeout(700)
        order=[c['id'] for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if not c.get('is_implicit') and c['tab_id']==tab['id']]
        assert order==[ch1['id'],ch3['id'],ch2['id'],ch4['id']]
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v507_outline_navigation_briefly_pulses_destination(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.7 outline destination pulse')
    current=requests.get(base+f"/api/chapters/{ch['id']}",timeout=10).json()['blocks'][0]
    payload=[
        {'id':current['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Opening','text_content':'Opening','image_path':'','scene_id':None},
        {'id':None,'block_type':'paragraph','semantic_style':'heading2','content_html':'Subsection destination','text_content':'Subsection destination','image_path':'','scene_id':None},
        {'id':None,'block_type':'paragraph','semantic_style':'body','content_html':'After','text_content':'After','image_path':'','scene_id':None},
    ]
    requests.put(base+f"/api/chapters/{ch['id']}/blocks",json={'blocks':payload},timeout=10).raise_for_status()
    target=requests.get(base+f"/api/chapters/{ch['id']}",timeout=10).json()['blocks'][1]
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_function('(id)=>!!document.querySelector(`.outline-nav[data-block-id="${id}"]`)',arg=target['id'])
        page.locator(f'.outline-nav[data-block-id="{target["id"]}"]').click()
        page.wait_for_function('(id)=>document.querySelector(`.manuscript-block-wrap[data-block-id="${id}"]`)?.classList.contains("bookmark-jump-pulse")',arg=target['id'])
        assert page.locator(f'.manuscript-block-wrap[data-block-id="{target["id"]}"] .manuscript-block').inner_text()=='Subsection destination'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v507_replacing_selected_chapter_title_is_rename_not_whole_chapter_delete_guard(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.7 direct Chapter title rename')
    requests.patch(base+f"/api/chapters/{ch['id']}/title",json={'title':'Old Chapter Title'},timeout=10).raise_for_status()
    _set_blocks(base,ch['id'],['Prose must stay'])
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('id=>selectChapter(id)',ch['id']);page.wait_for_function('(id)=>state.chapter?.id===id',arg=ch['id'])
        _select_focused_chapter_title(page)
        paste_blocked=page.evaluate("""()=>{const ev=new InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'insertFromPaste',data:'Pasted Chapter Title'});document.querySelector('#chapterTitle').dispatchEvent(ev);return ev.defaultPrevented}""")
        assert paste_blocked is False
        _select_focused_chapter_title(page)
        page.keyboard.type('New Chapter Title')
        page.wait_for_function("document.querySelector('#chapterTitle').innerText==='New Chapter Title'")
        assert 'whole Chapter is selected' not in page.locator('#toast').inner_text()
        assert page.locator('.manuscript-block').first.inner_text()=='Prose must stay'
        page.evaluate('flushSave()');page.wait_for_timeout(250)
        saved=requests.get(base+f"/api/chapters/{ch['id']}",timeout=10).json()
        assert saved['chapter']['title']=='New Chapter Title'
        assert saved['blocks'][0]['text_content']=='Prose must stay'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v508_chapter_drop_line_slot_is_authoritative_and_ui_moves_before_server_finishes(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0.8 authoritative instant Chapter drag')
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    ch3=requests.post(base+'/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']},timeout=10).json()
    ch4=requests.post(base+'/api/chapters',json={'title':'Chapter 4','tab_id':tab['id']},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_function("document.querySelectorAll('.chapter-row').length===4")
        # Hold the persistence request long enough to prove the rail itself does not wait
        # for History/Undo/database work before reflecting the drop.
        page.evaluate("""()=>{const real=window.fetch.bind(window);window.fetch=(url,options)=>{const request=real(url,options);return String(url).includes('/api/chapters/reorder')?request.then(response=>new Promise(resolve=>setTimeout(()=>resolve(response),900))):request}}""")
        rows=page.locator('.chapter-row')
        rows.nth(1).drag_to(rows.nth(3),target_position={'x':40,'y':4})
        page.wait_for_function("()=>Array.from(document.querySelectorAll('.chapter-row')).map(r=>Number(r.dataset.id)).join(',')==='1,3,2,4'",timeout=350)
        # The server eventually converges to the exact same slot represented by the line.
        page.wait_for_function("()=>!state.dragChapterId",timeout=1000)
        deadline=time.time()+4
        order=[]
        while time.time()<deadline:
            order=[c['id'] for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if not c.get('is_implicit') and c['tab_id']==tab['id']]
            if order==[ch1['id'],ch3['id'],ch2['id'],ch4['id']]: break
            time.sleep(.08)
        assert order==[ch1['id'],ch3['id'],ch2['id'],ch4['id']]
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v508_chapter_drag_bottom_half_uses_after_slot(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.0.8 Chapter drag after slot')
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    ch3=requests.post(base+'/api/chapters',json={'title':'Chapter 3','tab_id':tab['id']},timeout=10).json()
    ch4=requests.post(base+'/api/chapters',json={'title':'Chapter 4','tab_id':tab['id']},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_function("document.querySelectorAll('.chapter-row').length===4")
        rows=page.locator('.chapter-row'); box=rows.nth(2).bounding_box(); assert box
        # Bottom half of Chapter 3 shows the line below Chapter 3, therefore Chapter 1
        # must land after Chapter 3 and before Chapter 4.
        rows.nth(0).drag_to(rows.nth(2),target_position={'x':40,'y':max(2,box['height']-3)})
        page.wait_for_timeout(650)
        order=[c['id'] for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if not c.get('is_implicit') and c['tab_id']==tab['id']]
        assert order==[ch2['id'],ch3['id'],ch1['id'],ch4['id']]
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v509_split_tasks_is_open_only_supports_create_and_checkbox_completion(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.9 Split View Tasks')
    open_task=requests.post(base+'/api/tasks',json={'title':'Open from split','status':'open'},timeout=10).json()
    requests.post(base+'/api/tasks',json={'title':'Later hidden','status':'later'},timeout=10).raise_for_status()
    requests.post(base+'/api/tasks',json={'title':'Done hidden','status':'done'},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openTasksSplit()')
        page.wait_for_function("state.split?.type==='tasks' && !document.querySelector('#splitTaskAdd').classList.contains('hidden')")
        assert page.locator('[data-split-task]').count()==1
        assert page.locator('[data-split-task]').first.inner_text().strip()=='Open from split'
        assert page.locator('#splitBody').inner_text().find('Later hidden')<0
        assert page.locator('#splitBody').inner_text().find('Done hidden')<0

        # The compact + action reuses the normal task editor; creating an open task
        # refreshes the already-open split list immediately.
        page.locator('#splitTaskAdd').click(); page.wait_for_selector('#taskName')
        assert page.locator('#modalTitle').inner_text()=='New Task'
        page.locator('#taskName').fill('Created in Split View')
        page.locator('#saveTask').click()
        page.wait_for_function("()=>Array.from(document.querySelectorAll('[data-split-task] b')).some(x=>x.textContent==='Created in Split View')")
        assert page.locator('[data-split-task]').count()==2

        # The task body still opens Details, while its checkbox is a direct complete
        # action and must not open the modal.
        first=page.locator(f'[data-split-task="{open_task["id"]}"]')
        first.locator('.split-task-body').click(); page.wait_for_selector('#taskName')
        assert page.locator('#taskName').input_value()=='Open from split'
        page.locator('#modalClose').click(); page.wait_for_function("document.querySelector('#modal').classList.contains('hidden')")
        first=page.locator(f'[data-split-task="{open_task["id"]}"]')
        first.locator('input[type=checkbox]').check()
        page.wait_for_function(f"!document.querySelector('[data-split-task=\"{open_task['id']}\"]')")
        assert page.locator('#modal').evaluate("el=>el.classList.contains('hidden')") is True
        saved=next(t for t in requests.get(base+'/api/tasks',timeout=10).json() if t['id']==open_task['id'])
        assert saved['status']=='done'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v5010_split_tasks_expand_open_milestones_and_complete_children(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.0.10 Split View Milestones')
    task=requests.post(base+'/api/tasks',json={'title':'Revise opening','status':'open'},timeout=10).json()
    one=requests.post(base+f'/api/tasks/{task["id"]}/milestones',json={'title':'Tighten first paragraph'},timeout=10).json()
    two=requests.post(base+f'/api/tasks/{task["id"]}/milestones',json={'title':'Check continuity'},timeout=10).json()
    done=requests.post(base+f'/api/tasks/{task["id"]}/milestones',json={'title':'Already finished'},timeout=10).json()
    requests.patch(base+f'/api/task-milestones/{done["id"]}',json={'completed':True},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('openTasksSplit()')
        row=page.locator(f'[data-split-task="{task["id"]}"]')
        page.wait_for_function(f"!!document.querySelector('[data-split-task=\"{task['id']}\"] [data-split-milestone-toggle]')")
        toggle=row.locator('[data-split-milestone-toggle]')
        assert toggle.get_attribute('aria-expanded')=='false'
        toggle.click()
        page.wait_for_function(f"document.querySelectorAll('[data-split-task=\"{task['id']}\"] [data-split-milestone]').length===2")
        labels=row.locator('[data-split-milestone] .split-task-milestone-title').all_inner_texts()
        assert labels==['Tighten first paragraph','Check continuity']
        assert 'Already finished' not in row.inner_text()

        # Child text opens the canonical parent Task editor.
        row.locator(f'[data-split-milestone="{one["id"]}"] .split-task-milestone-title').click()
        page.wait_for_selector('#taskName')
        assert page.locator('#taskName').input_value()=='Revise opening'
        page.locator('#modalClose').click(); page.wait_for_function("document.querySelector('#modal').classList.contains('hidden')")

        # Completing a milestone removes only that child. The parent remains Open.
        row=page.locator(f'[data-split-task="{task["id"]}"]')
        row.locator(f'[data-split-milestone="{one["id"]}"] input[type=checkbox]').check()
        page.wait_for_function(f"!document.querySelector('[data-split-milestone=\"{one['id']}\"]')")
        assert row.count()==1
        saved=next(m for m in requests.get(base+f'/api/tasks/{task["id"]}/milestones',timeout=10).json() if m['id']==one['id'])
        assert saved['completed']==1
        parent=next(t for t in requests.get(base+'/api/tasks',timeout=10).json() if t['id']==task['id'])
        assert parent['status']=='open'

        # Completing the final open milestone collapses the child area and removes
        # the chevron, but does not auto-complete the parent task.
        row.locator(f'[data-split-milestone="{two["id"]}"] input[type=checkbox]').check()
        page.wait_for_function(f"!document.querySelector('[data-split-task=\"{task['id']}\"] [data-split-milestone-toggle]')")
        assert page.locator(f'[data-split-task="{task["id"]}"]').count()==1
        parent=next(t for t in requests.get(base+'/api/tasks',timeout=10).json() if t['id']==task['id'])
        assert parent['status']=='open'
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v510_tasks_empty_creation_context_and_marker_swatch(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'5.1.0 Tasks workspace polish')
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="tasks"]').click()
        page.wait_for_function("document.querySelector('#tasksView').classList.contains('active')")
        assert 'Capture work and jump back to its context.' not in page.locator('#tasksView').inner_text()

        # Active planning filters offer creation directly in their empty state and
        # seed the canonical Task editor with the filter's useful default status.
        for filter_name,expected_status in [('open','open'),('later','later'),('all','open')]:
            page.locator(f'[data-task-filter="{filter_name}"]').click()
            page.wait_for_function("!!document.querySelector('[data-empty-task-add]')")
            page.locator('[data-empty-task-add]').click(); page.wait_for_selector('#taskStatus')
            assert page.locator('#taskStatus').input_value()==expected_status
            page.locator('#modalClose').click(); page.wait_for_function("document.querySelector('#modal').classList.contains('hidden')")

        # Completed-history views stay intentionally non-creative when empty.
        for filter_name in ('done','archived'):
            page.locator(f'[data-task-filter="{filter_name}"]').click(); page.wait_for_timeout(100)
            assert page.locator('[data-empty-task-add]').count()==0

        created=page.evaluate("""async()=>{
          const marker=await api('/api/labels',{method:'POST',body:JSON.stringify({name:'Continuity',color:'#d86b55'})});
          const task=await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Check timeline',status:'open',marker_id:marker.id})});
          return {marker,task};
        }""")
        marker,task=created['marker'],created['task']
        page.locator('[data-task-filter="open"]').click()
        page.wait_for_function(f"!!document.querySelector('.task-row .task-marker-swatch')")
        row=page.locator('.task-row').filter(has_text='Check timeline').first
        swatch=row.locator('.task-marker-swatch')
        assert swatch.count()==1
        assert swatch.get_attribute('title')=='Continuity'
        # The color marker is immediately before the Links button in the action cluster.
        order=row.locator('.task-actions').evaluate("el=>Array.from(el.children).map(x=>x.matches('.task-marker-swatch')?'swatch':x.hasAttribute('data-link-task')?'link':x.hasAttribute('data-task-more')?'more':'other')")
        assert order[:3]==['swatch','link','more']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v513_task_description_hover_waits_flips_and_skips_empty_descriptions(scene_live_server,scene_browser):
    base=scene_live_server; _tab,_ch1=_new_project(base,'5.1.3 Task description hover')
    requests.post(base+'/api/tasks',json={'title':'No description','status':'open','description':''},timeout=10).raise_for_status()
    for i in range(10):
        requests.post(base+'/api/tasks',json={'title':f'Filler {i}','status':'open'},timeout=10).raise_for_status()
    description='This description should appear only after the hover delay and flip above when the row is near the bottom of the viewport.'
    requests.post(base+'/api/tasks',json={'title':'Hover target','status':'open','description':description},timeout=10).raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="tasks"]').click();page.wait_for_function("document.querySelector('#tasksView').classList.contains('active')")
        empty=page.locator('.task-row').filter(has_text='No description').first
        empty.hover();page.wait_for_timeout(360)
        assert page.locator('#taskDescriptionHover').count()==0

        row=page.locator('.task-row').filter(has_text='Hover target').first
        row.evaluate("el=>el.scrollIntoView({block:'end'})")
        row.hover()
        page.wait_for_timeout(180)
        assert page.locator('#taskDescriptionHover').count()==0
        page.wait_for_timeout(180)
        card=page.locator('#taskDescriptionHover')
        assert card.count()==1 and description in card.inner_text()
        placement=page.evaluate("""() => {const row=document.querySelector('.task-row:last-child'),card=document.querySelector('#taskDescriptionHover'),r=row.getBoundingClientRect(),c=card.getBoundingClientRect();return {rowTop:r.top,rowBottom:r.bottom,cardTop:c.top,cardBottom:c.bottom,height:innerHeight}}""")
        assert placement['cardBottom']<=placement['height']-7
        assert placement['cardBottom']<=placement['rowTop']+1,placement
        page.mouse.move(5,5);page.wait_for_timeout(30)
        assert page.locator('#taskDescriptionHover').count()==0
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v512_task_link_icon_opens_manager_with_row_navigate_add_and_remove(scene_live_server,scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'5.1.2 Task link manager')
    ch2=requests.post(base+'/api/chapters',json={'title':'Second destination','tab_id':tab['id']},timeout=10).json()
    task=requests.post(base+'/api/tasks',json={'title':'Verify linked context','status':'open'},timeout=10).json()
    first_ref=requests.post(base+'/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'chapter','target_id':str(ch1['id'])},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="tasks"]').click()
        page.wait_for_function("document.querySelector('#tasksView').classList.contains('active')")
        row=page.locator('.task-row').filter(has_text='Verify linked context').first
        row.locator('[data-link-task]').click()
        page.wait_for_function("document.querySelector('#modalTitle').textContent==='Links' && !!document.querySelector('#referenceManagerList')")
        assert 'Verify linked context' in page.locator('#modalBody').inner_text()
        assert 'Chapter' in page.locator('#referenceManagerList').inner_text()
        assert page.locator('#referenceManagerList .reference-manager-row').count()==1
        assert page.locator('#referenceManagerAdd').count()==1

        # Add a second link without leaving the manager.
        page.locator('#referenceManagerAdd').click()
        page.wait_for_function("document.querySelector('#modalTitle').textContent==='Add Link'")
        page.locator('#entityLinkType').select_option('chapter')
        page.wait_for_function("!!document.querySelector('#simpleLinkTarget')")
        page.locator('#simpleLinkTarget').select_option(str(ch2['id']))
        page.locator('#saveEntityLink').click()
        page.wait_for_function("document.querySelector('#modalTitle').textContent==='Links' && document.querySelectorAll('#referenceManagerList .reference-manager-row').length===2")
        assert 'Second destination' in page.locator('#referenceManagerList').inner_text()

        # Remove the older link in-place; removing the relation must not delete its Chapter.
        old_row=page.locator('#referenceManagerList .reference-manager-row').filter(has_text=ch1['title']).first
        old_row.locator('[data-delete-ref]').click()
        page.wait_for_function("document.querySelectorAll('#referenceManagerList .reference-manager-row').length===1")
        assert page.locator('#modalTitle').inner_text()=='Links'
        assert page.locator('#tasksView').get_attribute('class').find('active')>=0
        refs=requests.get(base+f'/api/references?source_type=task&source_id={task["id"]}',timeout=10).json()
        assert len(refs)==1 and refs[0]['target_id']==str(ch2['id'])
        assert requests.get(base+f'/api/chapters/{ch1["id"]}',timeout=10).status_code==200

        # The linked row itself is the navigation action; there is no redundant Go button.
        assert page.locator('#referenceManagerList .reference-manager-go').count()==0
        page.locator('#referenceManagerList .reference-manager-row').click()
        page.wait_for_function(f"document.querySelector('#modal').classList.contains('hidden') && state.chapter && state.chapter.id=={ch2['id']}")
        assert page.locator('[data-view="manuscript"]').get_attribute('class').find('active')>=0
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
