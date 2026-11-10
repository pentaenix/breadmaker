"""Scene/tab fast paths retain current authorization and exact-revision fencing."""
import json
from pathlib import Path
import subprocess
import threading
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from hosted.firestore.contracts import DocumentConflict


def test_manuscript_metadata_reads_overlap_with_request_context_and_generation_fence(monkeypatch):
    import hosted.http_api as http
    context = ContextVar('scene_tab_request', default=None)
    barrier = threading.Barrier(3)
    generations = [7, 7]
    def read(entity, parents, *, user_id):
        assert context.get() == 'owner'
        assert parents == {'project_id': 'project'} and user_id == 'user'
        barrier.wait(timeout=2)
        return [{'id': entity}]
    monkeypatch.setattr(http, '_uid', lambda: 'user')
    monkeypatch.setattr(http, '_domain', lambda: SimpleNamespace(list=read))
    monkeypatch.setattr(http, 'runtime', SimpleNamespace(projects=SimpleNamespace(get=lambda _: {'generation': generations.pop(0)})))
    token = context.set('owner')
    try:
        result = http._project_snapshot('project', manuscript_only=True, manifest_only=True)
        assert result['chapters'] == [{'id': 'chapter', 'revision': '7'}]
        generations[:] = [7, 8]
        with pytest.raises(DocumentConflict, match='GENERATION_CHANGED'):
            http._project_snapshot('project', manuscript_only=True, manifest_only=True)
    finally:
        context.reset(token)


def test_scene_projection_and_small_tab_bundle_preserve_fences():
    source = (Path(__file__).resolve().parents[1] / 'static/js/features/manuscript/rendering.js').read_text()
    functions = source[source.index('async function loadSceneData'):source.index('async function selectSceneGap')]
    harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
(async()=>{
  let actor='alice',project='project',mode='ok',calls=[];
  const book={project:{generation:7},read_tag:'seven',bundled:true,
    scenes:[{id:'scene'},{id:'foreign'}],chapters:[{id:'a'},{id:'b'}],
    blocks:[{id:'one',chapter_id:'a',scene_id:'scene'},{id:'other',chapter_id:'a',scene_id:'foreign'},{id:'two',chapter_id:'b',scene_id:'scene'}],
    annotations:[{block_id:'one'},{block_id:'other'},{chapter_id:'a'}]};
  const state={bookCache:new Map([['tab',book]]),hostedChapterFirst:true,editGeneration:0,chapters:[{id:'a',tab_id:'tab'}]};
  const context=vm.createContext({state,entityId:String,runtimeSupports:()=>true,currentProjectIdentity:()=>project,currentUserIdentity:()=>actor,
    orderedBookBlocks:data=>data.blocks,
    api:async(url,options)=>{
      calls.push(url);
      if(options?.acceptNotModified){
        assert.equal(options.headers['If-None-Match'],'seven');
        if(mode==='denied')throw Error('denied');
        if(mode==='actor')actor='bob';
        if(mode==='project')project='another';
        if(mode==='epoch')state.manuscriptReadEpoch=(state.manuscriptReadEpoch||0)+1;
        if(mode==='dirty'){state.editGeneration++;state.dirty=true;}
        return null;
      }
      if(url.includes('/api/scenes/'))return {scene:{id:'uncached'},blocks:[]};
      assert.ok(url.includes('manifest=true&bundled=true'),'Small tab did not request bounded bundle');
      return book;
    }});
  vm.runInContext(source,context);
  const scene=await context.loadSceneData('scene');
  assert.equal(calls.length,1);assert.deepEqual(Array.from(scene.blocks,b=>b.id),['one','two']);
  assert.equal(scene.annotations.length,2);assert.equal(scene.chapter_revisions.a,'7');assert.equal(scene.chapter_revisions.b,'7');
  for(const failure of ['denied','actor','project','epoch','dirty']){
    mode=failure;await assert.rejects(context.loadSceneData('scene'));
    state.dirty=false;
  }
  mode='ok';calls=[];state.bookCache.clear();
  const small=await context.loadBookData('tab');
  assert.equal(small,book);assert.equal(calls.length,2,'Bundled tab should need only read + authority validation');
  calls=[];await context.loadSceneData('uncached');assert.equal(calls.length,1);assert.ok(calls[0].includes('/api/scenes/uncached/manuscript'));
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness], input=json.dumps(functions), text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_moved_scene_falls_back_instead_of_publishing_empty_old_tab():
    source = (Path(__file__).resolve().parents[1] / 'static/js/features/manuscript/rendering.js').read_text()
    function = source[source.index('async function loadSceneData'):source.index('async function loadBookData')]
    harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
(async()=>{
  const stale={scenes:[{id:'s',tab_id:'old'}],blocks:[{id:'b',scene_id:'s'}]};
  const current={scenes:[{id:'s',tab_id:'new'}],blocks:[]};let calls=0;
  const context=vm.createContext({state:{bookCache:new Map([['old',stale]])},runtimeSupports:()=>true,
    loadBookData:async()=>current,api:async path=>{calls++;assert.equal(path,'/api/scenes/s/manuscript');return {blocks:[{text_content:'Current scene'}]}}});
  vm.runInContext(source,context);
  const result=await context.loadSceneData('s');assert.equal(calls,1);assert.equal(result.blocks[0].text_content,'Current scene');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness], input=json.dumps(function), text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
