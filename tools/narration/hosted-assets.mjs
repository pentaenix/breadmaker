// PUBLIC, pinned model binaries only. Never cache manuscripts, audio, identities,
// provider tokens, or arbitrary fetch responses. Loaded only by explicit narration.
const CHUNK=2*1024*1024,POOL=2*1024**3,CACHE='breadmaker-narration-public-v1';
const PREFIX='/_breadmaker_narration_public/v1/';
const hex=bytes=>[...new Uint8Array(bytes)].map(value=>value.toString(16).padStart(2,'0')).join('');
const sha=async bytes=>hex(await crypto.subtle.digest('SHA-256',bytes));
const canonical=value=>Array.isArray(value)?'['+value.map(canonical).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}':JSON.stringify(value);
const assetPath=/^(?:models\/onnx-community\/Kokoro-82M-v1\.0-ONNX-timestamped\/(?:config\.json|tokenizer\.json|tokenizer_config\.json|README\.md|onnx\/model(?:_quantized)?\.onnx|voices\/[a-z][a-z]_[a-z0-9_]+\.bin)|transformers\/ort-wasm[a-zA-Z0-9_.-]*\.wasm|dictionaries\/en-us\.txt)$/;
const digestPattern=/^[a-f0-9]{64}$/;
const nativeFetch=globalThis.fetch.bind(globalThis);
async function bounded(response,limit){
  const reader=response.body.getReader(),pieces=[];let size=0;
  try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>limit)throw Error('NARRATION_DOWNLOAD_TOO_LARGE');pieces.push(value)}}
  catch(error){await reader.cancel().catch(()=>{});throw error}
  finally{reader.releaseLock()}
  const bytes=new Uint8Array(size);let offset=0;for(const piece of pieces){bytes.set(piece,offset);offset+=piece.byteLength}return bytes;
}
const boundedJSON=async(response,limit)=>JSON.parse(new TextDecoder().decode(await bounded(response,limit)));

