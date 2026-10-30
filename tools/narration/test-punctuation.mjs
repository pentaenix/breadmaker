import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const scope={window:{},Float32Array};vm.createContext(scope);
vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-punctuation.js',import.meta.url),'utf8'),scope);
const pause=scope.narrationPunctuationPause,apply=scope.window.applyNarrationPunctuation;
for(const [text,token,expected] of [['Hello, world.','Hello',.12],['Stop. Next.','Stop',.3],['Wait... Next.','Wait',.35],['Dr. Jones','Dr',0],['Dra. López','Dra',0],['U.S. Army','U.S.',0],['A. Smith','A.',0],['3.14 litres','3',0],['3,14 litros','3',0],['“Enough!” she said','Enough',.3],['¿Listo? Sí.','Listo',.3],['Hello; next','Hello',.18],['No punctuation here','punctuation',0]]){
  const start=text.indexOf(token);assert.equal(pause(text,{start,end:start+token.length}),expected,text);
}
const text='One, two. Three!',words=[{start:0,end:3,time:0,duration:.2},{start:5,end:8,time:.22,duration:.2},{start:10,end:15,time:.45,duration:.2}],pcm=new Float32Array(24000).fill(.2);
const output=apply(text,pcm,words);
assert(Math.abs(output.timings[1].time-.32)<1/24000);
assert(Math.abs(output.timings[2].time-.82)<2/24000);
assert.equal(output.timings[2].start,10);assert.equal(output.timings[2].end,15);
assert.equal(words[1].time,.22,'Original timing objects must not be mutated');
assert.equal(output.pcm.length,pcm.length+8880,'Only missing silence is inserted; existing final pause already exceeds 300ms');
assert(output.pcm.slice(5280,7680).every(value=>value===0));
const already=[{...words[0],duration:.1},{...words[1],time:.5,duration:.1},{...words[2],time:1,duration:.1}];
const long=new Float32Array(48000).fill(.2);assert.equal(apply(text,long,already).pcm,long,'Do not double existing pauses');
const final=new Float32Array(12000).fill(.2);final.fill(0,6000);assert.equal(apply('Yes.',final,[{start:0,end:3,time:0,duration:.5}]).pcm.length,13200,'Detect 250ms of trailing acoustic silence even if alignment includes it');
const noWords=apply('No alignment.',pcm,[]);assert.equal(noWords.pcm,pcm,'Missing word alignment is a safe no-op');
console.log('PASS: punctuation minimums, English/Spanish abbreviations, initials/decimals/ellipses/quotes, gap top-up, trailing silence, unchanged source offsets and original timings.');
