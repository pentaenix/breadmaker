from __future__ import annotations

from storage import mutations as storage_mutations
from conftest import assert_ok


def make_map(client, name="Story Map"):
    story_map=assert_ok(client.post('/api/maps',json={'name':name}))
    line=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Main Story'}))
    return story_map,line


def test_maps_schema_and_bootstrap_contract(client, fresh_project):
    with storage_mutations.tx() as con:
        tables={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes={row[1] for row in con.execute("PRAGMA index_list(map_junctions)")}
    assert {'story_maps','map_lines','map_items','map_junctions'} <= tables
    assert 'idx_map_junction_unique' in indexes
    boot=assert_ok(client.get('/api/bootstrap'))
    assert 'maps' in boot


def test_map_defaults_and_line_palette_match_visual_contract(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Current Draft'}))
    assert story_map['background']=='#f3f3f1'
    first=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Main Story'}))
    second=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Finn'}))
    assert first['color']=='#f3680e'
    assert second['color']=='#0e8ef3'
    assert_ok(client.patch(f"/api/map-lines/{second['id']}",json={'color':'#123456'}))
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    assert detail['lines'][1]['color']=='#123456'


def test_stop_span_branch_merge_and_break_after_round_trip(client, fresh_project):
    story_map,main=make_map(client)
    side=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Finn'}))
    catalyst=assert_ok(client.post(f"/api/map-lines/{main['id']}/items",json={'title':'Catalyst','item_type':'stop','planned_start':.1,'schematic_start':.12}))
    finale=assert_ok(client.post(f"/api/map-lines/{main['id']}/items",json={'title':'Finale','item_type':'stop','planned_start':.9,'schematic_start':.88}))
    subplot=assert_ok(client.post(f"/api/map-lines/{side['id']}/items",json={'title':'Finn arc','item_type':'span','planned_start':.2,'planned_end':.7,'schematic_start':.25,'schematic_end':.7,'break_after':True}))
    branch=assert_ok(client.post(f"/api/maps/{story_map['id']}/junctions",json={'from_item_id':catalyst['id'],'to_item_id':subplot['id'],'kind':'branch'}))
    merge=assert_ok(client.post(f"/api/maps/{story_map['id']}/junctions",json={'from_item_id':subplot['id'],'to_item_id':finale['id'],'kind':'merge'}))
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    side_item=next(i for l in detail['lines'] if l['id']==side['id'] for i in l['items'])
    assert side_item['item_type']=='span'
    assert side_item['planned_start']==.2 and side_item['planned_end']==.7
    assert side_item['break_after']==1
    assert {(j['id'],j['kind']) for j in detail['junctions']}=={(branch['id'],'branch'),(merge['id'],'merge')}


def test_scaled_positions_use_manuscript_word_distance(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    first=' '.join(['a']*10)
    middle=' '.join(['middle']*2000)
    last=' '.join(['z']*10)
    ids=assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[
        {'text_content':first,'content_html':first},
        {'text_content':middle,'content_html':middle},
        {'text_content':last,'content_html':last},
    ]}))['ids']
    scenes=[assert_ok(client.post('/api/scenes',json={'title':name,'block_ids':[bid]})) for name,bid in zip(('A','B','C'),ids)]
    story_map,line=make_map(client,'Scaled Story')
    for scene in scenes:
        assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':scene['title'],'start_anchor_type':'scene','start_anchor_id':str(scene['id'])}))
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    positions=[item['scaled_start'] for item in detail['lines'][0]['items']]
    assert positions[0] < positions[1] < positions[2]
    assert (positions[2]-positions[1]) > (positions[1]-positions[0]) * 100
    assert all(item['start_source']=='manuscript' for item in detail['lines'][0]['items'])


def test_scene_anchor_follows_manuscript_reordering(client, fresh_project):
    boot=fresh_project['bootstrap']; first=boot['chapters'][0]
    manuscript=next(t for t in boot['tabs'] if t['is_manuscript'])
    second=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':manuscript['id']}))
    ids=assert_ok(client.put(f"/api/chapters/{first['id']}/blocks",json={'blocks':[{'text_content':'Opening scene words','content_html':'Opening scene words'}]}))['ids']
    scene=assert_ok(client.post('/api/scenes',json={'title':'Moving Scene','block_ids':ids}))
    story_map,line=make_map(client,'Chronology')
    stop=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Moving beat','start_anchor_type':'scene','start_anchor_id':str(scene['id'])}))
    before=next(i for i in assert_ok(client.get(f"/api/maps/{story_map['id']}"))['lines'][0]['items'] if i['id']==stop['id'])['scaled_start']
    second_ids=assert_ok(client.put(f"/api/chapters/{second['id']}/blocks",json={'blocks':[{'text_content':'Destination prose','content_html':'Destination prose'}]}))['ids']
    assert_ok(client.post(f"/api/scenes/{scene['id']}/move",json={'target_block_id':second_ids[0]}))
    after=next(i for i in assert_ok(client.get(f"/api/maps/{story_map['id']}"))['lines'][0]['items'] if i['id']==stop['id'])['scaled_start']
    assert after>before


