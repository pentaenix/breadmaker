from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from conftest import assert_ok


def test_v4161_static_markup_panel_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))

    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'function suggestMarkerColor()' in js
    assert '137.507764' in js  # algorithmic golden-angle stepping, not a palette
    assert "return 'Marker needs a name.'" in js
    assert 'already exists.`' in js
    assert "return 'That color is already used by another marker.'" in js
    assert 'markerCreatedSortValue' in js
    assert 'markerTextColor(color)' in js
    assert '.markup-panel{overflow:hidden;display:flex;flex-direction:column}' in css
    assert '.marker-list{min-height:0;flex:1;overflow-y:auto;overflow-x:hidden' in css
    assert '.marker-use,.marker-more{border:0!important' in css


def test_v4161_marker_create_rejects_empty_duplicate_name_and_duplicate_color(client,fresh_project):
    empty=client.post('/api/labels',json={'name':'   ','color':'#557799'})
    assert empty.status_code==400
    assert empty.json()['detail']=='Marker needs a name.'

    first=assert_ok(client.post('/api/labels',json={'name':'Continuity','color':'#557799'}))
    duplicate_name=client.post('/api/labels',json={'name':'continuity','color':'#995577'})
    assert duplicate_name.status_code==409
    assert 'already exists' in duplicate_name.json()['detail']

    duplicate_color=client.post('/api/labels',json={'name':'Rewrite','color':'#557799'})
    assert duplicate_color.status_code==409
    assert duplicate_color.json()['detail']=='That color is already used by another marker.'
    assert first['name']=='Continuity'


def test_v4161_marker_edit_cannot_collide_with_another_marker(client,fresh_project):
    first=assert_ok(client.post('/api/labels',json={'name':'Continuity','color':'#557799'}))
    second=assert_ok(client.post('/api/labels',json={'name':'Rewrite','color':'#995577'}))

    same_name=client.patch(f"/api/labels/{second['id']}",json={'name':'CONTINUITY'})
    assert same_name.status_code==409
    same_color=client.patch(f"/api/labels/{second['id']}",json={'color':'#557799'})
    assert same_color.status_code==409

    assert_ok(client.patch(f"/api/labels/{first['id']}",json={'name':'Continuity pass','color':'#557799'}))
