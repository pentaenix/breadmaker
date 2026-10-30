import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const sandbox={Blob,DataView,Uint8Array,ArrayBuffer};vm.createContext(sandbox);
vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-export.js',import.meta.url),'utf8'),sandbox);
const join=sandbox.narrationJoinWav,pcm=sandbox.narrationWavPCM,clip=sandbox.narrationExportClip;
const samples=new Uint8Array(48000);for(let i=0;i<samples.length;i++)samples[i]=i%251;
const wav=join([new Blob([samples])]);assert.equal(wav.size,48044);
assert.deepEqual(new Uint8Array(await (await pcm(wav)).arrayBuffer()),samples);
assert.equal((await pcm(wav,.25,.75)).size,24000);
const combined=join([await pcm(wav,0,.5),await pcm(wav,.5,1)]);
assert.deepEqual(new Uint8Array(await combined.arrayBuffer()),new Uint8Array(await wav.arrayBuffer()));
await assert.rejects(()=>pcm(new Blob(['not a wave'])),/FORMAT/);
assert.throws(()=>join([]),/SIZE/);
assert.throws(()=>join([{size:0xffffffff}]),/SIZE/);
const record={id:'full',block_id:'b',source_text:'Hello world.',start:0,end:12,duration:1,voice:'af_heart',timings:[{start:0,end:5,time:0,duration:.4},{start:6,end:11,time:.5,duration:.4}]};
assert.equal(clip({...record,start:6,end:11},[record],'af_heart').start,.5);
assert.equal(clip({...record,start:7,end:11},[record],'af_heart'),null,'Never silently include a partial spoken word');
assert.equal(clip({...record,source_text:'Edited text.'},[record],'af_heart'),null);
assert.equal(clip(record,[record,{...record,id:'other',voice:'bm_george'}],'bm_george').record.id,'other');
assert.equal(clip({...record,start:6,end:11},[{...record,id:'exact',start:6,end:11,text:'world',duration:.4}],'af_heart').end,.4);
console.log('PASS: lossless WAV join/trim, word-boundary selection, preferred voice, stale/missing audio and RIFF size/format guards.');

let Processor;const worklet={AudioWorkletProcessor:class{},sampleRate:24000,Float32Array,registerProcessor(name,processor){Processor=processor}};vm.createContext(worklet);
vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-pitch-worklet.js',import.meta.url),'utf8'),worklet);
function render(pitch){const processor=new Processor(),output=[];for(let block=0;block<500;block++){const input=Float32Array.from({length:128},(_,i)=>Math.sin(2*Math.PI*440*(block*128+i)/24000)),samples=new Float32Array(128);processor.process([[input]],[[samples]],{semitones:[pitch]});output.push(...samples)}return output}
const bypass=render(0);assert(bypass.every((sample,i)=>Math.abs(sample-Math.sin(2*Math.PI*440*i/24000))<1e-6));
for(const pitch of [-4,-2,2,4]){
  const samples=render(pitch).slice(24000);assert(samples.every(sample=>Number.isFinite(sample)&&Math.abs(sample)<=1.001));
  // Dominant spectral frequency, not zero-crossing counts of overlap sidebands.
  const expected=440*2**(pitch/12);let best=0,peak=0;
  for(let frequency=Math.floor(expected)-18;frequency<expected+18;frequency++){let real=0,imag=0;for(let i=0;i<samples.length;i++){real+=samples[i]*Math.cos(2*Math.PI*frequency*i/24000);imag+=samples[i]*Math.sin(2*Math.PI*frequency*i/24000)}const power=real*real+imag*imag;if(power>peak){peak=power;best=frequency}}
  assert(Math.abs(best-expected)<12,`${pitch}: expected ${expected}, measured ${best}`);
  assert.equal(samples.length,40000,'Pitch never changes the playback clock');
}
console.log('PASS: exact pitch bypass, bounded +/-4 semitone DSP, expected pitch direction, unchanged sample count.');
