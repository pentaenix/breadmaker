'use strict';

// REVIEW ONLY: no runtime hooks, endpoint, network API, SDK or automatic timers.
// All observations in this checkpoint must be synthetic. See README.md.
function createAppTelemetryFoundation({storage, now=Date.now, randomUUID=()=>crypto.randomUUID(),
  appVersion='0.0.0', schedule=setTimeout, cancel=clearTimeout}={}) {
  const key='breadmaker-optional-app-telemetry-v1', noticeVersion='preview-1';
  const bucketMs=15*60*1000, ttlMs=24*60*60*1000, maxQueue=96, batchSize=4;
  const features=Object.freeze(['manuscript','canvas','maps','tasks','reviews']);
  const version=/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(appVersion)?appVersion:'0.0.0';
  let queue=[], identity=null, decision='pending', generation=0, lastAttempt=-Infinity;
  let sessionStarted=false, pending=null, disposed=false;
  const listeners=new Set();
  const clock=()=>{const value=now();return Number.isSafeInteger(value)&&value>=0&&value<8640000000000000?value:0};
  function read(){
    try {
      const raw=storage?.getItem(key);if(!raw||raw.length>1024)return null;
      const value=JSON.parse(raw);
      if(value?.notice_version!==noticeVersion||!['granted','declined'].includes(value.choice))return null;
      if(value.choice==='granted'&&(!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value.installation_id)))return null;
      return value;
    } catch {return null}
  }
  function clear(){
    queue=[];sessionStarted=false;lastAttempt=-Infinity;generation++;
    if(pending){pending.controller.abort();pending.finish(false);pending=null}
  }
  function consent(){
    const value=read(),next=value?.choice||'pending',id=next==='granted'?value.installation_id:null;
    if(next!==decision||id!==identity){clear();decision=next;identity=id}
    return disposed?'declined':decision;
  }
  function notify(){for(const fn of listeners){try{fn(consent())}catch{}}}
  function setConsent(allowed){
    if(disposed||typeof allowed!=='boolean')return false;
    // Immediately stop work even if preference persistence subsequently fails.
    clear();decision='declined';identity=null;
    try {
      const value={notice_version:noticeVersion,choice:allowed?'granted':'declined',decided_at_utc:new Date(clock()).toISOString()};
      if(allowed){value.installation_id=randomUUID();if(!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value.installation_id))throw Error('Invalid random identifier')}
      storage.setItem(key,JSON.stringify(value));
      if(consent()!==(allowed?'granted':'declined'))throw Error('Preference unavailable');
      notify();return true;
    } catch {
      // A failed opt-out write must not resurrect old permission in this tab.
      disposed=true;try{storage.removeItem(key)}catch{}notify();return false;
    }
  }
  function prune(){const cutoff=clock()-ttlMs;queue=queue.filter(row=>row.bucket_utc_ms>cutoff);if(queue.length>maxQueue)queue=queue.slice(-maxQueue)}
  function bucket(){
    if(disposed||consent()!=='granted')return null;
    prune();const timestamp=Math.floor(clock()/bucketMs)*bucketMs;
    let row=queue.find(item=>item.bucket_utc_ms===timestamp);
    if(!row){row={schema:'bm-app-telemetry-preview-v1',installation_id:identity,notice_version:noticeVersion,app_version:version,bucket_utc_ms:timestamp,sessions:0,active_minutes:0,feature_visits:Object.fromEntries(features.map(name=>[name,0]))};queue.push(row);prune()}
    return row;
  }
  function startSyntheticSession(){
    if(consent()!=='granted'||sessionStarted)return false;
    const row=bucket();if(!row)return false;row.sessions=Math.min(15,row.sessions+1);sessionStarted=true;return true;
  }
  function recordSyntheticMinute(){const row=bucket();if(!row)return false;row.active_minutes=Math.min(15,row.active_minutes+1);return true}
  function recordSyntheticFeature(name){if(!features.includes(name))return false;const row=bucket();if(!row)return false;row.feature_visits[name]=Math.min(255,row.feature_visits[name]+1);return true}
  function snapshot(){if(disposed||consent()!=='granted')return [];prune();return JSON.parse(JSON.stringify(queue))}
  function deliverToSyntheticSink(sink){
    if(disposed||consent()!=='granted'||pending||typeof sink!=='function')return Promise.resolve(false);
    prune();const rows=queue.filter(row=>row.bucket_utc_ms+bucketMs<=clock()).slice(0,batchSize);
    if(!rows.length||clock()-lastAttempt<bucketMs)return Promise.resolve(false);
    lastAttempt=clock();const epoch=generation,controller=new AbortController();
    // Copies and a byte ceiling prevent mutable queue references or oversized batches.
    const body=JSON.stringify({schema:'bm-app-telemetry-batch-preview-v1',reports:rows});
    if(new TextEncoder().encode(body).length>4096)return Promise.resolve(false);
    return new Promise(resolve=>{
      let settled=false,timer;
      const finish=ok=>{if(settled)return;settled=true;cancel(timer);if(pending?.controller===controller)pending=null;resolve(ok)};
      pending={controller,finish};timer=schedule(()=>{controller.abort();finish(false)},500);
      Promise.resolve().then(()=>{
        if(disposed||epoch!==generation||consent()!=='granted')return false;
        return sink(body,{signal:controller.signal});
      }).then(ok=>{
        if(settled||disposed||epoch!==generation||consent()!=='granted')return finish(false);
        if(ok===true){const sent=new Set(rows);queue=queue.filter(row=>!sent.has(row));finish(true)}else finish(false);
      },()=>finish(false));
    });
  }
  consent();
  return Object.freeze({consent,setConsent,startSyntheticSession,recordSyntheticMinute,recordSyntheticFeature,
    snapshot,deliverToSyntheticSink,syncConsent(){consent();notify()},
    subscribe(fn){listeners.add(fn);return ()=>listeners.delete(fn)},
    dispose(){clear();disposed=true;listeners.clear()},
    limits:Object.freeze({bucketMs,ttlMs,maxQueue,batchSize,maxBatchBytes:4096,minDeliveryIntervalMs:bucketMs})});
}
