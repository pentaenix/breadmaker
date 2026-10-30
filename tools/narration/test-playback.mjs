import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
let timer=0;const timers=new Map();
const sandbox={window:{},setInterval:callback=>{timers.set(++timer,callback);return timer},clearInterval:id=>timers.delete(id)};
vm.createContext(sandbox);vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-playback.js',import.meta.url),'utf8'),sandbox);
const native={paused:true,ended:false,currentTime:0,playbackRate:1,pause(){this.paused=true;this.onpause?.()},async play(){this.paused=false;this.onplay?.()}};
const active=new Set(),context={currentTime:0,destination:{},async resume(){},createBufferSource(){
  const node={playbackRate:{value:1},connect(){},disconnect(){},stop(){active.delete(this)},start(when,offset){this.end=when+(this.buffer.duration-offset)/this.playbackRate.value;active.add(this)}};return node;
}};
function advance(time){context.currentTime=time;for(const node of [...active])if(node.end<=time){active.delete(node);node.onended?.()}}
const audio=new sandbox.window.BreadmakerNarrationPlayback(native);let ended=0,played=0;audio.onended=()=>ended++;audio.onplay=()=>played++;
audio.startStream(context);audio.appendStream({duration:2});await audio.play();assert(!audio.paused&&played===1);
advance(1);assert.equal(audio.currentTime,1);
audio.appendStream({duration:3});advance(2.5);assert.equal(audio.currentTime,2.5);
audio.pause();advance(10);assert.equal(audio.currentTime,2.5);assert(audio.paused);
audio.playbackRate=2;await audio.play();advance(11.25);assert(audio.waiting&&!audio.paused);assert.equal(audio.currentTime,5);assert.equal(ended,0);
audio.appendStream({duration:1});assert(!audio.waiting);audio.finishStream();advance(11.75);assert(audio.ended&&audio.paused);assert.equal(ended,1);
await audio.play();assert.equal(audio.currentTime,0);audio.currentTime=3;assert.equal(audio.currentTime,3);
audio.pause();audio.appendStream({duration:1});assert(audio.paused,'New chunks must not override Pause');
audio.src='blob:saved-recording';assert.equal(audio.stream,null);assert.equal(timers.size,0);await audio.play();assert(!native.paused);
audio.pause();assert(native.paused);assert.equal(native.src,'blob:saved-recording');
console.log('PASS: early chunk playback, buffering, pause/resume, seek, speed, restart, single ended event, saved WAV handoff, timer cleanup.');

const sources=[{block_id:'b',source_text:'Hello world.'},{block_id:'a',source_text:'Next passage.'},{block_id:'missing',source_text:'No audio.'}];
const record=(id,block_id,source_text,start,end,voice='af_heart')=>({id,block_id,source_text,start,end,voice});
const records=[record('part','b','Hello world.',0,5),record('other','b','Hello world.',0,12,'am_michael'),record('full','b','Hello world.',0,12),record('stale','missing','Old prose',0,9),record('a1','a','Next passage.',0,4),record('a2','a','Next passage.',5,13),record('overlap','a','Next passage.',2,9)];
const full={...sources[0],start:0,end:12};
assert.equal(sandbox.window.findNarrationRecording(full,records,'af_heart',true).id,'full');
assert.equal(sandbox.window.findNarrationRecording(full,records,'am_michael',true).id,'other');
assert.equal(sandbox.window.findNarrationRecording(full,records,'bm_george'),undefined);
assert.equal(sandbox.window.findNarrationRecording(full,records,'bm_george',true).id,'full');
assert.equal(sandbox.window.findNarrationRecording(full,[records[0]],'af_heart',true),undefined,'A partial selection cannot substitute for a complete block');
assert.equal(sandbox.window.findNarrationRecording({...full,source_text:'Edited text.'},records,'af_heart',true),undefined);
console.log('PASS: preferred voice, complete-source/range reuse, other saved voices reused only for scopes, stale/partial recordings require generation.');
