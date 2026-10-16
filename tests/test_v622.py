from __future__ import annotations
from source_contracts import frontend_source_from_app_path

import io
import json
from pathlib import Path


def upload(doc: dict, filename: str = "fragment.breadmaker.json"):
    raw=json.dumps(doc,ensure_ascii=False).encode('utf-8')
    return {'file':(filename,io.BytesIO(raw),'application/json')}


def test_v622_collection_menu_and_icon_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    assert 'id="addCanvas" class="manuscript-item-more nav-section-more canvas-collection-more"' in html
    assert 'id="newMapBtn" class="manuscript-item-more nav-section-more map-collection-more"' in html
    assert "label:'Add Canvas'" in js and "label:'Import Canvas…'" in js
    assert "label:'Add Map'" in js and "label:'Import Map…'" in js
    assert "$('.canvas-rail-head').oncontextmenu=openCanvasCollectionMenu" in js
    assert "$('.map-tree-head').oncontextmenu=openMapCollectionMenu" in js
    assert 'class="map-view-icon-label-extra" d="M15.5 7h3M15.5 10h3M15.5 13h3"' in html


def test_v622_scoped_canvas_and_map_fragment_imports(client,fresh_project):
    canvas_fragment={
        'format':'breadmaker-fragment','schema_version':'1.0','name':'Canvas Pack',
        'markers':[{'id':'marker:idea','name':'Idea','color':'#667788'}],
        'canvases':[{'id':'canvas:imported','name':'Imported Canvas','nodes':[{'id':'card:a','type':'card','title':'A','marker':'marker:idea'}]}],
    }
    canvas_context=json.dumps({'view':'canvas','fragment_scope':'canvas','canvas_id':None})
    preview=client.post('/api/json-import/preview',files=upload(canvas_fragment),data={'context':canvas_context})
    assert preview.status_code==200,preview.text
    result=client.post('/api/json-import',files=upload(canvas_fragment),data={'context':canvas_context,'target_choices':'{}'})
    assert result.status_code==200,result.text

    map_fragment={
        'format':'breadmaker-fragment','schema_version':'1.0','name':'Map Pack',
        'maps':[{'id':'map:imported','name':'Imported Map','lines':[{'id':'line:main','name':'Main'}]}],
    }
    map_context=json.dumps({'view':'maps','fragment_scope':'map','map_id':None,'map_line_id':None})
    preview=client.post('/api/json-import/preview',files=upload(map_fragment),data={'context':map_context})
    assert preview.status_code==200,preview.text
    result=client.post('/api/json-import',files=upload(map_fragment),data={'context':map_context,'target_choices':'{}'})
    assert result.status_code==200,result.text


def test_v622_scoped_import_rejects_project_and_mixed_fragment(client,fresh_project):
    canvas_context=json.dumps({'view':'canvas','fragment_scope':'canvas','canvas_id':None})
    project={'format':'breadmaker-project','schema_version':'1.0','name':'Wrong place'}
    response=client.post('/api/json-import/preview',files=upload(project,'project.breadmaker.json'),data={'context':canvas_context})
    assert response.status_code==400 and 'expects Breadmaker Fragment JSON' in response.text

    mixed={
        'format':'breadmaker-fragment','schema_version':'1.0','name':'Mixed',
        'canvases':[{'id':'canvas:a','name':'A'}],
        'tasks':[{'id':'task:a','title':'A'}],
    }
    response=client.post('/api/json-import/preview',files=upload(mixed),data={'context':canvas_context})
    assert response.status_code==400 and 'supporting Markers only' in response.text


def test_v622_version_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (6, 2, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    assert '/static/styles.css?v=6.2.2' in html
    assert '/static/app.js?v=6.2.2' in html
