from __future__ import annotations

from storage import mutations as storage_mutations

import random
from conftest import assert_ok


def _make_map(client, name="Reliability Map"):
    story_map=assert_ok(client.post('/api/maps',json={'name':name}))
    line=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Main Story'}))
    return story_map,line


def _refs(client, **params):
    return assert_ok(client.get('/api/references',params=params))


def test_map_deletion_cleans_polymorphic_references_at_every_level(client, fresh_project):
    story_map,line=_make_map(client)
    item=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Beat','planned_start':.2}))
    task=assert_ok(client.post('/api/tasks',json={'title':'Review Map'}))
    for typ,ident in [('map',story_map['id']),('map_line',line['id']),('map_item',item['id'])]:
        assert_ok(client.post('/api/references',json={
            'source_type':'task','source_id':str(task['id']),'target_type':typ,'target_id':str(ident),'relationship':'review'
        }))
    assert len(_refs(client,source_type='task',source_id=str(task['id'])))==3

    assert_ok(client.delete(f"/api/map-items/{item['id']}"))
    remaining=_refs(client,source_type='task',source_id=str(task['id']))
    assert {(r['target_type'],r['target_id']) for r in remaining}=={('map',str(story_map['id'])),('map_line',str(line['id']))}

    item2=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Beat 2','planned_start':.4}))
    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'map_item','target_id':str(item2['id']),'relationship':'review'}))
    assert_ok(client.delete(f"/api/map-lines/{line['id']}"))
    remaining=_refs(client,source_type='task',source_id=str(task['id']))
    assert {(r['target_type'],r['target_id']) for r in remaining}=={('map',str(story_map['id']))}

    line2=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Second'}))
    item3=assert_ok(client.post(f"/api/map-lines/{line2['id']}/items",json={'title':'Beat 3','planned_start':.6}))
    for typ,ident in [('map_line',line2['id']),('map_item',item3['id'])]:
        assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':typ,'target_id':str(ident),'relationship':'review'}))
    assert_ok(client.delete(f"/api/maps/{story_map['id']}"))
    assert _refs(client,source_type='task',source_id=str(task['id']))==[]


def test_duplicate_map_junction_is_rejected_and_schema_enforces_uniqueness(client, fresh_project):
    story_map,main=_make_map(client,'Junctions')
    side=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Side'}))
    a=assert_ok(client.post(f"/api/map-lines/{main['id']}/items",json={'title':'A','planned_start':.2}))
    b=assert_ok(client.post(f"/api/map-lines/{side['id']}/items",json={'title':'B','planned_start':.3}))
    assert_ok(client.post(f"/api/maps/{story_map['id']}/junctions",json={'from_item_id':a['id'],'to_item_id':b['id'],'kind':'branch'}))
    duplicate=client.post(f"/api/maps/{story_map['id']}/junctions",json={'from_item_id':a['id'],'to_item_id':b['id'],'kind':'branch'})
    assert duplicate.status_code==409
    assert 'MAP_024' in duplicate.text
    with storage_mutations.tx() as con:
        indexes={row[1] for row in con.execute("PRAGMA index_list(map_junctions)")}
        assert 'idx_map_junction_unique' in indexes


def test_auto_arrange_uses_story_order_not_creation_order(client, fresh_project):
    story_map,line=_make_map(client,'Arrange')
    late=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Late','planned_start':.8,'schematic_start':.1}))
    early=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Early','planned_start':.2,'schematic_start':.9}))
    middle=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Middle','planned_start':.5,'schematic_start':.5}))
    assert_ok(client.post(f"/api/maps/{story_map['id']}/auto-arrange"))
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    items=detail['lines'][0]['items']
    assert [i['id'] for i in items]==[early['id'],middle['id'],late['id']]
    assert [i['position'] for i in items]==[0,1,2]
    assert [i['schematic_start'] for i in items]==sorted(i['schematic_start'] for i in items)


