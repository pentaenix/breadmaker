from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path


def assert_ok(resp):
    assert resp.status_code < 400, resp.text
    return resp.json()


def test_v47_version_and_compact_maps_rail_contract():
    assert tuple(map(int,Path('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    css=frontend_styles_source_from_styles_path(Path('static/styles.css'))
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert '.maps-sidebar.collapsed{width:58px;min-width:58px}' in css
    assert '.maps-sidebar.collapsed .map-tree-map-initial,.maps-sidebar.collapsed .map-tree-line-badge{display:grid}' in css
    assert 'map-tree-map-initial' in js
    assert 'map-tree-line-badge' in js
    assert 'background:var(--surface2)' in css


def test_v47_horizontal_ruler_gap_and_fit_are_zoom_aware():
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert 'MAP_HORIZONTAL_RULER_GAP=18' in js
    assert "crossMargin=orientation==='horizontal'?100+MAP_HORIZONTAL_RULER_GAP:100" in js
    assert 'function mapFitsViewportAtZoom' in js
    assert 'mapLogicalLayout(state.mapData,zoom)' in js
    assert 'for(let i=0;i<28;i++)' in js
    assert 'const MAP_ZOOM_MIN=.15' in js


def test_v47_span_junctions_persist_start_middle_end_and_station_stays_point(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'4.7 anchors'}))
    left=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Left'}))
    right=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Right'}))
    span=assert_ok(client.post(f"/api/map-lines/{left['id']}/items",json={'item_type':'span','title':'Duration','planned_start':.2,'planned_end':.6,'schematic_start':.2,'schematic_end':.6}))
    targets=[]
    for title,p in [('A',.2),('B',.5),('C',.8)]:
        targets.append(assert_ok(client.post(f"/api/map-lines/{right['id']}/items",json={'title':title,'planned_start':p,'schematic_start':p})))
    made=[]
    for edge,target in zip(('start','point','end'),targets):
        made.append(assert_ok(client.post(f"/api/maps/{story_map['id']}/junctions",json={'kind':'branch','from_item_id':span['id'],'from_edge':edge,'to_item_id':target['id'],'to_edge':'end'})))
    assert [j['from_edge'] for j in made] == ['start','point','end']
    # Stations have exactly one semantic anchor, regardless of a caller asking for an edge.
    assert all(j['to_edge']=='point' for j in made)
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    assert [j['from_edge'] for j in detail['junctions']] == ['start','point','end']


def test_v47_duplicate_preserves_span_anchor_semantics(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Anchor duplicate'}))
    a=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'A'}))
    b=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'B'}))
    span=assert_ok(client.post(f"/api/map-lines/{a['id']}/items",json={'item_type':'span','title':'Span','planned_start':.1,'planned_end':.7,'schematic_start':.1,'schematic_end':.7}))
    stop=assert_ok(client.post(f"/api/map-lines/{b['id']}/items",json={'title':'Stop','planned_start':.5,'schematic_start':.5}))
    assert_ok(client.post(f"/api/maps/{story_map['id']}/junctions",json={'kind':'merge','from_item_id':span['id'],'from_edge':'end','to_item_id':stop['id']}))
    duplicate=assert_ok(client.post(f"/api/maps/{story_map['id']}/duplicate"))
    detail=assert_ok(client.get(f"/api/maps/{duplicate['id']}"))
    assert len(detail['junctions'])==1
    assert detail['junctions'][0]['from_edge']=='end'


def test_v47_frontend_and_metro_resolve_span_anchor_semantics():
    js=frontend_source_from_app_path(Path('static/app.js'))
    assert "function mapItemAnchor(point,edge='point')" in js
    assert "edge==='start'?span.startT:edge==='end'?span.endT:(span.startT+span.endT)/2" in js
    assert 'renderMapSpanConnectionAnchors' in js
    assert "From Middle" in js
    assert "target.edge||'point'" in js
