from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def _main_tab(client):
    boot=client.get('/api/bootstrap').json()
    return next(d for d in boot['tabs'] if d.get('is_manuscript'))


def test_v630_unlocked_linked_station_can_use_manual_override_without_losing_link(client,fresh_project):
    tab=_main_tab(client)
    chapter=client.post('/api/chapters',json={'title':'Linked Chapter','tab_id':tab['id']}).json()
    story=client.post('/api/maps',json={'name':'Movement Map','layout_mode':'scaled'}).json()
    line=client.post(f"/api/maps/{story['id']}/lines",json={'name':'Character Arc'}).json()
    station=client.post(f"/api/map-lines/{line['id']}/items",json={
        'item_type':'stop','title':'Linked Beat','planned_start':.82,'schematic_start':.61,
        'start_anchor_type':'chapter','start_anchor_id':str(chapter['id'])
    }).json()
    detail=client.get(f"/api/maps/{story['id']}").json()
    current=next(i for l in detail['lines'] if l['id']==line['id'] for i in l['items'] if i['id']==station['id'])
    assert current['start_source']=='manuscript'
    assert current['linked'] is True
    assert abs(current['scaled_start']-.82)<1e-6
    assert next(l for l in detail['lines'] if l['id']==line['id'])['ruled'] is True

    assert client.patch(f"/api/map-lines/{line['id']}",json={'layout_locked':True}).status_code==200
    locked=client.get(f"/api/maps/{story['id']}").json()
    locked_item=next(i for l in locked['lines'] if l['id']==line['id'] for i in l['items'] if i['id']==station['id'])
    assert locked_item['start_source']=='manuscript'
    assert locked_item['linked'] is True
    assert abs(locked_item['scaled_start']-.82)>1e-3


def test_v630_source_population_defaults_above_unruled_lines(client,fresh_project):
    tab=_main_tab(client)
    client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab['id']})
    story=client.post('/api/maps',json={'name':'Grouped Map'}).json()
    manual=client.post(f"/api/maps/{story['id']}/lines",json={'name':'Loose Notes'}).json()
    catalog=client.get('/api/map-populate').json()
    template=None
    for category in catalog['categories']:
        for row in category['templates']:
            if row.get('source')=='project.chapters': template=row['id']; break
        if template: break
    assert template
    populated=client.post(f"/api/maps/{story['id']}/populate",json={'template_id':template})
    assert populated.status_code==200,populated.text
    detail=client.get(f"/api/maps/{story['id']}").json()
    ids=[l['id'] for l in detail['lines']]
    assert ids.index(populated.json()['line_id']) < ids.index(manual['id'])
    assert next(l for l in detail['lines'] if l['id']==populated.json()['line_id'])['ruled'] is True
    assert next(l for l in detail['lines'] if l['id']==manual['id'])['ruled'] is False


def test_v630_span_junctions_preserve_start_middle_end(client,fresh_project):
    story=client.post('/api/maps',json={'name':'Connection Map'}).json()
    a=client.post(f"/api/maps/{story['id']}/lines",json={'name':'A'}).json()
    b=client.post(f"/api/maps/{story['id']}/lines",json={'name':'B'}).json()
    sa=client.post(f"/api/map-lines/{a['id']}/items",json={'item_type':'span','title':'Source Span','planned_start':.2,'planned_end':.4,'schematic_start':.2,'schematic_end':.4}).json()
    sb=client.post(f"/api/map-lines/{b['id']}/items",json={'item_type':'span','title':'Target Span','planned_start':.5,'planned_end':.7,'schematic_start':.5,'schematic_end':.7}).json()
    for from_edge,to_edge in [('start','end'),('point','point'),('end','start')]:
        created=client.post(f"/api/maps/{story['id']}/junctions",json={'kind':'merge','from_item_id':sa['id'],'from_edge':from_edge,'to_item_id':sb['id'],'to_edge':to_edge})
        assert created.status_code==200,created.text
        row=created.json();assert row['from_edge']==from_edge and row['to_edge']==to_edge


def test_v630_release_contract_source():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'function mapLineIsRuled' in js
    assert 'renderMapSpanConnectOutputs' in js
    assert "to_edge:target.edge||'point'" in js
    assert 'mapRulerCollisionRects' in js
    assert '.map-span-connect-output' in css
    assert '.map-inspector-switch-row' in css
