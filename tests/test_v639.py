from source_contracts import frontend_source_from_app_path
from pathlib import Path
from conftest import assert_ok


def test_v639_map_item_inspector_contract_and_cache_version():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert 'id="mapItemName"' in js and '{{i18n:dynamic.html.text.label.' in js
    assert 'class="section-label">{{i18n:dynamic.html.text.div.' in js and 'renderReferenceRows(displayRefs)' in js
    assert "openEntityLinker('map_item',String(item.id)" in js
    assert "allowTaskCreation:sourceType==='node'||sourceType==='map_item'" in js
    for legacy in ('mapItemShortLabel','mapItemActiveStart','mapItemActiveEnd','mapItemBreakAfter','saveMapItemInspector','openMapJunctionInspector'):
        assert legacy not in js
    assert "async function deleteMapItem(item,line){await flushMapItemInspectorSave(item.id);await withStructuralUndo" in js
    assert "const text=(item.title||'').trim()" in js


def test_v639_source_map_items_keep_writer_metadata_and_unlocked_geometry(client,fresh_project):
    story=assert_ok(client.post('/api/maps',json={'name':'Source edits','layout_mode':'scaled'}))
    populated=assert_ok(client.post(f"/api/maps/{story['id']}/populate",json={'template_id':'save_the_cat'}))
    detail=assert_ok(client.get(f"/api/maps/{story['id']}"));line=next(x for x in detail['lines'] if x['id']==populated['line_id'])
    item=line['items'][0]
    assert line['layout_locked']==1 and item['source_key']

    assert_ok(client.patch(f"/api/map-items/{item['id']}",json={'title':'Writer Name','description':'Writer description'}))
    refreshed=assert_ok(client.get(f"/api/maps/{story['id']}"));edited=next(i for l in refreshed['lines'] if l['id']==line['id'] for i in l['items'] if i['id']==item['id'])
    assert edited['title']=='Writer Name'
    assert edited['description']=='Writer description'

    assert_ok(client.patch(f"/api/map-lines/{line['id']}",json={'layout_locked':False}))
    new_start=.173
    assert_ok(client.patch(f"/api/map-items/{item['id']}",json={'planned_start':new_start}))
    refreshed=assert_ok(client.get(f"/api/maps/{story['id']}"));edited=next(i for l in refreshed['lines'] if l['id']==line['id'] for i in l['items'] if i['id']==item['id'])
    assert abs(edited['planned_start']-new_start)<1e-9
    assert edited['title']=='Writer Name' and edited['description']=='Writer description'


def test_v639_map_items_support_standard_link_rows(client,fresh_project):
    story=assert_ok(client.post('/api/maps',json={'name':'Links'}))
    line=assert_ok(client.post(f"/api/maps/{story['id']}/lines",json={'name':'Line'}))
    item=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'item_type':'stop','title':'Station'}))
    task=assert_ok(client.post('/api/tasks',json={'title':'Review station','status':'open'}))
    assert_ok(client.post('/api/references',json={'source_type':'map_item','source_id':str(item['id']),'target_type':'task','target_id':str(task['id'])}))
    refs=assert_ok(client.get('/api/references',params={'source_type':'map_item','source_id':str(item['id'])}))
    assert len(refs)==1
    assert refs[0]['target_type']=='task' and refs[0]['target_label']=='Review station'
