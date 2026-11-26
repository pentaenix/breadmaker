"""Execute the actual foreground sender with deterministic browser primitives."""
from pathlib import Path
import subprocess


def test_sender_stays_off_without_consent_and_stops_when_hidden_or_revoked():
    source = Path(__file__).parents[2] / 'static/js/features/account/activity.js'
    program = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const document=new EventTarget(),window=new EventTarget();let focused=true,tick=0,timer=null,calls=[];
document.visibilityState='visible';document.hasFocus=()=>focused;
const performance={now:()=>tick};
const setInterval=fn=>{timer=fn;return 1};const clearInterval=()=>{timer=null};
let usageConsentValue={allowed:false,collection_enabled:true},hosted=true;
const usageConsentAvailable=()=>hosted;
const api=async(url,options)=>{calls.push({url,body:options.body});return {accepted:true}};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 window.dispatchEvent(new Event('breadmaker-usage-choice'));await flush();
 assert.equal(calls.length,0);assert.equal(timer,null);
 usageConsentValue.allowed=true;hosted=false;
 window.dispatchEvent(new Event('focus'));await flush();assert.equal(calls.length,0);
 hosted=true;usageConsentValue.collection_enabled=false;
 window.dispatchEvent(new Event('focus'));await flush();assert.equal(calls.length,0);
 usageConsentValue.collection_enabled=true;
 window.dispatchEvent(new Event('breadmaker-usage-choice'));await flush();
 assert.equal(calls.length,1);assert.equal(typeof timer,'function');
 window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));
 await flush();assert.equal(calls.length,1);
 tick=45000;timer();await flush();assert.equal(calls.length,2);
 document.visibilityState='hidden';document.dispatchEvent(new Event('visibilitychange'));
 assert.equal(timer,null);tick=90000;window.dispatchEvent(new Event('focus'));
 await flush();assert.equal(calls.length,2);
 document.visibilityState='visible';usageConsentValue.allowed=false;
 document.dispatchEvent(new Event('visibilitychange'));await flush();
 assert.equal(calls.length,2);assert.equal(timer,null);
 usageConsentValue=null;window.dispatchEvent(new Event('breadmaker-usage-choice'));
 for(const call of calls){assert.equal(call.url,'/api/account/activity-pulse');assert.equal(call.body,'{}')}
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', program, str(source)], capture_output=True, text=True, timeout=2)
    assert result.returncode == 0, result.stderr
