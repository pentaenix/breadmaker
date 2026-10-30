// Adapter regression checks using the installed phonemizer, without inference.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {Language} from '../../static/vendor/narration/modules/language-en-us.mjs';
import {prepareSpanish} from './spanish.mjs';
const language=new Language();
await language.loadDictionary(new URL('../../static/vendor/narration/dictionaries/en-us.txt',import.meta.url).pathname);
const memo=new Map();
const sandbox={window:{sessionStorage:{getItem:key=>memo.get(key),setItem:(key,value)=>memo.set(key,value)}},navigator:{gpu:{requestAdapter:async()=>({})}},fetch:async()=>({ok:true,json:async()=>({gpu:true})}),Blob,performance,setInterval,clearInterval,setTimeout,clearTimeout};vm.createContext(sandbox);
vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-progress.js',import.meta.url),'utf8'),sandbox);
vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-punctuation.js',import.meta.url),'utf8'),sandbox);
vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-engine.js',import.meta.url),'utf8'),sandbox);
const adapter=new sandbox.window.BreadmakerKokoroEngine();
let spoken,setup;
adapter.pending=Promise.resolve({async setup(data){setup=data},async synthesize(data,progress){
  assert.equal(setup.voice,data.voice,'Worker setup must select the requested voice');
  assert.equal(setup.speed,data.speed,'Generation pace must reach both setup and synthesis');
  spoken=data.input;const generated=language.generate(spoken);
  const message={type:'audio',metaData:{part:0,partsTotal:1},data:{audio:{duration:1,getChannelData:()=>new Float32Array(24000)},words:generated.metadata.words,wtimes:generated.metadata.words.map((_,i)=>i*100),wdurations:generated.metadata.words.map(()=>100)}};
  progress(message);return [message];
}});
const text='I don’t know. Don’t go. I didn’t say that.';
const events=[];const result=await adapter.generate(text,'af_heart',event=>events.push(event));
assert.equal(spoken,"I don't know. Don't go. I didn't say that.");
assert.equal(language.generate("don't").phonemes.join(''),'dˈOnt');
assert.notEqual(language.generate("don't").phonemes.join(''),language.generate("didn't").phonemes.join(''));
assert(result.timings.some(word=>text.slice(word.start,word.end)==='don’t'));
assert(result.timings.some(word=>text.slice(word.start,word.end)==='didn’t'));
assert(events[0].loading&&events.at(-1).completed===1);
assert(result.blob.size>44);
console.log('PASS: smart contractions use dictionary pronunciation; original word offsets and progress preserved.');
await adapter.generate('A different pace.','af_heart',()=>{},()=>{},.95);
assert.equal(setup.speed,.95);
await assert.rejects(()=>adapter.generate('Bad pace.','af_heart',()=>{},()=>{},0),/PACE_INVALID/);

const spanish='¡Hola, niño! Mañana habrá 12 panes y 3,14 euros. ¿Dónde está el pingüino? México, carro y chocolate. Cafe\u0301.';
const items=prepareSpanish(spanish),vocab=JSON.parse(await readFile(new URL('../../static/vendor/narration/models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/tokenizer.json',import.meta.url),'utf8')).model.vocab;
assert.equal(items.find(item=>item.subtitles==='12').value.trim(),'dˈose');
assert(items.find(item=>item.subtitles==='3,14').value.includes('kˈoma'));
assert(items.find(item=>item.subtitles==='niño').value.includes('ɲ'));
assert(items.find(item=>item.subtitles==='México').value.includes('x'));
assert(items.find(item=>item.subtitles==='carro').value.includes('r'));
assert(items.find(item=>item.subtitles==='chocolate').value.includes('ʧ'));
for(const item of items)for(const char of item.value)assert(char in vocab,`Unknown Kokoro token: ${char}`);
assert.equal(items.find(item=>item.subtitles.startsWith('Cafe')).subtitles,'Cafe\u0301');
adapter.spanish=prepareSpanish;
const spanishResult=await adapter.generate(spanish,'ef_dora');
assert(Array.isArray(spoken));
assert(spanishResult.timings.some(word=>spanish.slice(word.start,word.end)==='niño'));
assert(spanishResult.timings.some(word=>spanish.slice(word.start,word.end)==='12'));
assert(spanishResult.timings.some(word=>spanish.slice(word.start,word.end)==='Cafe\u0301'));
console.log('PASS: Spanish stress, ñ/ü, numbers, IPA conversion, vocabulary compatibility, and unchanged source offsets.');

