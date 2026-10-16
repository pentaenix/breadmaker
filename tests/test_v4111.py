from source_contracts import backend_source, storage_source,frontend_source_from_app_path
from pathlib import Path
from conftest import assert_ok


def _save(client, chapter_id, text):
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks', json={'blocks':[{'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text}]}))


def test_v4111_orphan_restore_reconnects_checkpoint_lineage_even_with_new_chapter_id(client, fresh_project):
    boot=fresh_project['bootstrap']; original=boot['chapters'][0]; manuscript=next(t for t in boot['tabs'] if t['is_manuscript'])
    _save(client, original['id'], 'Original lineage paragraph.')
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':original['id'],'name':'Original'}))
    # Keep a higher chapter id alive so restoring the deleted source cannot simply reuse its old integer id.
    keeper=assert_ok(client.post('/api/chapters',json={'title':'Keeper','tab_id':manuscript['id']}))
    assert_ok(client.delete(f"/api/chapters/{original['id']}"))
    restored=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'add_as_chapter':True}))
    assert restored['chapter_id'] != original['id']
    rows=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    saved=next(x for x in rows if x['id']==cp['id'])
    assert saved['source_exists'] is True
    assert saved['current_chapter_id']==restored['chapter_id']
    assert [x['id'] for x in assert_ok(client.get(f"/api/checkpoints?chapter_id={restored['chapter_id']}"))['checkpoints']]==[cp['id']]
    # Even a complete rewrite that replaces every saved block id remains the same restored Chapter lineage.
    _save(client, restored['chapter_id'], 'A totally different rewrite with new block identity.')
    assert_ok(client.get(f"/api/checkpoints/compare/versions?left={cp['id']}&right=current"))
    cp2=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':restored['chapter_id'],'name':'After restore'}))
    assert_ok(client.get(f"/api/checkpoints/compare/versions?left={cp['id']}&right={cp2['id']}"))


def test_v4111_reused_integer_chapter_id_does_not_make_unrelated_checkpoints_compatible(client, fresh_project):
    boot=fresh_project['bootstrap']; original=boot['chapters'][0]; manuscript=next(t for t in boot['tabs'] if t['is_manuscript'])
    _save(client, original['id'], 'Old chapter identity.')
    first=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':original['id'],'name':'Old'}))
    assert_ok(client.delete(f"/api/chapters/{original['id']}"))
    replacement=assert_ok(client.post('/api/chapters',json={'title':'Unrelated replacement','tab_id':manuscript['id']}))
    _save(client, replacement['id'], 'Completely unrelated chapter.')
    second=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':replacement['id'],'name':'New'}))
    response=client.get(f"/api/checkpoints/compare/versions?left={first['id']}&right={second['id']}")
    assert response.status_code==400
    assert 'lineage' in response.text.lower()



def test_v4111_checkpoint_does_not_follow_prose_moved_to_an_unrelated_chapter(client, fresh_project, app_module_fixture):
    boot=fresh_project['bootstrap']; original=boot['chapters'][0]; manuscript=next(t for t in boot['tabs'] if t['is_manuscript'])
    _save(client, original['id'], 'A paragraph that will be moved elsewhere.')
    block=assert_ok(client.get(f"/api/chapters/{original['id']}"))['blocks'][0]
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':original['id'],'name':'Original chapter'}))
    target=assert_ok(client.post('/api/chapters',json={'title':'Different chapter','tab_id':manuscript['id']}))
    # Simulate a legitimate structural move that preserves the paragraph's stable id.
    with app_module_fixture.storage.tx() as con:
        con.execute('UPDATE manuscript_blocks SET chapter_id=?,tab_id=? WHERE id=?',(target['id'],manuscript['id'],block['id']))
    assert_ok(client.delete(f"/api/chapters/{original['id']}"))
    saved=next(x for x in assert_ok(client.get('/api/checkpoints'))['checkpoints'] if x['id']==cp['id'])
    assert saved['source_exists'] is False
    assert saved['current_chapter_id'] is None
    assert not assert_ok(client.get(f"/api/checkpoints?chapter_id={target['id']}"))['checkpoints']



def test_v4111_same_creation_timestamp_is_not_enough_to_claim_checkpoint_lineage(client, fresh_project, app_module_fixture):
    boot=fresh_project['bootstrap']; original=boot['chapters'][0]; manuscript=next(t for t in boot['tabs'] if t['is_manuscript'])
    _save(client, original['id'], 'Original chapter body.')
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':original['id'],'name':'Original'}))
    other=assert_ok(client.post('/api/chapters',json={'title':'Batch sibling','tab_id':manuscript['id']}))
    _save(client, other['id'], 'Different body.')
    with app_module_fixture.storage.tx() as con:
        created=con.execute('SELECT created_at FROM chapters WHERE id=?',(original['id'],)).fetchone()[0]
        con.execute('UPDATE chapters SET created_at=? WHERE id=?',(created,other['id']))
    assert_ok(client.delete(f"/api/chapters/{original['id']}"))
    saved=next(x for x in assert_ok(client.get('/api/checkpoints'))['checkpoints'] if x['id']==cp['id'])
    assert saved['source_exists'] is False
    assert saved['current_chapter_id'] is None


