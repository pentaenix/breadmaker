from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import mutations as storage_mutations

import io
import json
from pathlib import Path

from formats import fragment_json
def upload(doc: dict, filename: str = "fragment.breadmaker.json"):
    raw=json.dumps(doc,ensure_ascii=False).encode('utf-8')
    return {'file':(filename,io.BytesIO(raw),'application/json')}


def sample_fragment():
    return {
        'format':'breadmaker-fragment','schema_version':'1.0','name':'Planning Pieces',
        'markers':[{'id':'marker:arc','name':'Arc','color':'#7a5cff'}],
        'manuscript':{'tabs':[{'id':'tab:outline-ai','name':'AI Outline','description':'Generated outline','chapters':[{'id':'chapter:beat','title':'Beat','content':'A **bold** beat.'}]}]},
        'canvases':[{'id':'canvas:ideas','name':'Ideas','nodes':[{'id':'card:root','type':'card','title':'Root','body':'Standalone canvas','marker':'marker:arc'}]}],
        'maps':[{'id':'map:generated','name':'Generated Map','lines':[]}],
        'tasks':[{'id':'task:review','title':'Review generated material','marker':'marker:arc'}],
        'cuts':[{'id':'cut:maybe','title':'Maybe cut','content':'A *possible* cut.','note':'Generated suggestion'}],
        'mounts':[
            {'target':{'type':'canvas','name':'Existing Canvas'},'nodes':[
                {'id':'card:a','type':'card','title':'A','body':'Mounted A','marker':'marker:arc'},
                {'id':'card:b','type':'card','title':'B','body':'Mounted B'}
            ],'connectors':[{'from':'card:a','to':'card:b','route':'straight','marker':'marker:arc'}]},
            {'target':{'type':'map','name':'Existing Map'},'lines':[{'id':'line:new','name':'Imported Line','color':'#5f7385','items':[{'id':'item:start','type':'stop','title':'Start'}]}]},
        ]
    }


def test_v610_fragment_contract_reuses_project_entities():
    validated=fragment_json.validate_fragment(sample_fragment())
    assert validated['summary']['markers']==1
    assert validated['summary']['canvases']==1
    assert validated['summary']['cards']==3
    assert validated['summary']['map_lines']==1
    assert validated['summary']['cuts']==1
    assert validated['fragment']['manuscript']['tabs'][0]['main'] is False
    assert validated['fragment']['mounts'][0]['kind']=='canvas'


def test_v610_fragment_preview_and_atomic_import(client,fresh_project):
    canvas=client.post('/api/canvases',json={'name':'Existing Canvas','canvas_type':'blank'});assert canvas.status_code==200,canvas.text
    story_map=client.post('/api/maps',json={'name':'Existing Map'});assert story_map.status_code==200,story_map.text
    before_projects=len(client.get('/api/workspace').json()['projects'])
    doc=sample_fragment()
    preview=client.post('/api/json-import/preview',files=upload(doc),data={'context':'{}'})
    assert preview.status_code==200,preview.text
    pdata=preview.json();assert pdata['kind']=='fragment';assert all(not t['needs_choice'] for t in pdata['targets'])
    response=client.post('/api/json-import',files=upload(doc),data={'context':'{}','target_choices':'{}'})
    assert response.status_code==200,response.text
    assert len(client.get('/api/workspace').json()['projects'])==before_projects
    with storage_mutations.tx() as con:
        markers={r['name']:r['color'] for r in con.execute('SELECT name,color FROM markup_labels')}
        assert markers['Arc']=='#7a5cff'
        assert con.execute("SELECT COUNT(*) FROM document_tabs WHERE title='AI Outline'").fetchone()[0]==1
        target=con.execute("SELECT id FROM canvases WHERE name='Existing Canvas'").fetchone()[0]
        assert con.execute("SELECT COUNT(*) FROM canvas_nodes WHERE canvas_id=? AND title IN ('A','B')",(target,)).fetchone()[0]==2
        edge=dict(con.execute("SELECT * FROM canvas_edges WHERE canvas_id=?",(target,)).fetchone())
        assert edge['color']=='#7a5cff' and edge['marker_id'] is not None
        mid=con.execute("SELECT id FROM story_maps WHERE name='Existing Map'").fetchone()[0]
        assert con.execute("SELECT COUNT(*) FROM map_lines WHERE map_id=? AND name='Imported Line'",(mid,)).fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM cuts WHERE title='Maybe cut'").fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM canvases WHERE name='Ideas'").fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM tasks WHERE title='Review generated material'").fetchone()[0]==1


def test_v610_fragment_marker_reuse_and_conflict_preview(client,fresh_project):
    first={'format':'breadmaker-fragment','schema_version':'1.0','name':'Markers','markers':[{'id':'marker:arc','name':'Arc','color':'#7a5cff'}]}
    assert client.post('/api/json-import',files=upload(first),data={'context':'{}','target_choices':'{}'}).status_code==200
    assert client.post('/api/json-import',files=upload(first),data={'context':'{}','target_choices':'{}'}).status_code==200
    with storage_mutations.tx() as con: assert con.execute("SELECT COUNT(*) FROM markup_labels WHERE name='Arc'").fetchone()[0]==1
    conflict={'format':'breadmaker-fragment','schema_version':'1.0','name':'Bad','markers':[{'id':'marker:arc','name':'Arc','color':'#112233'}]}
    preview=client.post('/api/json-import/preview',files=upload(conflict),data={'context':'{}'})
    assert preview.status_code==400 and 'different color' in preview.text


