// Shared per-origin outbox. A synchronous pre-journal covers the interval before
// IndexedDB commits; one IDB transaction allocates a persistent device sequence
// and inserts its operation. A new tab can recover either representation.
(() => {
  const prefix='breadmaker-targets-journal:';
  let opening,chain=Promise.resolve();
  const uuid=()=>uid();
  function database(){
    if(!opening)opening=new Promise((resolve,reject)=>{
      const request=indexedDB.open('breadmaker-targets-journal',1);
      request.onupgradeneeded=()=>{const db=request.result;db.createObjectStore('streams',{keyPath:'context'});db.createObjectStore('operations',{keyPath:'id'}).createIndex('context','context')};
      request.onsuccess=()=>{const db=request.result;db.onversionchange=()=>{db.close();opening=null};resolve(db)};
      request.onerror=()=>{opening=null;reject(request.error)};
    });return opening;
  }
  const serial=work=>{const result=chain.catch(()=>{}).then(work);chain=result;return result};
  async function stage(context,operation){
    const db=await database();
    return new Promise((resolve,reject)=>{
      const tx=db.transaction(['streams','operations'],'readwrite'),ops=tx.objectStore('operations'),streams=tx.objectStore('streams');let result;
      const existing=ops.get(operation.id);
      existing.onsuccess=()=>{
        if(existing.result){if(existing.result.context!==context){tx.abort();return}result=existing.result;return}
        const current=streams.get(context);
        current.onsuccess=()=>{const stream=current.result||{context,id:uuid(),next:1};result={...operation,context,stream:stream.id,sequence:stream.next};stream.next++;ops.add(result);streams.put(stream)};
      };
      tx.oncomplete=()=>{try{localStorage.removeItem(prefix+context+':'+operation.id)}catch{}resolve(result)};
      tx.onabort=tx.onerror=()=>reject(tx.error||new Error('TARGETS_JOURNAL_ABORTED'));
    });
  }
  function enqueue(context,operation){
    // Do not coalesce or mutate an ID: another tab may recover it immediately.
    // If synchronous storage is unavailable, IDB remains the durable fallback.
    let prejournal=false;
    try{localStorage.setItem(prefix+context+':'+operation.id,JSON.stringify(operation));prejournal=true}catch{}
    return serial(()=>stage(context,{...operation,prejournal}));
  }
  async function read(context){
    const recovered=[];
    try{for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(!key?.startsWith(prefix+context+':'))continue;try{const op=JSON.parse(localStorage.getItem(key));if(op&&typeof op.id==='string'&&Number.isFinite(op.at)&&key===prefix+context+':'+op.id)recovered.push(op)}catch{}}}catch{/* IDB can remain usable when localStorage is restricted. */}
    for(const operation of recovered)await serial(()=>stage(context,{...operation,prejournal:true}));
    await chain;
    const db=await database();
    return new Promise((resolve,reject)=>{const tx=db.transaction('operations'),request=tx.objectStore('operations').index('context').getAll(context);request.onsuccess=()=>resolve(request.result.sort((a,b)=>a.stream.localeCompare(b.stream)||a.sequence-b.sequence));request.onerror=()=>reject(request.error)});
  }
  async function acknowledge(context,receipts){
    const db=await database();
    return new Promise((resolve,reject)=>{const tx=db.transaction('operations','readwrite'),store=tx.objectStore('operations'),cursor=store.index('context').openCursor(context);cursor.onsuccess=()=>{const row=cursor.result;if(!row)return;if((receipts?.[row.value.stream]||0)>=row.value.sequence){let removable=row.value.prejournal===false;try{const key=prefix+context+':'+row.value.id;localStorage.removeItem(key);removable=localStorage.getItem(key)===null}catch{}if(removable)row.delete()}row.continue()};tx.oncomplete=resolve;tx.onabort=tx.onerror=()=>reject(tx.error)});
  }
  window.BreadmakerTargetsJournal={enqueue,read,acknowledge,drain:()=>chain};
})();
