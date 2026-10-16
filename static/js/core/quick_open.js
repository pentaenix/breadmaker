// -------------------- quick open --------------------
let quickOpenItems=[];let quickOpenActiveIndex=0;
document.addEventListener('keydown',event=>{if(document.body.classList.contains('reviewer-mode-active'))return;const key=String(event.key||'').toLowerCase();if((event.metaKey||event.ctrlKey)&&!event.altKey&&!event.shiftKey&&key==='k'){event.preventDefault();event.stopPropagation();if($('#quickOpen').classList.contains('hidden'))openQuickOpen();else $('#quickOpenInput').focus()}},true);
$('#quickOpenBtn').onclick=()=>openQuickOpen();
$('#quickOpenInput').addEventListener('input',renderQuickOpenResults);
$('#quickOpenInput').addEventListener('keydown',e=>{
  if(e.key==='Escape'){e.preventDefault();closeQuickOpen();return}
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();moveQuickOpenSelection(e.key==='ArrowDown'?1:-1);return}
  if(e.key==='Enter'){e.preventDefault();const item=quickOpenItems[quickOpenActiveIndex];if(item){closeQuickOpen();item.action()}}
});
function closeQuickOpen(){$('#quickOpen').classList.add('hidden')}
function moveQuickOpenSelection(delta){if(!quickOpenItems.length)return;quickOpenActiveIndex=(quickOpenActiveIndex+delta+quickOpenItems.length)%quickOpenItems.length;syncQuickOpenSelection()}
function syncQuickOpenSelection(){for(const button of $$('[data-quick-index]',$('#quickOpenResults'))){const active=+button.dataset.quickIndex===quickOpenActiveIndex;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));if(active)button.scrollIntoView({block:'nearest'})}}
function invalidateQuickOpenDetailIndex(){state.quickOpenDetailIndex=null;state.quickOpenDetailIndexPromise=null;clearTimeout(state.quickOpenRenderTimer);state.quickOpenRenderTimer=null}
function quickOpenPlainText(value){return String(value||'').replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim()}
function scheduleQuickOpenRefresh(){if($('#quickOpen').classList.contains('hidden'))return;clearTimeout(state.quickOpenRenderTimer);state.quickOpenRenderTimer=setTimeout(()=>{state.quickOpenRenderTimer=null;renderQuickOpenResults().catch(console.warn)},35)}
async function quickOpenConcurrent(items,worker,limit=4){let cursor=0;const runners=Array.from({length:Math.min(limit,items.length)},async()=>{while(cursor<items.length){const item=items[cursor++];await worker(item)}});await Promise.all(runners)}
async function buildQuickOpenDetailIndex(){
  if(state.quickOpenDetailIndexPromise)return state.quickOpenDetailIndexPromise;
  if(state.quickOpenDetailIndex?.complete)return state.quickOpenDetailIndex;
  const index=state.quickOpenDetailIndex={cards:[],mapItems:[],milestones:[],canvasOwners:new Map(),mapItemOwners:new Map(),complete:false};
  state.quickOpenDetailIndexPromise=(async()=>{
    const tasks=await getTasks();index.milestones=tasks.flatMap(task=>milestoneQuickEntries(task).map(milestone=>({task,milestone})));scheduleQuickOpenRefresh();
    await Promise.all([
      quickOpenConcurrent(state.canvases,async canvas=>{try{const detail=state.currentCanvas?.id===canvas.id&&state.canvasNodes?.length?{nodes:state.canvasNodes}:await api(`/api/canvases/${canvas.id}`);for(const node of detail.nodes||[]){if(['routing','label','figure'].includes(node.node_type))continue;index.cards.push({canvas,node});index.canvasOwners.set(+node.id,+canvas.id)}scheduleQuickOpenRefresh()}catch(error){console.warn('Quick Open Canvas index skipped',canvas.id,error)}}),
      quickOpenConcurrent(state.maps,async map=>{try{const detail=state.currentMap?.id===map.id&&state.mapData?.map?.id===map.id?state.mapData:await api(`/api/maps/${map.id}`);for(const line of detail.lines||[])for(const item of line.items||[]){index.mapItems.push({map,line,item});index.mapItemOwners.set(+item.id,+map.id)}scheduleQuickOpenRefresh()}catch(error){console.warn('Quick Open Map index skipped',map.id,error)}}),
    ]);
    index.complete=true;return index;
  })().finally(()=>{state.quickOpenDetailIndexPromise=null;scheduleQuickOpenRefresh()});
  return state.quickOpenDetailIndexPromise;
}
function scheduleQuickOpenDetailIndex(){if(state.quickOpenDetailIndex?.complete||state.quickOpenDetailIndexPromise)return;const run=()=>buildQuickOpenDetailIndex().catch(error=>console.warn('Quick Open detail index could not be fully prepared',error));if('requestIdleCallback'in window)requestIdleCallback(run,{timeout:350});else setTimeout(run,0)}
async function quickOpenCanvasOwner(nodeId){const cached=state.quickOpenDetailIndex?.canvasOwners?.get(+nodeId);if(cached)return cached;const detail=await buildQuickOpenDetailIndex();return detail.canvasOwners.get(+nodeId)||null}
async function quickOpenMapItemOwner(itemId){const cached=state.quickOpenDetailIndex?.mapItemOwners?.get(+itemId);if(cached)return cached;const detail=await buildQuickOpenDetailIndex();return detail.mapItemOwners.get(+itemId)||null}
async function openQuickOpen(){
  $('#quickOpen').classList.remove('hidden');$('#quickOpenInput').value='';quickOpenActiveIndex=0;$('#quickOpenInput').focus();await renderQuickOpenResults();scheduleQuickOpenDetailIndex();
}
async function renderQuickOpenResults(){
  const q=$('#quickOpenInput').value.trim().toLocaleLowerCase(i18nLocale()),tasks=await getTasks(),detail=state.quickOpenDetailIndex||{cards:[],mapItems:[],milestones:[]};
  const documentName=id=>state.documents.find(d=>+d.id===+id)?.title||'';
  const chapterName=id=>visibleChapterById(+id)?.title||'';
  const items=[
    ...visibleChapters().map(x=>({kind:'Chapter',label:x.title,context:documentName(x.tab_id),searchText:`${x.title} ${x.description||''} ${documentName(x.tab_id)}`,action:()=>{setView('manuscript');selectChapter(x.id)}})),
    ...state.scenes.map(x=>({kind:'Scene',label:x.title||i18nSource('Scene'),context:chapterName(x.chapter_id),searchText:`${x.title||''} ${x.description||''} ${chapterName(x.chapter_id)}`,action:()=>{setView('manuscript');jumpToScene(x.id)}})),
    ...state.canvases.map(x=>({kind:'Canvas',label:x.name,searchText:`${x.name} ${x.description||''}`,action:()=>{setView('canvas');selectCanvas(x.id)}})),
    ...detail.cards.map(({canvas,node})=>({kind:'Card',label:node.title||quickOpenPlainText(node.body)||i18nSource('Untitled Card'),context:canvas.name,searchText:`${node.title||''} ${quickOpenPlainText(node.body)} ${canvas.name}`,action:async()=>{setView('canvas');await selectCanvas(canvas.id);state.selectedNodes=new Set([node.id]);renderNodeSelection();openNodeInspector(node.id)}})),
    ...state.maps.map(x=>({kind:'Map',label:x.name,searchText:`${x.name} ${x.description||''}`,action:()=>{setView('maps');selectMap(x.id)}})),
    ...detail.mapItems.map(({map,line,item})=>({kind:item.item_type==='span'?'Span':'Station',label:item.title||i18nSource('Untitled item'),context:`${map.name} · ${line.name}`,searchText:`${item.title||''} ${item.description||''} ${map.name} ${line.name}`,action:()=>openMapAtItem(map.id,item.id)})),
    ...tasks.map(x=>({kind:'Task',label:x.title,searchText:`${x.title} ${x.description||''} ${x.marker_name||''}`,action:()=>{setView('tasks');openTaskDetails(x.id)}})),
    ...detail.milestones.map(({task,milestone})=>({kind:'Milestone',label:milestone.title||i18nSource('Milestone'),context:task.title,searchText:`${milestone.title||''} ${milestone.description||''} ${task.title} ${task.description||''}`,action:()=>{setView('tasks');openTaskDetails(task.id)}})),
    ...state.documents.map(x=>({kind:'Tab',label:x.title,searchText:`${x.title} ${x.description||''}`,action:async()=>{setView('manuscript');state.activeDocumentId=x.id;renderDocuments();renderChapterTree();const c=visibleChapters(x.id)[0];if(c)await selectChapter(c.id)}})),
  ].filter(x=>!q||`${x.kind} ${i18nSource(x.kind)} ${x.label} ${x.context||''} ${x.searchText||''}`.toLocaleLowerCase(i18nLocale()).includes(q)).slice(0,80);
  quickOpenItems=items;quickOpenActiveIndex=Math.min(quickOpenActiveIndex,Math.max(0,items.length-1));
  const box=$('#quickOpenResults');box.innerHTML=items.map((x,i)=>`<button class="quick-result ${i===quickOpenActiveIndex?'active':''}" data-quick-index="${i}" role="option" aria-selected="${i===quickOpenActiveIndex?'true':'false'}"><small>${escapeHtml(i18nSource(x.kind))}</small><span>${escapeHtml(x.label)}${x.context?`<em>${escapeHtml(x.context)}</em>`:''}</span></button>`).join('')||'<p class="small-muted quick-open-empty">{{i18n:dynamic.html.text.p.7e6291e8}}</p>';
  $$('[data-quick-index]',box).forEach(b=>{b.onmouseenter=()=>{quickOpenActiveIndex=+b.dataset.quickIndex;syncQuickOpenSelection()};b.onclick=()=>{const item=quickOpenItems[+b.dataset.quickIndex];closeQuickOpen();item?.action()}});
}

