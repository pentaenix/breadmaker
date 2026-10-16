from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def _set_text(client, chapter_id, block_id, text):
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[{
        'id':block_id,'block_type':'paragraph','semantic_style':'body',
        'content_html':text,'text_content':text,'image_path':'','scene_id':None,
    }]}))


def _highlight(client, block_id, marker_id, start, end, text):
    item={'block_id':block_id,'start_offset':start,'end_offset':end,'selected_text':text[start:end],
          'prefix_context':text[max(0,start-45):start],'suffix_context':text[end:end+45]}
    return assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker_id,'body':'','items':[item]}))


def test_v4179_clear_markers_subtracts_only_selected_range_and_is_undoable(fresh_project,client):
    chapter=fresh_project['bootstrap']['chapters'][0]
    book=assert_ok(client.get('/api/book')); block=book['blocks'][0]
    text='abcdefghijklmnop'
    _set_text(client,chapter['id'],block['id'],text)
    first=assert_ok(client.post('/api/labels',json={'name':'First','description':'','color':'#f0c75e'}))
    second=assert_ok(client.post('/api/labels',json={'name':'Second','description':'','color':'#72c6ef'}))
    _highlight(client,block['id'],first['id'],0,10,text)
    _highlight(client,block['id'],second['id'],4,14,text)

    token=assert_ok(client.post('/api/undo/begin?label=Clear%20markers'))['token']
    item={'block_id':block['id'],'start_offset':6,'end_offset':8,'selected_text':text[6:8],
          'prefix_context':text[:6],'suffix_context':text[8:]}
    result=assert_ok(client.post('/api/annotations/clear-highlights',json={'items':[item]}))
    assert result['changed']==2
    assert assert_ok(client.post(f'/api/undo/finish/{token}'))['changed']>0

    rows=[r for r in assert_ok(client.get('/api/annotations?status=all')) if r['annotation_type']=='highlight']
    spans=sorted((r['label_id'],r['start_offset'],r['end_offset']) for r in rows)
    assert spans==sorted([
        (first['id'],0,6),(first['id'],8,10),
        (second['id'],4,6),(second['id'],8,14),
    ])

    assert_ok(client.post(f'/api/undo/apply/{token}?direction=undo'))
    rows=[r for r in assert_ok(client.get('/api/annotations?status=all')) if r['annotation_type']=='highlight']
    assert sorted((r['label_id'],r['start_offset'],r['end_offset']) for r in rows)==sorted([
        (first['id'],0,10),(second['id'],4,14),
    ])
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=redo'))
    rows=[r for r in assert_ok(client.get('/api/annotations?status=all')) if r['annotation_type']=='highlight']
    assert sorted((r['label_id'],r['start_offset'],r['end_offset']) for r in rows)==spans


def test_v4179_markup_rack_has_clear_markers_first_and_active_eraser_mode():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'marker-clear-row' in js and 'Clear markers' in js
    assert "list.innerHTML=clearRow+(markers.length?markers.map" in js
    assert "state.activeMarkupClear=!state.activeMarkupClear" in js
    assert "api('/api/annotations/clear-highlights'" in js
    assert "state.activeMarkupMarkerId||state.activeMarkupClear" in js
    assert '.marker-clear-row{order:-1}' in css
