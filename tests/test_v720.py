from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
import json
import io
import re
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]
APP=frontend_source_from_app_path(ROOT/'static/app.js')
CSS=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
INDEX=(ROOT/'static/index.html').read_text(encoding='utf-8')





def test_v720_card_alignment_and_figure_api(client,fresh_project):
    cv=assert_ok(client.post('/api/canvases',json={'name':'7.2 Canvas'}))
    card=assert_ok(client.post(f"/api/canvases/{cv['id']}/nodes",json={'shape':'rect','title':'Aligned','text_align':'center'}))
    assert card['text_align']=='center'
    assert_ok(client.patch(f"/api/nodes/{card['id']}",json={'text_align':'right'}))
    data=assert_ok(client.get(f"/api/canvases/{cv['id']}"));card=next(n for n in data['nodes'] if n['id']==card['id'])
    assert card['text_align']=='right'

    for shape in ('rect','circle','triangle','oval','line'):
        size={'w':180,'h':180} if shape=='circle' else {'w':240,'h':30 if shape=='line' else 150}
        fig=assert_ok(client.post(f"/api/canvases/{cv['id']}/nodes",json={
            'node_type':'figure','shape':shape,**size,
            'figure_fill_color':'#abcdef','figure_stroke_color':'#123456',
            'figure_border_width':2.5,'figure_layer':'front'
        }))
        assert fig['shape']==shape
        assert fig['figure_fill_color']=='#abcdef'
        assert fig['figure_stroke_color']=='#123456'
        assert float(fig['figure_border_width'])==2.5
        assert fig['figure_layer']=='front'
        assert_ok(client.patch(f"/api/nodes/{fig['id']}",json={'figure_layer':'back','figure_border_width':4}))
        data=assert_ok(client.get(f"/api/canvases/{cv['id']}"));patched=next(n for n in data['nodes'] if n['id']==fig['id'])
        assert patched['figure_layer']=='back'
        assert float(patched['figure_border_width'])==4



def test_v720_project_json_preserves_alignment_and_figure_appearance(client,fresh_project):
    project=fresh_project['project']
    cv=assert_ok(client.post('/api/canvases',json={'name':'Export 7.2'}))
    assert_ok(client.post(f"/api/canvases/{cv['id']}/nodes",json={'shape':'rect','title':'Centered export','text_align':'center'}))
    assert_ok(client.post(f"/api/canvases/{cv['id']}/nodes",json={'node_type':'figure','shape':'oval','w':280,'h':120,'figure_fill_color':'#445566','figure_stroke_color':'#ddeeff','figure_border_width':3.5,'figure_layer':'front'}))
    response=client.get(f"/api/projects/{project['id']}/export-json")
    assert response.status_code==200,response.text
    payload=json.loads(response.content)
    canvas=next(c for c in payload['canvases'] if c['name']=='Export 7.2')
    card=next(n for n in canvas['nodes'] if n['type']=='card' and n['title']=='Centered export')
    figure=next(n for n in canvas['nodes'] if n['type']=='figure')
    assert card['text_align']=='center'
    assert figure['shape']=='oval'
    assert figure['fill_color']=='#445566'
    assert figure['stroke_color']=='#ddeeff'
    assert float(figure['border_width'])==3.5
    assert figure['layer']=='front'


