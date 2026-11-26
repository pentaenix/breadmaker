// Optional hosted foreground windows. No keystroke, content or item listeners.
// The server alone decides account, time bucket, consent and deduplication.
(()=>{
  let timer=null,request=null,lastSent=-Infinity;
  const allowed=()=>typeof usageConsentValue!=='undefined'&&usageConsentAvailable()&&usageConsentValue?.allowed===true&&usageConsentValue?.collection_enabled===true;
  const foreground=()=>document.visibilityState==='visible'&&document.hasFocus();
  function stop(){if(timer!==null)clearInterval(timer);timer=null;request?.abort();request=null}
  async function pulse(){
    if(!allowed()||!foreground()||request||performance.now()-lastSent<20000)return;
    const controller=new AbortController();request=controller;lastSent=performance.now();
    const timeout=setTimeout(()=>controller.abort(),5000);
    try{const result=await api('/api/account/activity-pulse',{method:'POST',body:'{}',signal:controller.signal});if(result?.accepted!==true)stop()}
    catch{/* Activity outages never interrupt editing. */}
    finally{clearTimeout(timeout);if(request===controller)request=null}
  }
  function sync(){
    if(!allowed()||!foreground()){stop();return}
    if(timer===null)timer=setInterval(pulse,45000);
    pulse();
  }
  document.addEventListener('visibilitychange',sync);
  window.addEventListener('focus',sync);
  window.addEventListener('blur',stop);
  window.addEventListener('pagehide',stop);
  window.addEventListener('breadmaker-usage-choice',sync);
})();
