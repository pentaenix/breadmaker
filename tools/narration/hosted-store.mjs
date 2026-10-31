// Private derivative transport. NOTHING in this module writes localStorage,
// sessionStorage, IndexedDB, CacheStorage, the filesystem, or a service worker.
const PART=2*1024**2,MAX_AUDIO=32*1024**2,MAX_RECORDS=20002;
const opaque=/^[A-Za-z0-9_-]{1,160}$/,accountPattern=/^usr_[a-f0-9]{32}$/;
const validId=value=>typeof value==='string'&&opaque.test(value)&&!['null','undefined','NaN'].includes(value);
const hash=async text=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))].map(v=>v.toString(16).padStart(2,'0')).join('');
const contextError=()=>Error('NARRATION_ACCOUNT_CONTEXT_CHANGED');

class MemoryLRU {
  constructor(limit,count){this.limit=limit;this.count=count;this.items=new Map();this.bytes=0}
  clear(){this.items.clear();this.bytes=0}
  get(key){const item=this.items.get(key);if(!item)return null;this.items.delete(key);this.items.set(key,item);return item.value}
  put(key,value,size){
    if(!Number.isSafeInteger(size)||size<0||size>this.limit)return;
    if(this.items.has(key)){this.bytes-=this.items.get(key).size;this.items.delete(key)}
    while(this.items.size&&(this.bytes+size>this.limit||this.items.size>=this.count)){
      const first=this.items.keys().next().value;this.bytes-=this.items.get(first).size;this.items.delete(first);
    }
    this.items.set(key,{value,size});this.bytes+=size;
  }
}

async function bounded(response,limit){
  const reader=response.body.getReader(),parts=[];let length=0;
  try{while(true){const {value,done}=await reader.read();if(done)break;length+=value.byteLength;if(length>limit)throw Error('NARRATION_RESPONSE_TOO_LARGE');parts.push(value)}}
  catch(error){await reader.cancel().catch(()=>{});throw error}
  finally{reader.releaseLock()}
  const bytes=new Uint8Array(length);let offset=0;for(const value of parts){bytes.set(value,offset);offset+=value.length}return bytes;
}

function summary(value){
  const fields='audio_bytes,block_id,chapter_id,document_id,duration,end,engine,generation,id,publication,source_generation,source_hash,source_length,start,target_type,text,voice';
  if(!value||Object.keys(value).sort().join(',')!==fields||!validId(value.id)||!validId(value.publication)||
    !validId(value.chapter_id)||!validId(value.document_id)||!['block','chapter_title'].includes(value.target_type)||
    (value.target_type==='block'?!validId(value.block_id):value.block_id!=='chapter-title:'+value.chapter_id)||
    !/^[a-f0-9]{64}$/.test(value.source_hash)||!Number.isSafeInteger(value.source_length)||value.source_length<1||value.source_length>200000||
    !Number.isSafeInteger(value.start)||!Number.isSafeInteger(value.end)||value.start<0||value.start>=value.end||value.end>value.source_length||
    !Number.isSafeInteger(value.generation)||value.generation<1||!Number.isSafeInteger(value.source_generation)||value.source_generation<1||
    typeof value.text!=='string'||[...value.text].length>160||typeof value.voice!=='string'||!/^[a-z]{2}_[a-z0-9_]{1,60}$/.test(value.voice)||
    typeof value.engine!=='string'||value.engine.length>160||!Number.isFinite(value.duration)||value.duration<=0||value.duration>700||
    !Number.isSafeInteger(value.audio_bytes)||value.audio_bytes<=44||value.audio_bytes>MAX_AUDIO)throw Error('NARRATION_INDEX_INVALID');
  return Object.freeze({...value,thin:true});
}

