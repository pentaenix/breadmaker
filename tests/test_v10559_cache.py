"""A book cache is data, never authority or a newer write precondition."""
import json
from pathlib import Path
import subprocess


def test_book_projection_checks_actor_epoch_and_exact_revision_without_rebasing():
    source=(Path(__file__).resolve().parents[1]/'static/js/features/manuscript/rendering.js').read_text()
    source=source[source.index('async function loadChapterData'):source.index('let fullTextLoading')]
    harness=r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
(async()=>{
  let actor='alice',calls=0,release,changed=false;
  const book={project:{generation:7},read_tag:'verified-seven',chapters:[{id:'chapter',tab_id:'book'}],
    blocks:[{id:'block',chapter_id:'chapter',text_content:'verified seven'}]};
  const state={bookCache:new Map([['book',book]]),chapterCache:new Map(),editGeneration:0,hostedChapterFirst:true};
  const context=vm.createContext({state,currentProjectIdentity:()=>'project',currentUserIdentity:()=>actor,runtimeSupports:()=>true,
    api:async(url,options)=>{
      calls++;
      if(url.startsWith('/api/book?')){assert.equal(options.headers['If-None-Match'],'verified-seven');
        if(changed)return {project:{generation:8}};
        return new Promise(resolve=>release=resolve);
      }
      return {chapter:{id:'chapter',tab_id:'book'},blocks:[{text_content:'new eight'}],revision:'8',next_block_offset:null};
    }});
  vm.runInContext(source,context);
  const one=context.loadChapterData('chapter'),two=context.loadChapterData('chapter');
  assert.equal(calls,1);release(null);
  const [a,b]=await Promise.all([one,two]);assert.equal(a,b);assert.equal(a.revision,'7');assert.equal(a.blocks[0],book.blocks[0]);
  state.chapterCache.clear();const oldActor=context.loadChapterData('chapter');actor='bob';release(null);
  await assert.rejects(oldActor,/changed while loading/);assert.equal(state.chapterCache.size,0);
  const oldEpoch=context.loadChapterData('chapter');state.manuscriptReadEpoch=1;release(null);
  await assert.rejects(oldEpoch,/changed while loading/);assert.equal(state.chapterCache.size,0);
  changed=true;const fresh=await context.loadChapterData('chapter');
  assert.equal(fresh.revision,'8');assert.equal(fresh.blocks[0].text_content,'new eight');
  assert.equal(book.project.generation,7);assert.equal(book.blocks[0].text_content,'verified seven');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result=subprocess.run(['node','-e',harness],input=json.dumps(source),text=True,capture_output=True,timeout=3)
    assert result.returncode==0,result.stderr
