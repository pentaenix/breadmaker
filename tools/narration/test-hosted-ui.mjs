// Actual UI owners + actual private store, synthetic account/source only.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {HostedNarrationStore} from './hosted-store.mjs';

const pid='a'.repeat(32),first='usr_'+'b'.repeat(32),second='usr_'+'c'.repeat(32);
let account=first,resolveGeneration,posts=0,disposed=0,revoked=0,lease=0;
const nodes=new Map();
const node=selector=>{if(!nodes.has(selector))nodes.set(selector,{value:selector==='#narrationVoice'?'af_heart':'1',textContent:'',classList:{add(){},remove(){},toggle(){},contains(){return false}},replaceChildren(){},setAttribute(){}});return nodes.get(selector)};
const context={console,Blob,URL:{revokeObjectURL(){revoked++}},Event,crypto,setTimeout,clearTimeout,performance,Audio:class{pause(){}},CLIENT_ID:'synthetic-client',
  state:{runtime:{mode:'hosted'},viewMode:'chapter',activeDocumentId:'tab_manuscript'},
  runtimeSupports:key=>['hosted_persistence','narration'].includes(key),currentProjectIdentity:()=>pid,currentUserIdentity:()=>account,
  $:node,$$:()=>[],i18nText:key=>key,applyI18n(){},flushSave:async()=>{},
  narrationPreferences:{project:null,value:{gpu:false,earlyPlayback:false,autoplay:false,generationPace:1,punctuationPauses:true}},
  normalizeNarrationPreferences:value=>value||{},narrationVoiceCatalog:{value:null,pending:null},
  window:{addEventListener(){},dispatchEvent(){},BreadmakerNarrationPlayback:class{pause(){}clearStream(){}},
    BreadmakerKokoroEngine:class{dispose(){disposed++}generate(){return new Promise(resolve=>resolveGeneration=resolve)}},
    BreadmakerNarrationProgress:class{constructor(){}event(){}completeSource(){}stop(){}}},
};
vm.createContext(context);
for(const file of ['narration-playback.js','narration.js','narration-export.js'])vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/'+file,import.meta.url),'utf8'),context);
const run=source=>vm.runInContext(source,context);
const store=new HostedNarrationStore({request:async()=>{posts++;throw Error('Unexpected private I/O')},
  context:()=>({account,project:pid,sessionEpoch:lease}),onInvalidated:context.resetNarrationPrivateUI});
context.privateStore=store;
run(`narrationStore=privateStore;narration.project=currentProjectIdentity();
  narrationSourceProse=()=>null;syncNarrationActions=()=>{};syncNarrationPlayControl=()=>{};
  clearNarrationPreviewHighlights=()=>{};renderNarrations=()=>{};narrationTime=()=>{};`);
const original=run('narrationSnapshot()');
context.original=original;
assert.equal(run('narrationCurrent(original)'),true);
const source={block_id:'b_synthetic',chapter_id:'c_synthetic',source_text:'Synthetic prose.',text:'Synthetic prose.',start:0,end:16,background:true};
context.syntheticSource=source;
const work=run('generateNarrations([syntheticSource],false,{generateOnly:true})');
while(!resolveGeneration)await new Promise(resolve=>setTimeout(resolve,0));
account=second;
resolveGeneration({blob:new Blob([new Uint8Array(524)]),timings:[],metrics:{backend:'wasm'}});
await assert.rejects(()=>work,/ACCOUNT_CONTEXT_CHANGED/);
assert.equal(posts,0,'old inference completion must not POST under the new account');
assert.equal(disposed,1,'account change stops the private inference worker');
assert.equal(run('narration.busy'),false);
assert.equal(run('narration.records.length'),0);
assert.equal(store.memory().detailsBytes,0);

// A late old action/finally cannot reset a replacement account's current work.
const replacement=run('narrationSnapshot()');context.replacement=replacement;
run('narration.busy=true;narration.url="blob:synthetic-current"');
assert.throws(()=>run('requireNarrationContext(original)'),/ACCOUNT_CONTEXT_CHANGED/);
assert.equal(run('narration.busy'),true);
assert.equal(run('narrationCurrent(replacement)'),true);
run('clearHostedNarrationContext()');lease++;
assert.equal(revoked,1);assert.equal(run('narration.busy'),false);
assert.equal(run('narration.url'),null);
assert.equal(run('narrationCurrent(replacement)'),false);

// Thin index matching must use the verified complete source hash, never excerpt.
const prepared=(await store.sources(pid,[source]))[0];
const thin={id:'r',thin:true,block_id:source.block_id,source_hash:prepared.source_hash,source_length:source.source_text.length,text:'Synthetic',start:0,end:source.end,voice:'af_heart',duration:1};
context.prepared=prepared;context.thin=thin;
assert.equal(run('findNarrationRecording(prepared,[thin],"af_heart").id'),'r');
assert.equal(run('findNarrationRecording({...prepared,source_hash:"0".repeat(64)},[thin],"af_heart")'),undefined);
assert.equal(run('narrationExportCandidate(prepared,[thin],"af_heart").id'),'r');
assert.equal(run('narrationExportClip({...prepared,start:1},[thin],"af_heart")'),null,'partial export must hydrate word timing metadata');
console.log('PASS: old UI generation cannot cross accounts; reset stops workers/audio and old finalizers cannot clobber new state; thin matching/export retain source proof.');
