"""Execute the real Canvas matching/switch functions with opaque imported IDs."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run_js(body, *, switch=False):
    source = (ROOT / 'static/js/features/canvas/hierarchy.js').read_text()
    runtime = (ROOT / 'static/js/core/runtime.js').read_text()
    helpers = runtime[runtime.index('function entityId('):runtime.index('const CLIENT_ID')]
    matching = source[source.index('function versionGeometryMatches('):source.index('async function initializeCanvasVersions(')]
    switching = source[source.index('let canvasVersionSwitchBusy='):source.index('async function createCanvasVersion(')] if switch else ''
    if switch:
        switching += source[source.index('function versionSwitchChoice('):source.index('async function refreshCanvasVersionState(')]
    script = 'const assert=require("node:assert/strict");\n' + helpers + '\n' + matching + '\n' + switching + '\n' + body
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_native_structure_geometry_uses_opaque_source_ids_and_style_keys():
    run_js('''
    const state={currentCanvas:{developmental_scope:'chapters'},canvasNodes:[
      {id:'node_legacy_1',node_type:'chapter',chapter_id:'chapter_legacy_17',lane_position:0,x:120,y:390,color:'#123456',label_id:'label_legacy_9'},
      {id:'node_legacy_2',node_type:'chapter',chapter_id:'chapter_legacy_18',lane_position:null,x:420,y:600,color:'',label_id:null}
    ],canvasEdges:[]};
    const isDevelopmentalStructureCard=node=>node.node_type==='chapter';
    const developmentalObjectVisible=node=>node.developmental_hidden!==1;
    const detail={items:[
      {id:'item_1',entity_id:'chapter_legacy_17',placed:1,lane_position:0,x:120,y:390},
      {id:'item_2',entity_id:'chapter_legacy_18',placed:0,lane_position:null,x:420,y:600},
      {id:'missing',entity_id:'chapter_deleted',placed:1,lane_position:2}
    ],workspace:{schema:3,nodes:[],edges:[],structural_styles:{'chapter:chapter_legacy_17':{color:'#123456',label_id:'label_legacy_9'}}}};
    assert.equal(versionGeometryMatches(detail),true);
    detail.items[1].x=450;assert.equal(versionGeometryMatches(detail),false);
    detail.items[1].x=420;detail.workspace.structural_styles['chapter:chapter_legacy_17'].color='#ffffff';
    assert.equal(versionGeometryMatches(detail),false);
    ''')


SWITCH_ENV = '''
let canvasSelectionEpoch=0,workspaceNavigation=0;
let project='project_original',actor='actor';
const currentProjectIdentity=()=>project,currentUserIdentity=()=>actor;
const state={currentCanvas:{id:'canvas_imported'},activeCanvasVersionId:'version_old'};
const canvasVersionDirty=()=>false,runtimeSupports=()=>true;
let renders=0,actions=0,persists=0,inAction=false,restored;
const cancelQueuedCanvasViewportPersist=()=>{},renderNodes=()=>{renders++},drawEdges=()=>{},renderCanvasGuides=()=>{},openStructurePanel=()=>{};
const canvasViewportKey=id=>'canvas-viewport-'+id;
const restoreCanvasViewport=async(id,saved)=>{restored=saved};
const persistCurrentCanvasViewport=async()=>{assert.equal(inAction,false);persists++};
const requestAnimationFrame=callback=>callback();
const withStructuralUndo=async(label,fn,context)=>{assert.equal(context.canvasId,'canvas_imported');actions++;inAction=true;try{return await fn()}finally{inAction=false}};
'''


def test_native_structure_switch_serializes_repeats_and_restores_private_viewport():
    run_js(SWITCH_ENV + '''
let release;const calls=[];
const api=async(url,options)=>{
 calls.push(url);
 if(url.endsWith('/activate'))return await new Promise(resolve=>release=()=>resolve({workspace:{viewport:{zoom:1.5,left:30}}}));
 if(url.includes('/api/ui-state/'))return {value:{zoom:1,textSize:16,font:'georgia',snap:false}};
 if(url.includes('/versions/'))return {version:{id:'version_native'},items:[],workspace:{}};
 return {nodes:[{id:'restored_node'}],edges:[]};
};
(async()=>{
 const first=switchCanvasVersion('version_native'),second=switchCanvasVersion('version_native');
 release();await Promise.all([first,second]);
 assert.equal(actions,1);assert.equal(renders,1);assert.equal(persists,1);
 assert.equal(state.activeCanvasVersionId,'version_native');
 assert.deepEqual(restored,{zoom:1.5,left:30,textSize:16,font:'georgia',snap:false});
 assert.equal(calls.filter(url=>url.endsWith('/activate')).length,1);
})().catch(error=>{console.error(error);process.exitCode=1});
''', switch=True)


def test_native_structure_late_activation_does_not_replace_newer_canvas():
    run_js(SWITCH_ENV + '''
let release;const calls=[];
const api=async(url)=>{calls.push(url);return await new Promise(resolve=>release=()=>resolve({ok:true}))};
(async()=>{
 const first=switchCanvasVersion('version_native');
 workspaceNavigation++;state.currentCanvas={id:'newer_canvas'};release();await first;
 assert.equal(renders,0);assert.equal(persists,0);
 assert.equal(state.currentCanvas.id,'newer_canvas');assert.equal(state.activeCanvasVersionId,'version_old');
 assert.equal(calls.length,1);
})().catch(error=>{console.error(error);process.exitCode=1});
''', switch=True)


def test_native_structure_dirty_prompt_dismissal_unlocks_version_switch():
    run_js(SWITCH_ENV.replace('canvasVersionDirty=()=>false', 'canvasVersionDirty=()=>true') + '''
let dismiss,currentModalDismiss=null,calls=0;
const controls={};const $=selector=>controls[selector]||(controls[selector]={});
const showModal=(title,body,actions,onDismiss)=>{dismiss=onDismiss};
const closeModal=()=>{};const api=async()=>{calls++};
(async()=>{const pending=switchCanvasVersion('version_native');dismiss();await pending;
 assert.equal(canvasVersionSwitchBusy,false);assert.equal(calls,0);assert.equal(actions,0);
})().catch(error=>{console.error(error);process.exitCode=1});
''', switch=True)
