from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import context as storage_context

import json
from pathlib import Path
from bs4 import BeautifulSoup

from backend import diagnostics
from conftest import assert_ok


def test_ui_state_is_project_scoped(client, fresh_project):
    first=fresh_project['project']
    assert_ok(client.put('/api/ui-state/canvas-tree',json={'value':[1,4,9]}))
    assert assert_ok(client.get('/api/ui-state/canvas-tree'))['value']==[1,4,9]
    second=assert_ok(client.post('/api/projects',json={'name':'Other Project'}))['project']
    assert assert_ok(client.get('/api/ui-state/canvas-tree'))['value'] is None
    assert_ok(client.put('/api/ui-state/canvas-tree',json={'value':[7]}))
    assert_ok(client.post(f"/api/projects/{first['id']}/activate"))
    assert assert_ok(client.get('/api/ui-state/canvas-tree'))['value']==[1,4,9]
    assert second['id'] != first['id']


def test_task_lifecycle_and_universal_link_count(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'block_type':'paragraph','text_content':'Anchor text','content_html':'Anchor text'}]}))
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    task=assert_ok(client.post('/api/tasks',json={'title':'Revisit this','description':'Later, not done.','status':'later'}))
    assert_ok(client.post('/api/references',json={
        'source_type':'task','source_id':str(task['id']),'target_type':'manuscript_range','target_id':'range',
        'start_block_id':block['id'],'end_block_id':block['id'],'start_offset':0,'end_offset':6,'excerpt':'Anchor'
    }))
    row=next(t for t in assert_ok(client.get('/api/tasks')) if t['id']==task['id'])
    assert row['status']=='later' and row['reference_count']==1
    assert_ok(client.patch(f"/api/tasks/{task['id']}",json={'status':'archived'}))
    row=next(t for t in assert_ok(client.get('/api/tasks')) if t['id']==task['id'])
    assert row['status']=='archived'


def test_diagnostics_are_structured_and_project_local(fresh_project):
    occurrence=diagnostics.log_event('TEST_EVENT_001','Synthetic reliability event.',context={'object_id':'safe-id'},data_safe=True)
    path=Path(storage_context.PROJECT_ROOT)/'logs'/'breadmaker.jsonl'
    rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    row=next(r for r in reversed(rows) if r['occurrence']==occurrence)
    assert row['event_id']=='TEST_EVENT_001'
    assert row['message']=='Synthetic reliability event.'
    assert row['context']=={'object_id':'safe-id'}
    assert row['user_data_safe'] is True


def test_static_shell_has_unique_ids_and_permanent_navigation():
    root=Path(__file__).resolve().parents[1]
    soup=BeautifulSoup((root/'static/index.html').read_text(encoding='utf-8'),'html.parser')
    ids=[tag.get('id') for tag in soup.find_all(attrs={'id':True})]
    assert len(ids)==len(set(ids)), 'Duplicate DOM IDs make event wiring ambiguous.'
    catalog=json.loads((root/'locales/en.json').read_text(encoding='utf-8'))['strings']

    def localized_marker(value: str) -> str:
        assert value.startswith('{{i18n:') and value.endswith('}}')
        key=value[len('{{i18n:'):-2]
        assert key in catalog
        return catalog[key]

    tabs=[localized_marker(x.get_text(' ',strip=True)) for x in soup.select('.main-tab')]
    assert tabs==['Manuscript','Canvas','Maps','Reviews','Tasks']
    assert soup.select_one('#undoBtn svg') is not None
    assert soup.select_one('#redoBtn svg') is not None
    assert localized_marker(soup.select_one('#viewFindReplaceBtn').get_text(strip=True))=='Find and replace'
    assert soup.select_one('#findBtn') is None
    assert soup.select_one('#markupBtn svg') is not None
    assert soup.select_one('#manuscriptImageBtn svg') is not None
    assert soup.select_one('#manuscriptReviewsBtn svg') is not None



def test_local_ui_assets_are_versioned_and_never_cacheable(client):
    root=Path(__file__).resolve().parents[1]
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    html=(root/'static/index.html').read_text(encoding='utf-8')

    shell=client.get('/')
    assert shell.status_code==200
    assert 'no-store' in shell.headers.get('cache-control','').lower()
    script=client.get(f'/static/app.js?v={version}')
    assert script.status_code==200
    assert 'no-store' in script.headers.get('cache-control','').lower()


def test_document_canvas_link_uses_universal_reference_model(client, fresh_project):
    document=assert_ok(client.post('/api/documents',json={'title':'World Research','is_manuscript':False}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'World Research','canvas_type':'blank'}))
    node=assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes",json={
        'shape':'rect','title':'World Research','body':'Linked document','x':120,'y':100,'w':240,'h':120
    }))
    ref=assert_ok(client.post('/api/references',json={
        'source_type':'node','source_id':str(node['id']),'target_type':'document','target_id':str(document['id']),
        'relationship':'source document'
    }))
    links=assert_ok(client.get(f"/api/references?source_type=node&source_id={node['id']}"))
    assert any(x['id']==ref['id'] and x['target_type']=='document' and x['target_id']==str(document['id']) for x in links)
    js=frontend_source_from_app_path(Path(__file__).resolve().parents[1]/'static/app.js')
    assert 'Create canvas from tab…' in js
    assert "relationship:'source document'" in js
