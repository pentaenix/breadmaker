from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]


def test_v42_scene_descriptions_are_persisted(client, fresh_project):
    chapter = fresh_project['bootstrap']['chapters'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks", json={"blocks":[{
        "block_type":"paragraph","semantic_style":"body","content_html":"First scene paragraph","text_content":"First scene paragraph"
    }]}))
    block = assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    scene = assert_ok(client.post('/api/scenes', json={
        'title':'Kitchen argument','description':'Cinna decides she cannot keep everyone from leaving.','block_ids':[block['id']]
    }))
    assert scene['description'].startswith('Cinna decides')
    assert_ok(client.patch(f"/api/scenes/{scene['id']}", json={'description':'Revised scene purpose'}))
    boot = assert_ok(client.get('/api/bootstrap'))
    saved = next(x for x in boot['scenes'] if x['id'] == scene['id'])
    assert saved['description'] == 'Revised scene purpose'


def test_v42_deleting_marker_preserves_everything_that_used_it(client, fresh_project):
    chapter = fresh_project['bootstrap']['chapters'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks", json={"blocks":[{
        "block_type":"paragraph","semantic_style":"body","content_html":"Alpha prose","text_content":"Alpha prose"
    }]}))
    block = assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    marker = assert_ok(client.post('/api/labels', json={'name':'Continuity','color':'#ffd966'}))
    annotation = assert_ok(client.post('/api/annotations/batch', json={
        'annotation_type':'highlight','label_id':marker['id'],'items':[{
            'block_id':block['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha','prefix_context':'','suffix_context':' prose'
        }]
    }))
    task = assert_ok(client.post('/api/tasks', json={'title':'Check continuity','marker_id':marker['id']}))
    canvas = assert_ok(client.post('/api/canvases', json={'name':'Continuity map'}))
    node = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'title':'Alpha','label_id':marker['id']}))

    usage = assert_ok(client.get(f"/api/labels/{marker['id']}/usage"))
    assert usage == {'annotations':1,'tasks':1,'nodes':1,'total':3}
    assert_ok(client.delete(f"/api/labels/{marker['id']}"))

    annotations = assert_ok(client.get('/api/annotations?status=all'))
    saved_annotation = next(x for x in annotations if x['id'] == annotation['ids'][0])
    assert saved_annotation['label_id'] is None
    tasks = assert_ok(client.get('/api/tasks'))
    assert next(x for x in tasks if x['id'] == task['id'])['marker_id'] is None
    nodes = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['nodes']
    assert next(x for x in nodes if x['id'] == node['id'])['label_id'] is None


def test_v42_connector_target_and_style_are_editable_and_persist(client, fresh_project):
    canvas = assert_ok(client.post('/api/canvases', json={'name':'Styled connectors'}))
    first = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'title':'A','x':80,'y':80}))
    second = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'title':'B','x':420,'y':80}))
    third = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'title':'C','x':420,'y':300}))
    edge = assert_ok(client.post(f"/api/canvases/{canvas['id']}/edges", json={
        'from_node_id':first['id'],'to_node_id':second['id'],'from_port':'right','to_port':'left'
    }))
    assert edge['to_port'] == 'left'
    assert_ok(client.patch(f"/api/edges/{edge['id']}", json={
        'to_node_id':third['id'],'to_port':'top_left','color':'#223344','line_style':'dashed','end_style':'diamond','line_width':3.5
    }))
    saved = next(x for x in assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['edges'] if x['id'] == edge['id'])
    assert saved['to_node_id'] == third['id']
    assert saved['to_port'] == 'top_left'
    assert saved['color'] == '#223344'
    assert saved['line_style'] == 'dashed'
    assert saved['end_style'] == 'diamond'
    assert saved['line_width'] == 3.5
    assert client.patch(f"/api/edges/{edge['id']}", json={'line_style':'zigzag'}).status_code == 400


def _run_reset(workspace: Path, *args: str):
    env = os.environ.copy()
    env['BREADMAKER_HOME'] = str(workspace)
    return subprocess.run([str(ROOT/'reset'), *args], cwd=ROOT, env=env, text=True, capture_output=True, timeout=10)


