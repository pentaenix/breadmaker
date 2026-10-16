// -------------------- checkpoints --------------------
let checkpointWorkspace=null,checkpointPanelHoverTimer=null,checkpointPanelHoverSourceId=null;
const CHECKPOINT_PANEL_HOVER_DELAY_MS=300;
function checkpointDisplayName(cp){return cp?.display_name||cp?.name||cp?.default_name||cp?.source_chapter_title||cp?.source_tab_title||i18nSource(cp?.scope==='book'?'Book':'Chapter')}
function checkpointCompatible(a,b){if(!a||!b||a.scope!==b.scope)return false;if(a.scope==='book')return true;const as=[+a.source_chapter_id||0,String(a.source_chapter_created_at||'')],bs=[+b.source_chapter_id||0,String(b.source_chapter_created_at||'')];if(as[0]&&as[1]&&as[0]===bs[0]&&as[1]===bs[1])return true;const ac=[+a.current_source_chapter_id||0,String(a.current_source_chapter_created_at||'')],bc=[+b.current_source_chapter_id||0,String(b.current_source_chapter_created_at||'')];return !!(ac[0]&&ac[1]&&ac[0]===bc[0]&&ac[1]===bc[1])}
function checkpointHumanTime(value){return i18nDateTime(value)}
function checkpointSourceLabel(cp){if(cp.scope==='chapter')return cp.source_chapter_title||cp.default_name||i18nSource('Chapter');return cp.source_tab_title||cp.default_name||i18nSource('Book')}
function hideCheckpointPanelHover(){clearTimeout(checkpointPanelHoverTimer);checkpointPanelHoverTimer=null;checkpointPanelHoverSourceId=null;$('#checkpointPanelHoverCard')?.classList.add('hidden')}
function queueCheckpointPanelHover(cp,row){hideCheckpointPanelHover();const description=String(cp?.description||'').trim();if(!description)return;const id=+cp.id;checkpointPanelHoverSourceId=id;checkpointPanelHoverTimer=setTimeout(()=>{if(checkpointPanelHoverSourceId!==id||!row.isConnected)return;const card=$('#checkpointPanelHoverCard');if(!card)return;card.innerHTML=`<div class="checkpoint-panel-hover-description">${escapeHtml(description).replace(/\n/g,'<br>')}</div>`;positionMarkerPanelHover(card,row)},CHECKPOINT_PANEL_HOVER_DELAY_MS)}
async function ensureCheckpointWorkspace(refreshItems=true){
  if(!checkpointWorkspace)checkpointWorkspace={items:[],selectedId:null,showDiff:false,linkedScroll:true,changeIndex:-1,changeCount:0,changeTargets:[]};
  if(refreshItems||!checkpointWorkspace.items.length){const data=await api('/api/checkpoints');checkpointWorkspace.items=data.checkpoints||[]}
  return checkpointWorkspace;
}
function checkpointMatchesCurrentContext(cp){
  if(state.viewMode==='book')return cp.scope==='book';
  if(state.viewMode==='chapter'&&state.chapter)return cp.scope==='chapter'&&cp.source_exists&&+cp.current_chapter_id===+state.chapter.id;
  return false;
}
function checkpointOptionLabel(cp){return `${i18nSource(cp.scope==='book'?'Book':'Chapter')} · ${checkpointDisplayName(cp)}`}
function refreshCheckpointPanelFilter(){if(!checkpointWorkspace)return;renderCheckpointList()}
function syncCheckpointPanelContext(){
  const panel=$('#checkpointPanel'),scope=$('#checkpointScopeFilter');if(!panel||panel.classList.contains('hidden')||!checkpointWorkspace||!scope)return;const currentOption=scope.querySelector('option[value="current"]');if(currentOption)currentOption.disabled=!(state.viewMode==='chapter'&&state.chapter);if(scope.value==='current'&&currentOption?.disabled)scope.value='all';renderCheckpointList();
}
function syncCheckpointComparisonContext(){
  if(state.split?.type!=='checkpoint'||!checkpointWorkspace)return;const cp=checkpointWorkspace.items.find(x=>+x.id===+state.split.id);if(!cp){closeSplitView();return}scheduleCheckpointSplitRender();
}
function checkpointFilterItems(){
  const ws=checkpointWorkspace;if(!ws)return[];const q=String($('#checkpointSearch')?.value||'').trim().toLowerCase(),filter=$('#checkpointScopeFilter')?.value||'all';
  return ws.items.filter(cp=>{
    if(filter==='current'&&(!state.chapter||cp.scope!=='chapter'||!cp.source_exists||+cp.current_chapter_id!==+state.chapter.id))return false;
    if(filter==='chapters'&&cp.scope!=='chapter')return false;
    if(filter==='book'&&cp.scope!=='book')return false;
    const hay=[checkpointDisplayName(cp),cp.description,cp.source_chapter_title,cp.source_tab_title].join(' ').toLowerCase();return !q||hay.includes(q);
  });
}
function renderCheckpointList(){
  hideCheckpointPanelHover();const ws=checkpointWorkspace,box=$('#checkpointList');if(!ws||!box)return;const items=checkpointFilterItems();
  box.innerHTML=items.map(cp=>{const name=checkpointDisplayName(cp),scope=i18nSource(cp.scope==='book'?'Book':'Chapter'),wordCount=i18nCount(cp.word_count||0,'status.words.one','status.words.many'),sourceDeleted=!cp.source_exists&&cp.scope==='chapter'?` · ${i18nSource('Source deleted')}`:'';return `<div class="checkpoint-list-row ${+cp.id===+ws.selectedId?'active':''}" data-checkpoint-row="${cp.id}"><button type="button" class="checkpoint-use" data-checkpoint-compare="${cp.id}" title="${escapeHtml(i18nText('checkpoint.compare_named',{name}))}"><span class="checkpoint-list-main"><b>${escapeHtml(name)}</b><small>${escapeHtml(scope)} · ${escapeHtml(checkpointHumanTime(cp.created_at))} · ${escapeHtml(wordCount)}${escapeHtml(sourceDeleted)}</small></span></button><button type="button" class="checkpoint-more manuscript-item-more" data-checkpoint-more="${cp.id}" aria-label="${escapeHtml(i18nText('checkpoint.actions_for',{name}))}"><span aria-hidden="true">⋮</span></button></div>`}).join('')||`<div class="manuscript-panel-empty checkpoint-empty-list"><b>${escapeHtml(i18nSource(ws.items.length?'No matching checkpoints':'No checkpoints yet'))}</b><span>${escapeHtml(i18nSource(ws.items.length?'Try a different search or scope.':'Save a version you may want to reference later.'))}</span><button id="checkpointEmptyCreate" type="button">{{i18n:dynamic.html.text.button.f0293fb6}}</button></div>`;
  $$('[data-checkpoint-compare]',box).forEach(button=>button.onclick=()=>openCheckpointSplit(+button.dataset.checkpointCompare).catch(error=>toast(error.message||'Checkpoint could not be compared.')));
  $$('[data-checkpoint-more]',box).forEach(button=>button.onclick=e=>{e.stopPropagation();openCheckpointMenu(e,+button.dataset.checkpointMore)});
  $$('[data-checkpoint-row]',box).forEach(row=>{const cp=ws.items.find(x=>+x.id===+row.dataset.checkpointRow);row.onmouseenter=()=>queueCheckpointPanelHover(cp,row);row.onmouseleave=hideCheckpointPanelHover;row.oncontextmenu=e=>{e.preventDefault();openCheckpointMenu(e,+row.dataset.checkpointRow)}});
  $('#checkpointEmptyCreate')?.addEventListener('click',showCheckpointCreate);
}
function checkpointImageUnitHtmlClient(block){
  const width=Math.max(5,Math.min(100,Number(block?.image_width)||100)),path=String(block?.image_path||''),description=String(block?.image_description||''),footer=String(block?.image_footer||'');
  return `<figure class="checkpoint-image-figure" style="--checkpoint-image-width:${width}%"><img class="checkpoint-image" src="${escapeHtml(projectAssetDisplayUrl(path))}" alt="${escapeHtml(description)}">${footer?`<figcaption>${escapeHtml(footer)}</figcaption>`:''}</figure>`;
}
function checkpointSnapshotUnitsClient(snapshot){
  const units=[];const blockUnit=b=>b.block_type==='image'?{text:'',html:checkpointImageUnitHtmlClient(b),style:'image'}:{text:b.text_content||'',style:b.semantic_style||'body'};
  const appendBlocks=blocks=>{const ordered=[...(blocks||[])].sort((a,b)=>(a.position||0)-(b.position||0)||String(a.id||'').localeCompare(String(b.id||'')));let i=0;while(i<ordered.length){const block=ordered[i];if(block.block_type==='image'&&String(block.image_row_id||'').trim()){const rid=String(block.image_row_id),group=[block];let j=i+1;while(j<ordered.length&&ordered[j].block_type==='image'&&String(ordered[j].image_row_id||'')===rid){group.push(ordered[j]);j++}if(group.length>=2&&group.length<=4){const width=Math.max(5,Math.min(100,Number(group[0].image_width)||100));units.push({text:'',style:'image',html:`<div class="readonly-image-row checkpoint-image-row" style="width:${width}%">${group.map(checkpointImageUnitHtmlClient).join('')}</div>`});i=j;continue}}units.push(blockUnit(block));i++}};
  if(snapshot.scope==='chapter'){units.push({text:snapshot.chapter?.title||i18nSource('Untitled Chapter'),style:'chapter'});appendBlocks(snapshot.blocks||[]);}
  else{const by=new Map();for(const b of snapshot.blocks||[]){if(!by.has(+b.chapter_id))by.set(+b.chapter_id,[]);by.get(+b.chapter_id).push(b)}for(const ch of [...(snapshot.chapters||[])].sort((a,b)=>(a.position||0)-(b.position||0))){units.push({text:ch.title||i18nSource('Untitled Chapter'),style:'chapter'});appendBlocks(by.get(+ch.id)||[])}}
  return units;
}
function checkpointCompareUnitHtml(text,style){const safe=text||'&nbsp;';return `<div class="checkpoint-unit ${escapeHtml(style||'body')}">${safe}</div>`}
function checkpointLiveUnits(){
  const editor=$('#editor'),content=Array.from(editor?.children||[]).filter(el=>el.classList?.contains('manuscript-block-wrap')||el.classList?.contains('manuscript-image-row')||el.classList?.contains('book-chapter-heading'));
  if(state.viewMode==='chapter')return [$('#chapterTitle'),...content.filter(el=>!el.classList.contains('book-chapter-heading'))].filter(Boolean);
  return content;
}
function applyCheckpointLiveHighlights(){clearCheckpointLiveHighlights();const ws=checkpointWorkspace;if(!ws||!ws.showDiff)return;for(const target of ws.changeTargets||[])target.liveEl?.classList.add('checkpoint-live-change')}
function wireCheckpointSplitScrollSync(){
  checkpointSplitScrollCleanup?.();checkpointSplitScrollCleanup=null;const left=$('#documentScroller'),right=$('#splitBody');if(!left||!right||state.split?.type!=='checkpoint')return;let syncing=false;
  const sync=(from,to)=>{if(syncing||!checkpointWorkspace?.linkedScroll)return;const fm=Math.max(1,from.scrollHeight-from.clientHeight),tm=Math.max(0,to.scrollHeight-to.clientHeight);syncing=true;to.scrollTop=(from.scrollTop/fm)*tm;requestAnimationFrame(()=>syncing=false)};
  const fromLeft=()=>sync(left,right),fromRight=()=>sync(right,left);left.addEventListener('scroll',fromLeft,{passive:true});right.addEventListener('scroll',fromRight,{passive:true});checkpointSplitScrollCleanup=()=>{left.removeEventListener('scroll',fromLeft);right.removeEventListener('scroll',fromRight)};
}
function moveCheckpointSplitChange(delta){
  const ws=checkpointWorkspace;if(!ws||!ws.changeCount)return;ws.changeIndex=(ws.changeIndex+delta+ws.changeCount)%ws.changeCount;const target=ws.changeTargets[ws.changeIndex];target?.splitEl?.scrollIntoView({block:'center',behavior:'smooth'});target?.liveEl?.scrollIntoView({block:'center',behavior:'smooth'});$('#checkpointSplitChangeCounter').textContent=`${ws.changeIndex+1} / ${ws.changeCount}`;
}
function checkpointSplitOptions(){return checkpointWorkspace.items.map(other=>({value:String(other.id),label:checkpointOptionLabel(other)}))}
function syncCheckpointContextToolbar(cp){
  const ws=checkpointWorkspace,bar=$('#checkpointCompareToolbar'),select=$('#checkpointSplitSelect');if(!ws||!bar||!select)return;bar.classList.remove('hidden');const comparable=checkpointMatchesCurrentContext(cp);$('.checkpoint-context-label',bar).innerHTML=comparable?`${escapeHtml(i18nSource('Current'))} <span aria-hidden="true">↔</span>`:escapeHtml(i18nSource('Reference'));
  const options=checkpointSplitOptions();select.innerHTML=options.map(o=>`<option value="${escapeHtml(o.value)}" ${+o.value===+cp.id?'selected':''}>${escapeHtml(o.label)}</option>`).join('');
  if(!comparable)ws.showDiff=false;$('#checkpointSplitShowDiff').checked=comparable&&ws.showDiff;$('#checkpointSplitShowDiff').disabled=!comparable;$('#checkpointSplitLinked').checked=ws.linkedScroll;$('#checkpointSplitLinked').disabled=!comparable;$('#checkpointSplitPrev').disabled=!comparable;$('#checkpointSplitNext').disabled=!comparable;$('#checkpointSplitChangeCounter').textContent=comparable?(ws.changeCount?`${Math.max(0,ws.changeIndex+1)} / ${i18nNumber(ws.changeCount)}`:i18nSource('0 changes')):i18nSource('Reference');
  $('#checkpointSplitApply').textContent=i18nSource('Apply…');
}
async function renderCheckpointSplit(){
  const ws=checkpointWorkspace;if(!ws||state.split?.type!=='checkpoint')return;const cp=ws.items.find(x=>+x.id===+state.split.id);if(!cp)return closeSplitView();
  $('#splitTitle').textContent=i18nText('checkpoint.read_only_title',{title:checkpointDisplayName(cp)});$('#splitPane').classList.remove('hidden');openSplitLayout();$('#splitBody').classList.add('checkpoint-split-body');ws.changeIndex=-1;ws.changeTargets=[];clearCheckpointLiveHighlights();
  syncCheckpointContextToolbar(cp);const comparable=checkpointMatchesCurrentContext(cp);
  if(!comparable){
    checkpointSplitScrollCleanup?.();checkpointSplitScrollCleanup=null;const data=await api(`/api/checkpoints/${cp.id}`),units=checkpointSnapshotUnitsClient(data.snapshot),deleted=!cp.source_exists&&cp.scope==='chapter';$('#splitBody').innerHTML=`<article class="checkpoint-split-paper"><div class="checkpoint-readonly-note">${escapeHtml(i18nText('checkpoint.reference_note',{deleted:deleted?i18nSource(' · Source chapter deleted'):''}))}</div>${units.map(u=>`<div class="checkpoint-compare-row">${checkpointCompareUnitHtml(u.html||escapeHtml(u.text),u.style)}</div>`).join('')}</article>`;bindReadOnlyImageDescriptions($('#splitBody'));ws.changeCount=0;$('#checkpointSplitChangeCounter').textContent=i18nSource('Reference');return;
  }
  try{
    const data=await api(`/api/checkpoints/compare/versions?left=${encodeURIComponent(cp.id)}&right=current`),liveUnits=checkpointLiveUnits();let liveIndex=0,changeIndex=-1;const rows=[];ws.changeCount=0;
    for(const row of data.rows){const hasCurrent=row.kind!=='removed';const liveEl=hasCurrent?liveUnits[liveIndex++]||null:null;if(row.kind!=='same'){changeIndex++;ws.changeCount++;}const attr=row.kind!=='same'?` data-change-index="${changeIndex}"`:'';let content=row.left;if(!content&&row.kind!=='same')content=`<span class="checkpoint-current-only">${i18nSource(row.kind==='added'?'Added in Current':'Changed in Current')}</span>`;rows.push(`<div class="checkpoint-compare-row ${row.kind}"${attr}>${checkpointCompareUnitHtml(content,row.left_style)}</div>`);if(row.kind!=='same')ws.changeTargets.push({liveEl,splitIndex:changeIndex});}
    $('#splitBody').innerHTML=`<article class="checkpoint-split-paper checkpoint-comparison ${ws.showDiff?'show-diff':''}"><div class="checkpoint-readonly-note">{{i18n:dynamic.html.text.div.51ac5da9}}</div>${rows.join('')}</article>`;bindReadOnlyImageDescriptions($('#splitBody'));
    ws.changeTargets.forEach(target=>target.splitEl=$(`#splitBody [data-change-index="${target.splitIndex}"]`));$('#checkpointSplitChangeCounter').textContent=ws.changeCount?`0 / ${ws.changeCount}`:i18nSource('No changes');applyCheckpointLiveHighlights();wireCheckpointSplitScrollSync();
  }catch(error){$('#splitBody').innerHTML=`<div class="checkpoint-compare-error">${escapeHtml(error.message||i18nSource('Comparison could not be loaded.'))}</div>`;ws.changeCount=0;$('#checkpointSplitChangeCounter').textContent='';}
}
function scheduleCheckpointSplitRender(){clearTimeout(checkpointSplitRenderTimer);if(state.split?.type==='checkpoint')checkpointSplitRenderTimer=setTimeout(()=>renderCheckpointSplit().catch(error=>console.warn('Checkpoint comparison refresh failed',error)),100)}
async function openCheckpointSplit(checkpointId){
  await flushSave();const ws=await ensureCheckpointWorkspace(true),cp=ws.items.find(x=>+x.id===+checkpointId);if(!cp)throw new Error('Checkpoint no longer exists.');
  closeCheckpointComparisonUI();closeHistoryComparisonUI();state.split={type:'checkpoint',id:+cp.id};ws.selectedId=+cp.id;$('#splitPane').classList.remove('hidden');openSplitLayout();renderCheckpointList();closeCheckpointPanel();await renderCheckpointSplit();
}
function checkpointApplyPlan(cp){
  const name=checkpointDisplayName(cp);
  if(cp.scope==='book')return{body:{},message:i18nText('checkpoint.apply.replace_book',{name}),confirmLabel:i18nSource('Replace Book')};
  if(state.viewMode==='chapter'&&state.chapter){const currentTitle=state.chapter.title||i18nSource('Current Chapter'),same=cp.source_exists&&+cp.current_chapter_id===+state.chapter.id;return{body:{target_chapter_id:+state.chapter.id},message:same?i18nText('checkpoint.apply.replace_same_chapter',{current:currentTitle,name}):i18nText('checkpoint.apply.replace_other_chapter',{current:currentTitle,name,source:checkpointSourceLabel(cp)}),confirmLabel:i18nSource('Replace Chapter')}}
  return{body:{add_as_chapter:true},message:i18nText('checkpoint.apply.add_chapter',{name}),confirmLabel:i18nSource('Add Chapter')};
}
function checkpointConflictSummary(preflight){
  const parts=[];const losses=preflight?.losses||[],renames=preflight?.renames||[],notices=preflight?.notices||[];
  const notes=losses.filter(x=>x.kind==='Note').length,markup=losses.filter(x=>x.kind==='Markup').length,other=losses.length-notes-markup;
  if(notes)parts.push(i18nText(notes===1?'checkpoint.conflict.notes.one':'checkpoint.conflict.notes.many',{count:i18nNumber(notes)}));
  if(markup)parts.push(i18nText(markup===1?'checkpoint.conflict.markup.one':'checkpoint.conflict.markup.many',{count:i18nNumber(markup)}));
  if(other)parts.push(i18nText(other===1?'checkpoint.conflict.annotations.one':'checkpoint.conflict.annotations.many',{count:i18nNumber(other)}));
  for(const item of renames)if(item.kind==='scene')parts.push(i18nText('checkpoint.conflict.scene_rename',{from:item.from,to:item.to}));
  parts.push(...notices.map(i18nSource));return parts.join(' ');
}
async function applyCheckpointFromUI(cp){
  if(!cp)return;const plan=checkpointApplyPlan(cp);await flushSave();const preflight=await api(`/api/checkpoints/${cp.id}/preflight`,{method:'POST',body:JSON.stringify(plan.body)});
  if(!preflight.can_apply){await confirmDialog('Checkpoint cannot be applied',preflight.hard_conflicts?.[0]||'This Checkpoint would create invalid manuscript structure.',{confirmLabel:'Close'});return}
  const conflict=checkpointConflictSummary(preflight),message=preflight.requires_confirmation?`${plan.message} ${conflict}`:plan.message;
  const ok=await confirmDialog(preflight.requires_confirmation?'Review Checkpoint changes':'Apply Checkpoint',message,{confirmLabel:plan.confirmLabel,danger:true});if(!ok)return;
  const viewport=captureManuscriptViewport(),body={...plan.body,confirm_conflicts:!!preflight.requires_confirmation,preflight_token:preflight.preflight_token};
  let result;try{result=await withStructuralUndo(`Apply checkpoint: ${checkpointDisplayName(cp)}`,()=>api(`/api/checkpoints/${cp.id}/apply`,{method:'POST',body:JSON.stringify(body)}),{view:'manuscript',chapterId:state.chapter?.id||null,quietRollbackCodes:['CHECKPOINT_PREFLIGHT_STALE']})}catch(error){if(error?.code==='CHECKPOINT_PREFLIGHT_STALE'){toast('The manuscript changed while you were reviewing this Checkpoint. Checking it again…',3200);return applyCheckpointFromUI(cp)}throw error}closeSplitView();await refresh(false);await ensureCheckpointWorkspace(true);
  if(result.scope==='book')await openWholeBook(false);else if(result.mode==='replace_chapter'&&result.chapter_id)await selectChapter(result.chapter_id,false);else await openWholeBook(false);
  restoreManuscriptViewport(viewport);renderCheckpointList();toast('Checkpoint applied · previous state saved to History',4200);
}
function checkpointEditForm(cp){return `<div class="checkpoint-details-grid"><span>{{i18n:dynamic.html.text.span.ad71cefe}}</span><b>${escapeHtml(checkpointSourceLabel(cp))}${!cp.source_exists&&cp.scope==='chapter'?` · ${i18nSource('source deleted')}`:''}</b><span>{{i18n:dynamic.html.text.span.b3715a4d}}</span><b>${escapeHtml(i18nSource(cp.scope==='book'?'Book':'Chapter'))}</b><span>{{i18n:dynamic.html.text.span.b8e48f23}}</span><b>${escapeHtml(checkpointHumanTime(cp.created_at))}</b><span>{{i18n:dynamic.html.text.span.437519f2}}</span><b>${i18nNumber(cp.word_count||0)}</b></div><div class="field"><label>{{i18n:dynamic.html.text.label.450ca425}} <span class="optional-label">{{i18n:dynamic.html.text.span.196b480c}}</span></label><input id="checkpointEditName" value="${escapeHtml(cp.name||'')}" placeholder="${escapeHtml(cp.default_name||checkpointSourceLabel(cp))}"></div><div class="field"><label>{{i18n:dynamic.html.text.label.40a966a4}} <span class="optional-label">{{i18n:dynamic.html.text.span.196b480c}}</span></label><textarea id="checkpointEditDescription" rows="4" placeholder="{{i18n:dynamic.html.attr.placeholder.19d26b8b}}">${escapeHtml(cp.description||'')}</textarea></div>`}
function openCheckpointDetails(cp){
  if(!cp)return;showModal('Checkpoint Details',checkpointEditForm(cp),'<button id="checkpointEditDelete" class="danger">{{i18n:dynamic.html.text.button.f64ffc00}}</button><button id="checkpointEditSave">{{i18n:dynamic.html.text.button.9faf5fce}}</button>');$('#checkpointEditSave').onclick=async()=>{const updated=await api(`/api/checkpoints/${cp.id}`,{method:'PATCH',body:JSON.stringify({name:$('#checkpointEditName').value,description:$('#checkpointEditDescription').value})});const idx=checkpointWorkspace.items.findIndex(x=>+x.id===+cp.id);if(idx>=0)checkpointWorkspace.items[idx]=updated;closeModal();renderCheckpointList();if(state.split?.type==='checkpoint'&&+state.split.id===+cp.id){$('#splitTitle').textContent=i18nText('checkpoint.read_only_title',{title:checkpointDisplayName(updated)});syncCheckpointContextToolbar(updated)}toast('Checkpoint updated')};$('#checkpointEditDelete').onclick=async()=>{if(!await deleteCheckpointFromUI(cp))return;dismissModalTree()};
}
async function deleteCheckpointFromUI(cp){
  if(!cp||!await confirmDialog('Delete Checkpoint',i18nText('confirm.delete_checkpoint',{name:checkpointDisplayName(cp)}),{confirmLabel:'Delete checkpoint',danger:true}))return false;await api(`/api/checkpoints/${cp.id}`,{method:'DELETE'});if(state.split?.type==='checkpoint'&&+state.split.id===+cp.id)closeSplitView();await reloadCheckpointItems();toast('Checkpoint deleted');return true;
}
function checkpointMenuItems(cp){return[{label:'Compare',action:()=>openCheckpointSplit(cp.id).catch(error=>toast(error.message||'Checkpoint could not be compared.'))},{label:'Details',action:()=>openCheckpointDetails(cp)},{label:'Delete',danger:true,action:()=>deleteCheckpointFromUI(cp)}]}
function openCheckpointMenu(event,id){
  hideCheckpointPanelHover();const cp=checkpointWorkspace?.items.find(x=>+x.id===+id);if(!cp)return;
  if(event.type==='contextmenu')openContextMenu(event.clientX,event.clientY,checkpointMenuItems(cp));
  else{const rect=event.currentTarget.getBoundingClientRect();openContextMenu(rect.left,rect.bottom+4,checkpointMenuItems(cp),{anchorRect:rect})}
}
async function reloadCheckpointItems(preferId=null){const ws=await ensureCheckpointWorkspace(true);if(preferId&&ws.items.some(x=>+x.id===+preferId))ws.selectedId=+preferId;else if(!ws.items.some(x=>+x.id===+ws.selectedId))ws.selectedId=null;renderCheckpointList()}
function showCheckpointCreate(){
  const primary=state.documents.find(d=>d.is_manuscript)||state.documents[0],chapters=visibleChapters(primary?.id),defaultScope=state.viewMode==='chapter'&&state.chapter?'chapter':'book',defaultChapter=state.chapter?.id||chapters[0]?.id||'';
  showModal('New Checkpoint',`<div class="field"><label>{{i18n:dynamic.html.text.label.312c205b}}</label><select id="checkpointCreateScope"><option value="chapter" ${defaultScope==='chapter'?'selected':''}>{{i18n:dynamic.html.text.option.f1d2ba55}}</option><option value="book" ${defaultScope==='book'?'selected':''}>{{i18n:dynamic.html.text.option.0ed89f87}}</option></select></div><div id="checkpointCreateChapterField" class="field"><label>{{i18n:dynamic.html.text.label.1beaac9a}}</label><select id="checkpointCreateChapter">${chapters.map(ch=>`<option value="${ch.id}" ${+ch.id===+defaultChapter?'selected':''}>${escapeHtml(ch.title)}</option>`).join('')}</select></div><div class="field"><label>{{i18n:dynamic.html.text.label.450ca425}} <span class="optional-label">{{i18n:dynamic.html.text.span.196b480c}}</span></label><input id="checkpointCreateName" placeholder="{{i18n:dynamic.html.attr.placeholder.9dc850be}}"></div><div class="field"><label>{{i18n:dynamic.html.text.label.40a966a4}} <span class="optional-label">{{i18n:dynamic.html.text.span.196b480c}}</span></label><textarea id="checkpointCreateDescription" rows="4" placeholder="{{i18n:dynamic.html.attr.placeholder.19d26b8b}}"></textarea></div><div class="field checkpoint-include-field"><label>{{i18n:dynamic.html.text.label.8c20c022}}</label><label class="check-row"><input id="checkpointIncludeScenes" type="checkbox" checked> {{i18n:dynamic.html.text.b.de3f8328}}</label><label class="check-row"><input id="checkpointIncludeMarkup" type="checkbox" checked> {{i18n:checkpoint.include_markup}}</label><label class="check-row"><input id="checkpointIncludeNotes" type="checkbox" checked> {{i18n:checkpoint.include_notes}}</label></div>`,`<button id="checkpointCreateConfirm">{{i18n:dynamic.html.text.button.246922ba}}</button>`);
  const sync=()=>$('#checkpointCreateChapterField').classList.toggle('hidden',$('#checkpointCreateScope').value!=='chapter');sync();$('#checkpointCreateScope').onchange=sync;
  $('#checkpointCreateConfirm').onclick=async()=>{await preserveManuscriptViewport(async()=>{await flushSave();const scope=$('#checkpointCreateScope').value,chapterId=scope==='chapter'?+$('#checkpointCreateChapter').value:null;if(scope==='chapter'&&!chapterId)return toast('Choose a chapter');const created=await api('/api/checkpoints',{method:'POST',body:JSON.stringify({scope,chapter_id:chapterId,name:$('#checkpointCreateName').value,description:$('#checkpointCreateDescription').value,include_scenes:$('#checkpointIncludeScenes').checked,include_markup:$('#checkpointIncludeMarkup').checked,include_notes:$('#checkpointIncludeNotes').checked})});closeModal();await reloadCheckpointItems(created.id);toast(i18nText('checkpoint.created_named',{name:checkpointDisplayName(created)}))})};
}
function closeCheckpointPanel(){hideCheckpointPanelHover();$('#checkpointPanel').classList.add('hidden');$('#checkpointBtn').classList.remove('active');syncCheckpointTooltipStatus()}
async function openCheckpoints(){
  const panel=$('#checkpointPanel');if(!panel.classList.contains('hidden')){closeCheckpointPanel();return}await flushSave();await ensureCheckpointWorkspace(true);closeCutsPanel();closeCommentsPanel();closeMarkupPanel();closeManuscriptImagePanel();closeManuscriptReviewsPanel();panel.classList.remove('hidden');$('#checkpointBtn').classList.add('active');syncCheckpointTooltipStatus();
  const scope=$('#checkpointScopeFilter'),currentOption=scope.querySelector('option[value="current"]');if(currentOption)currentOption.disabled=!(state.viewMode==='chapter'&&state.chapter);scope.value=state.viewMode==='chapter'&&state.chapter?'current':'all';renderCheckpointList();queueMicrotask(()=>$('#checkpointSearch')?.focus());
}
function clearCheckpointPanelSelection(){if(!checkpointWorkspace?.selectedId)return;checkpointWorkspace.selectedId=null;$$('#checkpointList [data-checkpoint-row].active').forEach(row=>row.classList.remove('active'))}
syncCheckpointTooltipStatus();$('#checkpointBtn').onclick=()=>openCheckpoints().catch(error=>{console.error('CHECKPOINT_UI_001',error);toast(error.message||'Checkpoints could not be opened.')});$('#closeCheckpointPanel').onclick=closeCheckpointPanel;$('#checkpointSearch').oninput=refreshCheckpointPanelFilter;$('#checkpointScopeFilter').onchange=refreshCheckpointPanelFilter;$('#checkpointCreateBtn').onclick=showCheckpointCreate;$('#checkpointPanel').addEventListener('pointerdown',event=>{if(!event.target.closest?.('[data-checkpoint-row]'))clearCheckpointPanelSelection()});
$('#checkpointSplitSelect').onchange=async e=>{const id=+e.target.value;if(!id)return;state.split={type:'checkpoint',id};checkpointWorkspace.selectedId=id;renderCheckpointList();await renderCheckpointSplit()};
$('#checkpointSplitShowDiff').onchange=e=>{checkpointWorkspace.showDiff=e.target.checked;renderCheckpointSplit().catch(console.warn)};$('#checkpointSplitLinked').onchange=e=>{checkpointWorkspace.linkedScroll=e.target.checked};$('#checkpointSplitPrev').onclick=()=>moveCheckpointSplitChange(-1);$('#checkpointSplitNext').onclick=()=>moveCheckpointSplitChange(1);$('#checkpointSplitApply').onclick=()=>{const cp=checkpointWorkspace?.items.find(x=>+x.id===+state.split?.id);applyCheckpointFromUI(cp)};


