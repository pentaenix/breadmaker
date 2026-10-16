from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]


def test_v422_routing_points_and_labels_have_distinct_connector_rules(client, fresh_project):
    canvas = assert_ok(client.post('/api/canvases', json={'name':'Routing'}))
    a = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'title':'A','x':100,'y':100}))
    routing = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'node_type':'routing','shape':'routing','x':360,'y':150,'w':18,'h':18}))
    b = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'title':'B','x':600,'y':260}))
    label = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'node_type':'label','shape':'label','title':'1000','x':240,'y':330,'w':40,'h':40}))

    e1 = assert_ok(client.post(f"/api/canvases/{canvas['id']}/edges", json={'from_node_id':a['id'],'to_node_id':routing['id'],'route_style':'straight'}))
    e2 = assert_ok(client.post(f"/api/canvases/{canvas['id']}/edges", json={'from_node_id':routing['id'],'to_node_id':b['id'],'from_port':'right','to_port':'left','route_style':'curved'}))
    assert e1['route_style'] == 'straight'
    assert e2['route_style'] == 'curved'

    rejected = client.post(f"/api/canvases/{canvas['id']}/edges", json={'from_node_id':a['id'],'to_node_id':label['id']})
    assert rejected.status_code == 400
    assert 'cannot be connected' in rejected.text

    assert_ok(client.patch(f"/api/nodes/{label['id']}", json={'title':'7'}))
    duplicate = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'node_type':'label','shape':'label','title':'7','x':300,'y':330,'w':40,'h':40}))
    assert duplicate['title'] == '7'
    invalid = client.patch(f"/api/nodes/{label['id']}", json={'title':'AB'})
    assert invalid.status_code == 400

    color_marker=assert_ok(client.post('/api/labels',json={'name':'Meaning','color':'#445566'}))
    icon_marker=assert_ok(client.post('/api/icon-markers',json={'name':'Meaning icon','icon':'★'}))
    assert client.patch(f"/api/nodes/{label['id']}",json={'label_id':color_marker['id']}).status_code==400
    assert client.patch(f"/api/nodes/{routing['id']}",json={'icon_marker_id':icon_marker['id']}).status_code==400


def test_v422_static_canvas_contracts():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'id="canvasHandMode"' in html and 'id="canvasPointerMode"' in html
    assert 'id="canvasZoomFit"' in html and 'id="canvasWorld"' in html
    assert "state.canvasMode" in js and "state.canvasSpaceHeld?'hand':state.canvasMode" in js and 'beginCanvasLasso' in js
    assert 'event.shiftKey||event.ctrlKey||event.metaKey' in js
    assert "node_type:'routing'" in js and "node_type:'label'" in js
    assert "edge.route_style||'curved'" in js
    assert 'lastCanvasNumericLabelId' in js
    assert '.routing-node' in css and '.label-node' in css
