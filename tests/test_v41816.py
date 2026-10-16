from source_contracts import backend_source
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def _set_blocks(client,chapter_id,texts):
    data=assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    first=data['blocks'][0]
    payload=[]
    for i,text in enumerate(texts):
        payload.append({'id':first['id'] if i==0 else None,'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None})
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':payload}))
    return assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks']


def test_v41816_scene_created_inside_gap_uses_manuscript_outline_position(client,fresh_project):
    tab=next(t for t in fresh_project['bootstrap']['tabs'] if t['is_manuscript'])
    chapter=next(c for c in fresh_project['bootstrap']['chapters'] if c['tab_id']==tab['id'])
    blocks=_set_blocks(client,chapter['id'],['first scene','gap prose','later scene'])

    first=assert_ok(client.post('/api/scenes',json={'block_ids':[blocks[0]['id']]}))
    second=assert_ok(client.post('/api/scenes',json={'block_ids':[blocks[2]['id']]}))
    inserted=assert_ok(client.post('/api/scenes',json={'block_ids':[blocks[1]['id']]}))

    assert inserted['title'].endswith('3')
    boot=assert_ok(client.get('/api/bootstrap'))
    scenes=sorted([s for s in boot['scenes'] if s['tab_id']==tab['id']],key=lambda s:(s['position'],s['id']))
    assert [s['id'] for s in scenes]==[first['id'],inserted['id'],second['id']]
    assert [s['title'] for s in scenes]==[first['title'],inserted['title'],second['title']]


def test_v41816_scene_outline_sync_is_manuscript_driven():
    app=backend_source(ROOT)
    assert 'def _sync_scene_outline_to_manuscript' in app
    assert '_sync_scene_outline_to_manuscript(con,pid,tab_id)' in app
    assert 'ORDER BY c.position,b.position,b.id' in app


def test_v41816_release_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert '### 4.18.16 Scene outline follows manuscript position' in readme
    assert '### 4.18.16 Scene outline placement contract' in architecture
