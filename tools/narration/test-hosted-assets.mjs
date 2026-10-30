import assert from 'node:assert/strict';
import {HostedPublicAssets} from './hosted-assets.mjs';
const hash=async bytes=>Buffer.from(await crypto.subtle.digest('SHA-256',bytes)).toString('hex');
const canonical=value=>Array.isArray(value)?'['+value.map(canonical).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}':JSON.stringify(value);
const locks=new Map();
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{locks:{request(name,work){const next=(locks.get(name)||Promise.resolve()).then(work);locks.set(name,next.catch(()=>{}));return next}}}});
class Cache {
  constructor(){this.items=new Map()}
  key(input){return typeof input==='string'?input:input.url}
  async match(key){return this.items.get(this.key(key))?.clone()}
  async put(key,value){this.items.set(this.key(key),value.clone())}
  async delete(key){return this.items.delete(this.key(key))}
  async keys(){return [...this.items.keys()].map(key=>new Request(key))}
}
const path='models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/onnx/model_quantized.onnx';
const payload=new Uint8Array([1,2,3,4,5]),digest=await hash(payload);
const asset={id:(await hash(new TextEncoder().encode(path))).slice(0,24),path,size:payload.length,sha256:digest,chunks:[{size:payload.length,sha256:digest}]};
const manifest={v:1,assets:[asset]},manifestDigest=await hash(new TextEncoder().encode(canonical(manifest)));
const cache=new Cache(),calls=[];let fail=false,corrupt=false,huge=false;
const fetcher=async(input,options)=>{
  const url=new URL(input);calls.push([url,options]);
  if(url.pathname==='/api/narration-models')return new Response(JSON.stringify({digest:manifestDigest,...manifest}),{headers:{'Content-Type':'application/json'}});
  if(url.pathname.startsWith('/api/narration-models/'))return fail?new Response('',{status:403}):new Response(huge?new Uint8Array(1024):corrupt?new Uint8Array(5):payload);
  return new Response('delegated');
};
const open=()=>HostedPublicAssets.open({project:'a'.repeat(32),origin:'https://example.test',fetcher,expectedDigest:manifestDigest,cacheStorage:{open:async()=>cache}});
const modelURL='https://example.test/static/vendor/narration/'+path;
const transport=await open();assert.equal(calls.length,1);assert.equal(await transport.installed(path),false);
assert.deepEqual(new Uint8Array(await (await transport.fetch(modelURL)).arrayBuffer()),payload);
assert.equal(calls.length,2);assert.equal(await transport.installed(path),true);
assert.deepEqual(new Uint8Array(await (await transport.fetch(modelURL)).arrayBuffer()),payload);assert.equal(calls.length,2,'warm public binary must not redownload');
const key=transport.key(asset.chunks[0]);await cache.put(key,new Response(new Uint8Array(5)));
assert.deepEqual(new Uint8Array(await (await transport.fetch(modelURL)).arrayBuffer()),payload);assert.equal(calls.length,3,'corrupt public cache rechecked');
await cache.delete(key);corrupt=true;
await assert.rejects(async()=>await (await transport.fetch(modelURL)).arrayBuffer(),/INTEGRITY_FAILED/);
assert.equal(await cache.match(key),undefined);corrupt=false;huge=true;
await assert.rejects(async()=>await (await transport.fetch(modelURL)).arrayBuffer(),/DOWNLOAD_TOO_LARGE/);huge=false;
fail=true;await assert.rejects(async()=>await (await transport.fetch(modelURL)).arrayBuffer(),/DOWNLOAD_FAILED/);fail=false;
const before=calls.length;
assert.throws(()=>transport.fetch('https://example.test/static/vendor/narration/models/unpinned.onnx'),/NOT_PINNED/);
assert.throws(()=>transport.fetch(modelURL,{method:'POST'}),/METHOD_INVALID/);assert.equal(calls.length,before);
assert.equal(await (await transport.fetch('https://example.test/api/book')).text(),'delegated');
await cache.delete(key);const start=calls.length;
await Promise.all([transport.part(asset,0),transport.part(asset,0),transport.part(asset,0)]);
assert.equal(calls.length,start+1,'equivalent in-flight public chunks coalesce');
const privateStrings=['source_text','usr_','cookie','Bearer','project_id'];
for(const [name,value] of cache.items){const text=await value.clone().text();for(const secret of privateStrings)assert.equal((name+text).includes(secret),false,'cache contains public binary/schema only')}
for(const [url,options] of calls.filter(([url])=>url.pathname.startsWith('/api/narration-models/'))){assert.equal(url.origin,'https://example.test');assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store')}
await assert.rejects(()=>HostedPublicAssets.open({project:'../foreign',origin:'https://example.test',fetcher}),/PROJECT_REQUIRED/);
await assert.rejects(()=>HostedPublicAssets.open({project:'a'.repeat(32),origin:'https://example.test',fetcher,expectedDigest:'0'.repeat(64)}),/INTEGRITY_FAILED/);
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{}});
const fallback=await open();assert.equal(fallback.cache,null,'no unsafe persistent cache without cross-context locks');
assert.deepEqual(new Uint8Array(await (await fallback.fetch(modelURL)).arrayBuffer()),payload);
console.log('Hosted public-asset integrity, bounded reads, cache, coalescing and authorization failures passed');
