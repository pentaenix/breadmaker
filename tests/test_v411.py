from source_contracts import backend_source, storage_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok


def save_text(client, chapter_id, text, style='body'):
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[{'block_type':'paragraph','semantic_style':style,'content_html':text,'text_content':text}]}))


def test_v411_chapter_checkpoint_is_independent_searchable_and_restorable_after_chapter_delete(client,fresh_project):
    boot=fresh_project['bootstrap']; chapter=boot['chapters'][0]
    save_text(client,chapter['id'],'The original deliberate version.')
    cp=assert_ok(client.post('/api/checkpoints',json={
        'scope':'chapter','chapter_id':chapter['id'],'name':'Before experiment',
        'description':'The quieter ending before the rewrite.'
    }))
    assert cp['scope']=='chapter' and cp['source_exists'] is True
    original_hash=cp['snapshot_hash']

    updated=assert_ok(client.patch(f"/api/checkpoints/{cp['id']}",json={'name':'Quiet ending','description':'Keep this one around.'}))
    assert updated['snapshot_hash']==original_hash
    assert updated['name']=='Quiet ending'
    found=assert_ok(client.get('/api/checkpoints?q=quiet'))['checkpoints']
    assert [x['id'] for x in found]==[cp['id']]

    save_text(client,chapter['id'],'A much louder rewrite.')
    diff=assert_ok(client.get(f"/api/checkpoints/compare/versions?left={cp['id']}&right=current"))
    assert diff['change_count']>=1
    from bs4 import BeautifulSoup
    joined=BeautifulSoup(' '.join(row['left']+row['right'] for row in diff['rows']),'html.parser').get_text(' ')
    assert 'original' in joined and 'deliberate' in joined and 'louder' in joined and 'rewrite' in joined

    assert_ok(client.delete(f"/api/chapters/{chapter['id']}"))
    after_delete=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    saved=next(x for x in after_delete if x['id']==cp['id'])
    assert saved['source_exists'] is False
    cuts=assert_ok(client.get('/api/cuts'))
    assert cuts, 'Deleting the chapter should still create the normal Cut independently of the checkpoint.'

    restored=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'add_as_chapter':True}))
    assert restored['scope']=='chapter' and restored['chapter_id']
    chapter_data=assert_ok(client.get(f"/api/chapters/{restored['chapter_id']}"))
    assert chapter_data['chapter']['title']==chapter['title']
    assert any(b['text_content']=='The original deliberate version.' for b in chapter_data['blocks'])
    checkpoints=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    assert [x['id'] for x in checkpoints]==[cp['id']], 'Apply must not create automatic Checkpoints.'
    history=assert_ok(client.get('/api/history'))['revisions']
    assert any('Before applying checkpoint' in x['label'] for x in history), 'Apply preserves the pre-apply live state in automatic History.'


def test_v411_book_checkpoint_restores_manuscript_structure_without_becoming_history(client,fresh_project):
    boot=fresh_project['bootstrap']; first=boot['chapters'][0]; manuscript=next(t for t in boot['tabs'] if t['is_manuscript'])
    save_text(client,first['id'],'Chapter one checkpoint text.')
    second=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':manuscript['id']}))
    save_text(client,second['id'],'Chapter two checkpoint text.')
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'book','name':'Two chapter draft'}))
    assert cp['scope']=='book' and cp['word_count']>=6

    assert_ok(client.delete(f"/api/chapters/{second['id']}"))
    assert len([c for c in assert_ok(client.get('/api/bootstrap'))['chapters'] if c['tab_id']==manuscript['id']])==1
    result=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={}))
    assert result['scope']=='book'
    after=assert_ok(client.get('/api/bootstrap'))
    book_chapters=[c for c in after['chapters'] if c['tab_id']==manuscript['id']]
    assert [c['title'] for c in book_chapters]==[first['title'],'Chapter 2']
    restored_two=next(c for c in book_chapters if c['title']=='Chapter 2')
    assert any(b['text_content']=='Chapter two checkpoint text.' for b in assert_ok(client.get(f"/api/chapters/{restored_two['id']}"))['blocks'])



def test_v411_deleted_source_is_not_confused_with_reused_chapter_id(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Original lineage'}))
    old_id=chapter['id']
    assert_ok(client.delete(f'/api/chapters/{old_id}'))
    manuscript=next(t for t in assert_ok(client.get('/api/bootstrap'))['tabs'] if t['is_manuscript'])
    replacement=assert_ok(client.post('/api/chapters',json={'title':'Replacement chapter','tab_id':manuscript['id']}))
    listed=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    saved=next(x for x in listed if x['id']==cp['id'])
    if replacement['id']==old_id:
        assert saved['source_exists'] is False

def test_v411_checkpoint_static_contract_and_history_terminology():
    root=Path(__file__).resolve().parents[1]; html=(root/'static/index.html').read_text(); js=frontend_source_from_app_path(root/'static/app.js'); css=frontend_styles_source_from_styles_path((root/'static/styles.css')); storage=storage_source(root)
    assert 'id="checkpointBtn"' in html
    assert 'openCheckpoints' in js and '/api/checkpoints/compare/versions' in js
    assert 'checkpointSplitShowDiff' in js and 'checkpointSplitLinked' in js and 'checkpointSearch' in html
    assert 'Create named history version' not in js and 'New History Version' not in js
    assert "@app.post(\"/api/revisions\")" not in backend_source(root)
    assert '.checkpoint-panel' in css and '.checkpoint-context-toolbar' in css and '.checkpoint-diff-added' in css
    assert 'CREATE TABLE IF NOT EXISTS checkpoints' in storage
