// -------------------- universal linking --------------------
function uiEntityType(type){if(type==='review_answer'||type==='review_general_feedback')return i18nText('reviews.feedback');const known=({document:'Document',url:'Web',task:'Task',milestone:'Milestone',canvas:'Canvas',node:'Canvas Card',map:'Map',map_item:'Map item',map_line:'Map line',chapter:'Chapter',scene:'Scene',annotation:'Annotation',manuscript_range:'Manuscript passage',review_highlight:'Review highlight',review_comment:'Review comment',review_suggestion:'Review suggestion'})[type];return known?i18nSource(known):String(type||'').replaceAll('_',' ')}
function renderReferenceRows(refs,{readOnly=false,manager=false}={}){return refs.length?refs.map(r=>{const label=escapeHtml(r.target_label||uiEntityType(r.target_type)),type=escapeHtml(uiEntityType(r.target_type)),missing=!!r.target_missing,locked=readOnly||!!r.read_only;if(manager){const openAttr=missing?'':r.target_type==='url'?`data-reference-url="${escapeHtml(r.target_id)}"`:`data-open-ref="${r.id}"`,missingText=missing?'<span class="small-muted reference-manager-missing">{{i18n:dynamic.html.text.span.b488ee74}}</span>':'';return `<div class="link-row reference-manager-row${missing?' is-missing':''}" ${openAttr} role="${missing?'group':'button'}" ${missing?'':`tabindex="0"`}><span><b>${label}</b><br><small class="small-muted">${type}</small></span><div class="reference-manager-actions">${missingText}${locked?'':`<button class="bare-button" data-delete-ref="${r.id}" title="{{i18n:dynamic.html.attr.title.9b839def}}" aria-label="{{i18n:dynamic.html.attr.aria.label.15f5afb5}}">×</button>`}</div></div>`}const target=r.target_type==='url'?`<a href="${escapeHtml(r.target_id)}" target="_blank" rel="noopener noreferrer"><b>${label}</b></a>`:`<button type="button" class="reference-target" data-open-ref="${r.id}"><b>${label}</b></button>`;return `<div class="link-row${r.structural_binding?' structural-binding-row':''}"><span>${target}<br><small class="small-muted">${type}${r.structural_binding?` · ${i18nSource('Structural link')}`:''}</small></span>${locked?'':`<button class="bare-button" data-delete-ref="${r.id}">×</button>`}</div>`}).join(''):'<span class="small-muted">{{i18n:dynamic.html.text.span.113bd530}}</span>';}
function structuralNodeReference(node){if(!isDevelopmentalStructureCard(node))return null;const scene=state.currentCanvas?.developmental_scope==='scenes',targetType=scene?'scene':'chapter',targetId=scene?node.scene_id:node.chapter_id;if(!targetId)return null;return{id:`structural-${targetType}-${targetId}-${node.id}`,target_type:targetType,target_id:String(targetId),target_label:node.title||uiEntityType(targetType),read_only:true,structural_binding:true};}
async function openReferenceManager(sourceType,sourceId,{title='{{i18n:dynamic.html.attr.title.5bae2c43}}',subtitle='',readOnly=false,onChanged=null}={}){
  showModal(title,`${subtitle?`<p class="small-muted reference-manager-context">${escapeHtml(subtitle)}</p>`:''}<div id="referenceManagerList" class="link-list"><span class="small-muted">{{i18n:dynamic.html.text.span.e28f48b0}}</span></div>${readOnly?'':`<button id="referenceManagerAdd" class="full-soft reference-manager-add">{{i18n:dynamic.html.text.button.d45ca59d}}</button>`}`,'<button id="referenceManagerClose">{{i18n:dynamic.html.text.button.972112a0}}</button>');
  const refresh=async()=>{const refs=await api(`/api/references?source_type=${encodeURIComponent(sourceType)}&source_id=${encodeURIComponent(sourceId)}`),box=$('#referenceManagerList');if(!box)return;box.innerHTML=renderReferenceRows(refs,{readOnly,manager:true});wireReferenceDeletes(refs,async()=>{await refresh();await onChanged?.()})};
  $('#referenceManagerClose').onclick=()=>closeModal();
  if(!readOnly)$('#referenceManagerAdd').onclick=()=>openEntityLinker(sourceType,String(sourceId),async()=>{await refresh();await onChanged?.()});
  await refresh();
}
let manuscriptReferenceHighlightTimer=null;
function clearManuscriptReferenceHighlight(){clearTimeout(manuscriptReferenceHighlightTimer);manuscriptReferenceHighlightTimer=null;if(globalThis.CSS?.highlights)CSS.highlights.delete('breadmaker-linked-range');$('#breadmaker-linked-range-style')?.remove()}
function paintManuscriptReferenceHighlight(ref){
  clearManuscriptReferenceHighlight();if(!globalThis.CSS?.highlights)return;const wraps=[...$('#editor').querySelectorAll('.manuscript-block-wrap')],startIndex=wraps.findIndex(w=>String(w.dataset.blockId)===String(ref.start_block_id)),endIndex=wraps.findIndex(w=>String(w.dataset.blockId)===String(ref.end_block_id||ref.start_block_id));if(startIndex<0||endIndex<0)return;const lo=Math.min(startIndex,endIndex),hi=Math.max(startIndex,endIndex),ranges=[];
  for(let i=lo;i<=hi;i++){const prose=wraps[i].querySelector('.manuscript-block');if(!prose)continue;const text=prose.innerText||'',start=i===startIndex?+ref.start_offset||0:0,end=i===endIndex?Math.min(text.length,+ref.end_offset||0):text.length,range=rangeFromOffsets(prose,Math.max(0,Math.min(start,text.length)),Math.max(0,Math.min(end,text.length)));if(range&&!range.collapsed)ranges.push(range)}
  if(!ranges.length)return;CSS.highlights.set('breadmaker-linked-range',new Highlight(...ranges));const style=document.createElement('style');style.id='breadmaker-linked-range-style';style.textContent='::highlight(breadmaker-linked-range){background:var(--reference-hit);color:inherit;outline:1px solid var(--reference-hit-line)}';document.head.appendChild(style);manuscriptReferenceHighlightTimer=setTimeout(clearManuscriptReferenceHighlight,2600);
}
async function openManuscriptRangeReference(ref){
  const startChapter=entityId(ref.target_start_chapter_id)||0,endChapter=entityId(ref.target_end_chapter_id)||startChapter;if(ref.target_missing||!startChapter)return toast('Linked manuscript passage no longer exists.');setView('manuscript');
  if(endChapter&&endChapter!==startChapter){const ch=visibleChapterById(startChapter);if(ch?.tab_id)state.activeDocumentId=entityId(ch.tab_id);await openWholeBook(false,false)}else await selectChapter(startChapter,false,false);
  const wrap=$(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(ref.start_block_id))}"]`);if(!wrap)return toast('Linked manuscript passage no longer exists.');revealOutlineTarget(wrap);wrap.scrollIntoView({behavior:'auto',block:'center'});paintManuscriptReferenceHighlight(ref);
}
async function openReferenceTarget(ref){
  if(!ref||ref.target_type==='url')return;const id=entityId(ref.target_id);dismissModalTree();
  if(ref.target_type==='task'){setView('tasks');await loadTasks();return openTaskDetails(id)}
  if(['review_highlight','review_comment','review_suggestion','review_answer','review_general_feedback'].includes(ref.target_type)){const kind=ref.target_type==='review_general_feedback'?'general':ref.target_type.replace('review_',''),ctx=await api(`/api/review-items/${encodeURIComponent(kind)}/${encodeURIComponent(ref.target_id)}`);setView('reviews');await refresh(false);selectReview(ctx.review_id);setTimeout(()=>{const item=$(`[data-review-feedback-id="${CSS.escape(String(ref.target_id))}"]`);item?.scrollIntoView({block:'center',behavior:'auto'});item?.classList.add('review-feedback-focus');setTimeout(()=>item?.classList.remove('review-feedback-focus'),1800)},100);return}
  if(ref.target_type==='manuscript_range')return openManuscriptRangeReference(ref)
  if(ref.target_type==='chapter'){setView('manuscript');return selectChapter(id,false)}
  if(ref.target_type==='scene'){setView('manuscript');return goScene(id)}
  if(ref.target_type==='document'){setView('manuscript');state.activeDocumentId=id;renderDocuments();renderChapterTree();const chapter=visibleChapters(id)[0];if(chapter)return selectChapter(chapter.id,false);return}
  if(ref.target_type==='canvas'){setView('canvas');return selectCanvas(id)}
  if(ref.target_type==='map'){setView('maps');return selectMap(id)}
  if(ref.target_type==='map_item'){const mapId=await quickOpenMapItemOwner(id);if(mapId)return openMapAtItem(mapId,id);return toast('Linked Map item no longer exists.')}
  if(ref.target_type==='node'){const canvasId=await quickOpenCanvasOwner(id);if(canvasId){setView('canvas');await selectCanvas(canvasId);state.selectedNodes=new Set([id]);renderNodeSelection();openNodeInspector(id);return}return toast('Linked Canvas Card no longer exists.')}
  toast('This link cannot be opened from here yet.');
}
function wireReferenceDeletes(refs,onDone){for(const ref of refs){$$(`[data-open-ref="${ref.id}"]`).forEach(target=>{target.onclick=e=>{if(e.target.closest('[data-delete-ref]'))return;openReferenceTarget(ref)};target.onkeydown=e=>{if((e.key==='Enter'||e.key===' ')&&!e.target.closest('[data-delete-ref]')){e.preventDefault();openReferenceTarget(ref)}}});$$(`[data-delete-ref="${ref.id}"]`).forEach(button=>button.onclick=async e=>{e?.preventDefault();e?.stopPropagation();await withStructuralUndo('Remove link',()=>api(`/api/references/${ref.id}`,{method:'DELETE'}),{view:currentView(),canvasId:state.currentCanvas?.id,mapId:state.currentMap?.id});onDone?.()})}$$('[data-reference-url]').forEach(row=>{row.onclick=e=>{if(e.target.closest('[data-delete-ref]'))return;window.open(row.dataset.referenceUrl,'_blank','noopener,noreferrer')};row.onkeydown=e=>{if((e.key==='Enter'||e.key===' ')&&!e.target.closest('[data-delete-ref]')){e.preventDefault();window.open(row.dataset.referenceUrl,'_blank','noopener,noreferrer')}}})}
async function linkEntityToSelection(sourceType,sourceId,items){await ensureSavedSelection(items);const first=items[0],last=items.at(-1);return api('/api/references',{method:'POST',body:JSON.stringify({source_type:sourceType,source_id:String(sourceId),target_type:'manuscript_range',target_id:'range',start_block_id:first.block_id,end_block_id:last.block_id,start_offset:first.start_offset,end_offset:last.end_offset,excerpt:items.map(i=>i.selected_text).join(' … ')})});}

