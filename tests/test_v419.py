from source_contracts import storage_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v419_single_bookmark_api_and_structural_undo(client,fresh_project):
    boot=fresh_project['bootstrap']
    tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    chapter=next(c for c in boot['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit'))
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    assert assert_ok(client.get('/api/bookmark'))['bookmark'] is None

    first=assert_ok(client.post('/api/undo/begin?label=Bookmark%20Chapter'))['token']
    saved=assert_ok(client.put('/api/bookmark',json={'target_type':'chapter','chapter_id':chapter['id']}))['bookmark']
    assert saved['target_type']=='chapter' and saved['chapter_id']==chapter['id']
    assert assert_ok(client.post(f'/api/undo/finish/{first}'))['changed']

    scene=assert_ok(client.post('/api/scenes',json={'block_ids':[block['id']]}))
    second=assert_ok(client.post('/api/undo/begin?label=Move%20bookmark'))['token']
    saved=assert_ok(client.put('/api/bookmark',json={'target_type':'scene','scene_id':scene['id']}))['bookmark']
    assert saved['target_type']=='scene' and saved['scene_id']==scene['id']
    assert assert_ok(client.post(f'/api/undo/finish/{second}'))['changed']

    assert_ok(client.post(f'/api/undo/apply/{second}?direction=undo'))
    restored=assert_ok(client.get('/api/bookmark'))['bookmark']
    assert restored['target_type']=='chapter' and restored['chapter_id']==chapter['id']
    assert_ok(client.post(f'/api/undo/apply/{second}?direction=redo'))
    restored=assert_ok(client.get('/api/bookmark'))['bookmark']
    assert restored['target_type']=='scene' and restored['scene_id']==scene['id']


def test_v419_text_bookmark_reports_structural_owner_and_is_single(client,fresh_project):
    boot=fresh_project['bootstrap']
    tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    chapter=next(c for c in boot['chapters'] if c['tab_id']==tab['id'] and not c.get('is_implicit'))
    data=assert_ok(client.get(f"/api/chapters/{chapter['id']}")); block=data['blocks'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'hello world','text_content':'hello world','image_path':'','scene_id':None}]}))
    scene=assert_ok(client.post('/api/scenes',json={'block_ids':[block['id']]}))
    saved=assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':1,'end_offset':5}))['bookmark']
    assert saved['target_type']=='range'
    assert saved['tab_id']==tab['id']
    assert saved['owner_chapter_id']==chapter['id']
    assert saved['owner_scene_id']==scene['id']
    boot2=assert_ok(client.get('/api/bootstrap'))
    assert boot2['bookmark']['start_block_id']==block['id']
    assert_ok(client.delete('/api/bookmark'))
    assert assert_ok(client.get('/api/bookmark'))['bookmark'] is None


def test_v419_bookmark_schema_is_undo_covered():
    storage=storage_source(ROOT)
    assert "'document_tabs', 'chapters', 'scenes', 'bookmarks', 'markup_labels'" in storage
    assert 'CREATE TABLE IF NOT EXISTS bookmarks(' in storage
    assert "'bookmark':bookmark" in storage
    assert "tree.get('bookmark')" in storage


def test_v419_ui_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    readme=(ROOT/'README.md').read_text(encoding='utf-8'); architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert '### 4.19 Single Bookmark and place restoration' in readme
    assert '### 4.19 Bookmark and manuscript-location contract' in architecture
    assert html.index('id="findBtn"') < html.index('id="bookmarkBtn"') < html.index('id="commentsBtn"')
    assert 'data-selection-action="bookmark"' in html
    assert "label:bookmarkExactChapter(ch.id)?'Remove Bookmark':'Bookmark'" in js
    assert "label:bookmarkExactScene(sceneId)?'Remove Bookmark':'Bookmark'" in js
    assert 'Click again to remove bookmark' in js
    assert 'restoreLastManuscriptLocation' in js and "manuscript-last-location" in js
    assert 'sidebar-bookmark-icon' in css and 'text-bookmark-marker' in css


def test_v419_history_restore_preserves_bookmark_project_state(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    boot=fresh_project['bootstrap']; chapter=next(c for c in boot['chapters'] if not c.get('is_implicit'))
    assert_ok(client.put('/api/bookmark',json={'target_type':'chapter','chapter_id':chapter['id']}))
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'Bookmark snapshot','auto',False)
    assert_ok(client.delete('/api/bookmark'))
    assert assert_ok(client.get('/api/bookmark'))['bookmark'] is None
    storage.restore_revision(rid,physical_backup=False,preserve_current=False)
    bookmark=assert_ok(client.get('/api/bookmark'))['bookmark']
    assert bookmark and bookmark['target_type']=='chapter' and bookmark['chapter_id']==chapter['id']
