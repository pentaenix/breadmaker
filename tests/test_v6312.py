from source_contracts import backend_source,frontend_source_from_app_path
from pathlib import Path
from conftest import assert_ok


def _main_tab(client):
    boot=assert_ok(client.get('/api/bootstrap'))
    return next(d for d in boot['tabs'] if d.get('is_manuscript'))


def _source_item(detail,line_id,source_key):
    line=next(row for row in detail['lines'] if row['id']==line_id)
    return next((item for item in line['items'] if item.get('source_key')==source_key),None)


def test_v6312_source_lines_sync_only_when_explicit(client,fresh_project):
    tab=_main_tab(client)
    a=assert_ok(client.post('/api/chapters',json={'title':'Sync A','tab_id':tab['id']}))
    b=assert_ok(client.post('/api/chapters',json={'title':'Sync B','tab_id':tab['id']}))
    story=assert_ok(client.post('/api/maps',json={'name':'Explicit Sync','layout_mode':'scaled','label_mode':'minimal'}))
    populated=assert_ok(client.post(f"/api/maps/{story['id']}/populate",json={'template_id':'chapters'}))
    detail=assert_ok(client.get(f"/api/maps/{story['id']}"))
    line=next(row for row in detail['lines'] if row['id']==populated['line_id'])
    assert line['layout_locked']==1 and line['provenance']=='source'
    item_a=_source_item(detail,line['id'],f"chapter:{a['id']}")
    item_b=_source_item(detail,line['id'],f"chapter:{b['id']}")
    assert item_a and item_b

    # Lock is viewport-only: metadata edits and deletion from the inspector/API remain valid.
    assert_ok(client.patch(f"/api/map-items/{item_a['id']}",json={'title':'Writer A','description':'Writer description'}))
    assert_ok(client.delete(f"/api/map-items/{item_b['id']}"))
    c=assert_ok(client.post('/api/chapters',json={'title':'Sync C','tab_id':tab['id']}))

    # Reading/reloading the Map must not reconcile source membership.
    unchanged=assert_ok(client.get(f"/api/maps/{story['id']}"))
    assert _source_item(unchanged,line['id'],f"chapter:{b['id']}") is None
    assert _source_item(unchanged,line['id'],f"chapter:{c['id']}") is None
    kept=_source_item(unchanged,line['id'],f"chapter:{a['id']}")
    assert kept['title']=='Writer A' and kept['description']=='Writer description'

    result=assert_ok(client.post(f"/api/map-lines/{line['id']}/sync"))
    assert result['added']>=2
    synced=assert_ok(client.get(f"/api/maps/{story['id']}"))
    assert _source_item(synced,line['id'],f"chapter:{b['id']}") is not None
    assert _source_item(synced,line['id'],f"chapter:{c['id']}") is not None
    kept=_source_item(synced,line['id'],f"chapter:{a['id']}")
    assert kept['title']=='Writer A' and kept['description']=='Writer description'


def test_v6312_manual_line_cannot_sync(client,fresh_project):
    story=assert_ok(client.post('/api/maps',json={'name':'Manual'}))
    line=assert_ok(client.post(f"/api/maps/{story['id']}/lines",json={'name':'Manual Line'}))
    response=client.post(f"/api/map-lines/{line['id']}/sync")
    assert response.status_code==409
    assert 'Only populated source Lines can be synced' in response.text


def test_v6312_explicit_sync_ui_and_cache_contract():
    root=Path(__file__).resolve().parents[1]
    app=backend_source(root)
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,3,12)
    assert '_sync_source_lines(con,pid,mid)' not in app
    assert 'def _sync_source_lines(' not in app
    assert '@app.post("/api/map-lines/{lid}/sync")' in app
    assert "if(line.provenance==='source'&&line.source_template_id)items.push({label:'Sync'" in js
    assert "api(`/api/map-lines/${line.id}/sync`,{method:'POST'})" in js
