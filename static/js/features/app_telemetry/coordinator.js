'use strict';

// Explicit synthetic preview only; never registered by production boot.
// Web Locks coordinate one browser origin/profile, not a native installation.
function createAppTelemetryCoordinatorPreview(client,{locks,events,schedule=setTimeout,cancel=clearTimeout}={}){
  let owned=false,disposed=false,acquiring=null,release=null;
  function dispose(){
    if(disposed)return;
    disposed=true;owned=false;
    acquiring?.stop();release?.();release=null;
    unsubscribe();events?.removeEventListener('storage',sync);events?.removeEventListener('pagehide',dispose);
    // Drop in-memory reports without changing the saved user choice.
    client.discardUnsent();
  }
  const unsubscribe=client.subscribe(choice=>{if(choice!=='granted')dispose()});
  const sync=()=>{client.syncConsent();if(client.consent()!=='granted')dispose()};
  events?.addEventListener('storage',sync);
  events?.addEventListener('pagehide',dispose);
  function acquire(){
    if(disposed||client.consent()!=='granted'||typeof locks?.request!=='function')return Promise.resolve(false);
    if(owned)return Promise.resolve(true);
    if(acquiring)return acquiring.result;
    const controller=new AbortController();let resolve,timer,settled=false;
    const result=new Promise(done=>resolve=done);
    const finish=value=>{if(settled)return;settled=true;cancel(timer);resolve(value)};
    const attempt={result,stop(){controller.abort();finish(false)}};acquiring=attempt;
    timer=schedule(()=>{attempt.stop();if(acquiring===attempt)acquiring=null},500);
    // Never wait for another tab's lock. No unavailable/failed-lock fallback.
    Promise.resolve().then(()=>{
      if(disposed||controller.signal.aborted)return false;
      // ifAvailable cannot be combined with a Web Locks signal. The local abort
      // token rejects late callbacks after the deadline instead of queueing.
      return locks.request('breadmaker-app-telemetry-preview-owner-v1',
        {mode:'exclusive',ifAvailable:true},async lock=>{
          if(!lock||disposed||controller.signal.aborted||client.consent()!=='granted'){finish(false);return}
          owned=true;
          const held=new Promise(done=>release=done);
          finish(true);
          try{await held}finally{owned=false;release=null}
        });
    }).catch(()=>{finish(false);dispose()}).finally(()=>{if(acquiring===attempt)acquiring=null});
    return result;
  }
  function isOwner(){
    if(!disposed&&client.consent()!=='granted')dispose();
    return !disposed&&owned;
  }
  return Object.freeze({acquire,isOwner,dispose,
    startSyntheticSession:()=>isOwner()?client.startSyntheticSession():false,
    recordSyntheticMinute:()=>isOwner()?client.recordSyntheticMinute():false,
    recordSyntheticFeature:name=>isOwner()?client.recordSyntheticFeature(name):false,
    deliverToSyntheticSink:sink=>isOwner()?client.deliverToSyntheticSink(sink):Promise.resolve(false)});
}
