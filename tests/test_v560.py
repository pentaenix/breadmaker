from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v560_canvas_source_contract():
    root = Path(__file__).resolve().parents[1]
    js = frontend_source_from_app_path(root/'static'/'app.js')
    css = frontend_styles_source_from_styles_path((root/'static'/'styles.css'))
    html = (root/'static'/'index.html').read_text()
    app = backend_source(root)
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 6, 0)
    assert '?v=5.6.0' in html

    assert "if(node.node_type==='routing')return['center']" in js
    assert "node_type:'routing',w:12,h:12" in js
    assert 'A Routing Point organizes connector paths' not in js
    assert 'id="edgeFromNode"' not in js
    assert 'id="edgeFromPort"' not in js
    assert 'id="edgeToNode"' not in js
    assert 'id="edgeToPort"' not in js
    assert 'connector-choice-group' in css
    assert '.edge-label' in css
    assert 'edgeVisualPath(geometry,edge,b)' in js
    assert 'split-developmental-lane' in js
    assert '<strong>Snap</strong>' in html
    assert "'center'" in app and 'VALID_EDGE_PORTS' in app


def test_v560_routing_center_accepts_many_inputs_and_outputs(client, fresh_project):
    canvas = client.post('/api/canvases', json={'name':'Routing','canvas_type':'blank'}).json()
    cards=[]
    for i in range(4):
        cards.append(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
            'node_type':'free','shape':'rect','title':f'Card {i+1}','x':50+i*240,'y':80,'w':180,'h':90
        }).json())
    routing = client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        'node_type':'routing','shape':'routing','x':400,'y':260,'w':12,'h':12
    }).json()
    for source in cards[:2]:
        r=client.post(f"/api/canvases/{canvas['id']}/edges", json={
            'from_node_id':source['id'],'to_node_id':routing['id'],'from_port':'bottom','to_port':'center'
        })
        assert r.status_code == 200, r.text
    for target in cards[2:]:
        r=client.post(f"/api/canvases/{canvas['id']}/edges", json={
            'from_node_id':routing['id'],'to_node_id':target['id'],'from_port':'center','to_port':'bottom'
        })
        assert r.status_code == 200, r.text
    detail=client.get(f"/api/canvases/{canvas['id']}").json()
    assert len(detail['edges']) == 4
    assert sum(e['to_port']=='center' for e in detail['edges']) == 2
    assert sum(e['from_port']=='center' for e in detail['edges']) == 2


def test_v560_populate_is_reading_order_and_cards_grow_for_text(client, fresh_project):
    canvas=client.post('/api/canvases',json={'name':'Populate','canvas_type':'blank'}).json()
    result=client.post(f"/api/canvases/{canvas['id']}/populate",json={'template_id':'character_arc','x':100,'y':120})
    assert result.status_code == 200, result.text
    data=result.json(); nodes=data['nodes']
    assert len(nodes) >= 6
    # Row-major: first row moves left-to-right instead of alternating snake rows.
    xs=[n['x'] for n in nodes[:5]]
    assert xs == sorted(xs)
    # Later row returns to the starting x.
    assert nodes[5]['x'] == nodes[0]['x']
    # Template text is allowed to make a card taller than the old fixed minimum.
    assert max(n['h'] for n in nodes) >= 125
