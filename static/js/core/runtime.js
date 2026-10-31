const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value='') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const uid = () => globalThis.crypto?.randomUUID?.() || `id_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
// Local SQLite IDs are numbers; hosted Firestore IDs are opaque strings.
// Normalize DOM/UI-state round trips without coercing hosted identities to NaN.
// This is representation only: server membership checks remain authoritative.
function entityId(value){
  if(typeof value==='number')return value;
  if(value===null||value==='')return 0;
  if(value===undefined)return NaN;
  const text=String(value);
  return /^\d+$/.test(text)&&String(Number(text))===text&&Number.isSafeInteger(Number(text))?Number(text):text;
}
function isEntityId(value){return typeof value==='number'?Number.isSafeInteger(value)&&value>0:typeof value==='string'&&/^[A-Za-z0-9_-]+$/.test(value)&&!['NaN','undefined','null'].includes(value)}
function compareEntityIds(a,b){const left=entityId(a),right=entityId(b);return typeof left==='number'&&typeof right==='number'?left-right:String(left).localeCompare(String(right))}
const CLIENT_ID = (() => {
  try {
    let id = sessionStorage.getItem('breadmaker-client-id');
    if (!id) { id = uid(); sessionStorage.setItem('breadmaker-client-id', id); }
    return id;
  } catch { return uid(); }
})();

const LINK_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="link-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M4.715 6.542 3.343 7.914a3 3 0 1 0 4.243 4.243l1.828-1.829A3 3 0 0 0 8.586 5.5L8 6.086a1.002 1.002 0 0 0-.154.199 2 2 0 0 1 .861 3.337L6.88 11.45a2 2 0 1 1-2.83-2.83l.793-.792a4.018 4.018 0 0 1-.128-1.287z"/><path d="M6.586 4.672A3 3 0 0 0 7.414 9.5l.775-.776a2 2 0 0 1-.896-3.346L9.12 3.55a2 2 0 1 1 2.83 2.83l-.793.792c.112.42.155.855.128 1.287l1.372-1.372a3 3 0 1 0-4.243-4.243L6.586 4.672z"/></svg>`;


// Editor's Eye is intentionally a presentation-only manuscript lens. Presets are
// curated to be visibly different from one another while remaining comfortable for
// book-length editing. They use local/system font stacks only: no font payloads are
// bundled, and no value is ever written to project data.
const EDITORS_EYE_PRESETS = [
  {font:"Georgia, 'Times New Roman', serif",size:15.1,lineHeight:1.8,width:808,pad:76,gap:.62,tracking:.006,light:{bg:'#dceeff',text:'#17212b'},dusk:{bg:'#c8cfce',text:'#26302f'},dark:{bg:'#102a43',text:'#f1f7fa'}},
  {font:"'Avenir Next', Avenir, 'Segoe UI', sans-serif",size:14.6,lineHeight:1.76,width:910,pad:62,gap:.52,tracking:.012,light:{bg:'#fff0b8',text:'#2b2513'},dusk:{bg:'#d2cbb5',text:'#312d21'},dark:{bg:'#352b10',text:'#fff5d6'}},
  {font:"'American Typewriter', 'Courier New', Courier, monospace",size:15.7,lineHeight:1.72,width:785,pad:78,gap:.58,tracking:.008,light:{bg:'#ddf3d8',text:'#182719'},dusk:{bg:'#c5cec1',text:'#283128'},dark:{bg:'#15351f',text:'#eef8ef'}},
  {font:"Palatino, 'Palatino Linotype', 'Book Antiqua', serif",size:16.2,lineHeight:1.7,width:830,pad:82,gap:.55,tracking:.002,light:{bg:'#e8dff8',text:'#241d30'},dusk:{bg:'#cbc5cf',text:'#302a33'},dark:{bg:'#2d1f3d',text:'#f4eefa'}},
  {font:"Verdana, Geneva, sans-serif",size:14.35,lineHeight:1.84,width:895,pad:64,gap:.66,tracking:-.008,light:{bg:'#ffe0d3',text:'#2b1f1a'},dusk:{bg:'#d0c2ba',text:'#342a25'},dark:{bg:'#3a201b',text:'#fff0e9'}},
  {font:"'Trebuchet MS', 'Gill Sans', Arial, sans-serif",size:15.45,lineHeight:1.78,width:852,pad:70,gap:.6,tracking:.01,light:{bg:'#d9f0ee',text:'#162624'},dusk:{bg:'#c2cdca',text:'#273130'},dark:{bg:'#123536',text:'#eff9f8'}},
];

