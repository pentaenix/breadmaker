"""No cache outage, project switch, or idle editor can become a save dependency."""
import json
from pathlib import Path
import subprocess


def test_active_editor_cache_queue_is_bounded_resumable_and_not_a_poller():
    source=(Path(__file__).resolve().parents[1]/'static/js/features/manuscript/persistence.js').read_text()
    source=source[source.index('const hostedReadCacheQueue'):source.index('function resetDirtyState')]
    harness=r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
(async()=>{
  const state={dirty:true,saveInFlight:null},idle=[],events={},calls=[],warnings=[];
  let project='one',hosted=true,mode='more',release=null;
  const document={visibilityState:'visible',addEventListener:(name,callback)=>events[name]=callback};
  const context=vm.createContext({state,document,currentProjectIdentity:()=>project,runtimeSupports:()=>hosted,
    window:{requestIdleCallback:callback=>idle.push(callback)},console:{warn:(...args)=>warnings.push(args)},
    api:async(path,options)=>{calls.push([project,path,options]);
      if(mode==='fail')throw Object.assign(new Error('Synthetic provider failure'),{status:503});
      if(mode==='delay')return await new Promise(resolve=>release=resolve);
      if(mode==='more'){mode='done';return {more:true}}
      return {more:false};
    }});
  vm.runInContext(source,context);
  async function turn(){assert.ok(idle.length);idle.shift()();await new Promise(resolve=>setImmediate(resolve))}
  context.queueHostedReadCache('chapter','1');assert.equal(idle.length,0,'Dirty drafts triggered maintenance');
  state.dirty=false;context.kickHostedReadCache();assert.equal(idle.length,1);
  await turn();assert.equal(calls.length,1);assert.equal(idle.length,1,'Resumable work was abandoned');
  await turn();assert.equal(calls.length,2);assert.equal(idle.length,0,'Completed work kept polling');
  context.queueHostedReadCache('chapter','1');assert.equal(idle.length,0,'Same-revision hints repeated completed work');
  mode='done';state.saveInFlight={};context.queueHostedReadCache('saving','1');
  assert.equal(idle.length,0,'Maintenance ran before the canonical save finished');
  state.saveInFlight=null;context.kickHostedReadCache();await turn();assert.equal(idle.length,0);
  mode='fail';context.queueHostedReadCache('chapter','2');await turn();
  assert.equal(idle.length,0,'Provider failure caused an idle retry loop');assert.equal(state.dirty,false);
  assert.equal(warnings.length,1);
  mode='done';context.queueHostedReadCache('chapter','2');await turn();assert.equal(idle.length,0);
  // A new save while an old cache request is in flight must remain queued.
  mode='delay';context.queueHostedReadCache('chapter','3');await turn();
  context.queueHostedReadCache('chapter','4');assert.equal(idle.length,0);
  release({more:false});await new Promise(resolve=>setImmediate(resolve));mode='done';
  assert.equal(idle.length,1);await turn();assert.equal(idle.length,0);
  context.queueHostedReadCache('chapter','4');assert.equal(idle.length,0);
  // Hidden editors do nothing, but becoming visible resumes queued work.
  document.visibilityState='hidden';context.queueHostedReadCache('hidden','1');assert.equal(idle.length,0);
  document.visibilityState='visible';events.visibilitychange();await turn();
  // A project switch drops old work before issuing any request in the new context.
  state.dirty=true;context.queueHostedReadCache('old-project-chapter','1');project='two';
  context.queueHostedReadCache('new-project-chapter','1');state.dirty=false;context.kickHostedReadCache();
  await turn();assert.ok(calls.at(-1)[1].includes('new-project-chapter'));
  assert.ok(!calls.some(row=>row[0]==='two'&&row[1].includes('old-project-chapter')));
  assert.ok(calls.every(row=>row[2].method==='POST'));assert.equal(idle.length,0);
  hosted=false;context.queueHostedReadCache('local','1');assert.equal(idle.length,0,'Local editor acquired a cloud dependency');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result=subprocess.run(['node','-e',harness],input=json.dumps(source),text=True,capture_output=True,timeout=3)
    assert result.returncode==0,result.stderr
