from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from conftest import assert_ok


def test_v790_chapter_title_rename_preserves_chapter_identity_links_and_map_anchor(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    task=assert_ok(client.post('/api/tasks',json={'title':'Linked review task','status':'open'}))
    reference=assert_ok(client.post('/api/references',json={
        'source_type':'task','source_id':str(task['id']),
        'target_type':'chapter','target_id':str(chapter['id'])
    }))
    assert_ok(client.put('/api/bookmark',json={'target_type':'chapter','chapter_id':chapter['id']}))
    story_map=assert_ok(client.post('/api/maps',json={'name':'Chapter identity map'}))
    line=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Main'}))
    item=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={
        'title':'Chapter anchor','item_type':'stop',
        'start_anchor_type':'chapter','start_anchor_id':str(chapter['id'])
    }))

    renamed='Editable Chapter Header'
    assert_ok(client.patch(f"/api/chapters/{chapter['id']}/title",json={'title':renamed}))

    saved=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert saved['chapter']['id']==chapter['id']
    assert saved['chapter']['title']==renamed

    refs=assert_ok(client.get(f"/api/references?source_type=task&source_id={task['id']}"))
    ref=next(row for row in refs if row['id']==reference['id'])
    assert ref['target_type']=='chapter'
    assert int(ref['target_id'])==chapter['id']
    assert ref['target_label']==renamed

    bookmark=assert_ok(client.get('/api/bookmark'))['bookmark']
    assert bookmark['target_type']=='chapter'
    assert bookmark['chapter_id']==chapter['id']

    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    anchored=next(row for map_line in detail['lines'] for row in map_line['items'] if row['id']==item['id'])
    assert anchored['start_anchor_type']=='chapter'
    assert int(anchored['start_anchor_id'])==chapter['id']


def test_v790_maps_empty_state_has_direct_create_action_in_static_shell():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    html=(root/'static'/'index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static'/'app.js')
    assert 'id="emptyCreateMap"' in html
    assert '{{i18n:map.empty_create}}' in html
    assert "$('#emptyCreateMap')?.addEventListener('click',()=>newMapDialog())" in js
    assert "createButton?.classList.toggle('hidden',!noMaps)" in js
    assert "name.textContent=map?.name||i18nSource('Maps')" in js
    assert "details.classList.toggle('hidden',!has)" in js
    assert "editMapDetailsBtn'])" not in js


def test_v790_inline_book_chapter_title_is_editable_without_making_boundary_editable():
    from pathlib import Path
    js=frontend_source_from_app_path(Path(__file__).resolve().parents[1]/'static'/'app.js')
    assert "heading.contentEditable='false'" in js
    assert "title.contentEditable='true'" in js
    assert "title.className='book-chapter-title'" in js
    assert "if(e.target.closest('.book-chapter-title'))return" in js
    assert "visibleChapterTitleText(chapterId)" in js
