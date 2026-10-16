from __future__ import annotations
from pathlib import Path
from source_contracts import frontend_styles_source_from_styles_path
from source_contracts import frontend_source_from_app_path

from storage import mutations as storage_mutations
from conftest import assert_ok


def test_v451_junction_endpoint_can_be_repositioned_without_recreating_connection(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Reconnect'}))
    a=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Bohor'}))
    b=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Story'}))
    station=assert_ok(client.post(f"/api/map-lines/{b['id']}/items",json={'title':'Appearance','planned_start':.72,'schematic_start':.72}))
    junction=assert_ok(client.post(f"/api/maps/{story_map['id']}/junctions",json={
        'kind':'merge',
        'from_line_id':a['id'],'from_edge':'point','from_planned_position':.25,'from_schematic_position':.25,
        'to_line_id':b['id'],'to_edge':'point','to_planned_position':.55,'to_schematic_position':.55,
    }))
    moved=assert_ok(client.patch(f"/api/map-junctions/{junction['id']}",json={
        'from_line_id':a['id'],'from_item_id':None,'from_edge':'point','from_planned_position':.63,'from_schematic_position':.63,
        'to_line_id':b['id'],'to_item_id':station['id'],'to_edge':'point','to_planned_position':None,'to_schematic_position':None,
    }))
    assert moved['id']==junction['id']
    assert moved['from_line_id']==a['id'] and moved['from_planned_position']==.63
    assert moved['to_line_id']==b['id'] and moved['to_item_id']==station['id']
    assert moved['to_planned_position'] is None and moved['to_schematic_position'] is None
    with storage_mutations.tx() as con:
        assert con.execute('PRAGMA foreign_key_check').fetchone() is None


def test_v451_junction_reposition_rejects_collapsing_both_ends_onto_one_line(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Reconnect Guard'}))
    a=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'A'}))
    b=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'B'}))
    junction=assert_ok(client.post(f"/api/maps/{story_map['id']}/junctions",json={
        'kind':'merge',
        'from_line_id':a['id'],'from_edge':'point','from_planned_position':.2,'from_schematic_position':.2,
        'to_line_id':b['id'],'to_edge':'point','to_planned_position':.8,'to_schematic_position':.8,
    }))
    denied=client.patch(f"/api/map-junctions/{junction['id']}",json={
        'to_line_id':a['id'],'to_item_id':None,'to_edge':'point','to_planned_position':.7,'to_schematic_position':.7,
    })
    assert denied.status_code==400 and 'MAP_021' in denied.text


def test_v451_maps_visual_contract_has_compact_spans_reconnect_handles_and_vertical_chapter_ruler():
    js=frontend_source_from_app_path('static/app.js')
    css=frontend_styles_source_from_styles_path(Path('static/styles.css'))
    assert 'MAP_SPAN_STROKE=6, MAP_SPAN_CROSS=20, MAP_SPAN_MIN_AXIS=26' in js
    assert 'Connect to Another Line…' in js
    assert 'Adjust Connection Points' in js
    assert 'map-junction-handle' in js
    assert 'map-ruler-chapter-label' in js and 'rotate(90' in js
    assert '.map-ruler-chapter-label{fill-opacity:.42}' in css
