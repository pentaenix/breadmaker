import requests

from test_v41812_browser import (
    scene_live_server, scene_browser, _new_project, _set_blocks, _page,
    _select_text, _shutdown_page,
)


def test_v41813_scene_hover_remove_overflow_and_gap_flyer_cleanup(scene_live_server,scene_browser):
    base=scene_live_server; tab,chapter=_new_project(base,'Scene polish browser')
    blocks=_set_blocks(base,chapter['id'],['scene edge','scene tail','outside'])
    scene=requests.post(base+'/api/scenes',json={'title':'Hover Scene','description':'The scene description appears beside the row.','block_ids':[blocks[0]['id'],blocks[1]['id']]},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        page.locator('#scenesNavMode').click(); page.wait_for_function("document.querySelectorAll('.scene-outline-row').length===1")
        row=page.locator('.scene-outline-row').first
        row.hover(); page.wait_for_timeout(360)
        hover=page.locator('#sceneDetailsHover')
        assert hover.count()==1 and 'scene description appears beside the row' in hover.inner_text().lower()

        # An edge selection containing only Scene prose exposes Remove in the primary toolbar position.
        page.locator('#bookViewBtn').click(); page.wait_for_timeout(250)
        _select_text(page,1)
        scene_button=page.locator('#selectionToolbar [data-selection-action="scene"]')
        assert scene_button.inner_text().strip()=='Remove from Scene' and not scene_button.is_disabled()
        scene_button.click(); page.wait_for_timeout(500)
        current=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=10).json()['blocks']
        assert current[1]['scene_id'] is None and current[0]['scene_id']==scene['id']

        # The gap flyer is removed immediately when the navigator rerenders.
        page.locator('#scenesNavMode').click(); page.wait_for_timeout(200)
        gap=page.locator('.scene-gap-row').first; gap.hover(); page.wait_for_timeout(360)
        assert page.locator('#sceneGapHover').count()==1
        page.evaluate('renderChapterTree()'); page.wait_for_timeout(50)
        assert page.locator('#sceneGapHover').count()==0
        assert not errors,errors
    finally:
        try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
        except Exception: pass
        _shutdown_page(page)


def test_v41813_add_scene_and_chapter_after_focused_item(scene_live_server,scene_browser):
    base=scene_live_server; tab,first_chapter=_new_project(base,'Focused insertion browser')
    blocks=_set_blocks(base,first_chapter['id'],['first scene','outside','last scene'])
    first_scene=requests.post(base+'/api/scenes',json={'title':'First Scene','block_ids':[blocks[0]['id']]},timeout=10).json()
    requests.post(base+'/api/scenes',json={'title':'Last Scene','block_ids':[blocks[2]['id']]},timeout=10).raise_for_status()
    tail=requests.post(base+'/api/chapters',json={'title':'Tail Chapter','tab_id':tab['id']},timeout=10).json()
    page,errors=_page(base,scene_browser)
    try:
        # Focus a Scene, then Add Scene: the new blank Scene belongs immediately after it in prose and outline.
        page.locator('#scenesNavMode').click(); page.wait_for_function("document.querySelectorAll('.scene-outline-row').length===2")
        page.locator('.scene-outline-row').first.locator('.scene-outline-main').dblclick(); page.wait_for_timeout(250)
        page.locator('#navigatorMenuBtn').click()
        page.locator('#contextMenu button').filter(has_text='Add Scene').click(); page.wait_for_timeout(650)
        book=requests.get(base+f'/api/book?tab_id={tab["id"]}',timeout=10).json()
        first_chapter_blocks=[b for b in book['blocks'] if b['chapter_id']==first_chapter['id']]
        assert [b['text_content'] for b in first_chapter_blocks[:4]]==['first scene','','outside','last scene']
        new_scene_id=first_chapter_blocks[1]['scene_id']; assert new_scene_id and new_scene_id!=first_scene['id']
        scenes=[s for s in requests.get(base+'/api/bootstrap',timeout=10).json()['scenes'] if s['tab_id']==tab['id']]
        scenes.sort(key=lambda s:(s['position'],s['id']))
        assert [s['id'] for s in scenes][:2]==[first_scene['id'],new_scene_id]

        # Focus the first Chapter, then Add Chapter: it inserts before the existing tail Chapter.
        page.locator('#chaptersNavMode').click(); page.wait_for_timeout(200)
        page.locator('.chapter-row').filter(has=page.locator('.chapter-full',has_text=first_chapter['title'])).dblclick(); page.wait_for_timeout(250)
        page.locator('#navigatorMenuBtn').click()
        page.locator('#contextMenu button').filter(has_text='Add Chapter').click(); page.wait_for_timeout(550)
        chapters=[c for c in requests.get(base+'/api/bootstrap',timeout=10).json()['chapters'] if c['tab_id']==tab['id'] and not c['is_implicit']]
        chapters.sort(key=lambda c:(c['position'],c['id']))
        assert chapters[0]['id']==first_chapter['id'] and chapters[2]['id']==tail['id']
        assert chapters[1]['id'] not in {first_chapter['id'],tail['id']}
        assert not errors,errors
    finally:
        try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
        except Exception: pass
        _shutdown_page(page)
