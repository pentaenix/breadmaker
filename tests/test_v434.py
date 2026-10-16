from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
import json
from pathlib import Path

from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v434_populate_catalog_is_json_driven_and_contains_core_templates(client,fresh_project):
    config=json.loads((ROOT/'config/story_templates.json').read_text(encoding='utf-8'))
    assert config['version']==1
    configured={t['id'] for c in config['categories'] for t in c['templates']}
    assert {'chapters','scenes','character_arc','save_the_cat','hero_journey'} <= configured

    catalog=assert_ok(client.get('/api/canvas-populate'))
    returned={t['id']:t for c in catalog['categories'] for t in c['templates']}
    assert configured==set(returned)
    assert returned['save_the_cat']['item_count']==15
    map_catalog=assert_ok(client.get('/api/map-populate'))
    maps={t['id']:t for c in map_catalog['categories'] for t in c['templates']}
    assert maps['hero_journey']['item_count']==12
    assert maps['save_the_cat']['item_count']==15


def test_v434_populate_framework_is_repeatable_and_sequence_is_connected(client,fresh_project):
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Frameworks','canvas_type':'blank'}))
    first=assert_ok(client.post(f"/api/canvases/{canvas['id']}/populate",json={'template_id':'save_the_cat','x':100,'y':120}))
    assert len(first['nodes'])==15
    assert len(first['edges'])==14
    assert first['nodes'][0]['title']=='Opening Image'
    assert first['nodes'][-1]['title']=='Final Image'

    second=assert_ok(client.post(f"/api/canvases/{canvas['id']}/populate",json={'template_id':'save_the_cat','x':1600,'y':120}))
    assert len(second['nodes'])==15 and len(second['edges'])==14
    saved=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    assert len(saved['nodes'])==30
    assert len(saved['edges'])==28
    assert min(n['x'] for n in second['nodes'])>=1600


def test_v434_populate_chapters_and_scenes_are_live_project_data(client,fresh_project):
    boot=assert_ok(client.get('/api/bootstrap'))
    manuscript=next(tab for tab in boot['tabs'] if tab['is_manuscript'])
    assert_ok(client.post('/api/chapters',json={'title':'Second Chapter','tab_id':manuscript['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Live structure','canvas_type':'blank'}))
    chapters=assert_ok(client.post(f"/api/canvases/{canvas['id']}/populate",json={'template_id':'chapters','x':40,'y':40}))
    current=assert_ok(client.get('/api/bootstrap'))
    manuscript_chapters=[c for c in current['chapters'] if c['tab_id']==manuscript['id']]
    assert len(chapters['nodes'])==len(manuscript_chapters)
    assert all(n['node_type']=='chapter' and n['chapter_id'] for n in chapters['nodes'])

    scenes_response=client.post(f"/api/canvases/{canvas['id']}/populate",json={'template_id':'scenes','x':40,'y':500})
    assert scenes_response.status_code==400
    assert 'Nothing is available for Scenes yet' in scenes_response.text


def test_v434_old_hardcoded_canvas_seeds_are_removed(client,fresh_project):
    assert client.post('/api/canvases',json={'name':'Old character','canvas_type':'character'}).status_code==400
    assert client.post('/api/canvases',json={'name':'Old plot','canvas_type':'plot'}).status_code==400
    source=backend_source(ROOT)
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert 'def seed_canvas' not in source
    assert "'character_arc'" not in source.split('VALID_CANVAS_TYPES=',1)[1].split('\n',1)[0]
    assert 'SAVE_THE_CAT_SLOTS' not in js


def test_v434_ui_contract_has_nested_populate_menu_and_versioned_assets():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert "label:'Populate'" in js
    assert 'canvasPopulateCatalog' in js and '/api/canvas-populate' in js
    assert '/populate' in js and 'withStructuralUndo(`Populate canvas:' in js
    assert 'context-submenu' in css and 'context-heading' in css
    assert '<option value="character">Character</option>' not in js
    assert '<option value="plot">Plot</option>' not in js
