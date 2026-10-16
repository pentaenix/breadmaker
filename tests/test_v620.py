from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v620_maps_standardization_contract(client):
    # New Maps/Lines use full timeline by default.
    created=client.post('/api/maps',json={'name':'Story Map'}); assert created.status_code==200
    mid=created.json()['id']
    line=client.post(f'/api/maps/{mid}/lines',json={'name':'Main Plot'}); assert line.status_code==200
    payload=line.json()
    assert payload['planned_start']==0.0 and payload['planned_end']==1.0
    assert payload['schematic_start']==0.0 and payload['schematic_end']==1.0

    # Percentage ruler remains a first-class Map setting.
    changed=client.patch(f'/api/maps/{mid}',json={'ruler':'percent'}); assert changed.status_code==200
    detail=client.get(f'/api/maps/{mid}').json()
    assert detail['map']['ruler']=='percent'


def test_v620_map_reorder_is_project_scoped(client):
    first=client.post('/api/maps',json={'name':'First'}).json()
    second=client.post('/api/maps',json={'name':'Second'}).json()
    current=[m['id'] for m in client.get('/api/bootstrap').json()['maps']]
    assert first['id'] in current and second['id'] in current
    reversed_ids=list(reversed(current))
    response=client.post('/api/maps/reorder',json={'ids':reversed_ids})
    assert response.status_code==200
    assert [m['id'] for m in client.get('/api/bootstrap').json()['maps']]==reversed_ids


def test_v620_source_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'id="mapViewPopover"' in html
    assert 'id="mapViewRuler"' in html and '<option value="percent">Percentage</option>' in html
    assert 'id="editMapDetailsBtn"' in html
    assert 'map-tree-line-add' not in html
    assert "${collapsed?'▸':'▾'}" in js
    assert 'map-tree-more manuscript-item-more' in js
    assert 'map-tree-grip chapter-grip' in js
    assert "api('/api/ui-state/experimental-features')" in js
    assert "map_renderer:saved.map_renderer===true" in js
    assert '.maps-sidebar{width:236px;min-width:196px' in css
    assert '.map-inspector{width:290px;min-width:255px}' in css
    assert '.metro-view-button.hidden{display:none!important}' in css


def test_v620_fragment_created_lines_share_full_timeline_default(client,fresh_project):
    import io,json
    story=client.post('/api/maps',json={'name':'Import Target'}).json()
    fragment={
        'format':'breadmaker-fragment','schema_version':'1.0','name':'Full Line',
        'mounts':[{'target':{'type':'map','name':'Import Target'},'lines':[{'id':'line:full','name':'Imported Plot'}]}],
    }
    raw=json.dumps(fragment).encode('utf-8')
    files={'file':('full-line.breadmaker.json',io.BytesIO(raw),'application/json')}
    preview=client.post('/api/json-import/preview',files=files,data={'context':'{}'})
    assert preview.status_code==200,preview.text
    raw=json.dumps(fragment).encode('utf-8')
    imported=client.post('/api/json-import',files={'file':('full-line.breadmaker.json',io.BytesIO(raw),'application/json')},data={'context':'{}','target_choices':'{}'})
    assert imported.status_code==200,imported.text
    detail=client.get(f"/api/maps/{story['id']}").json()
    line=next(row for row in detail['lines'] if row['name']=='Imported Plot')
    assert line['planned_start']==0.0 and line['planned_end']==1.0
    assert line['schematic_start']==0.0 and line['schematic_end']==1.0
