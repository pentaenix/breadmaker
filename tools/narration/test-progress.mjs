import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

let now=0,nextTimer=0;
const data=new Map(),timers=new Map();
const storage={getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value)};
const context={window:{localStorage:storage},crossOriginIsolated:false,navigator:{hardwareConcurrency:8},performance:{now:()=>now},setInterval:callback=>{const id=++nextTimer;timers.set(id,callback);return id},clearInterval:id=>timers.delete(id)};
vm.createContext(context);
vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-progress.js',import.meta.url),'utf8'),context);
const Timing=context.window.BreadmakerNarrationTiming,Progress=context.window.BreadmakerNarrationProgress;
const timing=new Timing();assert.equal(timing.threads,1);
timing.recordLoad(6000);timing.recordGeneration(39,'af_heart',10000,false);
assert.equal(new Timing().estimate(39,'af_heart'),10000);
assert.equal(new Timing().data.loadMs,6000);
assert.equal(timing.parts('Hello. Come inside.','af_heart'),2);
assert.equal(timing.parts('Hola. Pase adelante.','ef_dora'),1);
for(let i=0;i<20;i++)timing.recordGeneration(39,'af_heart',10000,false);
assert.equal(timing.data.samples['en-warm'].length,8);
assert(![...data.values()].some(value=>value.includes('source_text')||value.includes('recording')));

const updates=[],engine={ready:false,generated:false,timing};
const progress=new Progress(engine,[{text:'A'.repeat(39)},{text:'B'.repeat(39)}],'af_heart',status=>updates.push(status),()=>now);
assert(updates[0].fraction>0&&updates[0].remainingSeconds>0);
for(let i=0;i<10;i++){now+=150;progress.tick()}
assert(new Set(updates.map(status=>status.fraction)).size>8,'Progress moves before any model callback');
progress.event(0,{phase:'synthesis',completed:0,total:2});
for(let i=0;i<10;i++){now+=150;progress.tick()}
progress.event(0,{phase:'synthesis',completed:1,total:2});
now+=30000;progress.tick();assert(updates.at(-1).overrun);
assert(updates.at(-1).fraction<1);
progress.event(0,{phase:'saving'});progress.completeSource(0);
progress.event(1,{phase:'synthesis',completed:0,total:1});
for(let i=0;i<10;i++){now+=150;progress.tick()}
assert(updates.every((status,index)=>index===0||status.fraction>=updates[index-1].fraction),'Progress never moves backward');
progress.stop(false);assert.equal(timers.size,0);assert(updates.at(-1).fraction<1);
const completed=[];const finishing=new Progress({...engine,ready:true},[{text:'Hi'}],'af_heart',status=>completed.push(status),()=>now);
finishing.stop(true);assert.equal(completed.at(-1).fraction,1);assert.equal(timers.size,0);

context.crossOriginIsolated=true;
const mixed=new Progress({...engine,ready:true},[{text:'Already recorded',cached:true},{text:'Missing audio'}],'af_heart',()=>{},()=>now);
assert.equal(mixed.estimates[0],0);assert.equal(mixed.costs[0],0);assert(mixed.estimates[1]>0);mixed.stop();
const multicore=new Timing();assert.equal(multicore.threads,4);assert.notEqual(multicore.key,timing.key);
const gpu=new Timing('webgpu');assert.equal(gpu.threads,0);assert.notEqual(gpu.key,multicore.key);
assert(gpu.estimate(39,'af_heart',true,2)>gpu.estimate(39,'af_heart',false,2)+14000);
gpu.recordGeneration(39,'af_heart',16000,true,2);assert.equal(gpu.estimate(39,'af_heart',true,2),16000);
data.set(multicore.key,'invalid JSON');assert.doesNotThrow(()=>new Timing());
data.set(multicore.key,JSON.stringify({loadMs:6000,samples:{'en-warm':[null,{chars:'bad',ms:10}]}}));
assert(Number.isFinite(new Timing().estimate(39,'af_heart')));
context.window.localStorage={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}};
assert.doesNotThrow(()=>new Timing().recordLoad(6000));
console.log('PASS: immediate/smooth monotonic estimates, learned timings, cold/warm thread profiles, overruns, failure cleanup, denied/corrupt storage.');