def test_v4111_restore_restores_scene_metadata_and_preserves_annotation_links_that_belong_to_snapshot(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    _save(client,chapter['id'],'Alpha beta gamma.')
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    scene=assert_ok(client.post('/api/scenes',json={'title':'Original scene','description':'Original description','block_ids':[block['id']]}))
    ann_id=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'note','body':'Original note','items':[{'block_id':block['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))['ids'][0]
    task=assert_ok(client.post('/api/tasks',json={'title':'Keep link'}))
    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'annotation','target_id':str(ann_id)}))
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Scene and note'}))

    assert_ok(client.patch(f"/api/scenes/{scene['id']}",json={'title':'Changed scene','description':'Changed description'}))
    # Add post-checkpoint editorial work. 5.0 preserves current-only annotations and
    # their external links whenever the anchor can still be deterministically placed.
    post_ann=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'note','body':'Post checkpoint','items':[{'block_id':block['id'],'start_offset':6,'end_offset':10,'selected_text':'beta'}]}))['ids'][0]
    task2=assert_ok(client.post('/api/tasks',json={'title':'Drop link'}))
    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task2['id']),'target_type':'annotation','target_id':str(post_ann)}))

    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':chapter['id']}))
    scenes=assert_ok(client.get('/api/bootstrap'))['scenes']
    restored_scene=next(s for s in scenes if s['id']==scene['id'])
    assert restored_scene['title']=='Changed scene'
    assert restored_scene['description']=='Changed description'
    refs=assert_ok(client.get('/api/references'))
    assert any(r['source_type']=='task' and int(r['source_id'])==task['id'] and r['target_type']=='annotation' and int(r['target_id'])==ann_id for r in refs)
    assert any(r['target_type']=='annotation' and int(r['target_id'])==post_ann for r in refs)
    annotations=assert_ok(client.get('/api/annotations?status=all'))
    assert any(a['id']==ann_id and a['body']=='Original note' for a in annotations)
    assert any(a['id']==post_ann and a['body']=='Post checkpoint' for a in annotations)




def test_v4111_book_restore_rebinds_existing_chapter_checkpoints_before_further_rewrites(client, fresh_project):
    boot=fresh_project['bootstrap']; original=boot['chapters'][0]; manuscript=next(t for t in boot['tabs'] if t['is_manuscript'])
    _save(client, original['id'], 'Original version worth keeping.')
    chapter_cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':original['id'],'name':'Chapter version'}))
    keeper=assert_ok(client.post('/api/chapters',json={'title':'Keeper','tab_id':manuscript['id']}))
    _save(client, keeper['id'], 'Keep this chapter too.')
    book_cp=assert_ok(client.post('/api/checkpoints',json={'scope':'book','name':'Book with original'}))
    assert_ok(client.delete(f"/api/chapters/{original['id']}"))
    assert_ok(client.post(f"/api/checkpoints/{book_cp['id']}/apply",json={}))
    chapters=assert_ok(client.get('/api/bootstrap'))['chapters']
    restored=next(c for c in chapters if c['title']==original['title'])
    assert restored['id'] != original['id']
    _save(client, restored['id'], 'Completely rewritten after the book restore.')
    saved=next(x for x in assert_ok(client.get('/api/checkpoints'))['checkpoints'] if x['id']==chapter_cp['id'])
    assert saved['source_exists'] is True and saved['current_chapter_id']==restored['id']
    assert_ok(client.get(f"/api/checkpoints/compare/versions?left={chapter_cp['id']}&right=current"))


def test_v4111_invalid_comparison_reference_is_a_client_error(client, fresh_project):
    response=client.get('/api/checkpoints/compare/versions?left=not-a-version&right=current')
    assert response.status_code==400
    assert 'invalid comparison version' in response.text.lower()


def test_v4111_schema_and_legacy_checkpoint_terminology_contract():
    root=Path(__file__).resolve().parents[1]
    workspace=(root/'src'/'workspace'/'__init__.py').read_text(encoding='utf-8')
    storage=storage_source(root)
    js=frontend_source_from_app_path(root/'static/app.js')
    assert 'APP_SCHEMA_VERSION = 18' in workspace
    assert '"schema_version":18' in storage
    assert 'current_source_chapter_id INTEGER' in storage
    assert 'CHECKPOINT_META_COLUMNS' in backend_source(root)
    assert '/api/undo/checkpoint' not in js
    assert 'Manual checkpoint' not in js and 'Automatic checkpoint' not in js
    assert "async function saveNow(){await flushPendingCanvasNodeSaves();await flushSave();toast('Saved ✓')}" in js
    assert "$('#checkpointSplitSelect').onchange=async e=>" in js