async function availableLinkFamilies({selection=false,allowTaskCreation=false}={}){
  const openTasks=(await getTasks()).filter(task=>task.status==='open');
  const families=[];
  if(state.canvases.length)families.push({value:'canvas_family',label:'Canvas'});
  if(state.maps.length)families.push({value:'map_family',label:'Map'});
  if(openTasks.length||allowTaskCreation)families.push({value:'task',label:'Task'});
  if(!selection){
    if(state.scenes.length)families.push({value:'scene',label:'Scene'});
    if(visibleChapters().length)families.push({value:'chapter',label:'Chapter'});
    if(state.documents.length)families.push({value:'document',label:'Document'});
  }
  return {families,openTasks};
}
function linkerTypeOptions(families){return families.map(item=>`<option value="${escapeHtml(item.value)}">${escapeHtml(i18nSource(item.label))}</option>`).join('')}
function compoundLinkOption(type,id,label,selected=false){return `<option value="${escapeHtml(type)}:${escapeHtml(String(id))}" ${selected?'selected':''}>${escapeHtml(label)}</option>`}
function compoundLinkTarget(){
  const select=$('#compoundLinkTarget');if(!select?.value)return null;
  const split=select.value.indexOf(':');if(split<1)return null;
  return {type:select.value.slice(0,split),id:select.value.slice(split+1),label:select.selectedOptions[0]?.textContent?.trim()||'item'};
}
async function canvasLinkChooserHtml(){
  const details=await Promise.all(state.canvases.map(canvas=>api(`/api/canvases/${canvas.id}`).then(detail=>({canvas,detail}))));
  const groups=details.map(({canvas,detail})=>{const nodes=(detail.nodes||[]).filter(node=>!['routing','label','figure'].includes(node.node_type));return `<optgroup label="${escapeHtml(canvas.name)}">${compoundLinkOption('canvas',canvas.id,i18nText('links.canvas_named',{name:canvas.name}))}${nodes.map(node=>compoundLinkOption('node',node.id,i18nText('links.card_named',{name:node.title||node.body||i18nSource('Untitled Card')}))).join('')}</optgroup>`});
  return `<div class="field"><label>{{i18n:dynamic.html.text.label.a1f867f1}}</label><select id="compoundLinkTarget">${groups.join('')}</select></div>`;
}
async function mapLinkChooserHtml(){
  const details=await Promise.all(state.maps.map(map=>api(`/api/maps/${map.id}`).then(detail=>({map,detail}))));
  const groups=details.map(({map,detail})=>{const items=(detail.lines||[]).flatMap(line=>(line.items||[]).map(item=>({item,line})));return `<optgroup label="${escapeHtml(map.name)}">${compoundLinkOption('map',map.id,i18nText('links.map_named',{name:map.name}))}${items.map(({item,line})=>compoundLinkOption('map_item',item.id,i18nText('links.item_named',{line:line.name,name:item.title||i18nSource('Untitled item')}))).join('')}</optgroup>`});
  return `<div class="field"><label>{{i18n:dynamic.html.text.label.803f3150}}</label><select id="compoundLinkTarget">${groups.join('')}</select></div>`;
}
async function linkerChooserHtml(type,{openTasks=null}={}){
  if(type==='canvas_family')return canvasLinkChooserHtml();
  if(type==='map_family')return mapLinkChooserHtml();
  if(type==='task'){
    const tasks=openTasks||((await getTasks()).filter(task=>task.status==='open'));
    return `<div class="field"><label>{{i18n:dynamic.html.text.label.6033c0db}}</label>${tasks.length?`<select id="simpleLinkTarget">${tasks.map(task=>`<option value="${task.id}">${escapeHtml(task.title)}</option>`).join('')}</select>`:'<p class="small-muted">{{i18n:dynamic.html.text.p.547c45c4}}</p>'}<button id="createLinkedTask" type="button" class="full-soft" style="margin-top:8px">{{i18n:dynamic.html.text.button.1e391cfb}}</button></div>`;
  }
  const items={scene:state.scenes,chapter:visibleChapters(),document:state.documents}[type];
  if(items?.length)return `<div class="field"><label>${escapeHtml(i18nSource(type==='document'?'Document':type==='scene'?'Scene':'Chapter'))}</label><select id="simpleLinkTarget">${items.map(item=>`<option value="${item.id}">${escapeHtml(item.name||item.title)}</option>`).join('')}</select></div>`;
  return '<p class="small-muted">{{i18n:dynamic.html.text.p.2f0c0a57}}</p>';
}
function selectedLinkTarget(type){
  if(type==='canvas_family'||type==='map_family')return compoundLinkTarget();
  const select=$('#simpleLinkTarget');if(!select?.value)return null;
  return {type,id:select.value,label:select.selectedOptions[0]?.textContent?.trim()||uiEntityType(type)};
}
async function openLinkerForSelection(items){
  await ensureSavedSelection(items);
  const available=await availableLinkFamilies({selection:true});
  if(!available.families.length)return showModal('Link Manuscript Selection','<p class="small-muted">{{i18n:dynamic.html.text.p.7457ad20}}</p>','');
  showModal('Link Manuscript Selection',`<p class="small-muted">{{i18n:dynamic.html.text.p.5b96e5bc}}</p><div class="field"><label>{{i18n:dynamic.html.text.label.7579a345}}</label><select id="selectionLinkType">${linkerTypeOptions(available.families)}</select></div><div id="selectionLinkChooser"></div>`,'<button id="saveSelectionLink">{{i18n:dynamic.html.text.button.d664ea15}}</button>');
  let renderSeq=0;const render=async()=>{const seq=++renderSeq,type=$('#selectionLinkType').value,html=await linkerChooserHtml(type,{openTasks:available.openTasks});if(seq!==renderSeq)return;$('#selectionLinkChooser').innerHTML=html};
  $('#selectionLinkType').onchange=render;await render();
  $('#saveSelectionLink').onclick=async()=>{const family=$('#selectionLinkType').value,target=selectedLinkTarget(family);if(!target)return;await withStructuralUndo('Link manuscript selection',()=>linkEntityToSelection(target.type,String(target.id),items),{view:'manuscript',chapterId:items[0]?.chapter_id});closeModal();toast(i18nText('links.linked_to',{label:target.label}))};
}

