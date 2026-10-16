from storage import mutations as storage_mutations
import storage as storage_package

from source_contracts import backend_source, storage_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok


def _seed_text(client, fresh_project, text='Alpha beta gamma delta'):
    chapter=fresh_project['bootstrap']['chapters'][0]
    data=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    block=data['blocks'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[
        {'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':''}
    ]}))
    return chapter, assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]


def test_v416_static_markup_is_direct_and_has_no_review_layers():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    app=backend_source(root)
    storage_py=storage_source(root)
    workspace=(root/'src'/'workspace'/'__init__.py').read_text(encoding='utf-8')

    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'APP_SCHEMA_VERSION = 18' in workspace
    assert 'id="markupPanel"' in html and 'id="labelList"' in html and 'id="newLabelBtn"' in html
    assert 'data-selection-action="highlight"' in html
    assert 'Review Layer' not in html
    assert 'activeMarkupMarkerId' in js
    assert 'async function useMarker(id)' in js
    assert 'async function applyActiveMarkupSelection()' in js
    assert "selected?'Create & highlight':'Create'" in js
    assert "{label:'Navigate',action:()=>navigateMarker(id)}" in js
    assert '.marker-row' in css and '.marker-use' in css
    for source in (js,app,storage_py):
        assert 'editing_passes' not in source
        assert 'pass_id' not in source
        assert '/api/passes' not in source


def test_v416_schema_and_bootstrap_have_no_review_layer_axis(client,fresh_project,app_module_fixture):
    boot=assert_ok(client.get('/api/bootstrap'))
    assert 'passes' not in boot
    assert client.get('/api/passes').status_code in {404,405}
    with storage_mutations.tx() as con:
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'editing_passes' not in tables
        for table in ('annotations','comment_threads','tasks'):
            cols={r[1] for r in con.execute(f'PRAGMA table_info({table})')}
            assert 'pass_id' not in cols


def test_v416_marker_only_highlight_api_and_navigation_filter(client,fresh_project):
    chapter,block=_seed_text(client,fresh_project)
    marker=assert_ok(client.post('/api/labels',json={'name':'Continuity','color':'#aabbcc'}))
    created=assert_ok(client.post('/api/annotations/batch',json={
        'annotation_type':'highlight','label_id':marker['id'],'body':'','items':[{
            'block_id':block['id'],'start_offset':6,'end_offset':10,'selected_text':'beta',
            'prefix_context':'Alpha ','suffix_context':' gamma delta'
        }]
    }))
    assert len(created['ids'])==1
    rows=assert_ok(client.get(f"/api/annotations?status=open&label_id={marker['id']}"))
    assert len(rows)==1
    assert rows[0]['annotation_type']=='highlight'
    assert rows[0]['label_id']==marker['id']
    assert rows[0]['label_name']=='Continuity'
    assert rows[0]['selected_text']=='beta'


def test_v416_history_shape_contains_markers_but_no_review_layers(client,fresh_project,app_module_fixture):
    marker=assert_ok(client.post('/api/labels',json={'name':'Rewrite','color':'#fedcba'}))
    pid=fresh_project['bootstrap']['project']['id']
    with storage_mutations.tx() as con:
        tree=storage_package.revision_tree(con,pid)
    assert tree['format_version']==8
    assert 'passes' not in tree
    assert any(x['id']==marker['id'] for x in tree['labels'])
    for key in ('comment_threads',):
        assert all('pass_id' not in row for row in tree[key])
