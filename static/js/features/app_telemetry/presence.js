'use strict';

// Dormant detector preview. No listeners, timers, input contents or report hooks.
// The only interaction timestamps come from explicit synthetic test calls.
function createAppTelemetryPresencePreview({client,document:surface,monotonic=()=>performance.now()}={}){
  let last=null,disposed=false;
  const unsubscribe=client?.subscribe?.(()=>{last=null})||(()=>{});
  function foreground(){
    try{return !disposed&&client?.consent()==='granted'&&surface?.visibilityState==='visible'&&surface.hasFocus()===true}catch{return false}
  }
  function eligible(){
    const now=monotonic();
    if(!foreground()||last===null||!Number.isFinite(now)||now<last||now-last>=60000){last=null;return false}
    return true;
  }
  function noteSyntheticInteraction(){
    if(!foreground()){last=null;return false}
    const now=monotonic();if(!Number.isFinite(now)){last=null;return false}
    last=now;return true;
  }
  return Object.freeze({eligible,noteSyntheticInteraction,dispose(){disposed=true;last=null;unsubscribe()}});
}
