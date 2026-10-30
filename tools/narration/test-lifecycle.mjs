// No models or dependencies needed: exercise stalled/disposed worker lifetimes.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
let deadline;
const sandbox={window:{BreadmakerNarrationTiming:class{}},performance,Blob,
  setTimeout(fn){deadline=fn;return 1},clearTimeout(){}};
vm.createContext(sandbox);
vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-engine.js',import.meta.url),'utf8'),sandbox);
function fixture(){
  const engine=new sandbox.window.BreadmakerKokoroEngine({backend:'wasm'});
  let stopped=0,consume;
  engine.ready=true;
  const worker={ww:{terminate(){stopped++}},async setup(){},synthesize(data,callback){consume=callback;return new Promise(()=>{})}};
  engine.worker=worker;engine.load=async()=>worker;
  return {engine,stopped:()=>stopped,late:()=>consume({type:'audio',data:{audio:{}}})};
}
const stalled=fixture();let chunks=0;
const pending=stalled.engine.generate('Hello.','af_heart',()=>{},()=>chunks++);
await new Promise(resolve=>setImmediate(resolve));
await assert.rejects(()=>stalled.engine.generate('Overlap.','af_heart'),/BUSY/);
deadline();await assert.rejects(()=>pending,/STALLED/);
assert.equal(stalled.stopped(),1);stalled.late();assert.equal(chunks,0);
assert.equal(stalled.engine.generating,false);
const disposed=fixture(),work=disposed.engine.generate('Hello.','af_heart');
await new Promise(resolve=>setImmediate(resolve));disposed.engine.dispose();
await assert.rejects(()=>work,/REPLACED/);
assert.equal(disposed.stopped(),1);assert.equal(disposed.engine.generating,false);
console.log('PASS: CPU stall, concurrent synthesis, disposal and late callbacks are bounded.');