def test_v720_fragment_json_import_preserves_new_canvas_fields(client,fresh_project):
    fragment={
        'format':'breadmaker-fragment','schema_version':'1.0','name':'7.2 Canvas fields',
        'canvases':[{'id':'canvas:v72','name':'Imported 7.2','nodes':[
            {'id':'card:v72','type':'card','title':'Aligned import','body':'Body','text_align':'right'},
            {'id':'figure:v72','type':'figure','shape':'triangle','fill_color':'#223344','stroke_color':'#ccddee','border_width':5,'layer':'front','position':{'x':100,'y':120,'width':220,'height':170}}
        ]}]
    }
    raw=json.dumps(fragment).encode('utf-8')
    files={'file':('v72.breadmaker.json',io.BytesIO(raw),'application/json')}
    response=client.post('/api/json-import',files=files,data={'context':'{}','target_choices':'{}'})
    assert response.status_code==200,response.text
    boot=assert_ok(client.get('/api/bootstrap'));cv=next(c for c in boot['canvases'] if c['name']=='Imported 7.2')
    data=assert_ok(client.get(f"/api/canvases/{cv['id']}"))
    card=next(n for n in data['nodes'] if n['node_type']=='free')
    figure=next(n for n in data['nodes'] if n['node_type']=='figure')
    assert card['text_align']=='right'
    assert figure['shape']=='triangle' and figure['figure_layer']=='front'
    assert figure['figure_fill_color']=='#223344' and figure['figure_stroke_color']=='#ccddee'
    assert float(figure['figure_border_width'])==5

def test_v720_tasks_expose_milestone_search_text(client,fresh_project):
    task=assert_ok(client.post('/api/tasks',json={'title':'Release preparation','status':'open'}))
    assert_ok(client.post(f"/api/tasks/{task['id']}/milestones",json={'title':'Translate launch notes','description':'French storefront copy'}))
    rows=assert_ok(client.get('/api/tasks'))
    row=next(item for item in rows if item['id']==task['id'])
    assert 'Translate launch notes' in row['milestone_search_text']
    assert 'French storefront copy' in row['milestone_search_text']


def test_v720_frontend_contract():
    assert 'id="taskSearch"' in INDEX
    assert '{{i18n:tasks.search_placeholder}}' in INDEX
    assert "['rect','circle','triangle','oval','line']" in APP or "{id:'triangle',key:'canvas.figure.triangle'}" in APP
    assert "function cardTextAlignControl(node)" in APP
    assert "data-card-text-align" in APP
    assert "text_align:cardTextAlign(node)" in APP
    assert "figure_fill_color" in APP and "figure_stroke_color" in APP and "figure_border_width" in APP
    assert "figureLayer(node)==='back'" in APP
    assert "canvas.figure.move_back" in APP and "canvas.figure.move_forward" in APP
    # Connector deletion must stay in structural Undo, but must not prompt.
    fn=APP.split('async function deleteSelectedEdge(){',1)[1].split('function nearestPort',1)[0]
    assert 'withStructuralUndo' in fn
    assert 'confirmDialog' not in fn
    assert ".node-title-line{border-bottom-color:var(--line2)}" in CSS
    assert '.context-menu-row.submenu-open>.context-submenu{display:block}' in CSS


def test_v720_all_locales_cover_new_strings_and_preserve_parity():
    locales={'en','es','fr','it','ja','pt-BR','zh-CN'}
    en=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    required={
        'canvas.text_alignment','canvas.text_align.left','canvas.text_align.center','canvas.text_align.right',
        'canvas.figure.title','canvas.figure.fill_color','canvas.figure.edge_color','canvas.figure.border_width',
        'canvas.figure.default','canvas.figure.move_back','canvas.figure.move_forward','canvas.figure.rectangle',
        'canvas.figure.circle','canvas.figure.triangle','canvas.figure.oval','canvas.figure.line',
        'tasks.search_label','tasks.search_placeholder','tasks.search_empty',
        'canvas.undo.align_text','canvas.undo.figure_move_back','canvas.undo.figure_move_forward',
        'canvas.undo.figure_background','canvas.undo.figure_background_default','canvas.undo.figure_edge',
        'canvas.undo.figure_edge_default','canvas.undo.figure_edge_weight',
    }
    assert required <= set(en['strings'])
    token=re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')
    for code in locales:
        data=json.loads((ROOT/'locales'/f'{code}.json').read_text(encoding='utf-8'))
        assert set(data['strings'])==set(en['strings'])
        assert data.get('source_index',{})==en.get('source_index',{})
        for key,value in en['strings'].items():
            assert sorted(token.findall(value))==sorted(token.findall(data['strings'][key])),(code,key)
