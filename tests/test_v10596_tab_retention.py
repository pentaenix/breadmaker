"""Bounded auxiliary-tab retention and latest-selection navigation contracts."""
import json
from pathlib import Path
import subprocess

SOURCE = (Path(__file__).resolve().parents[1] / 'static/js/features/manuscript/rendering.js').read_text()


def run_node(functions, harness):
    result = subprocess.run(['node', '-e', harness], input=json.dumps(functions), text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_small_tabs_remain_bounded_and_hits_refresh_recency_without_skipping_authority():
    run_node(SOURCE[SOURCE.index('async function loadBookData'):SOURCE.index('async function selectSceneGap')], r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('fs').readFileSync(0,'utf8'));
(async()=>{
 let reads=0,validations=0,denied=false;
 const state={bookCache:new Map(),hostedChapterFirst:true,editGeneration:0};
 const c=vm.createContext({state,entityId:String,runtimeSupports:()=>true,currentProjectIdentity:()=> 'p',currentUserIdentity:()=> 'u',
 api:async(url,opts)=>{if(opts?.acceptNotModified){validations++;if(denied)throw Error('denied');return null;}reads++;
 const id=new URL(url,'http://localhost').searchParams.get('tab_id');
 return {bundled:true,read_tag:id,project:{generation:1},chapters:[],blocks:id.startsWith('large')?Array.from({length:257},()=>({text_content:'text'})):[]};}});
 vm.runInContext(source,c);
 for(const id of ['large1','guide','license','matrix'])await c.loadBookData(id);
 assert.equal(reads,4);assert.equal(validations,4);
 for(let i=0;i<5;i++)for(const id of ['large1','guide','license','matrix'])await c.loadBookData(id);
 assert.equal(reads,4,'Warm tab cycle reread body');assert.equal(validations,24,'Every hit must recheck authority');
 for(const id of ['s4','s5','s6','s7'])await c.loadBookData(id);
 await c.loadBookData('large1');await c.loadBookData('s8');
 assert.equal(state.bookCache.size,8);assert(state.bookCache.has('large1'));assert(!state.bookCache.has('guide'));
 await c.loadBookData('large2');await c.loadBookData('large3');
 assert(!state.bookCache.has('large1'));assert(state.bookCache.has('large2'));assert(state.bookCache.has('large3'));
 denied=true;await assert.rejects(c.loadBookData('large3'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_latest_queued_tab_selection_is_not_dropped_and_obsolete_result_never_renders():
    run_node(SOURCE[SOURCE.index('async function openWholeBook'):SOURCE.index('function captureManuscriptViewport')], r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('fs').readFileSync(0,'utf8'));
(async()=>{
 let release;const loads=[],renders=[];
 const state={activeDocumentId:'a',viewMode:'book',bookCache:new Map(),chapterRevisions:new Map()};
 const c=vm.createContext({state,fullTextLoading:false,fullTextSettled:Promise.resolve(),workspaceNavigation:0,entityId:String,
 runtimeSupports:()=>false,cancelFullTextPreview:()=>{},flushSave:async()=>{},
 loadBookData:async id=>{loads.push(id);if(id==='a')await new Promise(r=>{release=r});return {id,chapters:[]};},
 renderBookEditor:()=>{renders.push(state.bookData.id);return false;},$:()=>({scrollTop:0}),
 normalizeManuscriptNavMode:()=>{},syncManuscriptViewControls:()=>{},resetDirtyState:()=>{},setSaveState:()=>{},renderDocuments:()=>{},renderChapterTree:()=>{},refreshFindForContext:()=>{},syncCheckpointPanelContext:()=>{},syncCheckpointComparisonContext:()=>{},syncHistoryComparisonContext:()=>{},scheduleCommentsWorkspaceSync:()=>{},schedulePersistManuscriptLocation:()=>{}});
 vm.runInContext(source,c);
 const a=c.openWholeBook(false);state.activeDocumentId='b';const b=c.openWholeBook(false);state.activeDocumentId='c';const last=c.openWholeBook(false);
 release();await Promise.all([a,b,last]);
 assert.deepEqual(loads,['a','c']);assert.deepEqual(renders,['c']);assert.equal(c.fullTextLoading,false);
 loads.length=0;renders.length=0;state.activeDocumentId='a';const nextA=c.openWholeBook(false);
 state.activeDocumentId='b';const nextB=c.openWholeBook(false);c.workspaceNavigation++;
 release();await Promise.all([nextA,nextB]);assert.deepEqual(loads,['a']);assert.deepEqual(renders,[]);
})().catch(e=>{console.error(e);process.exitCode=1});
''')
