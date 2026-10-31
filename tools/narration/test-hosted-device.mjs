import assert from 'node:assert/strict';
import {HostedNarrationDevice} from './hosted-device.mjs';
const project='a'.repeat(32),account='usr_'+'b'.repeat(32),origin='https://synthetic.test';
const hash=async value=>Buffer.from(await crypto.subtle.digest('SHA-256',value)).toString('hex');
const canonical=value=>Array.isArray(value)?'['+value.map(canonical).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}':JSON.stringify(value);
const root='models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/',payload=new Uint8Array([1,3,5,7]);
const assets=[];for(const path of [root+'onnx/model.onnx',root+'voices/af_heart.bin'])assets.push({path,id:(await hash(new TextEncoder().encode(path))).slice(0,24),size:payload.length,sha256:await hash(payload),chunks:[{size:payload.length,sha256:await hash(payload)}]});
const manifest={v:1,assets},digest=await hash(new TextEncoder().encode(canonical(manifest)));
let permitted=true,consent=false,prompts=0,bodies=0,denied=false,invalidated=0;
const fetcher=async input=>{
  if(denied)return new Response('',{status:403});
  const url=new URL(input),headers={'X-Breadmaker-Narration-Actor':account,'X-Breadmaker-Narration-Project':project};
  if(url.pathname==='/api/narration-models')return new Response(JSON.stringify({...manifest,digest}),{headers});
  assert.match(url.pathname,/^\/api\/narration-models\/[a-f0-9]{24}$/);bodies++;return new Response(payload,{headers});
};
const device=new HostedNarrationDevice({project,account,manifest:digest,origin,fetcher,
  check(){if(!permitted)throw Error('NARRATION_ACCOUNT_CONTEXT_CHANGED')},onContextLost(){invalidated++},
  consentGPU(size){assert.equal(size,payload.length);prompts++;return consent}});
assert.equal(bodies,0,'constructing a device must download no model');
assert.equal(await device.chooseGPU(),false);assert.equal(prompts,1);assert.equal(bodies,0);
assert.equal(await device.chooseGPU(),false);assert.equal(prompts,1,'do not repeatedly prompt after decline');
consent=true;device.gpuChoice=null;assert.equal(await device.chooseGPU(),true);assert.equal(bodies,0,'model download belongs to explicit inference loader, not permission dialog');
const voice={id:'af_heart',size:payload.length,sha256:assets[1].sha256};
assert.equal((await device.voices({voices:[voice]})).voices[0].installed,false,'server model availability does not mean installed on this device');
await assert.rejects(()=>device.downloadVoice({...voice,sha256:'0'.repeat(64)}),/PIN_MISMATCH/);assert.equal(bodies,0);
assert.equal((await device.downloadVoice(voice)).installed,true);assert.equal(bodies,1);
permitted=false;await assert.rejects(()=>device.downloadVoice(voice),/CONTEXT_CHANGED/);assert.equal(bodies,1);
permitted=true;denied=true;await assert.rejects(()=>device.authorize(),/AUTHORIZATION_FAILED/);assert.equal(invalidated,1);
console.log('PASS: public device voice installation, immutable pins, explicit optional GPU consent and revocation; no startup model download.');