let hostedStructuralActionToken=null;
class BreadmakerError extends Error {
  constructor(message, status=0, code='', params={}) { super(message); this.status=status; this.code=code; this.params=params||{}; }
}
function localizedApiErrorMessage(code,message,params={}){
  const key=code?`error.${code}`:'';
  if(key&&i18nCatalog?.strings?.[key]!=null)return i18nText(key,params);
  const clean=String(message||'').replace(/^([A-Z][A-Z0-9_]+_\d+):\s*/, '').trim();
  return i18nSource(clean||message||i18nSource('Request failed'));
}
function breadmakerErrorFromPayload(payload,status=0){
  let message=`Request failed (${status})`,code='',params={};const detail=payload?.detail??payload;
  if(detail&&typeof detail==='object'&&!Array.isArray(detail)){message=detail.message||detail.detail||message;code=String(detail.code||'');params=detail.params&&typeof detail.params==='object'?detail.params:{}}
  else if(detail!=null)message=String(detail);
  const codeMatch=String(message).match(/^([A-Z][A-Z0-9_]+_\d+):\s*(.*)$/s);if(!code&&codeMatch)code=codeMatch[1];
  return new BreadmakerError(localizedApiErrorMessage(code,message,params),status,code,params);
}
function localizedDiagnostic(value){return value==null||value===''?'':breadmakerErrorFromPayload({detail:value},0).message;}
async function api(url, options={}) {
  const headers = options.body instanceof FormData ? {} : {'Content-Type':'application/json'};
  const projectId=currentProjectIdentity(),userId=runtimeSupports('account_sessions')?'':currentUserIdentity();
  const contextHeaders={...(projectId?{'X-Breadmaker-Project':projectId}:{}),...(userId?{'X-Breadmaker-User':userId}:{})};
  // M8: attach one request-specific nonce to each hosted mutation. Proxies may
  // replay the exact HTTP request after an instance dies; Firestore performs the
  // actual deduplication. Explicit callers may supply idempotencyKey to reuse
  // the same nonce across a deliberate retry; never persist it in localStorage.
  const method=String(options.method||'GET').toUpperCase();
  const mutates=!['GET','HEAD','OPTIONS'].includes(method);
  const hosted=typeof runtimeSupports==='function'&&runtimeSupports('hosted_persistence');
  // Derivatives have their own publication CAS, not canonical structural Undo.
  const narrationRequest=/^\/api\/(?:narrations(?:\/|\?|$)|narration-(?:models|pronunciation|voices)(?:\/|\?|$))/.test(url);
  const nonceHeaders=hosted&&mutates?{'Idempotency-Key':String(options.idempotencyKey||uid()),...(hostedStructuralActionToken&&!url.startsWith('/api/undo/')&&!narrationRequest?{'X-Breadmaker-Action':hostedStructuralActionToken}:{})}:{};
  const {idempotencyKey,acceptNotModified,rawResponse,...fetchOptions}=options;
  const response = await fetch(url, {...fetchOptions, headers:{...headers, ...contextHeaders, ...nonceHeaders, 'X-Breadmaker-Client':CLIENT_ID, 'X-Breadmaker-Locale':i18nLocale(), ...(options.headers||{})}});
  if(response.status===304&&acceptNotModified&&method==='GET')return null;
  if (!response.ok) {
    // Missing membership intentionally looks like a missing project (404).
    const missingManuscript=method==='GET'&&response.status===404&&(url.startsWith('/api/book?')||url.startsWith('/api/chapters/'));
    if(hosted&&(response.status===401||response.status===403||missingManuscript)&&typeof clearManuscriptReadCache==='function')clearManuscriptReadCache();
    if(hosted&&(response.status===401||response.status===403)&&typeof clearHostedNarrationContext==='function')clearHostedNarrationContext();
    const raw=await response.text().catch(()=> '');let payload;
    try{payload=raw?JSON.parse(raw):{}}catch{payload={detail:raw||`Request failed (${response.status})`}}
    throw breadmakerErrorFromPayload(payload,response.status);
  }
  if(hosted&&method==='POST'&&url==='/api/auth/logout'&&typeof clearManuscriptReadCache==='function')clearManuscriptReadCache();
  if(hosted&&method==='POST'&&url==='/api/auth/logout'&&typeof clearHostedNarrationContext==='function')clearHostedNarrationContext(true);
  if(rawResponse)return response; // caller owns bounded private JSON/binary parsing
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response;
}
async function apiList(url,key,options={}){
  const result=await api(url,options),rows=Array.isArray(result)?result:result?.[key];
  if(!Array.isArray(rows))throw new BreadmakerError(`Invalid ${key} response`,0,'COLLECTION_RESPONSE_INVALID');
  return rows;
}

