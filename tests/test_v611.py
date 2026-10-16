from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import mutations as storage_mutations

import io
import json
from pathlib import Path

from formats import fragment_json
from formats.project_json import validation as project_json_validation
from formats.project_json import markup as project_json_markup
def upload(doc: dict):
    raw=json.dumps(doc,ensure_ascii=False).encode('utf-8')
    return {'file':('review.breadmaker.json',io.BytesIO(raw),'application/json')}


def test_v611_rejects_empty_and_malformed_fragments_cleanly(client,fresh_project):
    empty={'format':'breadmaker-fragment','schema_version':'1.0','name':'Nothing'}
    r=client.post('/api/json-import/preview',files=upload(empty),data={'context':'{}'})
    assert r.status_code==400 and 'does not contain anything' in r.text

    malformed={'format':'breadmaker-fragment','schema_version':'1.0','name':'Malformed','manuscript':{'tabs':['not an object']}}
    r=client.post('/api/json-import/preview',files=upload(malformed),data={'context':'{}'})
    assert r.status_code==400 and 'Expected an object' in r.text

    for key in ('maps','tasks'):
        malformed_array={'format':'breadmaker-fragment','schema_version':'1.0','name':'Malformed',key:123}
        r=client.post('/api/json-import/preview',files=upload(malformed_array),data={'context':'{}'})
        assert r.status_code==400 and 'Expected an array' in r.text


def test_v611_links_are_unambiguous_and_only_target_persistable_entities():
    both={'format':'breadmaker-project','schema_version':'1.0','name':'Both','tasks':[{'id':'task:a','title':'A','links':[{'target':'task:a','url':'https://example.com'}]}]}
    try: project_json_validation.validate_document(both)
    except project_json_validation.ProjectJSONError as exc: assert 'exactly one' in exc.detail()
    else: raise AssertionError('A link with both target and url must fail validation')

    marker={'format':'breadmaker-project','schema_version':'1.0','name':'Marker link','markers':[{'id':'marker:a','name':'A','color':'#112233'}],'tasks':[{'id':'task:a','title':'A','links':['marker:a']}]}
    try: project_json_validation.validate_document(marker)
    except project_json_validation.ProjectJSONError as exc: assert 'expected' in exc.detail().lower()
    else: raise AssertionError('Marker links cannot be persisted in references_map and must be rejected')


def test_v611_developmental_structure_rejects_duplicates():
    doc={'format':'breadmaker-project','schema_version':'1.0','name':'Duplicate structure','manuscript':{'tabs':[{'id':'tab:main','name':'Manuscript','main':True,'chapters':[{'id':'chapter:a','title':'A'}]}]},'canvases':[{'id':'canvas:dev','name':'Dev','type':'developmental','structure':['chapter:a','chapter:a']}]}
    try: project_json_validation.validate_document(doc)
    except project_json_validation.ProjectJSONError as exc: assert 'more than once' in exc.detail()
    else: raise AssertionError('Duplicate structure entries silently collapse in the live Canvas and must be rejected')


def test_v611_map_ranges_normalize_and_junction_items_must_match_their_line():
    backwards={'format':'breadmaker-project','schema_version':'1.0','name':'Ranges','maps':[{'id':'map:x','name':'X','lines':[{'id':'line:x','name':'X','items':[{'id':'item:span','type':'span','title':'Span','start':.8,'end':.2},{'id':'item:stop','type':'stop','title':'Stop','end':.9}]}]}]}
    val=project_json_validation.validate_document(backwards)
    span,stop=val['document']['maps'][0]['lines'][0]['items']
    assert span['start']==.2 and span['end']==.8
    assert stop['end'] is None and any('ignored for a stop' in w for w in val['warnings'])

    mismatch={'format':'breadmaker-project','schema_version':'1.0','name':'Junction','maps':[{'id':'map:x','name':'X','lines':[{'id':'line:a','name':'A','items':[{'id':'item:a','title':'A'}]},{'id':'line:b','name':'B','items':[{'id':'item:b','title':'B'}]}],'junctions':[{'from':{'line':'line:a','item':'item:b'},'to':{'line':'line:b','item':'item:b'}}]}]}
    try: project_json_validation.validate_document(mismatch)
    except project_json_validation.ProjectJSONError as exc: assert 'selected Junction line' in exc.detail()
    else: raise AssertionError('A Junction item from another line must not validate')


