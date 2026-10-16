from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from conftest import assert_ok


def test_4174_linker_and_optional_ui_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    app=backend_source(root)
    assert "task.status==='open'" in js
    assert "filter(task=>task.status==='open')" in js
    assert "value:'canvas_family',label:'Canvas'" in js
    assert "value:'map_family',label:'Map'" in js
    assert "Canvas or Card" in js
    assert "Map or item" in js
    assert "Linked to ${target.label}" in js
    selection=js[js.index('async function openLinkerForSelection'):js.index('async function openEntityLinker')]
    entity=js[js.index('async function openEntityLinker'):js.index('function selectionCutHtml')]
    assert 'Scene' not in selection  # manuscript selection still links outward; Scene attachment is structural
    assert "value:'scene',label:'Scene'" in js
    scene_menu=js[js.index('function openSceneMenu'):js.index('async function goScene')]
    scene_details=js[js.index('async function openSceneDetails'):js.index('async function deleteScene')]
    assert "label:'Add link…'" not in scene_menu
    assert 'sceneDetailsAddLink' in scene_details
    assert '"scene"' in app[app.index('_REFERENCE_SOURCE_TYPES'):app.index('def _entity_exists')]
    assert '.optional-label{' in css
    assert 'font-style:italic' in css
    assert js.count('<span class="optional-label">(optional)</span>') >= 10
    assert 'placeholder="Optional"' not in js
    assert 'Optional context for this step.' not in js


def test_418_scene_is_a_universal_reference_target(client, fresh_project):
    boot=fresh_project['bootstrap']
    chapter=boot['chapters'][0]
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    scene=assert_ok(client.post('/api/scenes',json={'title':'Not a link target','block_ids':[block['id']]}))
    task=assert_ok(client.post('/api/tasks',json={'title':'Open link source','status':'open'}))
    response=client.post('/api/references',json={
        'source_type':'task','source_id':str(task['id']),
        'target_type':'scene','target_id':str(scene['id']),
    })
    assert_ok(response)
