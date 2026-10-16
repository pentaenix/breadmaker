from source_contracts import frontend_source_from_app_path
from pathlib import Path


def assert_ok(response):
    assert response.status_code < 400, response.text
    return response.json()


def test_v632_line_ui_is_details_driven_and_menu_is_pruned():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    start=js.index('function mapLineMenu(line,position=null){')
    end=js.index('function focusMapLine',start)
    menu=js[start:end]
    assert "label:'Details…'" in menu
    assert "line.visible?'Hide':'Show'" in menu
    assert "label:'Add Station'" in menu and "label:'Add Span'" in menu
    assert "label:'Branch New Line Here'" in menu
    assert "label:'Delete…'" in menu
    for retired in ("label:'Focus'","label:'Color…'","label:'Populate'","label:'New Line'","label:'Import JSON…'","label:'Connect to Another Line…'","label:'Duplicate as Manual Line'","label:'Lock Line'","label:'Unlock Line'"):
        assert retired not in menu
    assert 'function openMapLineInspector' not in js
    assert "openMapLineDetails(line)" in js
    assert "mapLineClipboard" in js and "copySelectedMapLine" in js and "pasteMapLine" in js


def test_v632_duplicate_manual_can_paste_into_another_map(client,fresh_project):
    first=assert_ok(client.post('/api/maps',json={'name':'First'}))
    second=assert_ok(client.post('/api/maps',json={'name':'Second'}))
    line=assert_ok(client.post(f"/api/maps/{first['id']}/lines",json={'name':'Arc','color':'#336699','description':'Copied details'}))
    item=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Beat','planned_start':.3,'schematic_start':.3}))
    copy=assert_ok(client.post(f"/api/map-lines/{line['id']}/duplicate-manual?target_map_id={second['id']}"))
    assert copy['map_id']==second['id']
    detail=assert_ok(client.get(f"/api/maps/{second['id']}"))
    pasted=next(row for row in detail['lines'] if row['id']==copy['id'])
    assert pasted['name']=='Arc Copy'
    assert pasted['description']=='Copied details'
    assert pasted['color'].lower()=='#336699'
    assert pasted['provenance']=='manual'
    assert [x['title'] for x in pasted['items']]==['Beat']
