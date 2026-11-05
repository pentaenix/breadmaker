const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const context=vm.createContext({TextEncoder,AbortController,setTimeout,clearTimeout});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/js/features/app_telemetry/foundation.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/js/features/app_telemetry/coordinator.js'),'utf8'),context);
function fixture(overrides={}){
  const values=new Map();let time=Date.UTC(2026,10,4,10,0),ids=0;
  const storage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const make=()=>context.createAppTelemetryFoundation({storage,now:()=>time,randomUUID:()=>{ids++;return `00000000-0000-4000-8000-${String(ids).padStart(12,'0')}`},appVersion:'10.5.66',...overrides});
  return {client:make(),make,storage,values,advance:ms=>time+=ms,idCount:()=>ids};
}
test('pending and declining create no identifier, observations, reports or sink calls',async()=>{
  const f=fixture();let calls=0;
  for(const stage of ['pending','declined']){
    assert.equal(f.client.consent(),stage);
    assert.equal(f.client.startSyntheticSession(),false);
    assert.equal(f.client.recordSyntheticMinute(),false);
    assert.equal(f.client.recordSyntheticFeature('manuscript'),false);
    assert.equal(await f.client.deliverToSyntheticSink(()=>{calls++;return true}),false);
    if(stage==='pending')assert.equal(f.client.setConsent(false),true);
  }
  assert.equal(f.idCount(),0);assert.equal(calls,0);assert.equal(f.client.snapshot().length,0);
});
test('affirmative choice persists versioned local receipt; identity renews after withdrawal',()=>{
  const f=fixture();assert.equal(f.client.setConsent(true),true);assert.equal(f.make().consent(),'granted');
  f.client.startSyntheticSession();const id=f.client.snapshot()[0].installation_id;
  f.client.setConsent(false);assert.equal(f.client.snapshot().length,0);
  assert.ok(![...f.values.values()].join('').includes(id));
  f.client.setConsent(true);f.client.startSyntheticSession();assert.notEqual(f.client.snapshot()[0].installation_id,id);
});
test('closed fields, enum-only observations, clamped counts, immutable snapshots',()=>{
  const f=fixture();f.client.setConsent(true);
  assert.equal(f.client.recordSyntheticFeature('private/project/path'),false);
  f.client.startSyntheticSession();assert.equal(f.client.startSyntheticSession(),false);
  for(let i=0;i<300;i++){f.client.recordSyntheticMinute();f.client.recordSyntheticFeature('canvas')}
  const row=f.client.snapshot()[0];
  assert.deepEqual(Object.keys(row).sort(),['schema','installation_id','notice_version','app_version','bucket_utc_ms','sessions','active_minutes','feature_visits'].sort());
  assert.equal(row.active_minutes,15);assert.equal(row.sessions,1);assert.equal(row.feature_visits.canvas,255);
  row.feature_visits.canvas=0;assert.equal(f.client.snapshot()[0].feature_visits.canvas,255);
});
test('queue expires at 24h and remains bounded without storage or network reporting',()=>{
  const f=fixture();f.client.setConsent(true);
  for(let i=0;i<200;i++){f.client.recordSyntheticMinute();f.advance(900000)}
  const rows=f.client.snapshot();assert.ok(rows.length<=96);
  f.advance(86400000);assert.equal(f.client.snapshot().length,0);
  assert.equal(f.values.size,1);assert.ok(![...f.values.values()][0].includes('active_minutes'));
});
test('completed buckets batch at most four, under byte ceiling, no more than once per 15m',async()=>{
  const f=fixture();f.client.setConsent(true);
  for(let i=0;i<8;i++){f.client.startSyntheticSession();f.client.recordSyntheticMinute();f.advance(900000)}
  let calls=0;
  const sink=body=>{calls++;assert.ok(Buffer.byteLength(body)<=4096);assert.equal(JSON.parse(body).reports.length,4);return true};
  assert.equal(await f.client.deliverToSyntheticSink(sink),true);assert.equal(f.client.snapshot().length,4);
  assert.equal(await f.client.deliverToSyntheticSink(sink),false);assert.equal(calls,1);
  f.advance(900000);assert.equal(await f.client.deliverToSyntheticSink(sink),true);
});
test('exceptions and hanging sink are silent, bounded, retain queued batches, throttle retry',async()=>{
  const f=fixture({schedule:fn=>setTimeout(fn,5)});f.client.setConsent(true);f.client.startSyntheticSession();f.advance(900000);
  assert.equal(await f.client.deliverToSyntheticSink(()=>{throw Error('offline')}),false);
  assert.equal(f.client.snapshot().length,1);f.advance(900000);
  let signal;assert.equal(await f.client.deliverToSyntheticSink((_,options)=>{signal=options.signal;return new Promise(()=>{})}),false);
  assert.equal(signal.aborted,true);assert.equal(f.client.snapshot().length,1);
  assert.equal(await f.client.deliverToSyntheticSink(()=>true),false);
});
test('withdrawal cancels pending sink and clears queue; late completion never republishes',async()=>{
  const f=fixture();f.client.setConsent(true);f.client.startSyntheticSession();f.advance(900000);
  let release,signal;const pending=f.client.deliverToSyntheticSink((_,options)=>{signal=options.signal;return new Promise(resolve=>release=resolve)});
  await Promise.resolve();f.client.setConsent(false);assert.equal(await pending,false);assert.equal(signal.aborted,true);
  release(true);await Promise.resolve();assert.equal(f.client.snapshot().length,0);
});
test('withdrawal before async sink invocation prevents invocation entirely',async()=>{
  const f=fixture();f.client.setConsent(true);f.client.startSyntheticSession();f.advance(900000);let calls=0;
  const pending=f.client.deliverToSyntheticSink(()=>{calls++;return true});f.client.setConsent(false);
  assert.equal(await pending,false);assert.equal(calls,0);
});
test('other tab withdrawal and identity changes invalidate stale batches',async()=>{
  const f=fixture();f.client.setConsent(true);const other=f.make();other.startSyntheticSession();
  f.client.setConsent(false);assert.equal(other.recordSyntheticMinute(),false);assert.equal(other.snapshot().length,0);
  f.client.setConsent(true);other.recordSyntheticMinute();const id=other.snapshot()[0].installation_id;
  f.client.setConsent(false);f.client.setConsent(true);other.recordSyntheticMinute();
  assert.equal(other.snapshot().length,1);assert.notEqual(other.snapshot()[0].installation_id,id);
});
test('corrupt, outdated, blocked storage and non-random identifiers fail closed',()=>{
  const f=fixture();f.client.setConsent(true);
  const key=[...f.values.keys()][0];f.values.set(key,'{bad');assert.equal(f.make().consent(),'pending');
  f.values.set(key,JSON.stringify({choice:'granted',notice_version:'old',installation_id:'00000000-0000-4000-8000-000000000001'}));assert.equal(f.make().consent(),'pending');
  const blocked=fixture({storage:{getItem(){throw Error('blocked')},setItem(){throw Error('blocked')},removeItem(){throw Error('blocked')}}});
  assert.equal(blocked.client.setConsent(true),false);assert.equal(blocked.client.recordSyntheticMinute(),false);
  const invalid=fixture({randomUUID:()=>'/Users/private'});assert.equal(invalid.client.setConsent(true),false);assert.equal(invalid.client.snapshot().length,0);
});
function fakeLocks(){
  let held=false;
  return {request(_name,options,fn){
    assert.equal(options.ifAvailable,true);assert.equal(options.mode,'exclusive');
    if(held)return Promise.resolve(fn(null));
    held=true;return Promise.resolve().then(()=>fn({})).finally(()=>held=false);
  }};
}
const turn=()=>new Promise(resolve=>setImmediate(resolve));
test('coordinator has no owner or observations before consent, or without Web Locks',async()=>{
  const f=fixture();let calls=0;
  const owner=context.createAppTelemetryCoordinatorPreview(f.client,{locks:{request(){calls++}}});
  assert.equal(await owner.acquire(),false);assert.equal(calls,0);assert.equal(owner.recordSyntheticMinute(),false);
  f.client.setConsent(true);
  const unavailable=context.createAppTelemetryCoordinatorPreview(f.client);
  assert.equal(await unavailable.acquire(),false);assert.equal(unavailable.startSyntheticSession(),false);
  owner.dispose();unavailable.dispose();assert.equal(f.client.consent(),'granted');
});
test('only one tab observes, and disposing drops unsent reports while allowing handover',async()=>{
  const f=fixture();f.client.setConsent(true);const second=f.make(),locks=fakeLocks();
  const a=context.createAppTelemetryCoordinatorPreview(f.client,{locks}),b=context.createAppTelemetryCoordinatorPreview(second,{locks});
  assert.equal(await a.acquire(),true);assert.equal(await b.acquire(),false);
  assert.equal(a.startSyntheticSession(),true);assert.equal(b.startSyntheticSession(),false);
  assert.equal(a.recordSyntheticMinute(),true);assert.equal(b.recordSyntheticFeature('canvas'),false);
  assert.equal(second.snapshot().length,0);a.dispose();await turn();
  assert.equal(f.client.snapshot().length,0);assert.equal(f.client.consent(),'granted');
  assert.equal(await b.acquire(),true);assert.equal(b.startSyntheticSession(),true);b.dispose();
});
test('lock rejection or timeout stays silent; a late callback cannot become an owner',async()=>{
  const f=fixture();f.client.setConsent(true);
  for(const locks of [{request(){throw Error('unsupported')}},{request(){return Promise.reject(Error('unavailable'))}}]){
    const a=context.createAppTelemetryCoordinatorPreview(f.client,{locks});assert.equal(await a.acquire(),false);assert.equal(a.isOwner(),false);a.dispose();
  }
  let callback;
  const a=context.createAppTelemetryCoordinatorPreview(f.client,{locks:{request(_name,_options,fn){callback=fn;return new Promise(()=>{})}},schedule:fn=>setTimeout(fn,5)});
  assert.equal(await a.acquire(),false);await callback({});assert.equal(a.isOwner(),false);a.dispose();
});
test('withdrawal releases ownership, aborts sink and never changes choice back to granted',async()=>{
  const f=fixture();f.client.setConsent(true);
  const a=context.createAppTelemetryCoordinatorPreview(f.client,{locks:fakeLocks()});assert.equal(await a.acquire(),true);
  a.startSyntheticSession();f.advance(900000);let signal;
  const pending=a.deliverToSyntheticSink((_,options)=>{signal=options.signal;return new Promise(()=>{})});
  await Promise.resolve();f.client.setConsent(false);
  assert.equal(await pending,false);assert.equal(signal.aborted,true);assert.equal(a.isOwner(),false);
  assert.equal(await a.acquire(),false);assert.equal(f.client.consent(),'declined');assert.equal(f.client.snapshot().length,0);
});
test('pagehide releases ownership and drops reports without withdrawing consent',async()=>{
  const f=fixture();f.client.setConsent(true);const events=new EventTarget(),locks=fakeLocks();
  const a=context.createAppTelemetryCoordinatorPreview(f.client,{locks,events});
  assert.equal(await a.acquire(),true);a.recordSyntheticMinute();events.dispatchEvent(new Event('pagehide'));
  assert.equal(a.isOwner(),false);assert.equal(f.client.snapshot().length,0);assert.equal(f.client.consent(),'granted');
  await turn();const b=context.createAppTelemetryCoordinatorPreview(f.make(),{locks});assert.equal(await b.acquire(),true);b.dispose();
});
test('discarding reports retains cooldown even after wall clock rollback',async()=>{
  const f=fixture();f.client.setConsent(true);f.client.recordSyntheticMinute();f.advance(900000);
  assert.equal(await f.client.deliverToSyntheticSink(()=>false),false);
  f.client.discardUnsent();f.advance(-900000);f.client.recordSyntheticMinute();f.advance(900001);
  assert.equal(await f.client.deliverToSyntheticSink(()=>true),false);
  f.advance(899999);assert.equal(await f.client.deliverToSyntheticSink(()=>true),true);
  assert.equal(f.client.consent(),'granted');
});
test('unexpected loss of a granted lock stops the owner and drops queued reports',async()=>{
  const f=fixture();f.client.setConsent(true);let breakLock;
  const broken=new Promise((_,reject)=>breakLock=reject);
  const a=context.createAppTelemetryCoordinatorPreview(f.client,{locks:{request(_name,_options,fn){return Promise.race([Promise.resolve().then(()=>fn({})),broken])}}});
  assert.equal(await a.acquire(),true);a.recordSyntheticMinute();breakLock(Error('lock broken'));await turn();
  assert.equal(a.isOwner(),false);assert.equal(a.recordSyntheticMinute(),false);assert.equal(f.client.snapshot().length,0);
  assert.equal(f.client.consent(),'granted');
});
