from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v417_static_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'Select prose and choose a marker to highlight it.' not in html
    assert '<option value="current">Current Chapter</option>' in html
    assert '<option value="chapters">Chapters</option>' in html
    assert '<option value="book">Book</option>' in html
    assert '<option value="all">All</option>' in html
    assert 'id="viewTooltip"' in html
    for value in ('arial','times','verdana','courier'):
        assert f'<option value="{value}">' in html
    assert "restoreManuscriptFont();" in js
    assert "rows=checkpointWorkspace.items;label='Checkpoint'" in js
    assert "const comparable=checkpointMatchesCurrentContext(cp)" in js
    assert "Checkpoint · Read only · Reference" in js
    assert 'marker-panel-hover-head' not in js
    assert '.comments-panel,.markup-panel,.checkpoint-panel,.cuts-panel{' in css
    assert '--manuscript-font-family' in css


def test_v417_deleted_chapter_checkpoint_remains_available(client,fresh_project):
    bootstrap=fresh_project['bootstrap']
    chapter=bootstrap['chapters'][0]
    cp=client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Old chapter guide'})
    assert cp.status_code==200,cp.text
    cid=cp.json()['id']
    deleted=client.delete(f"/api/chapters/{chapter['id']}")
    assert deleted.status_code==200,deleted.text
    rows=client.get('/api/checkpoints').json()['checkpoints']
    saved=next(x for x in rows if x['id']==cid)
    assert saved['scope']=='chapter'
    assert saved['source_chapter_title']==chapter['title']
    assert saved['source_exists'] is False
    detail=client.get(f'/api/checkpoints/{cid}')
    assert detail.status_code==200,detail.text
    assert detail.json()['snapshot']['chapter']['title']==chapter['title']