async function openEntityLinker(sourceType,sourceId,onDone=null){
  const available=await availableLinkFamilies({selection:false,allowTaskCreation:sourceType==='node'||sourceType==='map_item'});
  if(sourceType==='scene')available.families=available.families.filter(item=>item.value!=='scene');
  if(!available.families.length)return showNestedModal('Add Link','<p class="small-muted">{{i18n:dynamic.html.text.p.cabb8104}}</p>','<button id="closeEmptyLinker">{{i18n:dynamic.html.text.button.972112a0}}</button>'),setTimeout(()=>{$('#closeEmptyLinker')?.addEventListener('click',()=>closeModal())},0);
  showNestedModal('Add Link',`<div class="field"><label>{{i18n:dynamic.html.text.label.2c4f823c}}</label><select id="entityLinkType">${linkerTypeOptions(available.families)}</select></div><div id="entityLinkChooser"></div>`,'<button id="saveEntityLink">{{i18n:dynamic.html.text.button.d664ea15}}</button>');
  const createTaskFromChooser=()=>{
    showNestedModal('New Task','<div class="field"><label>{{i18n:dynamic.html.text.label.450ca425}}</label><input id="newLinkedTaskTitle" placeholder="{{i18n:dynamic.html.attr.placeholder.053ce8e8}}" autofocus></div>','<button id="cancelLinkedTask">{{i18n:dynamic.html.text.button.92113f72}}</button><button id="saveLinkedTask">{{i18n:dynamic.html.text.button.742354ce}}</button>');
    $('#cancelLinkedTask').onclick=()=>closeModal();
    const save=async()=>{const title=$('#newLinkedTaskTitle').value.trim();if(!title)return $('#newLinkedTaskTitle').focus();let created;await withStructuralUndo(`Create task: ${title}`,async()=>{created=await api('/api/tasks',{method:'POST',body:JSON.stringify({title,status:'open'})});await api('/api/references',{method:'POST',body:JSON.stringify({source_type:sourceType,source_id:String(sourceId),target_type:'task',target_id:String(created.id)})})},{view:currentView(),canvasId:state.currentCanvas?.id,mapId:state.currentMap?.id});dismissModalTree();toast('Task created and linked');onDone?.()};
    $('#saveLinkedTask').onclick=save;$('#newLinkedTaskTitle').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();save()}};
  };
  let renderSeq=0;const render=async()=>{const seq=++renderSeq,type=$('#entityLinkType').value,html=await linkerChooserHtml(type,{openTasks:available.openTasks});if(seq!==renderSeq)return;$('#entityLinkChooser').innerHTML=html;if(type==='task')$('#createLinkedTask')?.addEventListener('click',createTaskFromChooser)};
  $('#entityLinkType').onchange=render;await render();
  $('#saveEntityLink').onclick=async()=>{const family=$('#entityLinkType').value,target=selectedLinkTarget(family);if(!target)return;await withStructuralUndo('Add link',()=>api('/api/references',{method:'POST',body:JSON.stringify({source_type:sourceType,source_id:String(sourceId),target_type:target.type,target_id:String(target.id)})}),{view:currentView(),canvasId:state.currentCanvas?.id,mapId:state.currentMap?.id});closeModal();toast(i18nText('links.linked_to',{label:target.label}));onDone?.()};
}