def test_v611_current_targets_are_strict_and_bad_choice_values_are_400(client,fresh_project):
    canvas=client.post('/api/canvases',json={'name':'Only Canvas','canvas_type':'blank'}).json()
    frag={'format':'breadmaker-fragment','schema_version':'1.0','name':'Current','mounts':[{'target':{'type':'canvas','current':True},'nodes':[{'id':'card:a','type':'card','title':'A'}]}]}
    # No current Canvas context means a choice is required even if only one Canvas exists.
    preview=client.post('/api/json-import/preview',files=upload(frag),data={'context':'{}'})
    assert preview.status_code==200,preview.text
    target=preview.json()['targets'][0]
    assert target['needs_choice'] is True and target['candidates']==[{'id':canvas['id'],'label':'Only Canvas'}]

    bad_context=client.post('/api/json-import/preview',files=upload(frag),data={'context':json.dumps({'canvas_id':'not-a-number'})})
    assert bad_context.status_code==400 and 'invalid' in bad_context.text.lower()

    bad_choice=client.post('/api/json-import',files=upload(frag),data={'context':'{}','target_choices':json.dumps({'0':'not-a-number'})})
    assert bad_choice.status_code==400 and 'invalid' in bad_choice.text.lower()


def test_v611_canvas_path_selector_handles_slashes_inside_names(client,fresh_project):
    parent=client.post('/api/canvases',json={'name':'Story / World','canvas_type':'blank'}).json()
    child=client.post('/api/canvases',json={'name':'Characters','canvas_type':'blank','parent_id':parent['id']}).json()
    frag={'format':'breadmaker-fragment','schema_version':'1.0','name':'Path','mounts':[{'target':{'type':'canvas','path':['Story / World','Characters']},'nodes':[{'id':'card:a','type':'card','title':'A'}]}]}
    preview=client.post('/api/json-import/preview',files=upload(frag),data={'context':'{}'})
    assert preview.status_code==200,preview.text
    target=preview.json()['targets'][0]
    assert target['resolved_id']==child['id'] and target['needs_choice'] is False


def test_v611_crlf_content_is_normalized_to_paragraphs():
    lines=project_json_markup._content_lines('One\r\nTwo\rThree')
    assert [text for _,text in lines]==['One','Two','Three']
    assert all('\r' not in text for _,text in lines)


def test_v611_fragment_preview_source_includes_all_major_counts():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static'/'app.js')
    assert "['maps','maps']" in js
    assert "['connectors','connectors']" in js
    assert 'other_canvas_objects' in js


def test_v611_fragment_map_name_conflicts_are_caught_before_import(client,fresh_project):
    existing=client.post('/api/maps',json={'name':'Relationships'}); assert existing.status_code==200,existing.text
    duplicate={'format':'breadmaker-fragment','schema_version':'1.0','name':'Duplicate Map','maps':[{'id':'map:new','name':'relationships'}]}
    preview=client.post('/api/json-import/preview',files=upload(duplicate),data={'context':'{}'})
    assert preview.status_code==400 and 'already exists' in preview.text

    mid=existing.json()['id']
    line=client.post(f'/api/maps/{mid}/lines',json={'name':'Cinna'}); assert line.status_code==200,line.text
    duplicate_line={'format':'breadmaker-fragment','schema_version':'1.0','name':'Duplicate Line','mounts':[{'target':{'type':'map','name':'Relationships'},'lines':[{'id':'line:new','name':'cinna'}]}]}
    preview=client.post('/api/json-import/preview',files=upload(duplicate_line),data={'context':'{}'})
    assert preview.status_code==400 and 'Map Line' in preview.text and 'already exists' in preview.text