def test_map_population_is_repeatable_and_save_the_cat_has_duration_spans(client, fresh_project):
    story_map=assert_ok(client.post('/api/maps',json={'name':'Frameworks'}))
    first=assert_ok(client.post(f"/api/maps/{story_map['id']}/populate",json={'template_id':'save_the_cat'}))
    second=assert_ok(client.post(f"/api/maps/{story_map['id']}/populate",json={'template_id':'save_the_cat'}))
    assert first['item_count']==15 and second['item_count']==15
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    assert len(detail['lines'])==2
    assert detail['lines'][0]['name']=='Save the Cat Beats'
    assert detail['lines'][1]['name'].startswith('Save the Cat Beats')
    fun=next(i for i in detail['lines'][0]['items'] if i['title']=='Fun and Games')
    catalyst=next(i for i in detail['lines'][0]['items'] if i['title']=='Catalyst')
    assert fun['item_type']=='span' and fun['planned_start']==.2 and fun['planned_end']==.5
    assert catalyst['item_type']=='stop'


def test_chapter_and_scene_population_use_live_project_anchors(client, fresh_project):
    boot=fresh_project['bootstrap']; chapter=boot['chapters'][0]
    ids=assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'text_content':'Scene prose','content_html':'Scene prose'}]}))['ids']
    scene=assert_ok(client.post('/api/scenes',json={'title':'Opening','block_ids':ids}))
    story_map=assert_ok(client.post('/api/maps',json={'name':'Live Data'}))
    assert_ok(client.post(f"/api/maps/{story_map['id']}/populate",json={'template_id':'chapters'}))
    assert_ok(client.post(f"/api/maps/{story_map['id']}/populate",json={'template_id':'scenes'}))
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    chapter_line=detail['lines'][0]; scene_line=detail['lines'][1]
    assert chapter_line['items'][0]['start_anchor_type']=='chapter'
    opening=next(i for i in scene_line['items'] if i['title']=='Opening')
    assert opening['start_anchor_type']=='scene' and int(opening['start_anchor_id'])==scene['id']
    assert opening['start_source']=='manuscript'


def test_map_duplicate_is_deep_and_independent(client, fresh_project):
    story_map,line=make_map(client,'Original')
    stop=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Beat','planned_start':.4,'schematic_start':.4}))
    duplicate=assert_ok(client.post(f"/api/maps/{story_map['id']}/duplicate"))
    copy=assert_ok(client.get(f"/api/maps/{duplicate['id']}"))
    assert copy['map']['name']!=story_map['name']
    assert copy['lines'][0]['id']!=line['id']
    assert copy['lines'][0]['items'][0]['id']!=stop['id']
    assert_ok(client.patch(f"/api/map-items/{copy['lines'][0]['items'][0]['id']}",json={'title':'Copy Beat'}))
    original=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    assert original['lines'][0]['items'][0]['title']=='Beat'


def test_map_settings_auto_arrange_and_line_reorder(client, fresh_project):
    story_map,first=make_map(client,'Layout')
    second=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Second'}))
    a=assert_ok(client.post(f"/api/map-lines/{first['id']}/items",json={'title':'A','schematic_start':.8}))
    b=assert_ok(client.post(f"/api/map-lines/{first['id']}/items",json={'title':'B','schematic_start':.2}))
    assert_ok(client.patch(f"/api/maps/{story_map['id']}",json={'orientation':'vertical','layout_mode':'scaled','ruler':'percent'}))
    assert_ok(client.post(f"/api/maps/{story_map['id']}/lines/reorder",json={'ids':[second['id'],first['id']]}))
    assert_ok(client.post(f"/api/maps/{story_map['id']}/auto-arrange"))
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    assert (detail['map']['orientation'],detail['map']['layout_mode'],detail['map']['ruler'])==('vertical','scaled','percent')
    assert [l['id'] for l in detail['lines']]==[second['id'],first['id']]
    items=next(l for l in detail['lines'] if l['id']==first['id'])['items']
    assert items[0]['schematic_start'] < items[1]['schematic_start']
    assert {i['id'] for i in items}=={a['id'],b['id']}


def test_map_revision_restore_round_trip(client, fresh_project):
    story_map,line=make_map(client,'Revision Map')
    item=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Original Beat','planned_start':.3,'schematic_start':.3}))
    revision=assert_ok(client.post('/api/autocommit?label='+__import__('urllib.parse').parse.quote(str('Map checkpoint'))))['id']
    assert_ok(client.patch(f"/api/maps/{story_map['id']}",json={'description':'Changed'}))
    assert_ok(client.patch(f"/api/map-items/{item['id']}",json={'title':'Changed Beat'}))
    current_generation=assert_ok(client.get('/api/history/v2'))['current_generation']
    assert_ok(client.post(f"/api/history/v2/revisions/{revision}/restore",json={'expected_generation':current_generation}))
    restored=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    assert restored['map']['description']==''
    assert restored['lines'][0]['items'][0]['title']=='Original Beat'


def test_universal_references_support_maps_and_reject_unsupported_types(client, fresh_project):
    story_map,line=make_map(client,'References')
    stop=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Referenced Beat','planned_start':.2}))
    task=assert_ok(client.post('/api/tasks',json={'title':'Review beat'}))
    ref=assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'map_item','target_id':str(stop['id']),'relationship':'review'}))
    rows=assert_ok(client.get(f"/api/references?source_type=task&source_id={task['id']}"))
    assert any(row['id']==ref['id'] and row['target_type']=='map_item' and row['target_id']==str(stop['id']) for row in rows)
    old=client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'unsupported_type','target_id':'1','relationship':'invalid'})
    assert old.status_code==400