export class HostedPublicAssets {
  static async open({project,account,client='',origin=globalThis.location.origin,fetcher=nativeFetch,expectedDigest=null,cacheStorage=globalThis.caches,onContextLost=()=>{}}){
    if(!/^[a-f0-9]{32}$/.test(project))throw Error('NARRATION_PROJECT_REQUIRED');
    if(typeof account!=='string'||!/^usr_[a-f0-9]{32}$/.test(account))throw Error('NARRATION_ACCOUNT_REQUIRED');
    if(typeof client!=='string'||client.length>128||client&&!/^[a-zA-Z0-9_-]+$/.test(client))throw Error('NARRATION_CLIENT_INVALID');
    const headers={'X-Breadmaker-Project':project,'X-Breadmaker-Narration-Account':account,...(client?{'X-Breadmaker-Client':client}:{})};
    const response=await fetcher(new URL('/api/narration-models',origin),{credentials:'same-origin',cache:'no-store',headers});
    if(!response.ok){if([401,403,404].includes(response.status))onContextLost();throw Error('NARRATION_MODELS_AUTHORIZATION_FAILED')}
    if(response.headers.get('X-Breadmaker-Narration-Actor')!==account||response.headers.get('X-Breadmaker-Narration-Project')!==project){onContextLost();throw Error('NARRATION_ACCOUNT_CONTEXT_CHANGED')}
    const bytes=await bounded(response,256*1024);
    const value=JSON.parse(new TextDecoder().decode(bytes)),{digest,...manifest}=value;
    if(!digestPattern.test(digest)||expectedDigest&&digest!==expectedDigest||await sha(new TextEncoder().encode(canonical(manifest)))!==digest)throw Error('NARRATION_MANIFEST_INTEGRITY_FAILED');
    const assets=await HostedPublicAssets.validate(manifest);
    // Cross-tab/worker writes require a real browser Web Lock. Without it,
    // verified streaming still works, but never risks an unbounded shared cache.
    let cache=null;try{if(globalThis.navigator?.locks)cache=await cacheStorage?.open(CACHE)}catch{}
    const result=new HostedPublicAssets({project,account,origin,fetcher,cache,digest,manifest,assets,headers,onContextLost});
    await result.retainPublicManifests();return result;
  }
  static async validate(manifest){
    if(Object.keys(manifest).sort().join(',')!=='assets,v'||manifest.v!==1||!Array.isArray(manifest.assets)||!manifest.assets.length||manifest.assets.length>80)throw Error('NARRATION_MANIFEST_INVALID');
    let size=0;const assets=new Map(),ids=new Set();
    for(const asset of manifest.assets){
      if(Object.keys(asset).sort().join(',')!=='chunks,id,path,sha256,size'||!assetPath.test(asset.path)||assets.has(asset.path)||ids.has(asset.id)||asset.id!==(await sha(new TextEncoder().encode(asset.path))).slice(0,24)||!Number.isSafeInteger(asset.size)||asset.size<1||asset.size>400*1024**2||!digestPattern.test(asset.sha256)||!Array.isArray(asset.chunks)||asset.chunks.length!==Math.ceil(asset.size/CHUNK))throw Error('NARRATION_MANIFEST_INVALID');
      for(let i=0;i<asset.chunks.length;i++){const part=asset.chunks[i];if(Object.keys(part).sort().join(',')!=='sha256,size'||!Number.isSafeInteger(part.size)||part.size!==Math.min(CHUNK,asset.size-i*CHUNK)||!digestPattern.test(part.sha256))throw Error('NARRATION_MANIFEST_INVALID')}
      size+=asset.size;assets.set(asset.path,asset);ids.add(asset.id);
    }
    if(size>POOL)throw Error('NARRATION_PUBLIC_CACHE_CAPACITY');return assets;
  }
  constructor(values){Object.assign(this,values);this.pending=new Map();this.writeQueue=Promise.resolve()}
  async reauthorize(){
    const response=await this.fetcher(new URL('/api/narration-models',this.origin),{credentials:'same-origin',cache:'no-store',headers:this.headers});
    if(!response.ok){if([401,403,404].includes(response.status))this.onContextLost();throw Error('NARRATION_MODELS_AUTHORIZATION_FAILED')}
    if(response.headers.get('X-Breadmaker-Narration-Actor')!==this.account||response.headers.get('X-Breadmaker-Narration-Project')!==this.project){this.onContextLost();throw Error('NARRATION_ACCOUNT_CONTEXT_CHANGED')}
    const value=await boundedJSON(response,256*1024);
    if(value.digest!==this.digest)throw Error('NARRATION_MANIFEST_CHANGED');
  }
  key(part){return new URL(PREFIX+'chunks/'+part.sha256,this.origin).href}
  locked(fn){
    if(globalThis.navigator?.locks)return globalThis.navigator.locks.request('breadmaker-narration-public-cache-write',fn);
    const work=this.writeQueue.then(fn);this.writeQueue=work.catch(()=>{});return work;
  }
  async retainPublicManifests(){
    if(!this.cache)return;
    try{await this.locked(async()=>{
      // At most two public manifests. Response metadata never contains an actor,
      // project locator, cookie or server-supplied authentication header.
      const key=new URL(PREFIX+'manifests/'+this.digest,this.origin).href;
      await this.cache.delete(key);await this.cache.put(key,new Response(JSON.stringify(this.manifest)));
      const keys=await this.cache.keys(),manifests=keys.filter(item=>new URL(item.url).pathname.startsWith(PREFIX+'manifests/'));
      for(const item of manifests.slice(0,-2))await this.cache.delete(item);
      const keep=new Set();
      for(const item of manifests.slice(-2)){
        const response=await this.cache.match(item);if(!response)continue;
        const value=await boundedJSON(response,256*1024);await HostedPublicAssets.validate(value);
        for(const asset of value.assets)for(const part of asset.chunks)keep.add(this.key(part));
      }
      for(const item of keys)if(new URL(item.url).pathname.startsWith(PREFIX+'chunks/')&&!keep.has(item.url))await this.cache.delete(item);
      let total=0;
      for(const item of await this.cache.keys())if(new URL(item.url).pathname.startsWith(PREFIX+'chunks/')){const value=await this.cache.match(item);const size=Number(value.headers.get('Content-Length'));if(!Number.isSafeInteger(size)||size<1||size>CHUNK)throw Error('NARRATION_PUBLIC_CACHE_INVALID');total+=size}
      await this.cache.put(new URL(PREFIX+'usage',this.origin).href,new Response(JSON.stringify({bytes:total})));
    })}catch{this.cache=null} // private/no-persistence fallback is still bounded
  }
  async installed(path){
    const asset=this.assets.get(path);if(!asset||!this.cache)return false;
    for(const part of asset.chunks)if(!await this.cache.match(this.key(part)))return false;
    return true; // bytes are still hash-verified on every actual read below
  }
  async part(asset,ordinal){
    const part=asset.chunks[ordinal],key=this.key(part);
    if(this.pending.has(key))return this.pending.get(key);
    const work=async()=>{
      if(this.cache){
        const cached=await this.cache.match(key);
        if(cached){try{const bytes=await bounded(cached,part.size);if(bytes.length===part.size&&await sha(bytes)===part.sha256)return bytes}catch{}await this.cache.delete(key)}
      }
      const url=new URL('/api/narration-models/'+asset.id+'?part='+ordinal,this.origin);
      const response=await this.fetcher(url,{credentials:'same-origin',cache:'no-store',headers:this.headers});
      if(!response.ok){if([401,403,404].includes(response.status))this.onContextLost();throw Error(response.status===429?'NARRATION_TRANSFER_LIMIT':'NARRATION_MODEL_DOWNLOAD_FAILED')}
      if(response.headers.get('X-Breadmaker-Narration-Actor')!==this.account||response.headers.get('X-Breadmaker-Narration-Project')!==this.project){this.onContextLost();throw Error('NARRATION_ACCOUNT_CONTEXT_CHANGED')}
      const bytes=await bounded(response,part.size);
      if(bytes.length!==part.size||await sha(bytes)!==part.sha256)throw Error('NARRATION_MODEL_INTEGRITY_FAILED');
      if(this.cache){
        try{await this.locked(async()=>{
          // Two retained manifests can share chunks; enforce the actual byte
          // pool before adding anything. Never evict the current pinned model.
          if(await this.cache.match(key))return;
          const usageKey=new URL(PREFIX+'usage',this.origin).href,usage=await boundedJSON(await this.cache.match(usageKey),512);
          if(!Number.isSafeInteger(usage.bytes)||usage.bytes<0||usage.bytes+bytes.length>POOL)return;
          // Reserve first: a crash can overcount, not exceed the pool. Opening
          // the next explicit session recomputes this PUBLIC-only byte census.
          await this.cache.put(usageKey,new Response(JSON.stringify({bytes:usage.bytes+bytes.length})));
          await this.cache.put(key,new Response(bytes,{headers:{'Content-Length':String(bytes.length),'Content-Type':'application/octet-stream'}}));
        })}catch{} // storage quota refusal never changes binary/inference quality
      }
      return bytes;
    };
    const pending=(globalThis.navigator?.locks?globalThis.navigator.locks.request('breadmaker-narration-public-'+part.sha256,work):work()).finally(()=>this.pending.delete(key));
    this.pending.set(key,pending);return pending;
  }
  fetch(input,options={}){
    const url=new URL(typeof input==='string'||input instanceof URL?input:input.url,this.origin);
    const base='/static/vendor/narration/';
    if(url.origin!==this.origin||!url.pathname.startsWith(base))return this.fetcher(input,options);
    const path=decodeURIComponent(url.pathname.slice(base.length)),asset=this.assets.get(path);
    if(!asset){if(/\.(?:wasm|onnx|bin|txt)$/.test(path)||path.startsWith('models/'))throw Error('NARRATION_MODEL_NOT_PINNED');return this.fetcher(input,options)}
    if((options.method||input?.method||'GET')!=='GET')throw Error('NARRATION_MODEL_METHOD_INVALID');
    let ordinal=0,cancelled=false;const owner=this;
    // Every ordered <=2MiB chunk is checked against the trusted manifest before
    // it enters the consumer. No second 310MiB hash/copy of the complete model.
    return Promise.resolve(new Response(new ReadableStream({
      async pull(controller){try{if(cancelled)return;if(ordinal===asset.chunks.length){controller.close();return}const data=await owner.part(asset,ordinal++);if(!cancelled)controller.enqueue(data)}catch(error){controller.error(error)}},
      cancel(){cancelled=true}
    }),{headers:{'Content-Type':'application/octet-stream','Content-Length':String(asset.size)}}));
  }
  installFetchInterceptor(){
    if(this.interceptorInstalled)return;this.interceptorInstalled=true;
    globalThis.fetch=this.fetch.bind(this);
  }
}
