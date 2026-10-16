from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

import io
import json
import zipfile
from pathlib import Path

from conftest import assert_ok


def test_v4141_cuts_panel_is_compact_and_information_driven():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))

    assert 'id="cutsBtn"' in html
    assert 'Keep prose you remove from the manuscript so you can reuse it, insert it elsewhere, or export it for later.' in html
    assert 'class="cuts-panel-action-row"' in html
    assert 'id="cutSelectionBtn"' in html and 'id="cutInsertBtn"' in html
    assert 'id="cutsDetail"' not in html
    assert '-webkit-line-clamp:3' in css
    assert '.cuts-list-row.active .cuts-preview{-webkit-line-clamp:10}' in css
    assert 'cuts-more-icon' not in js
    for label in ('Insert','Details','Delete'):
        assert f"label:'{label}'" in js
    assert "label:'Add Note'" not in js and "label:'Delete Permanently'" not in js
    assert 'cut-information-provenance' in js
    assert 'cut-information-content' in js


def test_v4141_cut_records_project_chapter_scene_provenance(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    project_title=fresh_project['bootstrap']['project']['title']
    assert_ok(client.patch(f"/api/chapters/{chapter['id']}/title",json={'title':'The Return of Mordor'}))
    data=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    block=data['blocks'][0]
    scene=assert_ok(client.post('/api/scenes',json={
        'title':'Mordor monologue','description':'','block_ids':[block['id']]
    }))
    cut=assert_ok(client.post('/api/cuts',json={
        'title':'Hidden panel title','content_html':'<p>Three rings for the elven kings.</p>',
        'source_chapter_id':chapter['id'],'source_scene_id':scene['id']
    }))
    row=next(x for x in assert_ok(client.get('/api/cuts')) if x['id']==cut['id'])
    assert row['source_project']==project_title
    assert row['source_chapter']=='Chapter 1: The Return of Mordor'
    assert row['source_scene']=='Mordor monologue'
    assert row['source_display']==f'{project_title} | Chapter 1: The Return of Mordor | Mordor monologue'


def test_v4141_cut_without_scene_omits_scene_provenance(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    cut=assert_ok(client.post('/api/cuts',json={
        'title':'No scene','content_html':'<p>Standalone prose.</p>','source_chapter_id':chapter['id']
    }))
    row=next(x for x in assert_ok(client.get('/api/cuts')) if x['id']==cut['id'])
    assert row['source_project']
    assert row['source_chapter']==chapter['title']
    assert row['source_scene']==''


def test_v4141_breadcuts_preserves_original_provenance_additively(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    original_project=fresh_project['bootstrap']['project']['title']
    cut=assert_ok(client.post('/api/cuts',json={
        'title':'Portable provenance','content_html':'<p>Keep the origin.</p>',
        'source_chapter_id':chapter['id'],'note':'Remember this.'
    }))
    package=client.get('/api/cuts/export').content
    with zipfile.ZipFile(io.BytesIO(package)) as z:
        manifest=json.loads(z.read('cuts.json'))
        exported=next(x for x in manifest['cuts'] if x['title']=='Portable provenance')
        assert exported['source_project']==original_project
        assert exported['source_chapter']==chapter['title']
        assert 'source_scene' in exported

    before_ids={x['id'] for x in assert_ok(client.get('/api/cuts'))}
    result=assert_ok(client.post('/api/cuts/import',files={'file':('again.breadcuts',package,'application/octet-stream')}))
    assert result['additive'] is True
    imported=[x for x in assert_ok(client.get('/api/cuts')) if x['id'] not in before_ids]
    portable=next(x for x in imported if x['title']=='Portable provenance')
    assert portable['source_project']==original_project
    assert portable['source_chapter']==chapter['title']
    assert portable['source_chapter_id'] is None
    assert cut['id'] in {x['id'] for x in assert_ok(client.get('/api/cuts'))}