function selectionCutHtml(items){
  return items.map(item=>{
    const prose=item.wrap.querySelector('.manuscript-block'),range=prose?rangeFromOffsets(prose,item.start_offset,item.end_offset):null;
    if(!range)return `<p>${escapeHtml(item.selected_text||'')}</p>`;
    const holder=document.createElement('div');holder.appendChild(range.cloneContents());
    return `<p>${holder.innerHTML||escapeHtml(item.selected_text||'')}</p>`;
  }).join('');
}
async function showCutsOnboardingOnce(){
  if(state.cutsOnboardingSeen===null){
    try{state.cutsOnboardingSeen=(await api('/api/ui-state/cuts-onboarding-seen')).value===true}catch{state.cutsOnboardingSeen=false}
  }
  if(state.cutsOnboardingSeen)return;
  state.cutsOnboardingSeen=true;
  try{await api('/api/ui-state/cuts-onboarding-seen',{method:'PUT',body:JSON.stringify({value:true})})}catch(error){console.warn('Could not persist Cuts onboarding state',error)}
  showModal('Safely moved to Cuts',`<p>${escapeHtml(i18nSource('Your text is safely stored in Cuts. Use the Cuts button in the Manuscript toolbar to review it, add a note, or insert it somewhere else. Undo puts the text back in the manuscript and removes this Cut.'))}</p>`,'<button id="cutsOnboardingDone">{{i18n:dynamic.html.text.button.4004892e}}</button>');
  $('#cutsOnboardingDone').onclick=()=>closeModal();
}
async function cutSelection(items){
  await ensureSavedSelection(items);
  const chapters=[...new Set(items.map(i=>i.chapter_id))],sceneIds=[...new Set(items.map(i=>i.scene_id))],contentHtml=selectionCutHtml(items);
  const sourceSceneId=chapters.length===1&&sceneIds.length===1&&sceneIds[0]!=null?sceneIds[0]:null;
  await withStructuralUndo('Move selection to Cuts',async()=>{
    await api('/api/cuts',{method:'POST',body:JSON.stringify({title:chapters.length===1?i18nText('cuts.from_chapter',{title:visibleChapterById(chapters[0])?.title||i18nSource('Chapter')}):i18nText('cuts.across_chapters'),content_html:contentHtml,source_chapter_id:chapters.length===1?chapters[0]:null,source_scene_id:sourceSceneId})});
    [...items].reverse().forEach(item=>deleteTextRange(item.wrap.querySelector('.manuscript-block'),item.start_offset,item.end_offset));
    chapters.forEach(markDirty);await flushSave();
  },{view:'manuscript',chapterId:chapters[0],documentId:state.activeDocumentId,manuscriptViewMode:state.viewMode});
  await notifyCutsChanged();toast('Moved to Cuts');await showCutsOnboardingOnce();
}
function deleteTextRange(root,start,end){const range=rangeFromOffsets(root,start,end);if(range){range.deleteContents();root.normalize();}}