def test_v42_reset_requires_exact_confirmation_and_never_touches_external_files(tmp_path):
    workspace = tmp_path/'managed-state'
    project = workspace/'projects'/'one'
    project.mkdir(parents=True)
    (workspace/'registry.json').write_text('{"version":1}', encoding='utf-8')
    sentinel = project/'draft.txt'; sentinel.write_text('keep until confirmed', encoding='utf-8')
    external = tmp_path/'external.docx'; external.write_text('external manuscript', encoding='utf-8')

    refused = _run_reset(workspace)
    assert refused.returncode == 2 and sentinel.exists()
    refused_extra = _run_reset(workspace, '--confirm', 'anything')
    assert refused_extra.returncode == 2 and sentinel.exists()

    done = _run_reset(workspace, '--confirm')
    assert done.returncode == 0, done.stderr
    assert not workspace.exists()
    assert external.read_text(encoding='utf-8') == 'external manuscript'
    # Resetting an already clean installation is deliberately idempotent.
    again = _run_reset(workspace, '--confirm')
    assert again.returncode == 0


def test_v42_reset_refuses_a_running_project_lock(tmp_path):
    workspace = tmp_path/'managed-state'
    project = workspace/'projects'/'one'; project.mkdir(parents=True)
    lock = project/'breadmaker_editor.lock'; lock.touch()
    sentinel = project/'draft.txt'; sentinel.write_text('must survive', encoding='utf-8')
    holder = subprocess.Popen([
        sys.executable, '-c',
        "import fcntl,sys,time; f=open(sys.argv[1],'a+'); fcntl.flock(f.fileno(),fcntl.LOCK_EX); print('ready',flush=True); time.sleep(20)",
        str(lock),
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'ready'
        result = _run_reset(workspace, '--confirm')
        assert result.returncode == 1
        assert 'running' in result.stderr.lower()
        assert sentinel.exists()
    finally:
        holder.terminate(); holder.wait(timeout=5)


def test_v42_static_writing_interaction_contracts():
    html = (ROOT/'static/index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(ROOT/'static/app.js')
    css = frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert '>Split View<' in html
    assert 'id="selectionToolbar"' in html
    assert "$('#editor').addEventListener('contextmenu',event=>{" in js
    assert "const items=selectionItems();hideSelectionToolbar();if(!items.length)return" in js
    assert 'prompt(' not in js and 'confirm(' not in js
    assert "requestHistory(event.inputType==='historyUndo'?'undo':'redo','beforeinput')" in js
    assert 'historyChain' in js and 'historyBusy' in js
    assert ".focus .document-toolbar{display:flex}" in css
    assert '.edge-hit{stroke:rgba(0,0,0,.001);stroke-width:26' in css
    assert 'createConnectedCanvasNode' not in js
    assert "if(target&&target.node.id!==node.id)await makeEdge" in js
    assert "line_style:$('#edgeLineStyle').value" in js
    assert not (ROOT/'Reset.command').exists()
    assert not (ROOT/'reset.command').exists()


def test_v42_revision_restore_preserves_scene_and_connector_metadata(client, fresh_project):
    chapter = fresh_project['bootstrap']['chapters'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks", json={"blocks":[{
        "block_type":"paragraph","semantic_style":"body","content_html":"Revision prose","text_content":"Revision prose"
    }]}))
    block = assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    scene = assert_ok(client.post('/api/scenes', json={'title':'Revision Scene','description':'Original scene description','block_ids':[block['id']]}))
    canvas = assert_ok(client.post('/api/canvases', json={'name':'Revision Canvas'}))
    a = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'title':'A'}))
    b = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={'title':'B','x':500}))
    edge = assert_ok(client.post(f"/api/canvases/{canvas['id']}/edges", json={
        'from_node_id':a['id'],'to_node_id':b['id'],'color':'#334455','line_style':'dotted','end_style':'circle','line_width':2.5
    }))
    revision = assert_ok(client.post('/api/autocommit?label='+__import__('urllib.parse').parse.quote(str('V4.2 metadata'))))['id']

    assert_ok(client.patch(f"/api/scenes/{scene['id']}", json={'description':'Changed'}))
    assert_ok(client.patch(f"/api/edges/{edge['id']}", json={'color':'#abcdef','line_style':'solid','end_style':'none','line_width':6}))
    assert_ok(client.post(f'/api/revisions/{revision}/restore'))

    boot = assert_ok(client.get('/api/bootstrap'))
    restored_scene = next(x for x in boot['scenes'] if x['id']==scene['id'])
    restored_edge = next(x for x in assert_ok(client.get(f"/api/canvases/{canvas['id']}"))['edges'] if x['id']==edge['id'])
    assert restored_scene['description']=='Original scene description'
    assert (restored_edge['color'],restored_edge['line_style'],restored_edge['end_style'],restored_edge['line_width']) == ('#334455','dotted','circle',2.5)
