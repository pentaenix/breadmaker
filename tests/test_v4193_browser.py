import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _set_blocks, _page, _select_text, _shutdown_page

def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass

def test_v4193_chapter_focused_text_bookmark_marks_owning_scene(scene_live_server, scene_browser):
    base=scene_live_server; tab,ch=_new_project(base,'Bookmark owner chapter focus 4193')
    blocks=_set_blocks(base,ch['id'],['Scene bookmarked words','Outside words'])
    scene=requests.post(base+'/api/scenes',json={'block_ids':[blocks[0]['id']]},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate('(id)=>selectChapter(id,false,false)',ch['id']); page.wait_for_timeout(120)
        assert page.evaluate('state.viewMode')=='chapter'
        _select_text(page,0)
        page.locator('#selectionToolbar [data-selection-action="bookmark"]').click(); page.wait_for_timeout(350)
        saved=requests.get(base+'/api/bookmark',timeout=10).json()['bookmark']
        assert saved['owner_scene_id']==scene['id'], saved
        page.locator('#scenesNavMode').click(); page.wait_for_timeout(120)
        assert page.locator(f'.scene-outline-row[data-scene-id="{scene["id"]}"] .sidebar-bookmark-icon').count()==1
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)

def test_v4193_chapter_bookmark_projects_to_scene_and_updates_after_move(scene_live_server, scene_browser):
    base=scene_live_server; tab,ch1=_new_project(base,'Bookmark projection 4193')
    b1=_set_blocks(base,ch1['id'],['First scene words'])
    s1=requests.post(base+'/api/scenes',json={'block_ids':[b1[0]['id']]},timeout=10).json()
    ch2=requests.post(base+'/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']},timeout=10).json()
    b2=_set_blocks(base,ch2['id'],['Second scene words'])
    s2=requests.post(base+'/api/scenes',json={'block_ids':[b2[0]['id']]},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        # Bookmarking the Chapter has one concrete location: its start. Because
        # that start belongs to Scene 1, Scene navigation carries the ribbon too.
        page.evaluate('(id)=>selectChapter(id,false,false)',ch1['id']); page.wait_for_timeout(100)
        page.evaluate('(id)=>toggleChapterBookmark(state.chapters.find(ch=>+ch.id===+id))',ch1['id']); page.wait_for_timeout(300)
        page.locator('#scenesNavMode').click(); page.wait_for_timeout(100)
        assert page.locator(f'.scene-outline-row[data-scene-id="{s1["id"]}"] .sidebar-bookmark-icon').count()==1
        assert page.locator(f'.scene-outline-row[data-scene-id="{s2["id"]}"] .sidebar-bookmark-icon').count()==0

        # Moving the bookmark updates both the old and new structural breadcrumbs.
        page.locator('#chaptersNavMode').click(); page.wait_for_timeout(80)
        page.evaluate('(id)=>toggleChapterBookmark(state.chapters.find(ch=>+ch.id===+id))',ch2['id']); page.wait_for_timeout(300)
        page.locator('#scenesNavMode').click(); page.wait_for_timeout(100)
        assert page.locator(f'.scene-outline-row[data-scene-id="{s1["id"]}"] .sidebar-bookmark-icon').count()==0
        assert page.locator(f'.scene-outline-row[data-scene-id="{s2["id"]}"] .sidebar-bookmark-icon').count()==1
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)

def test_v4193_tabs_hamburger_is_optically_centered(scene_live_server, scene_browser):
    base=scene_live_server; _new_project(base,'Hamburger alignment 4193')
    page,errors=_page(base,scene_browser)
    try:
        metrics=page.evaluate("""() => {const b=document.querySelector('#toggleBookNavBtn').getBoundingClientRect();const s=document.querySelector('#toggleBookNavBtn span').getBoundingClientRect();return {button:(b.top+b.bottom)/2,glyph:(s.top+s.bottom)/2}}""")
        assert abs(metrics['button']-metrics['glyph']) <= 2
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
