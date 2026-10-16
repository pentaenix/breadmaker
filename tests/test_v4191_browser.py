import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page, _shutdown_page,
)


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v4191_bookmark_jump_preserves_full_text_and_chapter_focus(scene_live_server, scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'Bookmark context 4191')
    _set_blocks(base,ch1['id'],['Chapter one prose'])
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    blocks=_set_blocks(base,ch2['id'],['Target bookmark words here'])
    response=requests.put(base+'/api/bookmark',json={'target_type':'range','start_block_id':blocks[0]['id'],'end_block_id':blocks[0]['id'],'start_offset':7,'end_offset':15},timeout=10);response.raise_for_status()
    page,errors=_page(base,scene_browser)
    try:
        # Full Text stays Full Text.
        page.evaluate('openWholeBook(false,false)');page.wait_for_timeout(120)
        page.locator('#bookmarkBtn').click();page.wait_for_timeout(180)
        assert page.evaluate('state.viewMode')=='book'
        assert page.locator(f'.manuscript-block-wrap[data-block-id="{blocks[0]["id"]}"]').count()==1

        # Chapter focus stays Chapter focus and switches to the owning Chapter.
        page.evaluate('(id)=>selectChapter(id,false,false)',ch1['id']);page.wait_for_timeout(120)
        assert page.evaluate('state.viewMode')=='chapter'
        page.locator('#bookmarkBtn').click();page.wait_for_timeout(180)
        assert page.evaluate('state.viewMode')=='chapter'
        assert page.evaluate('state.chapter.id')==ch2['id']
        assert page.locator(f'.manuscript-block-wrap[data-block-id="{blocks[0]["id"]}"]').count()==1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v4191_bookmark_tooltip_is_hover_only_and_explains_feature(scene_live_server, scene_browser):
    base,tab_ch=scene_live_server,None
    tab,ch=_new_project(base,'Bookmark tooltip 4191');_set_blocks(base,ch['id'],['Some words'])
    page,errors=_page(base,scene_browser)
    try:
        button=page.locator('#bookmarkBtn');tip=page.locator('#bookmarkTooltip')
        button.focus();page.wait_for_timeout(80)
        assert 'hidden' in (tip.get_attribute('class') or '')
        button.hover();page.wait_for_timeout(80)
        assert 'hidden' not in (tip.get_attribute('class') or '')
        text=page.locator('#bookmarkTooltipText').inner_text()
        assert 'Mark one place in your text you want to come back to while writing or editing.' in text
        assert 'No bookmark set' not in text
        button.click();page.wait_for_timeout(80)
        assert 'hidden' in (tip.get_attribute('class') or '')
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)


def test_v4191_native_double_click_selects_word_in_manuscript(scene_live_server, scene_browser):
    base=scene_live_server;tab,ch=_new_project(base,'Double click 4191')
    _set_blocks(base,ch['id'],['Hello wonderful bakery world'])
    page,errors=_page(base,scene_browser)
    try:
        prose=page.locator('.manuscript-block').first
        prose.dblclick(position={'x':55,'y':10});page.wait_for_timeout(120)
        selected=page.evaluate('getSelection().toString()')
        assert selected.strip()
        assert page.locator('#selectionToolbar').is_visible()
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
