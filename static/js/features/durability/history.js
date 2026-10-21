// -------------------- history / cuts / principles --------------------
function historyId(rev){return String(rev?.revision_id||rev?.id||'')}
function historySameId(a,b){return String(a||'')===String(b||'')}
function historyDisplayName(rev){return rev?.label||i18nSource('Automatic History')}
function historyHumanTime(value){return i18nDateTime(value)}
function historyCauseLabel(rev){
  const cause=String(rev?.cause||'automatic');
  if(cause==='restore')return i18nText('history.cause.restore');
  if(cause==='pre_restore_safety')return i18nText('history.cause.safety');
  if(['continuous','idle','automatic'].includes(cause))return i18nText('history.cause.automatic');
  return i18nText('history.cause.edit');
}
function historyDayKey(value){const d=new Date(value);if(Number.isNaN(d.getTime()))return String(value||'');return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}
function historyDayLabel(value){
  const d=new Date(value);if(Number.isNaN(d.getTime()))return String(value||'');
  const today=new Date(),day=new Date(d.getFullYear(),d.getMonth(),d.getDate()),base=new Date(today.getFullYear(),today.getMonth(),today.getDate()),delta=Math.round((base-day)/86400000);
  if(delta===0)return i18nText('history.today');if(delta===1)return i18nText('history.yesterday');return i18nDate(value);
}
function historyWordDelta(rev){const value=rev?.word_delta;if(!Number.isInteger(value)||value===0)return'';return value>0?i18nText('history.words_added',{count:i18nNumber(value)}):i18nText('history.words_removed',{count:i18nNumber(Math.abs(value))})}
async function ensureHistoryWorkspace(refreshItems=true){
  if(!historyWorkspace)historyWorkspace={items:[],selectedId:null,currentGeneration:null,showDiff:false,linkedScroll:true,changeIndex:-1,changeCount:0,changeTargets:[],scope:null,chapterId:null};
  if(refreshItems||!historyWorkspace.items.length){const data=await api('/api/history/v2');historyWorkspace.items=data.revisions||[];historyWorkspace.currentGeneration=Number.isInteger(data.current_generation)?data.current_generation:+data.current_generation||null;if(!historyWorkspace.items.some(x=>historySameId(historyId(x),historyWorkspace.selectedId)))historyWorkspace.selectedId=historyWorkspace.items[0]?historyId(historyWorkspace.items[0]):null}
  return historyWorkspace;
}
function historyFilterItems(query=''){const q=String(query||'').trim().toLowerCase();return (historyWorkspace?.items||[]).filter(r=>!q||`${historyDisplayName(r)} ${historyCauseLabel(r)} ${historyHumanTime(r.created_at)} ${historyWordDelta(r)}`.toLowerCase().includes(q))}
function historyMenuClose(){const menu=$('#historyRowMenu');if(menu)menu.remove()}
function openHistoryRowMenu(button,rev,onDismiss){
  historyMenuClose();const rect=button.getBoundingClientRect(),menu=document.createElement('div');menu.id='historyRowMenu';menu.className='history-row-menu';menu.innerHTML=`<button data-history-menu="compare">${escapeHtml(i18nText('history.compare_current'))}</button><button data-history-menu="checkpoint">${escapeHtml(i18nText('history.save_checkpoint'))}</button><button data-history-menu="restore">${escapeHtml(i18nText('history.restore'))}</button>`;document.body.appendChild(menu);const width=190;menu.style.left=`${Math.max(8,Math.min(window.innerWidth-width-8,rect.right-width))}px`;menu.style.top=`${Math.min(window.innerHeight-menu.offsetHeight-8,rect.bottom+4)}px`;
  const dismiss=e=>{if(!menu.contains(e.target)&&e.target!==button){historyMenuClose();document.removeEventListener('pointerdown',dismiss,true)}};setTimeout(()=>document.addEventListener('pointerdown',dismiss,true),0);
  menu.querySelector('[data-history-menu="compare"]').onclick=async()=>{historyMenuClose();closeModal(false);try{await openHistorySplit(historyId(rev))}catch(error){toast(error.message||i18nText('history.compare_failed'),4500)}};
  menu.querySelector('[data-history-menu="checkpoint"]').onclick=async()=>{historyMenuClose();await saveHistoryAsCheckpoint(rev)};
  menu.querySelector('[data-history-menu="restore"]').onclick=async()=>{historyMenuClose();await restoreHistoryFromUI(rev)};
}
function renderHistoryModal(onDismiss=null,query=''){
  const items=historyFilterItems(query),groups=[];for(const row of items){const key=historyDayKey(row.created_at),last=groups[groups.length-1];if(!last||last.key!==key)groups.push({key,label:historyDayLabel(row.created_at),items:[row]});else last.items.push(row)}
  const list=groups.map(group=>`<section class="history-day-group"><div class="history-day-heading">${escapeHtml(group.label)}</div>${group.items.map(r=>{const delta=historyWordDelta(r),meta=[i18nTime(r.created_at),delta,r.is_safety?i18nText('history.safety_state'):''].filter(Boolean).join(' · ');return `<div class="history-modal-row"><div class="history-modal-main"><span class="history-cause">${escapeHtml(historyCauseLabel(r))}</span><b>${escapeHtml(historyDisplayName(r))}</b><small>${escapeHtml(meta)}</small></div><button class="bare-button history-more-button manuscript-item-more" data-history-more="${escapeHtml(historyId(r))}" aria-label="${escapeHtml(i18nText('history.actions_for',{name:historyDisplayName(r)}))}" title="${escapeHtml(i18nText('history.actions'))}">⋮</button></div>`}).join('')}</section>`).join('');
  const body=`<div class="history-modal-intro"><p>${escapeHtml(i18nText('history.intro'))}</p><input id="historyModalSearch" type="search" autocomplete="off" placeholder="${escapeHtml(i18nText('history.search_placeholder'))}" value="${escapeHtml(query)}" aria-label="${escapeHtml(i18nText('history.search_aria'))}"></div><div class="history-modal-list">${list||`<div class="project-list-empty">${escapeHtml(i18nText('history.empty'))}</div>`}</div>`;
  renderModal('History',body,'',onDismiss);$('#historyModalSearch').oninput=e=>renderHistoryModal(onDismiss,e.target.value);$$('[data-history-more]').forEach(button=>button.onclick=e=>{e.stopPropagation();const rev=historyWorkspace.items.find(x=>historySameId(historyId(x),button.dataset.historyMore));if(rev)openHistoryRowMenu(button,rev,onDismiss)});queueMicrotask(()=>$('#historyModalSearch')?.focus());
}
async function openHistory(onDismiss=null){await flushSave();await ensureHistoryWorkspace(true);renderHistoryModal(onDismiss,'')}
function historyComparisonContext(){return state.viewMode==='chapter'&&state.chapter?{scope:'chapter',chapterId:entityId(state.chapter.id)}:{scope:'book',chapterId:null}}
function closeHistoryComparisonUI(){clearTimeout(historySplitRenderTimer);historySplitRenderTimer=null;historySplitScrollCleanup?.();historySplitScrollCleanup=null;if(historyWorkspace){historyWorkspace.changeIndex=-1;historyWorkspace.changeCount=0;historyWorkspace.changeTargets=[]}$('#historyCompareToolbar')?.classList.add('hidden');clearCheckpointLiveHighlights()}
function syncHistoryComparisonContext(){if(state.split?.type!=='history'||!historyWorkspace)return;const ctx=historyComparisonContext();if(historyWorkspace.scope!==ctx.scope||entityId(historyWorkspace.chapterId||0)!==entityId(ctx.chapterId||0)){closeSplitView();return}scheduleHistorySplitRender()}
function wireHistorySplitScrollSync(){historySplitScrollCleanup?.();historySplitScrollCleanup=null;const left=$('#documentScroller'),right=$('#splitBody');if(!left||!right||state.split?.type!=='history')return;let syncing=false;const sync=(from,to)=>{if(syncing||!historyWorkspace?.linkedScroll)return;const fm=Math.max(1,from.scrollHeight-from.clientHeight),tm=Math.max(0,to.scrollHeight-to.clientHeight);syncing=true;to.scrollTop=(from.scrollTop/fm)*tm;requestAnimationFrame(()=>syncing=false)};const fromLeft=()=>sync(left,right),fromRight=()=>sync(right,left);left.addEventListener('scroll',fromLeft,{passive:true});right.addEventListener('scroll',fromRight,{passive:true});historySplitScrollCleanup=()=>{left.removeEventListener('scroll',fromLeft);right.removeEventListener('scroll',fromRight)}}
function moveHistorySplitChange(delta){const ws=historyWorkspace;if(!ws?.changeCount)return;ws.changeIndex=(ws.changeIndex+delta+ws.changeCount)%ws.changeCount;const target=ws.changeTargets[ws.changeIndex];target?.splitEl?.scrollIntoView({block:'center',behavior:'smooth'});target?.liveEl?.scrollIntoView({block:'center',behavior:'smooth'});$('#historySplitChangeCounter').textContent=`${ws.changeIndex+1} / ${ws.changeCount}`}
function syncHistoryContextToolbar(rev){const ws=historyWorkspace,bar=$('#historyCompareToolbar'),select=$('#historySplitSelect');if(!ws||!bar||!select)return;bar.classList.remove('hidden');select.innerHTML=ws.items.map(r=>`<option value="${escapeHtml(historyId(r))}" ${historySameId(historyId(r),historyId(rev))?'selected':''}>${escapeHtml(historyDisplayName(r))} · ${escapeHtml(i18nTime(r.created_at))}</option>`).join('');$('#historySplitShowDiff').checked=ws.showDiff;$('#historySplitLinked').checked=ws.linkedScroll;$('#historySplitChangeCounter').textContent=ws.changeCount?`${Math.max(0,ws.changeIndex+1)} / ${i18nNumber(ws.changeCount)}`:i18nSource('0 changes');$('#historySplitKeep').textContent=i18nSource(ws.scope==='chapter'?'Save Chapter as Checkpoint':'Save Book as Checkpoint')}
async function renderHistorySplit(){const ws=historyWorkspace;if(!ws||state.split?.type!=='history')return;const rev=ws.items.find(x=>historySameId(historyId(x),state.split.id));if(!rev)return closeSplitView();const ctx={scope:ws.scope,chapterId:ws.chapterId};$('#splitTitle').textContent=i18nText('history.read_only_title',{title:historyDisplayName(rev)});$('#splitPane').classList.remove('hidden');openSplitLayout();$('#splitBody').classList.add('checkpoint-split-body');ws.changeIndex=-1;ws.changeTargets=[];clearCheckpointLiveHighlights();syncHistoryContextToolbar(rev);try{const qs=new URLSearchParams({scope:ctx.scope});if(ctx.chapterId)qs.set('chapter_id',String(ctx.chapterId));const data=await api(`/api/history/v2/revisions/${historyId(rev)}/compare-current?${qs}`),liveUnits=checkpointLiveUnits();ws.currentGeneration=+data.current_generation||ws.currentGeneration;let liveIndex=0,changeIndex=-1;ws.changeCount=0;const rows=[];for(const row of data.rows){const hasCurrent=row.kind!=='removed';const liveEl=hasCurrent?liveUnits[liveIndex++]||null:null;if(row.kind!=='same'){changeIndex++;ws.changeCount++}const attr=row.kind!=='same'?` data-change-index="${changeIndex}"`:'';let content=row.left;if(!content&&row.kind!=='same')content=`<span class="checkpoint-current-only">${escapeHtml(i18nText('history.current_only'))}</span>`;rows.push(`<div class="checkpoint-compare-row ${row.kind}"${attr}>${checkpointCompareUnitHtml(content,row.left_style)}</div>`);if(row.kind!=='same')ws.changeTargets.push({liveEl,splitIndex:changeIndex})}$('#splitBody').innerHTML=`<article class="checkpoint-split-paper checkpoint-comparison ${ws.showDiff?'show-diff':''}"><div class="checkpoint-readonly-note">${escapeHtml(i18nText('history.read_only_note'))}</div>${rows.join('')}</article>`;bindReadOnlyImageDescriptions($('#splitBody'));ws.changeTargets.forEach(target=>target.splitEl=$(`#splitBody [data-change-index="${target.splitIndex}"]`));$('#historySplitChangeCounter').textContent=ws.changeCount?`0 / ${i18nNumber(ws.changeCount)}`:i18nSource('No changes');if(ws.showDiff)for(const target of ws.changeTargets)target.liveEl?.classList.add('checkpoint-live-change');wireHistorySplitScrollSync()}catch(error){$('#splitBody').innerHTML=`<div class="checkpoint-compare-error">${escapeHtml(error.message||i18nText('history.compare_failed'))}</div>`;ws.changeCount=0;$('#historySplitChangeCounter').textContent=''}}
function scheduleHistorySplitRender(){clearTimeout(historySplitRenderTimer);if(state.split?.type==='history')historySplitRenderTimer=setTimeout(()=>renderHistorySplit().catch(error=>console.warn('History comparison refresh failed',error)),100)}
async function openHistorySplit(revisionId){await flushSave();const ws=await ensureHistoryWorkspace(true),rid=String(revisionId||''),rev=ws.items.find(x=>historySameId(historyId(x),rid));if(!rev)throw new Error(i18nText('history.revision_missing'));const ctx=historyComparisonContext();ws.scope=ctx.scope;ws.chapterId=ctx.chapterId;ws.selectedId=historyId(rev);closeCheckpointComparisonUI();closeHistoryComparisonUI();state.split={type:'history',id:historyId(rev)};$('#splitPane').classList.remove('hidden');openSplitLayout();await renderHistorySplit()}
async function restoreHistoryFromUI(rev){
  if(!rev)return;await flushSave();const ws=await ensureHistoryWorkspace(true),current=ws.items.find(x=>historySameId(historyId(x),historyId(rev)));if(!current)return toast(i18nText('history.revision_missing'),4200);const expected=ws.currentGeneration;
  if(!await confirmDialog(i18nText('history.restore_title'),i18nText('history.restore_confirm',{name:historyDisplayName(current)}),{confirmLabel:i18nText('history.restore'),danger:false}))return;
  try{await api(`/api/history/v2/revisions/${historyId(current)}/restore`,{method:'POST',body:JSON.stringify({expected_generation:expected})});location.reload()}catch(error){if(error?.code==='HISTORY_RESTORE_STALE')return toast(i18nText('history.restore_stale'),4800);toast(error.message||i18nText('history.restore_failed'),4800)}
}
async function saveHistoryAsCheckpoint(rev){if(!rev)return;const ctx=historyComparisonContext();try{const body={scope:ctx.scope,chapter_id:ctx.chapterId,name:historyDisplayName(rev),description:i18nText('history.checkpoint_description')};const cp=await api(`/api/history/v2/revisions/${historyId(rev)}/checkpoint`,{method:'POST',body:JSON.stringify(body)});await ensureCheckpointWorkspace(true);toast(i18nText('checkpoint.saved_from_history',{name:checkpointDisplayName(cp)}),4200)}catch(error){toast(error.message||i18nText('history.checkpoint_failed'),4500)}}
$('#historySplitSelect')?.addEventListener('change',async e=>{const id=String(e.target.value||'');if(!id)return;state.split={type:'history',id};historyWorkspace.selectedId=id;await renderHistorySplit()});$('#historySplitShowDiff')?.addEventListener('change',e=>{historyWorkspace.showDiff=e.target.checked;renderHistorySplit().catch(console.warn)});$('#historySplitLinked')?.addEventListener('change',e=>historyWorkspace.linkedScroll=e.target.checked);$('#historySplitPrev')?.addEventListener('click',()=>moveHistorySplitChange(-1));$('#historySplitNext')?.addEventListener('click',()=>moveHistorySplitChange(1));$('#historySplitKeep')?.addEventListener('click',()=>saveHistoryAsCheckpoint(historyWorkspace?.items.find(x=>historySameId(historyId(x),state.split?.id))));$('#historySplitRestore')?.addEventListener('click',()=>restoreHistoryFromUI(historyWorkspace?.items.find(x=>historySameId(historyId(x),state.split?.id))));

