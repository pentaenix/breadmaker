"""Execute real routing/first-draft JS, including the zero-project save handoff."""
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_js(code):
    runtime = (ROOT / 'static/js/core/runtime.js').read_text()
    helpers = runtime.split('// Local SQLite IDs', 1)[1].split('const CLIENT_ID', 1)[0]
    helpers = '// Local SQLite IDs' + helpers
    routing = (ROOT / 'static/js/core/routing.js').read_text()
    boot = (ROOT / 'static/js/core/boot.js').read_text().split('// -------------------- lease / boot')[0]
    result = subprocess.run(['node', '-e', PRELUDE + helpers + routing + boot + code],
                            capture_output=True, text=True, timeout=2)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


PRELUDE = '''
const assert=require('node:assert/strict');
const uid=()=>`synthetic-first-project-${++nonceCount}`;let nonceCount=0;
const pid='1234567890abcdef1234567890abcdef';
let hosted=true,creates=0,saves=0,failSave=false;
const state={project:null,chapters:[],activeDocumentId:1,viewMode:'book',chapter:null};
const elements={'#chapterTitle':{innerText:'Synthetic title',textContent:''},
  '#editor':{innerText:'Synthetic first manuscript'},'#manuscriptPaper':{},'.manuscript-block':{textContent:''}};
const $=selector=>elements[selector];const $$=()=>[];
const window={addEventListener:()=>{}};
const runtimeSupports=()=>hosted;const currentProjectIdentity=()=>state.project?.id||'';
const navigation=[];const location={assign:path=>navigation.push(['assign',path]),replace:path=>navigation.push(['replace',path])};
const memory=new Map();const sessionStorage={getItem:key=>memory.get(key)||null,setItem:(key,value)=>memory.set(key,value),removeItem:key=>memory.delete(key)};
const setSaveState=()=>{};const toast=()=>{};
let api=async(path,options)=>{assert.equal(path,'/api/projects');assert.equal(options.method,'POST');creates++;return {project:{id:pid}}};
const selectChapter=async(id,save,search)=>{assert.equal(save,false);assert.equal(search,false);state.viewMode='chapter';state.chapter=state.chapters.find(c=>c.id===id)};
const markDirty=id=>assert.equal(id,state.chapter.id);
let durable=null;
const flushSave=async()=>{if(failSave)throw Error('synthetic save failure');saves++;durable={title:elements['#chapterTitle'].textContent,text:elements['.manuscript-block'].textContent}};
'''


@pytest.mark.parametrize('hosted,replace', [(True, False), (True, True), (False, False), (False, True)])
def test_open_project_uses_validated_project_route_in_hosted_and_local_modes(hosted, replace):
    result = run_js(f'hosted={json.dumps(hosted)};openWorkspaceProject(pid,{{replace:{json.dumps(replace)}}});'
                    'console.log(JSON.stringify(navigation));')
    assert result == [['replace' if replace else 'assign',
                       '/p/1234567890abcdef1234567890abcdef']]


@pytest.mark.parametrize('invalid', ['', '../other', '//attacker.invalid', 'bad-id'])
def test_invalid_hosted_project_cannot_navigate(invalid):
    assert run_js(f'assert.throws(()=>openWorkspaceProject({json.dumps(invalid)}),/PROJECT_ROUTE_ID_INVALID/);'
                  'console.log(JSON.stringify(navigation));') == []


@pytest.mark.parametrize('view', ['canvas','maps','tasks','reviews'])
def test_first_workspace_creates_project_and_routes_without_a_manuscript_draft(view):
    result = run_js('''
    (async()=>{
      elements['#chapterTitle'].innerText='Chapter 1';elements['#editor'].innerText='';
      await createHostedProjectFromBlankEditor({view:VIEW});
      assert.equal(creates,1);assert.equal(memory.size,0);
      console.log(JSON.stringify(navigation));
    })().catch(e=>{console.error(e);process.exitCode=1});
    '''.replace('VIEW',json.dumps(view)))
    assert result == [['replace','/p/1234567890abcdef1234567890abcdef/'+view]]


def test_first_workspace_navigation_coalesces_creation_and_honors_latest_intent():
    result = run_js('''
    (async()=>{
      let release;api=async()=>{creates++;return await new Promise(resolve=>{release=resolve})};
      const first=createHostedProjectFromBlankEditor({view:'canvas'});
      const next=createHostedProjectFromBlankEditor({view:'tasks'});
      assert.equal(creates,1);release({project:{id:pid}});await Promise.all([first,next]);
      assert.equal(JSON.parse(memory.get(HOSTED_FIRST_DRAFT_KEY)).text,'Synthetic first manuscript');
      console.log(JSON.stringify(navigation));
    })().catch(e=>{console.error(e);process.exitCode=1});
    ''')
    assert result == [['replace','/p/1234567890abcdef1234567890abcdef/tasks']]