def test_randomized_map_crud_never_leaves_dangling_junctions_or_references(client, fresh_project):
    rng=random.Random(441)
    task=assert_ok(client.post('/api/tasks',json={'title':'Map audit task'}))
    for cycle in range(18):
        story_map=assert_ok(client.post('/api/maps',json={'name':f'R{cycle}'}))
        lines=[assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={'name':f'L{i}'})) for i in range(3)]
        items=[]
        for line in lines:
            for i in range(4):
                item=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':f'I{line["id"]}-{i}','planned_start':rng.random(),'schematic_start':rng.random()}))
                items.append((line,item))
                if rng.random()<.35:
                    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'map_item','target_id':str(item['id']),'relationship':'audit'}))
        for i in range(4):
            left=items[i][1]; right=items[4+i][1]
            assert_ok(client.post(f"/api/maps/{story_map['id']}/junctions",json={'from_item_id':left['id'],'to_item_id':right['id'],'kind':'branch'}))
        # Exercise item, line, then whole-map cascades in different cycles.
        if cycle%3==0:
            assert_ok(client.delete(f"/api/map-items/{items[0][1]['id']}"))
        elif cycle%3==1:
            assert_ok(client.delete(f"/api/map-lines/{lines[1]['id']}"))
        assert_ok(client.delete(f"/api/maps/{story_map['id']}"))
        with storage_mutations.tx() as con:
            assert con.execute('PRAGMA foreign_key_check').fetchone() is None
            dangling=con.execute("""SELECT COUNT(*) FROM references_map r
                WHERE r.project_id=? AND r.target_type IN ('map','map_line','map_item')
                  AND NOT (
                    (r.target_type='map' AND EXISTS(SELECT 1 FROM story_maps m WHERE CAST(m.id AS TEXT)=r.target_id)) OR
                    (r.target_type='map_line' AND EXISTS(SELECT 1 FROM map_lines l WHERE CAST(l.id AS TEXT)=r.target_id)) OR
                    (r.target_type='map_item' AND EXISTS(SELECT 1 FROM map_items i WHERE CAST(i.id AS TEXT)=r.target_id))
                  )""",(fresh_project['project']['id'],)).fetchone()[0]
            assert dangling==0


def test_map_range_anchor_rehomes_when_editor_merges_paragraphs(client, fresh_project):
    chapter_id=fresh_project['bootstrap']['chapters'][0]['id']
    saved=assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[
        {'text_content':'Alpha paragraph.','content_html':'Alpha paragraph.','block_type':'paragraph','semantic_style':'body'},
        {'text_content':'Gamma selected phrase.','content_html':'Gamma selected phrase.','block_type':'paragraph','semantic_style':'body'},
    ]}))
    first,second=saved['ids']
    story_map,line=_make_map(client,'Merge anchors')
    item=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={
        'title':'Anchored beat','start_anchor_type':'manuscript_range','start_block_id':second,
        'start_offset':0,'excerpt':'Gamma selected phrase.'
    }))

    merged_text='Alpha paragraph. Gamma selected phrase.'
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[
        {'id':first,'text_content':merged_text,'content_html':merged_text,'block_type':'paragraph','semantic_style':'body'}
    ]}))
    detail=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    anchored=next(i for i in detail['lines'][0]['items'] if i['id']==item['id'])
    assert anchored['start_anchor_type']=='manuscript_range'
    assert anchored['start_block_id']==first
    assert anchored['start_offset']==merged_text.index('Gamma selected phrase.')
    assert anchored['start_source']=='manuscript'
    assert anchored['linked'] is True