// The same adjusted samples/times must reach streaming and the published WAV,
// including cumulative offsets across chunks and a GPU retry's configuration.
const adjusted=new sandbox.window.BreadmakerKokoroEngine(),audioBuffer=()=>({sampleRate:24000,duration:1,getChannelData:()=>new Float32Array(24000).fill(.2)});
adjusted.context={createBuffer(channels,length,rate){return {sampleRate:rate,duration:length/rate,copyToChannel(samples){this.samples=samples},getChannelData(){return this.samples}}}};
adjusted.pending=Promise.resolve({async setup(){},async synthesize(data,consume){const messages=[{type:'audio',metaData:{part:0,partsTotal:2},data:{audio:audioBuffer(),words:['Hello,','world.'],wtimes:[0,220],wdurations:[200,200]}},{type:'audio',metaData:{part:1,partsTotal:2},data:{audio:audioBuffer(),words:['Next.'],wtimes:[0],wdurations:[200]}}];messages.forEach(consume);return messages}});
const previews=[],padded=await adjusted.generate('Hello, world. Next.','af_heart',()=>{},part=>previews.push(part),1,true);
assert(Math.abs(previews[0].duration-1.1)<1/24000);
assert(Math.abs(padded.timings[1].time-.32)<1/24000);
assert(Math.abs(padded.timings[2].time-1.1)<1/24000);
assert.equal(padded.blob.size,44+previews.reduce((n,part)=>n+Math.round(part.buffer.duration*24000)*2,0));
assert.equal(previews.at(-1).timings.at(-1).start,padded.timings.at(-1).start);
await assert.rejects(()=>adjusted.generate('Bad setting.','af_heart',()=>{},()=>{},1,'yes'),/PUNCTUATION_INVALID/);
console.log('PASS: punctuation padding shared by early chunks and saved WAV, cross-chunk timing shifts, unchanged source offsets and option validation.');

// Option-group labels must translate initially and when the UI language changes.
const attributes=new Map([['label','{{i18n:narration.language_es}}']]);
const group={nodeType:1,isConnected:true,getAttribute:key=>attributes.get(key),setAttribute:(key,value)=>attributes.set(key,value)};
const i18nContext={Node:{ELEMENT_NODE:1},document:{documentElement:{style:{setProperty(){}}}},group,console};
vm.createContext(i18nContext);
vm.runInContext(await readFile(new URL('../../static/js/core/i18n.js',import.meta.url),'utf8'),i18nContext);
for(const locale of ['en','es','fr','it','ja','pt-BR','zh-CN']){
  const catalog=JSON.parse(await readFile(new URL(`../../locales/${locale}.json`,import.meta.url),'utf8'));
  for(const key of ['language_en','language_es','voice_heart','voice_michael','voice_emma','voice_george','voice_dora','voice_alex','voice_santa','backend_preparing','backend_gpu','backend_cpu','backend_fallback','buffering'])assert(catalog.strings['narration.'+key]);
  i18nContext.catalog=catalog;
  vm.runInContext('i18nCatalog=catalog;i18nReady=true;bindI18nAttrs(group);refreshI18nBindings()',i18nContext);
  assert.equal(attributes.get('label'),catalog.strings['narration.language_es']);
}
console.log('PASS: Spanish voice labels and live option-group localization in all seven UI languages.');

const gpu=new sandbox.window.BreadmakerKokoroEngine();
assert.equal(await gpu.chooseBackend(),'webgpu');
memo.set('breadmaker-narration-gpu-failed','1');
assert.equal(await gpu.chooseBackend(),'wasm');
assert.equal(await new sandbox.window.BreadmakerKokoroEngine({backend:'webgpu'}).chooseBackend(),'webgpu','Explicit diagnostic can retry after session fallback');
memo.clear();sandbox.navigator.gpu.requestAdapter=async()=>null;
assert.equal(await gpu.chooseBackend(),'wasm');
sandbox.navigator.gpu.requestAdapter=async()=>({});
sandbox.fetch=async()=>({ok:true,json:async()=>({gpu:false})});
assert.equal(await gpu.chooseBackend(),'wasm');
gpu.backend='webgpu';let attempts=0,terminated=false,closed=false,resets=0;
gpu.pending=Promise.resolve({ww:{terminate(){terminated=true}}});
gpu.context={state:'running',async close(){closed=true}};
gpu.synthesize=async()=>{attempts++;if(attempts===1)throw Error('device lost');return {metrics:{backend:gpu.backend}}};
const fallback=await gpu.generate('Hello','af_heart',()=>{},part=>{if(part.reset)resets++});
assert.equal(fallback.metrics.backend,'wasm');assert.equal(attempts,2);assert.equal(resets,1);assert(terminated&&closed);
assert.equal(memo.get('breadmaker-narration-gpu-failed'),'1');
gpu.synthesize=async()=>{throw Error('CPU failure')};
await assert.rejects(()=>gpu.generate('Hello','af_heart'),/CPU failure/);
const runtime=await readFile(new URL('../../static/vendor/narration/modules/headtts.mjs',import.meta.url),'utf8');
assert(runtime.includes('endpointLowerCase === "webgpu" ? 90000 : 60000'));
assert(runtime.includes('endpointLowerCase === "webgpu" ? 90000 : 30000'));
console.log('PASS: GPU eligibility, optional model, bounded CPU retry, partial-audio reset, and installed initialization deadlines.');
