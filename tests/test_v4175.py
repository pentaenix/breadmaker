from source_contracts import backend_source, storage_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok


def save_text(client, chapter_id, text):
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[{'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text}]}))


def test_v4175_checkpoint_metadata_is_name_description_only(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; chapter=boot['chapters'][0]
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'description':'Quiet reference copy.'}))
    assert cp['name']==''
    assert cp['display_name']==chapter['title']
    assert cp['default_name']==chapter['title']
    assert 'tags' not in cp
    found=assert_ok(client.get('/api/checkpoints?q=quiet'))['checkpoints']
    assert [x['id'] for x in found]==[cp['id']]
    updated=assert_ok(client.patch(f"/api/checkpoints/{cp['id']}",json={'name':'Before rewrite','description':'Use the old cadence.'}))
    assert updated['display_name']=='Before rewrite'
    with app_module_fixture.storage.tx() as con:
        cols={r[1] for r in con.execute('PRAGMA table_info(checkpoints)')}
    assert 'tags_json' not in cols


def test_v4175_book_checkpoint_uses_book_name_when_custom_name_is_blank(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'book'}))
    assert cp['name']==''
    assert cp['display_name']==(tab['title'] or fresh_project['name'])


def test_v4175_apply_chapter_targets_live_chapter_without_renaming_it(client,fresh_project):
    boot=fresh_project['bootstrap']; source=boot['chapters'][0]; tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    save_text(client,source['id'],'Saved source dialogue.')
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':source['id']}))
    target=assert_ok(client.post('/api/chapters',json={'title':'Target Chapter','tab_id':tab['id']}))
    save_text(client,target['id'],'Target draft text.')
    result=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':target['id']}))
    assert result['mode']=='replace_chapter' and result['chapter_id']==target['id']
    data=assert_ok(client.get(f"/api/chapters/{target['id']}"))
    assert data['chapter']['title']=='Target Chapter'
    assert any(b['text_content']=='Saved source dialogue.' for b in data['blocks'])
    listed=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    saved=next(x for x in listed if x['id']==cp['id'])
    assert saved['current_chapter_id']==source['id'], 'Applying to another Chapter must not rebind checkpoint provenance.'
    history=assert_ok(client.get('/api/history'))['revisions']
    assert any('Before applying checkpoint' in x['label'] for x in history)


def test_v4175_apply_deleted_chapter_checkpoint_can_add_new_chapter(client,fresh_project):
    source=fresh_project['bootstrap']['chapters'][0]
    save_text(client,source['id'],'Dialogue worth carrying forward.')
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':source['id']}))
    assert_ok(client.delete(f"/api/chapters/{source['id']}"))
    result=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'add_as_chapter':True}))
    assert result['mode']=='add_chapter'
    data=assert_ok(client.get(f"/api/chapters/{result['chapter_id']}"))
    assert data['chapter']['title']==source['title']
    assert any(b['text_content']=='Dialogue worth carrying forward.' for b in data['blocks'])
    listed=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    saved=next(x for x in listed if x['id']==cp['id'])
    assert saved['source_exists'] is True and saved['current_chapter_id']==result['chapter_id']


def test_v4175_restore_route_is_removed_and_apply_is_current_contract(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id']}))
    assert client.post(f"/api/checkpoints/{cp['id']}/restore").status_code in {404,405}
    assert client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':chapter['id']}).status_code==200


def test_v4175_checkpoint_panel_is_compact_and_apply_lives_in_split_view():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    app=backend_source(root)
    storage=storage_source(root)
    assert 'checkpointPanelDetail' not in html and 'checkpointSplitRestore' not in html
    assert 'id="checkpointSplitApply"' in html
    assert 'checkpoint-more' in js and 'Checkpoint Details' in js and 'Configure Checkpoint' not in js
    assert 'checkpointPanelHoverCard' in html and 'CHECKPOINT_PANEL_HOVER_DELAY_MS=300' in js
    assert 'checkpointCreateTags' not in js and 'checkpointTagsFromInput' not in js
    assert 'tags_json' not in storage and 'tags_json' not in app
    assert '@app.post("/api/checkpoints/{cid}/restore")' not in app
    assert '@app.post("/api/checkpoints/{cid}/apply")' in app
    assert '.checkpoint-list-row' in css and '.checkpoint-more' in css
