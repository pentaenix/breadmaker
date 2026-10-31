import assert from 'node:assert/strict';
import {HostedNarrationStore} from './hosted-store.mjs';

const account='usr_'+'a'.repeat(32),project='b'.repeat(32),id='c'.repeat(64),publication='d'.repeat(32);
const source='Synthetic 😀 prose',digest=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(source))).toString('hex');
const bytes=new Uint8Array(524).fill(7),blob=new Blob([bytes],{type:'audio/wav'});
let context={account,project,sessionEpoch:0},generation=1,active=true,published=false,wrongActor=false,oversize=false;
const calls=[];let invalidated=0,failDelete=false,lastDelete=null,holdMetadata=null,failFinish=false;
const item=()=>({id,publication,generation,block_id:'b_1',target_type:'block',chapter_id:'chapter_1',document_id:'tab_manuscript',
  source_generation:1,source_hash:digest,source_length:source.length,text:source,start:0,end:source.length,
  voice:'af_heart',engine:'kokoro-timestamped-q8-v1',duration:.01,audio_bytes:bytes.length});
const detail=()=>({...item(),source_text:source,timings:[{start:0,end:9,time:0,duration:.005}],index:item()});
const headers=()=>({'Content-Type':'application/json','X-Breadmaker-Narration-Actor':wrongActor?'usr_'+'e'.repeat(32):account,'X-Breadmaker-Narration-Project':project});
const json=value=>new Response(JSON.stringify(value),{headers:headers()});
const failure=(status,message)=>Object.assign(Error(message),{status});
const request=async(url,options)=>{
  assert.equal(options.rawResponse,true);assert.equal(options.headers['X-Breadmaker-Project'],project);
  assert.equal(options.headers['X-Breadmaker-Narration-Account'],account);assert.ok(options.signal);
  calls.push([url,options]);
  if(url==='/api/narrations'&&options.method==='POST'){
    const body=JSON.parse(options.body);
    assert.equal(body.audio_bytes,blob.size);assert.equal(body.metadata.source_text,source);
    for(const name of ['source_hash','background','engine','id','index'])assert.equal(name in body.metadata,false);
    return json({id,publication,part_bytes:2*1024**2,state:published?'published':'pending'});
  }
  if(url==='/api/narrations')return json({records:active?[item()]:[],next:null});
  if(url.endsWith('/manifest')){if(!active)throw failure(404,'NARRATION_NOT_FOUND');return json({publication,generation,audio_bytes:bytes.length,parts:1,part_bytes:2*1024**2})}
  if(url.includes('/audio?')){
    if(!active)throw failure(404,'NARRATION_NOT_FOUND');
    return new Response(oversize?new Uint8Array(bytes.length+1):bytes,{headers:headers()});
  }
  if(url.includes('/parts/')){assert.equal(options.body.size,blob.size);assert.equal(options.headers['Content-Type'],'application/octet-stream');return json({ok:true})}
  if(url.includes('/finish')){published=true;active=true;if(failFinish){failFinish=false;throw failure(0,'Synthetic lost finalize response')}return json(detail())}
  if(options.method==='DELETE'){
    const key=options.idempotencyKey;
    if(lastDelete)assert.equal(key,lastDelete,'uncertain DELETE must retain its operation identity');
    lastDelete=key;active=false;
    if(failDelete){failDelete=false;throw failure(0,'Synthetic lost response')}
    return json({undo_token:'f'.repeat(64)});
  }
  if(url.endsWith('/restore')){assert.equal(JSON.parse(options.body).undo_token,'f'.repeat(64));active=true;generation=3;return json(detail())}
  if(url.includes('?expected_generation=')){
    if(holdMetadata)await holdMetadata;
    return json(detail());
  }
  throw Error('Unexpected synthetic endpoint');
};
const store=new HostedNarrationStore({request,context:()=>context,onInvalidated:()=>invalidated++});
assert.equal(invalidated,0);
const listed=await store.list(project);assert.equal(listed.length,1);assert.equal(listed[0].thin,true);
assert.equal('source_text' in listed[0],false);assert.equal('timings' in listed[0],false);assert.equal(invalidated,0,'first explicit use does not invalidate a fresh context');
const beforeDetails=calls.length;
const full=await store.metadata(project,listed[0]);assert.equal(full.source_text,source);assert.ok(Object.isFrozen(full.timings[0]));
assert.equal(calls.length,beforeDetails+2);
await store.metadata(project,listed[0]);assert.equal(calls.length,beforeDetails+3,'warm private metadata still checks live admission/head but not body');
const beforeAudio=calls.filter(([url])=>url.includes('/audio?')).length;
assert.deepEqual(new Uint8Array(await (await store.audio(project,id)).arrayBuffer()),bytes);
assert.equal(calls.filter(([url])=>url.includes('/audio?')).length,beforeAudio+1);
await store.audio(project,id);assert.equal(calls.filter(([url])=>url.includes('/audio?')).length,beforeAudio+1,'warm buffer has no body transfer');
assert.equal(calls.at(-1)[0],`/api/narrations/${id}/manifest`,'private RAM is never an authorization bypass');

