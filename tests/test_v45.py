from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import mutations as storage_mutations
from conftest import assert_ok


def test_v45_map_defaults_are_scaled_minimal_and_pages_is_supported(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Readable Map'}))
    assert story_map['layout_mode']=='scaled'
    assert story_map['label_mode']=='minimal'
    updated=client.patch(f"/api/maps/{story_map['id']}",json={'ruler':'pages','label_mode':'hidden'})
    assert_ok(updated)
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    assert detail['map']['ruler']=='pages'
    assert detail['map']['label_mode']=='hidden'


def test_v45_manual_line_has_geometry_and_is_unlocked(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Geometry'}))
    line=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Manual'}))
    assert line['provenance']=='manual'
    assert line['source_template_id'] is None
    assert line['layout_locked']==0
    assert line['planned_start'] < line['planned_end']
    assert line['schematic_start'] < line['schematic_end']


def test_v45_population_creates_locked_source_line_and_line_population_is_contextual(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Sources'}))
    manual=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Use Me'}))
    result=assert_ok(client.post(f"/api/map-lines/{manual['id']}/populate",json={'template_id':'save_the_cat'}))
    assert result['line_id']==manual['id']
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    assert len(detail['lines'])==1
    line=detail['lines'][0]
    assert line['id']==manual['id']
    assert line['provenance']=='source'
    assert line['source_template_id']=='save_the_cat'
    assert line['layout_locked']==1
    assert len(line['items'])==15
    assert all(item['source_key'] for item in line['items'])


def test_v45_source_line_synchronizes_live_chapters_even_after_layout_unlock(client, fresh_project):
    boot=fresh_project['bootstrap']; manuscript=next(t for t in boot['tabs'] if t['is_manuscript'])
    story_map=assert_ok(client.post('/api/maps',json={'name':'Live Chapters'}))
    populated=assert_ok(client.post(f"/api/maps/{story_map['id']}/populate",json={'template_id':'chapters'}))
    line_id=populated['line_id']
    first=assert_ok(client.get(f"/api/maps/{story_map['id']}"))['lines'][0]
    assert first['layout_locked']==1 and first['provenance']=='source'
    first_item=first['items'][0]

    assert_ok(client.patch(f"/api/map-lines/{line_id}",json={'layout_locked':False}))
    assert_ok(client.patch(f"/api/map-items/{first_item['id']}",json={'schematic_start':.31}))
    second_chapter=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':manuscript['id']}))

    synced=assert_ok(client.get(f"/api/maps/{story_map['id']}"))['lines'][0]
    by_key={item['source_key']:item for item in synced['items']}
    assert by_key[first_item['source_key']]['schematic_start']==.31
    assert f"chapter:{second_chapter['id']}" in by_key
    assert synced['source_template_id']=='chapters'
    assert synced['layout_locked']==0


def test_v45_locked_line_rejects_layout_motion_but_unlock_preserves_source(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Locks'}))
    populated=assert_ok(client.post(f"/api/maps/{story_map['id']}/populate",json={'template_id':'save_the_cat'}))
    line_id=populated['line_id']
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}")); line=detail['lines'][0]; item=line['items'][0]
    denied=client.patch(f"/api/map-lines/{line_id}",json={'lane_offset':25})
    assert denied.status_code==409 and 'MAP_027' in denied.text
    denied_item=client.patch(f"/api/map-items/{item['id']}",json={'schematic_start':.2})
    assert denied_item.status_code==409 and 'MAP_028' in denied_item.text
    assert_ok(client.patch(f"/api/map-lines/{line_id}",json={'layout_locked':False,'lane_offset':25}))
    assert_ok(client.patch(f"/api/map-items/{item['id']}",json={'schematic_start':.2}))
    after=assert_ok(client.get(f"/api/maps/{story_map['id']}"))['lines'][0]
    assert after['provenance']=='source' and after['source_template_id']=='save_the_cat'
    assert after['layout_locked']==0 and after['lane_offset']==25


def test_v45_duplicate_as_manual_breaks_source_sync_without_losing_anchors(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Duplicate Source'}))
    populated=assert_ok(client.post(f"/api/maps/{story_map['id']}/populate",json={'template_id':'chapters'}))
    copy=assert_ok(client.post(f"/api/map-lines/{populated['line_id']}/duplicate-manual"))
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    line=next(row for row in detail['lines'] if row['id']==copy['id'])
    assert line['provenance']=='manual' and line['source_template_id'] is None and line['layout_locked']==0
    assert all(item['source_key'] is None for item in line['items'])
    assert any(item['start_anchor_type']=='chapter' for item in line['items'])


def test_v45_geometry_junction_can_connect_line_endpoint_to_route_without_fake_station(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Direct Geometry'}))
    orange=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Orange'}))
    blue=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Blue'}))
    junction=assert_ok(client.post(f"/api/maps/{story_map['id']}/junctions",json={
        'kind':'branch',
        'from_line_id':orange['id'],'from_edge':'point','from_planned_position':.4,'from_schematic_position':.4,
        'to_line_id':blue['id'],'to_edge':'start'
    }))
    assert junction['from_item_id'] is None and junction['to_item_id'] is None
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    assert sum(len(line['items']) for line in detail['lines'])==0
    assert detail['junctions'][0]['from_line_id']==orange['id']
    assert detail['junctions'][0]['to_line_id']==blue['id']
    with storage_mutations.tx() as con:
        assert con.execute('PRAGMA foreign_key_check').fetchone() is None


def test_v45_product_shell_uses_map_tree_and_single_view_control():
    html=open('static/index.html',encoding='utf-8').read()
    js=frontend_source_from_app_path('static/app.js')
    assert 'id="mapTree"' in html
    assert 'id="mapViewBtn"' in html
    assert 'id="mapSelect"' not in html
    assert 'id="mapPopulateBtn"' not in html
    assert 'id="mapOrientation"' not in html
    assert 'id="mapLayoutMode"' not in html
    assert 'id="mapRuler"' not in html
    assert 'MAP_MIN_VISIBLE_ROUTE_SCREEN_GAP=10' in js
    assert 'Duplicate as Manual Line' in js
