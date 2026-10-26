"""Chapter cache reuse always requires a current server revision/authority check."""
import json
from pathlib import Path
import subprocess


def test_chapter_cache_revalidation_is_bounded_and_never_publishes_partial_text():
    source=(Path(__file__).resolve().parents[1]/'static/js/features/manuscript/rendering.js').read_text()
    source=source[source.index('async function loadChapterData'):source.index('async function openWholeBook')]
    harness=r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
(async()=>{
  const state={hostedChapterFirst:true,chapterCache:new Map(),editGeneration:0,dirty:false,saveInFlight:null};
  let project='one',revision=1,denied=false,pageFailure=false,dirty=false,expired=false;const calls=[];
  const context=vm.createContext({state,runtimeSupports:()=>true,currentProjectIdentity:()=>project,
    api:async(url,options)=>{
      calls.push([url,options]);if(denied)throw new Error('denied');
      if(expired)state.manuscriptReadEpoch=(state.manuscriptReadEpoch||0)+1;
      if(options?.headers?.['If-None-Match']==='tag'+revision)return null;
      const last=url.includes('block_offset=128');
      if(last&&pageFailure)throw new Error('page failed');
      if(dirty){state.dirty=true;state.editGeneration++}
      return {chapter:{id:url.split('/').pop().split('?')[0]},revision:String(revision),read_tag:'tag'+revision,
        blocks:[{id:last?'last':'first',text_content:'revision '+revision}],next_block_offset:last?null:128};
    }});
  vm.runInContext(source,context);
  const first=await context.loadChapterData('chapter');assert.equal(first.blocks.length,2);
  let before=calls.length;assert.equal(await context.loadChapterData('chapter'),first);
  assert.equal(calls.length,before+1,'Warm cache still downloaded chapter pages');
  assert.equal(calls.at(-1)[1].headers['If-None-Match'],'tag1');
  denied=true;await assert.rejects(context.loadChapterData('chapter'),/denied/);denied=false;
  revision=2;const changed=await context.loadChapterData('chapter');
  assert.notEqual(changed,first);assert.equal(changed.blocks[0].text_content,'revision 2');
  project='two';before=calls.length;const foreign=await context.loadChapterData('chapter');
  assert.notEqual(foreign,changed);assert.equal(calls.length,before+2);
  assert.equal(calls[before][1],undefined,'Project switch reused another project cache');
  pageFailure=true;await assert.rejects(context.loadChapterData('partial'),/page failed/);
  assert.equal(state.chapterCache.has('two:partial'),false);pageFailure=false;
  dirty=true;await assert.rejects(context.loadChapterData('dirty'),/changed while loading/);
  assert.equal(state.chapterCache.has('two:dirty'),false);assert.equal(state.dirty,true);
  dirty=false;state.dirty=false;
  expired=true;await assert.rejects(context.loadChapterData('logout'),/changed while loading/);
  assert.equal(state.chapterCache.has('two:logout'),false,'A pre-logout request repopulated memory');expired=false;
  for(let i=0;i<20;i++)await context.loadChapterData('bounded'+i);
  assert.equal(state.chapterCache.size,16);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result=subprocess.run(['node','-e',harness],input=json.dumps(source),text=True,capture_output=True,timeout=3)
    assert result.returncode==0,result.stderr