def test_import_detaches_removed_map_anchors_without_losing_last_story_position(client, fresh_project):
    chapter_id=fresh_project['bootstrap']['chapters'][0]['id']
    saved=assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[
        {'text_content':'One two three four five.','content_html':'One two three four five.','block_type':'paragraph','semantic_style':'body'},
        {'text_content':'Six seven eight nine ten.','content_html':'Six seven eight nine ten.','block_type':'paragraph','semantic_style':'body'},
    ]}))
    first,second=saved['ids']
    story_map,line=_make_map(client,'Import anchors')
    chapter_item=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={
        'title':'Chapter anchor','start_anchor_type':'chapter','start_anchor_id':str(chapter_id)
    }))
    range_item=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={
        'title':'Range anchor','start_anchor_type':'manuscript_range','start_block_id':second,
        'start_offset':4,'excerpt':'seven'
    }))
    before=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    old={i['id']:i['scaled_start'] for i in before['lines'][0]['items']}
    task=assert_ok(client.post('/api/tasks',json={'title':'Old document link'}))
    old_document=fresh_project['bootstrap']['tabs'][0]['id']
    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'document','target_id':str(old_document),'relationship':'review'}))
    response=client.post('/api/import',files={'file':('replacement.txt',b'Brand new manuscript.\nSecond line.','text/plain')})
    assert_ok(response)

    after=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    by_id={i['id']:i for i in after['lines'][0]['items']}
    for iid in (chapter_item['id'],range_item['id']):
        row=by_id[iid]
        assert row['start_anchor_type'] is None
        assert row['start_anchor_id'] is None
        assert row['start_block_id'] is None
        assert row['start_source']=='planned'
        assert row['linked'] is False
        assert abs(row['planned_start']-old[iid])<1e-9
    assert _refs(client,source_type='task',source_id=str(task['id']))==[]


def test_chapter_delete_detaches_map_anchor_and_removes_dead_generic_links(client, fresh_project):
    chapter_id=fresh_project['bootstrap']['chapters'][0]['id']
    block=assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks'][0]
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[
        {'id':block['id'],'text_content':'Chapter to delete.','content_html':'Chapter to delete.','block_type':'paragraph','semantic_style':'body'}
    ]}))
    # Keep a surviving chapter so manuscript chronology remains meaningful.
    assert_ok(client.post('/api/chapters',json={'title':'Survivor'}))
    story_map,line=_make_map(client,'Delete chapter anchors')
    anchored=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={
        'title':'Chapter beat','start_anchor_type':'chapter','start_anchor_id':str(chapter_id)
    }))
    task=assert_ok(client.post('/api/tasks',json={'title':'Linked task'}))
    annotation=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'comment','body':'Delete with chapter','items':[{
        'block_id':block['id'],'start_offset':0,'end_offset':7,'selected_text':'Chapter','prefix_context':'','suffix_context':' to delete.'
    }]}))['ids'][0]
    for typ,ident in [('chapter',chapter_id),('annotation',annotation)]:
        assert_ok(client.post('/api/references',json={
            'source_type':'task','source_id':str(task['id']),'target_type':typ,'target_id':str(ident),'relationship':'review'
        }))
    before=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    old=next(i for i in before['lines'][0]['items'] if i['id']==anchored['id'])['scaled_start']

    assert_ok(client.delete(f'/api/chapters/{chapter_id}'))
    after=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    row=next(i for i in after['lines'][0]['items'] if i['id']==anchored['id'])
    assert row['start_anchor_type'] is None and row['start_source']=='planned' and row['linked'] is False
    assert abs(row['planned_start']-old)<1e-9
    assert _refs(client,source_type='task',source_id=str(task['id']))==[]


def test_non_finite_numbers_are_rejected_before_storage(client, fresh_project):
    story_map,line=_make_map(client,'Finite numbers')
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Finite canvas'}))
    cases=[
        ('post',f"/api/map-lines/{line['id']}/items",'{"title":"bad","planned_start":NaN}'),
        ('post',f"/api/map-lines/{line['id']}/items",'{"title":"bad","label_offset":Infinity}'),
        ('post',f"/api/canvases/{canvas['id']}/nodes",'{"title":"bad","x":NaN}'),
        ('post',f"/api/canvases/{canvas['id']}/nodes",'{"title":"bad","w":Infinity}'),
    ]
    for method,url,raw in cases:
        response=client.request(method,url,content=raw,headers={'content-type':'application/json'})
        assert response.status_code==422, (url,response.status_code,response.text)
    with storage_mutations.tx() as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert con.execute('PRAGMA foreign_key_check').fetchone() is None