export class HostedNarrationStore {
  constructor({request,context,onInvalidated=()=>{}}){
    if(typeof request!=='function'||typeof context!=='function')throw Error('NARRATION_CONTEXT_REQUIRED');
    Object.assign(this,{request,context,onInvalidated,epoch:0,key:null,mutations:0});
    this.records=new Map();this.controllers=new Set();this.flights=new Map();this.deleteOps=new Map();this.saves=new WeakMap();
    this.details=new MemoryLRU(16*1024**2,128);this.audioCache=new MemoryLRU(MAX_AUDIO,16);this.hashes=new MemoryLRU(16*1024**2,4096);
  }
  clear(){
    this.epoch++;this.key=null;this.mutations++;for(const controller of this.controllers)controller.abort();this.controllers.clear();
    this.records.clear();this.flights.clear();this.deleteOps.clear();this.saves=new WeakMap();this.details.clear();this.audioCache.clear();this.hashes.clear();
    this.onInvalidated();
  }
  capture(pid){
    const value=this.context();
    if(!value||!accountPattern.test(value.account)||!/^[a-f0-9]{32}$/.test(pid)||value.project!==pid||!Number.isSafeInteger(value.sessionEpoch)||value.sessionEpoch<0){this.clear();throw contextError()}
    const key=value.account+'\n'+pid+'\n'+value.sessionEpoch;
    if(this.key!==key){if(this.key!==null)this.clear();this.key=key}
    return Object.freeze({key,epoch:this.epoch,account:value.account,project:pid});
  }
  use(pid,snapshot){if(!snapshot)return this.capture(pid);this.check(snapshot);if(snapshot.project!==pid)throw contextError();return snapshot}
  check(snapshot){
    const value=this.context();
    if(snapshot.epoch!==this.epoch||snapshot.key!==this.key)throw contextError();
    if(!value||value.account!==snapshot.account||value.project!==snapshot.project||snapshot.key!==value.account+'\n'+value.project+'\n'+value.sessionEpoch){this.clear();throw contextError()}
  }
  invalidate(snapshot){if(this.epoch===snapshot.epoch&&this.key===snapshot.key)this.clear()}
  async call(snapshot,url,options={},limit=4*1024**2,binary=false){
    this.check(snapshot);const controller=new AbortController();this.controllers.add(controller);
    const timeout=setTimeout(()=>controller.abort(),60000);
    try{
      const response=await this.request(url,{...options,rawResponse:true,signal:controller.signal,
        headers:{...(options.headers||{}),'X-Breadmaker-Project':snapshot.project,'X-Breadmaker-Narration-Account':snapshot.account}});
      this.check(snapshot);
      if(!response?.ok||response.headers.get('X-Breadmaker-Narration-Actor')!==snapshot.account||response.headers.get('X-Breadmaker-Narration-Project')!==snapshot.project){this.invalidate(snapshot);throw contextError()}
      const bytes=await bounded(response,limit);this.check(snapshot);
      return binary?bytes:JSON.parse(new TextDecoder().decode(bytes));
    }catch(error){
      if([401,403,404].includes(error?.status)||String(error?.message).includes('NARRATION_ACCOUNT_CONTEXT_CHANGED'))this.invalidate(snapshot);
      throw error;
    }finally{clearTimeout(timeout);this.controllers.delete(controller)}
  }
  async sources(pid,sources,{remember=false,snapshot:captured}={}){
    const snapshot=this.use(pid,captured),result=[];
    for(let start=0;start<sources.length;start+=32){
      const page=await Promise.all(sources.slice(start,start+32).map(async source=>{
        if(typeof source.source_text!=='string'||[...source.source_text].length>100000)throw Error('NARRATION_SOURCE_INVALID');
        const digest=await hash(source.source_text);this.check(snapshot);
        if(remember)this.hashes.put(source.source_text,digest,source.source_text.length*2+128);
        return {...source,source_hash:digest};
      }));result.push(...page);
      if(start+32<sources.length)await new Promise(resolve=>setTimeout(resolve,0));
      this.check(snapshot);
    }
    return result;
  }
  fresh(record,text){return record?.thin===true&&typeof text==='string'&&this.hashes.get(text)===record.source_hash}
  async list(pid,captured){
    const snapshot=this.use(pid,captured),flight=snapshot.key+':list';
    if(this.flights.has(flight))return this.flights.get(flight);
    const changed=this.mutations;
    const work=(async()=>{
      const records=new Map(),seen=new Set();let after='';
      do{
        if(seen.has(after)||seen.size>=160)throw Error('NARRATION_INDEX_INVALID');seen.add(after);
        const page=await this.call(snapshot,'/api/narrations'+(after?'?after='+encodeURIComponent(after):''),{},512*1024);
        if(!page||Object.keys(page).sort().join(',')!=='next,records'||!Array.isArray(page.records)||page.records.length>128||
          page.next!==null&&(typeof page.next!=='string'||!/^\d{3}:[1-9]\d{0,18}$/.test(page.next)))throw Error('NARRATION_INDEX_INVALID');
        for(const raw of page.records){const item=summary(raw);if(records.has(item.id))throw Error('NARRATION_INDEX_INVALID');records.set(item.id,item)}
        if(records.size>MAX_RECORDS)throw Error('NARRATION_INDEX_CAPACITY');after=page.next;
      }while(after!==null);
      this.check(snapshot);if(changed!==this.mutations)throw Error('NARRATION_INDEX_CHANGED');this.records=records;return [...records.values()];
    })().finally(()=>{if(this.flights.get(flight)===work)this.flights.delete(flight)});
    this.flights.set(flight,work);return work;
  }
  async manifest(snapshot,id){
    if(!validId(id))throw Error('NARRATION_RECORD_INVALID');
    const value=await this.call(snapshot,`/api/narrations/${id}/manifest`,{},2048);
    if(!value||Object.keys(value).sort().join(',')!=='audio_bytes,generation,part_bytes,parts,publication'||
      !Number.isSafeInteger(value.audio_bytes)||value.audio_bytes<=44||value.audio_bytes>MAX_AUDIO||value.part_bytes!==PART||
      value.parts!==Math.ceil(value.audio_bytes/PART)||!Number.isSafeInteger(value.generation)||value.generation<1||!validId(value.publication))throw Error('NARRATION_MANIFEST_INVALID');
    return value;
  }
  cacheKey(id,manifest){return id+':'+manifest.publication+':'+manifest.generation}
  async decode(snapshot,value){
    const item=summary(value?.index);
    if(value.id!==item.id||value.generation!==item.generation||value.duration!==item.duration||
      typeof value.source_text!=='string'||value.source_text.length!==item.source_length||typeof value.text!=='string'||
      value.source_text.slice(value.start,value.end)!==value.text||value.start!==item.start||value.end!==item.end||
      value.voice!==item.voice||value.engine!==item.engine||await hash(value.source_text)!==item.source_hash||
      !Array.isArray(value.timings)||value.timings.length>10000)throw Error('NARRATION_METADATA_INVALID');
    this.check(snapshot);
    for(const word of value.timings){if(!Number.isSafeInteger(word.start)||!Number.isSafeInteger(word.end)||word.start<0||word.start>=word.end||word.end>value.text.length||
      !Number.isFinite(word.time)||!Number.isFinite(word.duration)||word.time<0||word.duration<0||word.time+word.duration>value.duration+1)throw Error('NARRATION_METADATA_INVALID');Object.freeze(word)}
    const result=Object.freeze({...value,index:item,timings:Object.freeze(value.timings)});
    this.details.put(this.cacheKey(item.id,item),result,new TextEncoder().encode(JSON.stringify(value)).length);
    return result;
  }
  async metadata(pid,record,captured){
    const snapshot=this.use(pid,captured),id=typeof record==='string'?record:record.id,manifest=await this.manifest(snapshot,id);
    if(typeof record==='object'&&(record.generation!==manifest.generation||record.publication&&record.publication!==manifest.publication))throw Error('NARRATION_RECORD_CHANGED');
    const key=this.cacheKey(id,manifest),cached=this.details.get(key);
    if(cached)return cached; // live manifest/admission, NEVER a private-cache-only auth hit
    const value=await this.call(snapshot,`/api/narrations/${id}?expected_generation=${manifest.generation}`);
    if(value?.index?.publication!==manifest.publication)throw Error('NARRATION_RECORD_CHANGED');
    return this.decode(snapshot,value);
  }
  async audio(pid,id,captured){
    const snapshot=this.use(pid,captured),manifest=await this.manifest(snapshot,id),key=id+':'+manifest.publication;
    const cached=this.audioCache.get(key);if(cached){if(cached.size!==manifest.audio_bytes)throw Error('NARRATION_AUDIO_INVALID');return cached}
    const parts=[];
    for(let part=0;part<manifest.parts;part++){
      const size=Math.min(PART,manifest.audio_bytes-part*PART);
      const bytes=await this.call(snapshot,`/api/narrations/${id}/audio?part=${part}`,{},size,true);
      if(bytes.length!==size)throw Error('NARRATION_AUDIO_INVALID');parts.push(bytes);
    }
    this.check(snapshot);const blob=new Blob(parts,{type:'audio/wav'});if(blob.size!==manifest.audio_bytes)throw Error('NARRATION_AUDIO_INVALID');
    // Recheck the current head after multi-part assembly too; a delete/revoke
    // between parts must not silently install a now-unavailable private buffer.
    const current=await this.manifest(snapshot,id);
    if(current.generation!==manifest.generation||current.publication!==manifest.publication)throw Error('NARRATION_RECORD_CHANGED');
    this.audioCache.put(key,blob,blob.size);return blob;
  }
  async save(pid,record,blob,captured){
    const snapshot=this.use(pid,captured);
    if(!(blob instanceof Blob)||blob.size<=44||blob.size>MAX_AUDIO)throw Error('NARRATION_AUDIO_INVALID');
    let operation=this.saves.get(blob);if(!operation){operation=crypto.randomUUID();this.saves.set(blob,operation)}
    const fields=['block_id','chapter_id','target_type','voice','language','source_text','text','start','end','timings','backend','generation_pace','punctuation_pauses','replaces'];
    const metadata=Object.fromEntries(fields.filter(key=>record[key]!==undefined).map(key=>[key,record[key]]));
    const made=await this.call(snapshot,'/api/narrations',{method:'POST',idempotencyKey:operation,body:JSON.stringify({metadata,audio_bytes:blob.size,operation})});
    if(!made||!validId(made.id)||!validId(made.publication)||made.part_bytes!==PART||!['pending','published'].includes(made.state))throw Error('NARRATION_UPLOAD_INVALID');
    if(made.state==='pending')for(let part=0;part<Math.ceil(blob.size/PART);part++)await this.call(snapshot,`/api/narrations/uploads/${made.publication}/parts/${part}`,
      {method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:blob.slice(part*PART,(part+1)*PART),idempotencyKey:operation+'_'+part});
    const result=await this.call(snapshot,`/api/narrations/uploads/${made.publication}/finish`,{method:'POST',idempotencyKey:operation+'_finish'});
    if(result?.id!==made.id||result?.index?.publication!==made.publication)throw Error('NARRATION_UPLOAD_INVALID');
    const value=await this.decode(snapshot,result);this.check(snapshot);this.mutations++;
    this.records.delete(record.replaces);this.records.set(value.id,value.index);this.audioCache.put(value.id+':'+value.index.publication,blob,blob.size);
    return value.index; // never accumulate all full-book source/timing bodies in UI records
  }
  async remove(pid,id,captured){
    const snapshot=this.use(pid,captured),item=this.records.get(id);
    if(!item)throw Error('NARRATION_RECORD_CHANGED');
    let operation=this.deleteOps.get(id);if(!operation){operation=crypto.randomUUID();if(this.deleteOps.size>=2048)this.deleteOps.delete(this.deleteOps.keys().next().value);this.deleteOps.set(id,operation)}
    const result=await this.call(snapshot,`/api/narrations/${id}?expected_generation=${item.generation}`,{method:'DELETE',idempotencyKey:operation});
    if(typeof result?.undo_token!=='string'||!/^[a-f0-9]{64}$/.test(result.undo_token))throw Error('NARRATION_UNDO_INVALID');
    this.check(snapshot);this.mutations++;this.records.delete(id);this.deleteOps.delete(id);return result;
  }
  async restore(pid,id,token,captured){
    const snapshot=this.use(pid,captured);
    if(!validId(id)||typeof token!=='string'||!/^[a-f0-9]{64}$/.test(token))throw Error('NARRATION_UNDO_INVALID');
    const result=await this.call(snapshot,`/api/narrations/${id}/restore`,{method:'POST',body:JSON.stringify({undo_token:token})});
    if(result?.id!==id)throw Error('NARRATION_UNDO_INVALID');
    const value=await this.decode(snapshot,result);this.check(snapshot);this.mutations++;this.records.set(id,value.index);return value.index;
  }
  memory(){return {indexRecords:this.records.size,detailsBytes:this.details.bytes,audioBytes:this.audioCache.bytes,hashBytes:this.hashes.bytes}}
}
