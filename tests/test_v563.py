from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v563_release_source_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static'/'app.js')
    css=frontend_styles_source_from_styles_path((root/'static'/'styles.css'))
    html=(root/'static'/'index.html').read_text()
    app=backend_source(root)
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 6, 3)
    assert '?v=5.6.3' in html
    assert "return (\"right\",\"left\")" in app
    assert '/nodes/delete-batch' in app and '/nodes/delete-batch' in js
    assert 'Drag any selected object to move the group.' not in js
    assert 'Figures, images, Routing Points, and Indexes move with the selection' not in js
    assert 'connector-end-none-svg' in js
    assert 'background:var(--surface);padding-left:2px' not in css
    assert 'calc(var(--canvas-font-size,14px) * .78)' in css
    assert 'connectorColorOptions' in js


def _card(client,cid,title,x):
    r=client.post(f'/api/canvases/{cid}/nodes',json={'node_type':'free','shape':'rect','title':title,'x':x,'y':100,'w':180,'h':90})
    assert r.status_code==200,r.text
    return r.json()


def test_v563_populate_always_right_to_left(client,fresh_project):
    canvas=client.post('/api/canvases',json={'name':'Populate Direction','canvas_type':'blank'}).json()
    response=client.post(f"/api/canvases/{canvas['id']}/populate",json={'template_id':'character_arc','x':100,'y':120})
    assert response.status_code==200,response.text
    edges=response.json()['edges']
    assert len(edges)>=2
    assert all(edge['from_port']=='right' and edge['to_port']=='left' for edge in edges)


def test_v563_connector_marker_and_bulk_delete(client,fresh_project):
    marker=client.post('/api/labels',json={'name':'Flow','description':'Connector meaning','color':'#336699'}).json()
    canvas=client.post('/api/canvases',json={'name':'Connector Marker','canvas_type':'blank'}).json()
    a=_card(client,canvas['id'],'A',100); b=_card(client,canvas['id'],'B',400); c=_card(client,canvas['id'],'C',700)
    edge=client.post(f"/api/canvases/{canvas['id']}/edges",json={'from_node_id':a['id'],'to_node_id':b['id'],'marker_id':marker['id'],'color':'#ff0000','label':'Flow'}).json()
    assert edge['marker_id']==marker['id']
    assert edge['color']=='#336699'
    usage=client.get(f"/api/labels/{marker['id']}/usage").json()
    assert usage['edges']>=1

    changed=client.patch(f"/api/labels/{marker['id']}",json={'color':'#884422'})
    assert changed.status_code==200,changed.text
    detail=client.get(f"/api/canvases/{canvas['id']}").json()
    saved=next(e for e in detail['edges'] if e['id']==edge['id'])
    assert saved['marker_id']==marker['id'] and saved['color']=='#884422'

    custom=client.patch(f"/api/edges/{edge['id']}",json={'color':'#123456'})
    assert custom.status_code==200,custom.text
    detail=client.get(f"/api/canvases/{canvas['id']}").json()
    saved=next(e for e in detail['edges'] if e['id']==edge['id'])
    assert saved['marker_id'] is None and saved['color']=='#123456'

    deleted=client.post(f"/api/canvases/{canvas['id']}/nodes/delete-batch",json={'ids':[a['id'],b['id'],c['id']]})
    assert deleted.status_code==200,deleted.text
    assert set(deleted.json()['deleted'])=={a['id'],b['id'],c['id']}
    detail=client.get(f"/api/canvases/{canvas['id']}").json()
    assert detail['nodes']==[] and detail['edges']==[]
