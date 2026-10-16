from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v433_background_markers_and_icon_markers_are_single_canonical_channels(client,fresh_project):
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Meanings','canvas_type':'blank'}))
    color_marker=assert_ok(client.post('/api/labels',json={'name':'Love','color':'#cc6688'}))
    icon_marker=assert_ok(client.post('/api/icon-markers',json={'name':'Important','icon':'★','description':'Important scene'}))
    node=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={
        'title':'Scene','label_id':color_marker['id'],'icon_marker_id':icon_marker['id']
    }))
    assert node['color']=='#cc6688'
    assert node['icon']=='★'
    assert node['label_id']==color_marker['id']
    assert node['icon_marker_id']==icon_marker['id']

    # Editing a reusable Marker updates the visual it owns.
    assert_ok(client.patch(f"/api/labels/{color_marker['id']}",json={'color':'#884466'}))
    assert_ok(client.patch(f"/api/icon-markers/{icon_marker['id']}",json={'icon':'!'}))
    saved=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['nodes'][0]
    assert saved['color']=='#884466' and saved['icon']=='!'

    # A hand-picked value is not a Marker and detaches the reusable meaning.
    assert_ok(client.patch(f"/api/nodes/{node['id']}",json={'color':'#123456'}))
    assert_ok(client.patch(f"/api/nodes/{node['id']}",json={'icon':'?'}))
    detached=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['nodes'][0]
    assert detached['color']=='#123456' and detached['label_id'] is None
    assert detached['icon']=='?' and detached['icon_marker_id'] is None

    # Selecting a Marker again makes it canonical, regardless of stale raw values sent with it.
    assert_ok(client.patch(f"/api/nodes/{node['id']}",json={
        'label_id':color_marker['id'],'color':'#ffffff','icon_marker_id':icon_marker['id'],'icon':'x'
    }))
    reassigned=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['nodes'][0]
    assert reassigned['color']=='#884466' and reassigned['icon']=='!'


def test_v433_revision_roundtrip_preserves_current_marker_model(client,fresh_project):
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Roundtrip'}))
    color_marker=assert_ok(client.post('/api/labels',json={'name':'Hope','color':'#ddcc66'}))
    icon_marker=assert_ok(client.post('/api/icon-markers',json={'name':'Key','icon':'◆'}))
    node=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={
        'title':'A','label_id':color_marker['id'],'icon_marker_id':icon_marker['id']
    }))
    rev=assert_ok(client.post('/api/autocommit?label='+__import__('urllib.parse').parse.quote(str('Current canvas model'))))['id']
    assert_ok(client.delete(f"/api/nodes/{node['id']}"))
    assert_ok(client.post(f'/api/revisions/{rev}/restore'))
    data=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    restored=next(n for n in data['nodes'] if n['id']==node['id'])
    assert restored['label_id']==color_marker['id']
    assert restored['icon_marker_id']==icon_marker['id']
    assert restored['color']=='#ddcc66' and restored['icon']=='◆'


def test_v433_canvas_ui_contract_is_single_background_single_icon_and_versioned():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert 'class="canvas-mode-overlay"' in html
    assert 'image-object' in js and 'canvas-free-image' in js
    assert "node.node_type==='image'" in js
    assert "node.shape==='diamond'" not in js and 'edgeGeometry' in js and 'endTangent' in js
    assert 'nodeBackgroundChoice' in js and 'nodeIconChoice' in js
    assert 'backgroundChoiceOptions' in js and 'iconChoiceOptions' in js
    assert 'icon_marker_id' in js and 'state.iconMarkers' in js
    assert 'nodeColorLegend' not in js and 'nodeIconLegend' not in js
    assert '/api/legend' not in js and 'state.legend=' not in js and 'state.legend.find' not in js
    assert '.canvas-node.diamond::after' not in css
    assert '.canvas-node.text .node-title-line' in css
    assert '.canvas-node.image-object' in css
