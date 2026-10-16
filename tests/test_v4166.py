from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from storage import mutations as storage_mutations

from pathlib import Path
from conftest import assert_ok


def test_v4166_marker_description_api_and_schema(client, fresh_project):
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'APP_SCHEMA_VERSION = 18' in (root/'src'/'workspace'/'__init__.py').read_text(encoding='utf-8')
    marker=assert_ok(client.post('/api/labels',json={
        'name':'Continuity','description':'Track timeline contradictions and callbacks.','color':'#557799'
    }))
    assert marker['description']=='Track timeline contradictions and callbacks.'
    assert_ok(client.patch(f"/api/labels/{marker['id']}",json={'description':'Timeline and callback checks.'}))
    boot=assert_ok(client.get('/api/bootstrap'))
    saved=next(x for x in boot['labels'] if x['id']==marker['id'])
    assert saved['description']=='Timeline and callback checks.'
    with storage_mutations.tx() as con:
        cols={row[1] for row in con.execute('PRAGMA table_info(markup_labels)')}
        assert 'description' in cols
        indexes={row[1] for row in con.execute("PRAGMA index_list('cuts')")}
        assert 'idx_cuts_project_root' in indexes


def test_v4166_static_markup_search_details_and_navigation_only_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'id="markerSearch"' in html
    assert "`${marker.name||''} ${marker.description||''}`" in js
    assert "{label:'Details',action:()=>editMarker(id)}" in js
    assert 'openMarkerDetails' not in js
    assert 'id="markerDescription"' in js
    assert "$('#findExpand').classList.toggle('hidden',!!state.find.navigatingMarkup)" in js
    assert '.marker-search{' in css


def test_v4166_cut_delete_does_not_reload_entire_cuts_library_in_client():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    start=js.index('async function permanentlyDeleteCut')
    end=js.index('function cutMenuItems',start)
    fn=js[start:end]
    assert "notifyCutsChanged(null,{removeId:cut.id})" in fn
    assert "await reloadCutsWorkspace" not in fn
    assert 'function removeCutFromWorkspace' in js