def test_failed_first_workspace_creation_keeps_draft_and_allows_retry():
    result = run_js('''
    (async()=>{
      const keys=[];
      api=async(path,options)=>{keys.push(options.idempotencyKey);creates++;if(creates===1)throw Error('synthetic rejection');return {project:{id:pid}}};
      await createHostedProjectFromBlankEditor({view:'maps'});
      assert.equal(elements['#manuscriptPaper'].contentEditable,'true');
      assert.equal(hostedFirstProjectCreating,false);assert.equal(navigation.length,0);
      assert.equal(elements['#editor'].innerText,'Synthetic first manuscript');
      await createHostedProjectFromBlankEditor({view:'reviews'});assert.equal(creates,2);
      assert.deepEqual(keys,['synthetic-first-project-1','synthetic-first-project-1']);
      console.log(JSON.stringify(navigation));
    })().catch(e=>{console.error(e);process.exitCode=1});
    ''')
    assert result == [['replace','/p/1234567890abcdef1234567890abcdef/reviews']]


@pytest.mark.parametrize('persist',[True,False])
def test_empty_boot_persists_first_project_without_input_but_not_an_extra_blank(persist):
    result=run_js('''
    globalThis.document={body:{classList:{add(){}}},documentElement:{removeAttribute(){}}};
    elements['#manuscriptView']={classList:{add(){}}};
    elements['#manuscriptPaper'].addEventListener=()=>{};
    elements['#editor'].focus=()=>{};
    elements['#chapterTitle'].innerText='Chapter 1';elements['#editor'].innerText='';
    state.workspace={active_project_id:'old-project',projects:[{id:'old-project'}]};
    (async()=>{
      await bootHostedBlankProject({persist:PERSIST});
      assert.equal(creates,PERSIST?1:0);assert.equal(memory.size,0);
      assert.equal(state.hostedZeroProject,true);
      assert.equal(state.workspace.active_project_id,'');assert.equal(state.workspace.projects.length,1);
      assert.equal(elements['#manuscriptPaper'].contentEditable,PERSIST?'false':'true');
      console.log(JSON.stringify(navigation));
    })().catch(e=>{console.error(e);process.exitCode=1});
    '''.replace('PERSIST',json.dumps(persist)))
    assert result==([['replace','/p/1234567890abcdef1234567890abcdef']] if persist else [])


@pytest.mark.parametrize('opaque', [False,True])
def test_first_keystrokes_survive_create_navigation_book_hydration_save_and_reload(opaque):
    result = run_js('''
    (async()=>{
      await createHostedProjectFromBlankEditor();
      assert.equal(creates,1);assert.deepEqual(navigation,[['replace','/p/'+pid]]);
      assert.equal(elements['#manuscriptPaper'].contentEditable,'false');
      const staged=JSON.parse(memory.get(HOSTED_FIRST_DRAFT_KEY));
      assert.equal(staged.text,'Synthetic first manuscript');
      // Normal new-project hydration opens whole-book view, not a chapter.
      state.project={id:pid};state.chapters=[{id:CHAPTER_ID,tab_id:TAB_ID,title:'Chapter 1'}];state.activeDocumentId=TAB_ID;
      await restoreHostedFirstDraft();
      assert.equal(state.viewMode,'chapter');assert.equal(saves,1);assert.equal(memory.size,0);
      elements['.manuscript-block'].textContent=''; // Reload from durable storage.
      elements['.manuscript-block'].textContent=durable.text;
      console.log(JSON.stringify(durable));
    })().catch(e=>{console.error(e);process.exitCode=1});
    '''.replace('CHAPTER_ID',json.dumps('chapter_1' if opaque else 1)).replace('TAB_ID',json.dumps('tab_manuscript' if opaque else 1)))
    assert result == {'title': 'Synthetic title', 'text': 'Synthetic first manuscript'}


@pytest.mark.parametrize('value,expected', [(1,1),('1',1),('001','001'),('chapter_1','chapter_1'),
    ('canvas_aabb','canvas_aabb'),('map_abcdef','map_abcdef'),('task_abc','task_abc'),
    ('review_abc','review_abc'),('9007199254740993','9007199254740993')])
def test_id_normalization_never_hashes_truncates_or_merges_opaque_ids(value,expected):
    assert run_js(f'console.log(JSON.stringify(entityId({json.dumps(value)})));')==expected


@pytest.mark.parametrize('failure', ['save', 'missing-chapter', 'wrong-project'])
def test_failed_or_mismatched_restore_never_discards_staged_draft(failure):
    result = run_js('''
    (async()=>{
      await createHostedProjectFromBlankEditor();
      const original=memory.get(HOSTED_FIRST_DRAFT_KEY);
      state.project={id:pid};state.chapters=[{id:1,tab_id:1,title:'Chapter 1'}];
    ''' + {'save': 'failSave=true;', 'missing-chapter': 'state.chapters=[];',
           'wrong-project': "state.project={id:'ffffffffffffffffffffffffffffffff'};"}[failure] + '''
      try{await restoreHostedFirstDraft()}catch{}
      assert.equal(saves,0);assert.equal(memory.get(HOSTED_FIRST_DRAFT_KEY),original);
      console.log(JSON.stringify({preserved:true}));
    })().catch(e=>{console.error(e);process.exitCode=1});
    ''')
    assert result == {'preserved': True}