def test_referenceable_entity_deletions_never_leave_polymorphic_links(client, fresh_project):
    task=assert_ok(client.post('/api/tasks',json={'title':'Source task'}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Linked canvas'}))
    node=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'title':'Linked node'}))
    for typ,ident in [('canvas',canvas['id']),('node',node['id'])]:
        assert_ok(client.post('/api/references',json={
            'source_type':'task','source_id':str(task['id']),'target_type':typ,'target_id':str(ident),'relationship':'review'
        }))
    # Also make the node a source so cleanup is symmetric.
    assert_ok(client.post('/api/references',json={
        'source_type':'node','source_id':str(node['id']),'target_type':'task','target_id':str(task['id']),'relationship':'supports'
    }))
    assert len(_refs(client))==3

    assert_ok(client.delete(f"/api/nodes/{node['id']}"))
    rows=_refs(client)
    assert {(r['source_type'],r['target_type']) for r in rows}=={('task','canvas')}
    assert client.delete(f"/api/nodes/{node['id']}").status_code==404

    assert_ok(client.delete(f"/api/canvases/{canvas['id']}"))
    assert _refs(client)==[]

    # A task used as either side of a link owns the same invariant.
    other=assert_ok(client.post('/api/tasks',json={'title':'Other task'}))
    story_map,line=_make_map(client,'Task cleanup')
    beat=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Beat','planned_start':.5}))
    assert_ok(client.post('/api/references',json={
        'source_type':'task','source_id':str(other['id']),'target_type':'map_item','target_id':str(beat['id']),'relationship':'review'
    }))
    assert_ok(client.post('/api/references',json={
        'source_type':'map_item','source_id':str(beat['id']),'target_type':'task','target_id':str(other['id']),'relationship':'supports'
    }))
    assert_ok(client.delete(f"/api/tasks/{other['id']}"))
    assert _refs(client)==[]

def test_stale_canvas_and_cut_ids_cannot_mutate_an_inactive_project(client, fresh_project):
    first=fresh_project['project']
    chapter=fresh_project['bootstrap']['chapters'][0]
    canvas=assert_ok(client.post('/api/canvases',json={'name':'First project canvas'}))
    a=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'title':'A'}))
    b=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={'title':'B','x':300}))
    edge=assert_ok(client.post(f"/api/canvases/{canvas['id']}/edges",json={'from_node_id':a['id'],'to_node_id':b['id']}))
    cut=assert_ok(client.post('/api/cuts',json={'title':'First cut','content_html':'<p>keep me</p>','source_chapter_id':chapter['id']}))

    second=assert_ok(client.post('/api/projects',json={'name':'Second project'}))['project']
    assert second['id']!=first['id']

    # IDs cached by an old workspace must never read or mutate another project after switching.
    assert client.get(f"/api/canvases/{canvas['id']}").status_code==404
    assert client.delete(f"/api/canvases/{canvas['id']}").status_code==404
    assert client.post(f"/api/canvases/{canvas['id']}/edges",json={'from_node_id':a['id'],'to_node_id':b['id']}).status_code==404
    assert client.delete(f"/api/edges/{edge['id']}").status_code==404
    assert client.delete(f"/api/cuts/{cut['id']}").status_code==404

    assert_ok(client.post(f"/api/projects/{first['id']}/activate"))
    restored=assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    assert {n['id'] for n in restored['nodes']}=={a['id'],b['id']}
    assert [e['id'] for e in restored['edges']]==[edge['id']]
    assert any(row['id']==cut['id'] for row in assert_ok(client.get('/api/cuts')))
