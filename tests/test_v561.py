from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v561_source_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static'/'app.js')
    css=frontend_styles_source_from_styles_path((root/'static'/'styles.css'))
    html=(root/'static'/'index.html').read_text()
    app=backend_source(root)
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 6, 1)
    assert '?v=5.6.1' in html
    assert 'settlePopulatedRows' in js and 'afterCanvasPaint' in js
    assert 'rowHeights' in js and 'columnWidths' in js
    assert "addEventListener('selectstart'" in js
    assert 'canvasSpaceKeyDown' in js and 'restoreCanvasSpaceTypingSnapshot' in js
    assert '.canvas-stage.panning *{user-select:none!important' in css
    assert 'def _canvas_populate_layout' in app


def test_v561_populate_rows_use_tallest_card_and_aligned_columns(client,fresh_project):
    canvas=client.post('/api/canvases',json={'name':'Populate rows','canvas_type':'blank'}).json()
    response=client.post(f"/api/canvases/{canvas['id']}/populate",json={'template_id':'character_arc','x':100,'y':120})
    assert response.status_code==200,response.text
    data=response.json(); nodes=data['nodes']; layout=data['layout']; columns=layout['columns']
    assert columns>=2 and len(nodes)>columns
    first=nodes[:columns]; second=nodes[columns:columns*2]
    assert all(abs(float(n['y'])-float(first[0]['y']))<0.01 for n in first)
    expected_y=float(first[0]['y'])+max(float(n['h']) for n in first)+float(layout['gap_y'])
    assert abs(float(second[0]['y'])-expected_y)<0.01
    # Column starts are stable from row to row instead of drifting with each Card width.
    for index,node in enumerate(second):
        assert abs(float(node['x'])-float(first[index]['x']))<0.01