def test_v610_context_target_can_mount_cards(client,fresh_project):
    canvas=client.post('/api/canvases',json={'name':'Current Target','canvas_type':'blank'}).json()
    frag={'format':'breadmaker-fragment','schema_version':'1.0','name':'Current Cards','mounts':[{'target':{'type':'canvas','current':True},'nodes':[{'id':'card:one','type':'card','title':'One'}]}]}
    context=json.dumps({'canvas_id':canvas['id'],'view':'canvas'})
    preview=client.post('/api/json-import/preview',files=upload(frag),data={'context':context});assert preview.status_code==200,preview.text
    assert preview.json()['targets'][0]['resolved_id']==canvas['id']
    result=client.post('/api/json-import',files=upload(frag),data={'context':context,'target_choices':'{}'});assert result.status_code==200,result.text
    with storage_mutations.tx() as con: assert con.execute("SELECT COUNT(*) FROM canvas_nodes WHERE canvas_id=? AND title='One'",(canvas['id'],)).fetchone()[0]==1



def test_v610_universal_import_still_accepts_project_json(client,fresh_project):
    doc={'format':'breadmaker-project','schema_version':'1.0','name':'Universal Project','markers':[{'id':'marker:test','name':'Test','color':'#334455'}]}
    preview=client.post('/api/json-import/preview',files=upload(doc),data={'context':'{}'})
    assert preview.status_code==200 and preview.json()['kind']=='project'
    before=len(client.get('/api/workspace').json()['projects'])
    result=client.post('/api/json-import',files=upload(doc),data={'context':'{}','target_choices':'{}'})
    assert result.status_code==200,result.text
    assert len(client.get('/api/workspace').json()['projects'])==before+1

def test_v610_release_contract_files():
    root=Path(__file__).resolve().parents[1]
    assert tuple(int(x) for x in (root/'VERSION').read_text().strip().split('.')) >= (6,1,0)
    js=frontend_source_from_app_path(root/'static'/'app.js')
    assert 'Import JSON…' in js
    assert 'breadmaker-fragment' in (root/'docs'/'BREADMAKER_FRAGMENT_JSON.md').read_text()
    assert (root/'docs'/'breadmaker-fragment.schema.json').is_file()

def test_v610_all_mount_container_types(client,fresh_project):
    boot=client.get('/api/bootstrap').json();main=next(t for t in boot['tabs'] if t['is_manuscript']);chapter=next(c for c in boot['chapters'] if c['tab_id']==main['id'])
    story_map=client.post('/api/maps',json={'name':'Mount Map'}).json()
    line=client.post(f"/api/maps/{story_map['id']}/lines",json={'name':'Existing Line','color':'#5f7385'}).json()
    task=client.post('/api/tasks',json={'title':'Existing Task'}).json()
    frag={
      'format':'breadmaker-fragment','schema_version':'1.0','name':'All mounts',
      'mounts':[
        {'target':{'type':'tab','name':main['title']},'chapters':[{'id':'chapter:mounted','title':'Mounted Chapter','content':'New chapter.'}]},
        {'target':{'type':'chapter','tab':main['title'],'name':chapter['title']},'scenes':[{'id':'scene:mounted','title':'Mounted Scene','content':'New scene.'}]},
        {'target':{'type':'map_line','map':'Mount Map','name':'Existing Line'},'items':[{'id':'item:mounted','type':'stop','title':'Mounted Station'}]},
        {'target':{'type':'task','name':'Existing Task'},'subtasks':[{'id':'subtask:mounted','title':'Mounted subtask'}]},
      ]
    }
    preview=client.post('/api/json-import/preview',files=upload(frag),data={'context':'{}'});assert preview.status_code==200,preview.text
    assert all(not x['needs_choice'] for x in preview.json()['targets'])
    result=client.post('/api/json-import',files=upload(frag),data={'context':'{}','target_choices':'{}'});assert result.status_code==200,result.text
    with storage_mutations.tx() as con:
        assert con.execute("SELECT COUNT(*) FROM chapters WHERE tab_id=? AND title='Mounted Chapter'",(main['id'],)).fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM scenes WHERE tab_id=? AND title='Mounted Scene'",(main['id'],)).fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM map_items WHERE line_id=? AND title='Mounted Station'",(line['id'],)).fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM task_milestones WHERE task_id=? AND title='Mounted subtask'",(task['id'],)).fetchone()[0]==1


def test_v610_missing_named_target_requires_explicit_choice(client,fresh_project):
    canvas=client.post('/api/canvases',json={'name':'Only Canvas','canvas_type':'blank'}).json()
    frag={'format':'breadmaker-fragment','schema_version':'1.0','name':'Wrong Target','mounts':[{'target':{'type':'canvas','name':'Does Not Exist'},'nodes':[{'id':'card:x','type':'card','title':'X'}]}]}
    preview=client.post('/api/json-import/preview',files=upload(frag),data={'context':'{}'});assert preview.status_code==200,preview.text
    target=preview.json()['targets'][0];assert target['needs_choice'] is True and target['candidates']==[{'id':canvas['id'],'label':'Only Canvas'}]
    failed=client.post('/api/json-import',files=upload(frag),data={'context':'{}','target_choices':'{}'});assert failed.status_code==400
    chosen=client.post('/api/json-import',files=upload(frag),data={'context':'{}','target_choices':json.dumps({'0':canvas['id']})});assert chosen.status_code==200,chosen.text