def test_v611_project_preview_reports_the_name_that_will_actually_be_created(client,fresh_project):
    # The active test project already exists; importing a JSON project with the same
    # name will therefore be suffixed. Preview should not promise a name that changes
    # only after the user confirms.
    doc={'format':'breadmaker-project','schema_version':'1.0','name':fresh_project['name']}
    preview=client.post('/api/json-import/preview',files=upload(doc),data={'context':'{}'})
    assert preview.status_code==200,preview.text
    data=preview.json()
    assert data['source_name']==fresh_project['name']
    assert data['name']!=fresh_project['name'] and data['name'].startswith(fresh_project['name']+' (')


def test_v611_export_refuses_cross_chapter_scene_instead_of_emitting_lossy_json(client,fresh_project):
    boot=client.get('/api/bootstrap').json()
    main=next(t for t in boot['tabs'] if t.get('is_manuscript'))
    first=next(c for c in boot['chapters'] if c['tab_id']==main['id'] and not c.get('is_implicit'))
    second_resp=client.post('/api/chapters',json={'title':'Chapter 2','tab_id':main['id']})
    assert second_resp.status_code==200,second_resp.text
    second=second_resp.json()
    block1=client.get(f"/api/chapters/{first['id']}").json()['blocks'][0]['id']
    block2=client.get(f"/api/chapters/{second['id']}").json()['blocks'][0]['id']
    scene=client.post('/api/scenes',json={'title':'Crossing Scene','block_ids':[block1,block2]})
    assert scene.status_code==200,scene.text
    project_id=fresh_project['project']['id']
    exported=client.get(f'/api/projects/{project_id}/export-json')
    assert exported.status_code==409
    assert 'crosses Chapter boundaries' in exported.text


def test_v611_fragment_machine_schema_rejects_noop_document():
    import jsonschema
    root=Path(__file__).resolve().parents[1]
    schema=json.loads((root/'docs'/'breadmaker-fragment.schema.json').read_text())
    empty={'format':'breadmaker-fragment','schema_version':'1.0','name':'Nothing'}
    try:
        jsonschema.Draft202012Validator(schema).validate(empty)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError('Published Fragment schema must reject the same no-op document as runtime validation')


def test_v611_release_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (6, 1, 1)
    html=(root/'static'/'index.html').read_text()
    assert '/static/styles.css?v=6.1.1' in html
    assert '/static/app.js?v=6.1.1' in html
    assert 'Lossless export boundary' in (root/'docs'/'BREADMAKER_PROJECT_JSON.md').read_text()
    assert 'empty/no-op Fragment' in (root/'docs'/'skills'/'breadmaker-project-builder'/'SKILL.md').read_text()


def test_v611_empty_secondary_tabs_import_as_normal_writable_tabs(client,fresh_project):
    frag={'format':'breadmaker-fragment','schema_version':'1.0','name':'Empty Tab','manuscript':{'tabs':[{'id':'tab:notes','name':'Notes'}]}}
    r=client.post('/api/json-import',files=upload(frag),data={'context':'{}','target_choices':'{}'})
    assert r.status_code==200,r.text
    with storage_mutations.tx() as con:
        tab=dict(con.execute("SELECT * FROM document_tabs WHERE title='Notes' ORDER BY id DESC LIMIT 1").fetchone())
        implicit=dict(con.execute("SELECT * FROM chapters WHERE tab_id=? AND is_implicit=1",(tab['id'],)).fetchone())
        blocks=[dict(x) for x in con.execute("SELECT * FROM manuscript_blocks WHERE chapter_id=?",(implicit['id'],)).fetchall()]
        assert len(blocks)==1 and blocks[0]['text_content']==''