const prepared=await store.sources(project,[{block_id:'b_1',source_text:source}],{remember:true});
assert.equal(prepared[0].source_hash,digest);assert.equal(store.fresh(listed[0],source),true);assert.equal(store.fresh(listed[0],source+' changed'),false);
failFinish=true;
await assert.rejects(()=>store.save(project,{...full,background:true,source_hash:digest,backend:'wasm'},blob),/lost finalize response/);
const saved=await store.save(project,{...full,background:true,source_hash:digest,backend:'wasm'},blob);
assert.equal(saved.thin,true);assert.equal('timings' in saved,false);assert.equal(store.records.size,1);
const putCount=calls.filter(([,options])=>options.method==='PUT').length;
await store.save(project,full,blob);assert.equal(calls.filter(([,options])=>options.method==='PUT').length,putCount,'published retry retrieves receipt, not another upload');
failDelete=true;
await assert.rejects(()=>store.remove(project,id),/lost response/);
const token=(await store.remove(project,id)).undo_token;
assert.equal(store.records.size,0);assert.equal(active,false);
const restored=await store.restore(project,id,token);assert.equal(restored.generation,3);
const afterRestore=calls.filter(([url])=>url.includes('/audio?')).length;
await store.audio(project,id);assert.equal(calls.filter(([url])=>url.includes('/audio?')).length,afterRestore,'live same-publication Undo proof can reuse immutable audio');

active=false;
await assert.rejects(()=>store.audio(project,id),/NOT_FOUND/);
assert.deepEqual(store.memory(),{indexRecords:0,detailsBytes:0,audioBytes:0,hashBytes:0});assert.ok(invalidated>0);
active=true;await store.list(project);wrongActor=true;
await assert.rejects(()=>store.metadata(project,id),/ACCOUNT_CONTEXT_CHANGED/);assert.equal(store.memory().indexRecords,0);wrongActor=false;

await store.list(project);
let release;holdMetadata=new Promise(resolve=>release=resolve);
const old=store.metadata(project,id);await new Promise(resolve=>setTimeout(resolve,0));
context={...context,sessionEpoch:1};store.clear();release();holdMetadata=null;
await assert.rejects(()=>old,/ACCOUNT_CONTEXT_CHANGED/);assert.equal(store.memory().detailsBytes,0,'late old-session payload cannot install');
await store.list(project);oversize=true;
await assert.rejects(()=>store.audio(project,id),/RESPONSE_TOO_LARGE/);assert.equal(store.memory().audioBytes,0);oversize=false;

const beforeWrongProject=calls.length;
await assert.rejects(()=>store.list('e'.repeat(32)),/ACCOUNT_CONTEXT_CHANGED/);assert.equal(calls.length,beforeWrongProject);
const boundedStore=new HostedNarrationStore({request,context:()=>context});
boundedStore.capture(project);
for(let i=0;i<100;i++)boundedStore.audioCache.put(String(i),new Blob([new Uint8Array(1024**2)]),1024**2);
assert.ok(boundedStore.memory().audioBytes<=32*1024**2);assert.ok(boundedStore.audioCache.items.size<=16);
for(let i=0;i<1000;i++)boundedStore.details.put(String(i),{synthetic:true},100000);
assert.ok(boundedStore.memory().detailsBytes<=16*1024**2);assert.ok(boundedStore.details.items.size<=128);
boundedStore.clear();assert.equal(boundedStore.memory().audioBytes,0);
const captured=boundedStore.capture(project),beforeStaleAction=calls.length;
context={...context,account:'usr_'+'9'.repeat(32)};
await assert.rejects(()=>boundedStore.save(project,full,blob,captured),/ACCOUNT_CONTEXT_CHANGED/);
await assert.rejects(()=>boundedStore.list(project,captured),/ACCOUNT_CONTEXT_CHANGED/);
await assert.rejects(()=>boundedStore.sources(project,[{source_text:source}],{snapshot:captured}),/ACCOUNT_CONTEXT_CHANGED/);
assert.equal(calls.length,beforeStaleAction,'an old async action must never recapture a new account before a write');
assert.equal(boundedStore.memory().detailsBytes,0);
console.log('Hosted private RAM limits, live admission, immutable audio reuse, upload retries, Delete/Undo and session fencing passed');