let cutsWorkspace={items:[],selectedId:null,dirty:true};
async function notifyCutsChanged(preferId=null,{removeId=null}={}){
  // Permanent deletion already knows exactly which row changed. Keep all Cut mutations
  // on one synchronization boundary, but avoid re-downloading/reparsing the entire
  // library when a precise local update is sufficient.
  if(removeId!=null){removeCutFromWorkspace(removeId);return}
  cutsWorkspace.dirty=true;
  const panel=$('#cutsPanel');
  if(panel&&!panel.classList.contains('hidden'))await reloadCutsWorkspace(preferId);
}
function cutHumanTime(value){return i18nDateTime(value)}
function cutInformationTime(value){
  const d=new Date(value);if(Number.isNaN(d.getTime()))return String(value||'');const pad=n=>String(n).padStart(2,'0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}-${pad(d.getMonth()+1)}-${pad(d.getDate())}-${String(d.getFullYear()).slice(-2)}`;
}
function cutProvenance(cut){
  const parts=[cut?.source_project,cut?.source_chapter,cut?.source_scene].map(x=>String(x||'').trim()).filter(Boolean),stamp=cutInformationTime(cut?.created_at);
  if(stamp)parts.push(stamp);return parts.join(' | ');
}
async function ensureCutsWorkspace(refresh=true){if(refresh||cutsWorkspace.dirty||!cutsWorkspace.items.length){cutsWorkspace.items=await api('/api/cuts');cutsWorkspace.dirty=false}if(cutsWorkspace.selectedId&&!cutsWorkspace.items.some(x=>entityId(x.id)===entityId(cutsWorkspace.selectedId)))cutsWorkspace.selectedId=null;return cutsWorkspace}
function filteredCuts(){const q=String($('#cutsSearch')?.value||'').trim().toLowerCase();return cutsWorkspace.items.filter(c=>!q||`${c.title||''} ${c.note||''} ${c.source_display||''} ${c.source_project||''} ${c.source_chapter||''} ${c.source_scene||''} ${c.content_text||''}`.toLowerCase().includes(q))}
function savedCutSelectionItems(){try{const r=state.lastManuscriptRange;if(!r||r.collapsed||!r.startContainer?.isConnected)return[];return selectionItems(r)}catch{return[]}}
function cutCaretAnchor(){try{const r=state.lastManuscriptRange;if(!r||!r.collapsed||!r.startContainer?.isConnected)return null;const node=r.startContainer.nodeType===Node.ELEMENT_NODE?r.startContainer:r.startContainer.parentElement,prose=node?.closest?.('.manuscript-block');if(!prose||!$('#editor').contains(prose))return null;const wrap=prose.closest('.manuscript-block-wrap');if(!wrap)return null;return{chapter_id:entityId(wrap.dataset.chapterId),block_id:wrap.dataset.blockId,offset:offsetIn(prose,r.startContainer,r.startOffset)}}catch{return null}}
function selectedCut(){return cutsWorkspace.items.find(x=>entityId(x.id)===entityId(cutsWorkspace.selectedId))||null}
function syncCutsPanelContext(){
  const panel=$('#cutsPanel');if(!panel||panel.classList.contains('hidden'))return;
  const cutBtn=$('#cutSelectionBtn');if(cutBtn){const available=savedCutSelectionItems().length>0;cutBtn.disabled=!available;cutBtn.title=i18nSource(available?'Move the selected manuscript text into Cuts':'Select manuscript text to create a Cut.')}
  const insert=$('#cutInsertBtn');if(insert){const cut=selectedCut(),anchor=cutCaretAnchor();insert.disabled=!(cut&&anchor);insert.title=i18nSource(!cut?'Select a Cut to insert it.':anchor?'Insert the selected Cut at the saved manuscript cursor':'Place your cursor in the manuscript to insert this Cut.')}
}
function renderCutsList(){
  const box=$('#cutsList');if(!box)return;const items=filteredCuts(),hasCuts=cutsWorkspace.items.length>0;
  box.innerHTML=items.map(c=>`<div class="cuts-list-row ${entityId(c.id)===entityId(cutsWorkspace.selectedId)?'active':''}" data-cut-row="${c.id}"><button class="cuts-list-main" data-cut-select="${c.id}" title="${escapeHtml(i18nSource(entityId(c.id)===entityId(cutsWorkspace.selectedId)?'Selected · preview expanded':'Select this Cut'))}"><span class="cuts-preview">${escapeHtml(c.content_text||i18nSource('[Image]'))}</span></button><button type="button" class="cuts-more manuscript-item-more" data-cut-more="${c.id}" title="{{i18n:dynamic.html.attr.title.4e6e04c3}}" aria-label="{{i18n:dynamic.html.attr.aria.label.ef80cf17}}"><span aria-hidden="true">⋮</span></button></div>`).join('')||`<div class="manuscript-panel-empty cuts-empty"><b>${escapeHtml(i18nSource(hasCuts?'No matching cuts':'No cuts yet'))}</b><span>${escapeHtml(i18nSource(hasCuts?'Try a different search.':'Select manuscript text and use Cut selection to keep removed prose here.'))}</span></div>`;
  $$('[data-cut-select]',box).forEach(b=>b.onclick=()=>selectCut(entityId(b.dataset.cutSelect)));
  $$('[data-cut-more]',box).forEach(b=>b.onclick=e=>{const cut=cutsWorkspace.items.find(x=>entityId(x.id)===entityId(b.dataset.cutMore));if(cut)openCutMenu(e,cut)});
  $$('[data-cut-row]',box).forEach(row=>row.oncontextmenu=e=>{const cut=cutsWorkspace.items.find(x=>entityId(x.id)===entityId(row.dataset.cutRow));if(cut)openCutMenu(e,cut)});
}
function selectCut(id){const cut=cutsWorkspace.items.find(x=>entityId(x.id)===entityId(id));if(!cut)return;cutsWorkspace.selectedId=entityId(id);renderCutsList();syncCutsPanelContext()}
async function reloadCutsWorkspace(preferId=null){
  await ensureCutsWorkspace(true);if(preferId&&cutsWorkspace.items.some(x=>entityId(x.id)===entityId(preferId)))cutsWorkspace.selectedId=entityId(preferId);
  else if(cutsWorkspace.selectedId&&!cutsWorkspace.items.some(x=>entityId(x.id)===entityId(cutsWorkspace.selectedId)))cutsWorkspace.selectedId=null;
  renderCutsList();syncCutsPanelContext();
}
async function updateCutNote(cut,value){await api(`/api/cuts/${cut.id}`,{method:'PATCH',body:JSON.stringify({note:value})});await notifyCutsChanged(cut.id);return selectedCut()||cut}
function openCutDetails(cut){
  const provenance=cutProvenance(cut)||cutHumanTime(cut.created_at);
  showModal('Cut Details',`<div class="cut-information-provenance">${escapeHtml(provenance)}</div><div class="field cut-information-note"><label>{{i18n:dynamic.html.text.label.8169ddd8}}</label><textarea id="cutInformationNote" rows="4" placeholder="{{i18n:dynamic.html.attr.placeholder.cd93c188}}">${escapeHtml(cut.note||'')}</textarea></div><div class="cut-information-label">{{i18n:dynamic.html.text.div.09dfbcde}}</div><article class="cut-information-content">${cut.content_html||''}</article>`,`<button id="cutInformationDelete" class="danger">{{i18n:dynamic.html.text.button.f87e4585}}</button><button id="cutInformationSave">{{i18n:dynamic.html.text.button.9faf5fce}}</button>`);
  $$('.cut-information-content figure[data-width]').forEach(figure=>{const width=Math.max(5,Math.min(100,entityId(figure.dataset.width)||100)),image=figure.querySelector('img'),inRow=!!String(figure.dataset.row||'').trim();figure.style.width='100%';if(image)image.style.width=`${inRow?100:width}%`});bindReadOnlyImageDescriptions($('.cut-information-content'));
  $('#cutInformationDelete').onclick=async()=>{if(!await permanentlyDeleteCut(cut))return;dismissModalTree()};
  $('#cutInformationSave').onclick=async()=>{const value=$('#cutInformationNote').value;cut=await updateCutNote(cut,value);toast(value.trim()?'Cut note saved':'Cut note removed');closeModal()};
}
function removeCutFromWorkspace(id){id=entityId(id);cutsWorkspace.items=cutsWorkspace.items.filter(c=>entityId(c.id)!==id);if(entityId(cutsWorkspace.selectedId)===id)cutsWorkspace.selectedId=null;cutsWorkspace.dirty=false;const row=$(`[data-cut-row="${id}"]`);row?.remove();if(!filteredCuts().length)renderCutsList();syncCutsPanelContext()}
async function permanentlyDeleteCut(cut){if(!await confirmDialog('Delete Cut','Permanently delete this Cut? Cuts are independent from Undo and History, so this cannot be recovered unless it exists in a project backup or Cuts export.',{confirmLabel:'Delete permanently',danger:true}))return false;await api(`/api/cuts/${cut.id}`,{method:'DELETE'});await notifyCutsChanged(null,{removeId:cut.id});toast('Cut deleted');return true}
function cutMenuItems(cut){const anchor=cutCaretAnchor();return [
  {label:'Insert',disabled:!anchor,disabledReason:'Place your cursor in the manuscript to insert this Cut.',action:()=>insertCutAtCursor(cut)},
  {label:'Details',action:()=>openCutDetails(cut)},
  {label:'Delete',danger:true,action:()=>permanentlyDeleteCut(cut)}
]}
function openCutMenu(event,cut){
  event.preventDefault();event.stopPropagation();const items=cutMenuItems(cut);
  if(event.type==='contextmenu')openContextMenu(event.clientX,event.clientY,items);
  else{const rect=event.currentTarget.getBoundingClientRect();openContextMenu(rect.left,rect.bottom+4,items,{anchorRect:rect})}
}
async function cutSelectionFromPanel(){if(!restoreLastManuscriptRange())return toast('Select manuscript text to create a Cut.');const items=selectionItems();if(!items.length)return toast('Select manuscript text to create a Cut.');await cutSelection(items);syncCutsPanelContext()}
async function insertCutAtCursor(cut){const anchor=cutCaretAnchor();if(!anchor)return toast('Place your cursor in the manuscript to insert this Cut.');let result;await withStructuralUndo(`Insert Cut: ${cut.title||'Cut'}`,async()=>{result=await api(`/api/cuts/${cut.id}/insert`,{method:'POST',body:JSON.stringify(anchor)})},{view:'manuscript',chapterId:anchor.chapter_id,documentId:state.activeDocumentId,manuscriptViewMode:state.viewMode});await refresh(false);if(state.viewMode==='book')await openWholeBook(false,false);else await selectChapter(anchor.chapter_id,false,false);cutsWorkspace.selectedId=null;await notifyCutsChanged();toast('Cut inserted into manuscript')}
function closeCutsPanel(){$('#cutsPanel').classList.add('hidden');$('#cutsBtn').classList.remove('active');$('#cutsBtn').setAttribute('aria-pressed','false');const status=$('#cutsTooltipStatus');if(status)status.textContent=''}
async function openCutsPanel(){const panel=$('#cutsPanel');if(!panel.classList.contains('hidden')){closeCutsPanel();return}await flushSave();await ensureCutsWorkspace(true);closeCheckpointPanel();closeCommentsPanel();closeMarkupPanel();closeManuscriptImagePanel();closeManuscriptReviewsPanel();panel.classList.remove('hidden');$('#cutsBtn').classList.add('active');$('#cutsBtn').setAttribute('aria-pressed','true');const status=$('#cutsTooltipStatus');if(status)status.textContent=i18nSource('Open — click to close the Cuts panel.');renderCutsList();syncCutsPanelContext()}
$('#cutsBtn').onclick=()=>openCutsPanel().catch(error=>{console.error('CUTS_UI_001',error);toast(error.message||'Cuts could not be opened.')});
$('#closeCutsPanel').onclick=closeCutsPanel;
$('#cutsSearch').oninput=()=>{const items=filteredCuts();if(cutsWorkspace.selectedId&&!items.some(x=>entityId(x.id)===entityId(cutsWorkspace.selectedId)))cutsWorkspace.selectedId=null;renderCutsList();syncCutsPanelContext()};
$('#cutSelectionBtn').onclick=cutSelectionFromPanel;
$('#cutInsertBtn').onclick=()=>{const cut=selectedCut();if(cut)insertCutAtCursor(cut);else toast('Select a Cut to insert it.')};
attachManuscriptToolTooltip('cutsBtn','cutsTooltip');


