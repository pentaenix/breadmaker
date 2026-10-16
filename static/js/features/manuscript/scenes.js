// -------------------- selection actions / scenes --------------------
// Preserve the native spelling/grammar menu for ordinary manuscript right-clicks.
// When text is actively selected, Breadmaker owns the menu so structural text styles
// and selection actions are available without losing the saved selection.
function manuscriptStyleMenuItems(headerChapterId=null){
  if(headerChapterId)return [{label:'Body',testId:'manuscript-style-body',action:()=>convertChapterHeaderToStyle(headerChapterId,'body')},{label:'H1  Chapter',testId:'manuscript-style-chapter',disabled:true,disabledReason:'This is already a Chapter header.'},{label:'H2  Section',testId:'manuscript-style-heading2',action:()=>convertChapterHeaderToStyle(headerChapterId,'heading2')},{label:'H3  Subsection',testId:'manuscript-style-heading3',action:()=>convertChapterHeaderToStyle(headerChapterId,'heading3')},{label:'H4  Minor section',testId:'manuscript-style-heading4',action:()=>convertChapterHeaderToStyle(headerChapterId,'heading4')}];
  return [{label:'Body',testId:'manuscript-style-body',action:()=>applyWritingStyle('body')},{label:'H1  Chapter',testId:'manuscript-style-chapter',action:()=>applyWritingStyle('chapter')},{label:'H2  Section',testId:'manuscript-style-heading2',action:()=>applyWritingStyle('heading2')},{label:'H3  Subsection',testId:'manuscript-style-heading3',action:()=>applyWritingStyle('heading3')},{label:'H4  Minor section',testId:'manuscript-style-heading4',action:()=>applyWritingStyle('heading4')}]
}
function selectionMoreItems(items,headerChapterId=null){
  if(headerChapterId)return [
    {label:'Link…',action:()=>openEntityLinker('chapter',String(headerChapterId))},
    {separator:true},
    {label:'Task',action:()=>taskFromChapter(headerChapterId)},
    {label:'Map',action:()=>mapFromChapter(headerChapterId)}
  ];
  const scenePlan=sceneSelectionPlan(items),offerRemove=scenePlan.kind==='extend'&&scenePlan.canRemove;return [
    ...(offerRemove?[{label:'Remove from Scene',action:()=>createSceneFromSelection(items,{forceRemove:true})},{separator:true}]:[]),
    {label:'Link…',action:()=>openLinkerForSelection(items)},
    {separator:true},
    {label:'Task',action:()=>taskFromSelection(items)},
    {label:'Map',action:()=>mapFromSelection(items)}
  ]
}
function selectionContextItems(items){const scenePlan=sceneSelectionPlan(items),bookmarked=bookmarkRangeMatches(items);return [
  {label:'Link…',action:()=>openLinkerForSelection(items)},
  {separator:true},
  {label:scenePlan.label||'Scene',disabled:!!scenePlan.disabled,disabledReason:scenePlan.reason||'',action:()=>createSceneFromSelection(items)},
  {label:'Move to Cuts',action:()=>cutSelection(items)},
  {label:i18nSource(bookmarked?'Remove bookmark':'Bookmark selection'),action:()=>toggleTextBookmark(items)},
  {label:'⋮',children:selectionMoreItems(items)}
]}
$('#editor').addEventListener('contextmenu',event=>{
  const items=selectionItems();hideSelectionToolbar();if(!items.length)return;
  try{state.lastManuscriptRange=getSelection().getRangeAt(0).cloneRange()}catch{}
  event.preventDefault();openContextMenu(event.clientX,event.clientY,[{label:'Aa  Text style',children:manuscriptStyleMenuItems()},{separator:true},...selectionContextItems(items)]);
});

function rememberMarkupMarkerUse(id){
  const markerId=+id||null;state.lastUsedMarkupMarkerId=markerId;
  try{if(markerId)localStorage.setItem('breadmaker-last-markup-marker',String(markerId));else localStorage.removeItem('breadmaker-last-markup-marker')}catch{}
}
function markerPickerOrder(){
  const markers=[...state.markers].sort((a,b)=>markerCreatedSortValue(b)-markerCreatedSortValue(a)||(+b.id)-(+a.id));
  const last=+state.lastUsedMarkupMarkerId||0;if(!last)return markers;
  const index=markers.findIndex(marker=>+marker.id===last);if(index<=0)return markers;
  return [markers[index],...markers.slice(0,index),...markers.slice(index+1)];
}
function positionSelectionHighlightPicker(picker,anchorRect){
  const margin=8,gap=6;picker.style.visibility='hidden';picker.classList.remove('hidden');picker.style.left='0px';picker.style.top='0px';
  const box=picker.getBoundingClientRect(),w=box.width||210,h=box.height||80;let left=(anchorRect?.left||margin)+(anchorRect?.width||0)/2-w/2;
  left=Math.max(margin,Math.min(left,innerWidth-w-margin));let top=(anchorRect?.bottom||80)+gap;if(top+h>innerHeight-margin)top=Math.max(margin,(anchorRect?.top||80)-h-gap);
  picker.style.left=`${Math.round(left)}px`;picker.style.top=`${Math.round(top)}px`;picker.style.visibility='';
}
function openSelectionHighlightPicker(items,anchorRect){
  const picker=$('#selectionHighlightPicker'),list=$('#selectionHighlightMarkerList');if(!picker||!list)return;
  const markers=markerPickerOrder();
  list.innerHTML=markers.length?markers.map(marker=>`<button type="button" class="selection-highlight-marker" data-selection-highlight-marker="${marker.id}" role="menuitem"><span class="color-chip" style="background:${escapeHtml(marker.color)}"></span><span>${escapeHtml(marker.name)}</span></button>`).join(''):'<div class="selection-highlight-empty">{{i18n:dynamic.html.text.div.859c2900}}</div>';
  $$('[data-selection-highlight-marker]',list).forEach(button=>{button.onpointerdown=e=>e.preventDefault();button.onclick=async()=>{const id=+button.dataset.selectionHighlightMarker,marker=state.markers.find(m=>+m.id===id);if(!marker)return;hideSelectionHighlightPicker();rememberMarkupMarkerUse(id);await applyAnnotation('highlight',items,id,'');renderMarkers();toast(i18nText('markup.highlighted_with',{name:marker.name}))}});
  const add=$('#selectionHighlightNewMarker');add.onpointerdown=e=>e.preventDefault();add.onclick=()=>{hideSelectionHighlightPicker();newMarkerDialog({items})};
  positionSelectionHighlightPicker(picker,anchorRect);queueMicrotask(()=>restoreSelectionToolbarRange());
}

function runInlineFormat(command){if(!restoreSelectionToolbarRange())return;const items=selectionItems(),wrap=activeManuscriptWrap();const chapterIds=[...new Set(items.length?items.map(i=>i.chapter_id):(wrap?[+wrap.dataset.chapterId]:[]))];if(!chapterIds.length)return;recordUndo(chapterIds,true);document.execCommand(command,false,null);chapterIds.forEach(markDirty);hideSelectionToolbar()}
function runSelectionAction(action){restoreSelectionToolbarRange();const items=state.selectionToolbarItems?.length?state.selectionToolbarItems:selectionItems(),headerChapterId=state.selectionToolbarHeaderChapterId||selectedChapterHeader(state.selectionToolbarRange||state.lastManuscriptRange,items)?.chapter_id||null;if(!items.length&&!headerChapterId)return;const trigger=$(`#selectionToolbar [data-selection-action="${action}"]`),triggerRect=trigger?.getBoundingClientRect?.();hideSelectionToolbar();hideSelectionHighlightPicker();if(action==='style'){openContextMenu(triggerRect?.left||8,(triggerRect?.bottom||80)+6,manuscriptStyleMenuItems(headerChapterId),{anchorRect:triggerRect});}else if(action==='highlight')openSelectionHighlightPicker(items,triggerRect);else if(action==='scene')createSceneFromSelection(items);else if(action==='cuts')cutSelection(items);else if(action==='task')taskFromSelection(items);else if(action==='map')mapFromSelection(items);else if(action==='comment')commentSelection(items);else if(action==='bookmark'){if(headerChapterId){const chapter=visibleChapterById(headerChapterId);if(chapter)toggleChapterBookmark(chapter)}else toggleTextBookmark(items);}else if(action==='more'){openContextMenu(triggerRect?.left||8,(triggerRect?.bottom||80)+6,selectionMoreItems(items,headerChapterId),{anchorRect:triggerRect})}}
function beginSelectionToolbarInteraction(event){const selection=getSelection();if(selection?.rangeCount&&!selection.isCollapsed){const range=selection.getRangeAt(0);if(rangeTouchesManuscriptSurface(range)){try{state.lastManuscriptRange=range.cloneRange()}catch{}}}state.selectionToolbarInteracting=true;event.preventDefault()}
function endSelectionToolbarInteraction(){setTimeout(()=>{state.selectionToolbarInteracting=false},0)}
$$('[data-selection-command]').forEach(button=>{button.addEventListener('pointerdown',beginSelectionToolbarInteraction);button.onclick=()=>{runInlineFormat(button.dataset.selectionCommand);endSelectionToolbarInteraction()}});
$$('[data-selection-action]').forEach(button=>{button.addEventListener('pointerdown',beginSelectionToolbarInteraction);button.onclick=()=>{runSelectionAction(button.dataset.selectionAction);endSelectionToolbarInteraction()}});
$('#selectionToolbar').addEventListener('pointerdown',e=>e.stopPropagation());
$('#selectionHighlightPicker')?.addEventListener('pointerdown',e=>e.stopPropagation());
document.addEventListener('pointerdown',e=>{if(!e.target.closest?.('#selectionHighlightPicker,[data-selection-action="highlight"]'))hideSelectionHighlightPicker()});

async function setBlockStyle(wrap,style){
  style=['heading2','heading3','heading4'].includes(style)?style:'body';recordUndo([+wrap.dataset.chapterId],true);if(style==='body')state.manuscriptCollapsedHeadings.delete(wrap.dataset.blockId);wrap.dataset.style=style;const prose=wrap.querySelector('.manuscript-block');prose.dataset.semanticStyle=style;applyOutlineVisibility();persistManuscriptOutlineState();markDirty(+wrap.dataset.chapterId);
  const existing=state.chapterOutlines.find(item=>String(item.id)===String(wrap.dataset.blockId));if(style==='body'){state.chapterOutlines=state.chapterOutlines.filter(item=>String(item.id)!==String(wrap.dataset.blockId))}else if(existing){existing.semantic_style=style;existing.text_content=prose.innerText}else state.chapterOutlines.push({id:wrap.dataset.blockId,chapter_id:+wrap.dataset.chapterId,position:wrapsForChapter(+wrap.dataset.chapterId).indexOf(wrap),semantic_style:style,text_content:prose.innerText});renderChapterTree();
}

function completeSceneBlockIds(items){
  if(!items?.length)return [];
  const wraps=$$('.manuscript-block-wrap'),first=items[0],last=items.at(-1),a=wraps.indexOf(first.wrap),b=wraps.indexOf(last.wrap);
  if(a<0||b<0)return [];
  return [...new Set(wraps.slice(Math.min(a,b),Math.max(a,b)+1).filter(w=>w.dataset.blockId&&!w.dataset.blockId.startsWith('tmp_')).map(w=>w.dataset.blockId))];
}
function sceneSelectionPlan(items){
  if(!items?.length)return {kind:'none',label:'Scene'};
  const wraps=$$('.manuscript-block-wrap'),first=items[0],last=items.at(-1),a=wraps.indexOf(first.wrap),b=wraps.indexOf(last.wrap);if(a<0||b<0)return {kind:'none',label:'Scene'};
  const selected=wraps.slice(Math.min(a,b),Math.max(a,b)+1).filter(w=>!w.classList.contains('scene-gap-hidden'));
  const sceneIds=[...new Set(selected.map(w=>w.dataset.sceneId?+w.dataset.sceneId:null).filter(Boolean))];
  if(sceneIds.length>1)return {kind:'conflict',label:'Scene',disabled:true,reason:'The selection crosses more than one Scene.'};
  if(!sceneIds.length)return {kind:'create',label:'Scene'};
  const sceneId=sceneIds[0],scene=sceneById(sceneId),hasUnassigned=selected.some(w=>!w.dataset.sceneId),sceneSelected=selected.filter(w=>+w.dataset.sceneId===sceneId);
  const ids=new Set(sceneSelected.map(w=>String(w.dataset.blockId))),sceneWraps=$$('.manuscript-block-wrap').filter(w=>+w.dataset.sceneId===sceneId&&!w.classList.contains('scene-gap-hidden'));
  const touchesStart=ids.has(String(scene?.first_block_id||''))||(state.viewMode==='scene'&&sceneWraps.length&&ids.has(String(sceneWraps[0].dataset.blockId)));
  const touchesEnd=ids.has(String(scene?.last_block_id||''))||(state.viewMode==='scene'&&sceneWraps.length&&ids.has(String(sceneWraps.at(-1).dataset.blockId)));
  const canRemove=!!sceneSelected.length&&(touchesStart||touchesEnd),removeBlockIds=sceneSelected.filter(w=>w.dataset.blockId&&!w.dataset.blockId.startsWith('tmp_')).map(w=>w.dataset.blockId);
  if(hasUnassigned)return {kind:'extend',sceneId,scene,label:'Add to Scene',canRemove,removeBlockIds};
  const allSame=selected.every(w=>+w.dataset.sceneId===sceneId);if(!allSame)return {kind:'conflict',label:'Scene',disabled:true};
  if(canRemove)return {kind:'remove',sceneId,scene,label:'Remove from Scene',canRemove:true,removeBlockIds};
  return {kind:'inside',sceneId,scene,label:'Remove from Scene',disabled:true,reason:'Select from the beginning or end of the Scene to remove prose from it.',canRemove:false,removeBlockIds};
}
function syncSelectionSceneAction(items){const button=$('#selectionToolbar [data-selection-action="scene"]');if(!button)return;const plan=sceneSelectionPlan(items);button.textContent=i18nSource(plan.label||'Scene');button.disabled=!!plan.disabled;button.title=i18nSource(plan.reason||'')}
async function createSceneFromSelection(items,{forceRemove=false}={}){
  const initialPlan=sceneSelectionPlan(items);if(forceRemove&&!initialPlan.canRemove){toast(initialPlan.reason||'Select prose from the beginning or end of a Scene to remove it.');return}if(!forceRemove&&initialPlan.disabled){if(initialPlan.reason)toast(initialPlan.reason);return}
  await preserveManuscriptViewport(async()=>{
    const previousView=state.viewMode,previousGap=state.sceneGap?{...state.sceneGap}:null;await ensureSavedSelection(items);const plan=sceneSelectionPlan(items),actionKind=forceRemove?'remove':plan.kind;const blockIds=actionKind==='remove'?(plan.removeBlockIds||[]):completeSceneBlockIds(items);if(!blockIds.length)return;let scene,result;
    if(actionKind==='create'){
      await withStructuralUndo('Create Scene',async()=>{scene=await api('/api/scenes',{method:'POST',body:JSON.stringify({block_ids:blockIds,default_title_prefix:i18nSource('Scene')})})},{view:'manuscript',chapterId:items[0]?.chapter_id});updateManuscriptTabPrefs({sceneMarkers:true});
    }else if(actionKind==='extend'){
      scene=plan.scene;await withStructuralUndo(`Extend scene: ${scene?.title||'Scene'}`,async()=>{result=await api(`/api/scenes/${plan.sceneId}/extend`,{method:'POST',body:JSON.stringify({block_ids:blockIds})})},{view:'manuscript',chapterId:items[0]?.chapter_id});
    }else if(actionKind==='remove'&&plan.canRemove){
      scene=plan.scene;await withStructuralUndo(`Remove prose from scene: ${scene?.title||'Scene'}`,async()=>{result=await api(`/api/scenes/${plan.sceneId}/remove-blocks`,{method:'POST',body:JSON.stringify({block_ids:blockIds})})},{view:'manuscript',chapterId:items[0]?.chapter_id});
    }else{if(plan.reason)toast(plan.reason);return}
    await refresh(false);syncViewScenesControl();renderChapterTree();
    if(actionKind==='create'&&previousView==='scene-gap'&&scene){await selectScene(scene.id,false)}
    else if(actionKind==='remove'&&previousView==='scene'){if(result?.scene_deleted)await openWholeBook(false,false);else await selectScene(plan.sceneId,false)}
    else if(previousView==='scene-gap'&&previousGap){const current=state.sceneGaps.find(g=>String(g.first_block_id)===String(previousGap.first_block_id));if(current)await selectSceneGap(current,false);else await openWholeBook(false,false)}
    else await reloadManuscriptView();
    if(actionKind==='create')toast(i18nText('entity.created',{title:scene.title}));else if(actionKind==='extend')toast(i18nText('scene.added_to',{title:scene?.title||i18nSource('Scene')}));else toast(result?.scene_deleted?'Scene removed':'Removed from Scene');
  });
}
async function sceneReferenceRows(sceneId){
  const [outgoing,incoming]=await Promise.all([api(`/api/references?source_type=scene&source_id=${sceneId}`),api(`/api/references?target_type=scene&target_id=${sceneId}`)]);
  return [...outgoing.map(r=>({...r,direction:'to',display_label:r.target_label,display_type:uiEntityType(r.target_type)})),...incoming.map(r=>({...r,direction:'from',display_label:r.source_label,display_type:uiEntityType(r.source_type)}))];
}
async function openSceneDetails(sceneId){
  const scene=sceneById(sceneId);if(!scene)return;const refs=await sceneReferenceRows(sceneId);const status=sceneLocationLabel(scene);
  const links=refs.length?refs.map(r=>`<div class="link-row"><span><b>${escapeHtml(r.display_label||'Link')}</b><br><small class="small-muted">${escapeHtml(i18nText('links.direction',{direction:i18nSource(r.direction==='from'?'From':'To'),type:r.display_type}))}</small></span><button class="bare-button" data-scene-ref-delete="${r.id}" aria-label="{{i18n:dynamic.html.attr.aria.label.15f5afb5}}">×</button></div>`).join(''):'<span class="small-muted">{{i18n:dynamic.html.text.span.113bd530}}</span>';
  showModal('Scene Details',`<div class="scene-details-grid"><span>{{i18n:dynamic.html.text.span.8204b12a}}</span><b>${escapeHtml(status)}</b></div><div class="field"><label>{{i18n:dynamic.html.text.label.450ca425}}</label><input id="sceneConfigName" value="${escapeHtml(scene.title||'')}"></div><div class="field"><label>{{i18n:dynamic.html.text.label.d1ac14a8}} <span class="optional-label">{{i18n:dynamic.html.text.span.196b480c}}</span></label><textarea id="sceneConfigDescription" rows="5" placeholder="{{i18n:dynamic.html.attr.placeholder.2ae8ae65}}">${escapeHtml(scene.description||'')}</textarea></div><div class="section-label">{{i18n:dynamic.html.text.div.7855b8e0}}</div><div class="scene-links">${links}</div><button id="sceneDetailsAddLink" class="full-soft">{{i18n:dynamic.html.text.button.d45ca59d}}</button>`,'<button id="saveSceneConfig">{{i18n:dynamic.html.text.button.9faf5fce}}</button>');
  const save=async({close=true,notify=true}={})=>{const name=$('#sceneConfigName').value.trim(),description=$('#sceneConfigDescription').value.trim();if(!name){toast('Scene needs a name.');return false}if(name!==scene.title||description!==(scene.description||'')){await withStructuralUndo(`Edit scene: ${scene.title}`,()=>api(`/api/scenes/${sceneId}`,{method:'PATCH',body:JSON.stringify({title:name,description})}),{view:'manuscript',chapterId:state.chapter?.id});await refresh(false);renderChapterTree();await reloadManuscriptView()}if(close)closeModal();if(notify)toast('Scene updated');return true};
  $('#saveSceneConfig').onclick=()=>save();
  $('#sceneDetailsAddLink').onclick=async()=>{if(!await save({close:false,notify:false}))return;openEntityLinker('scene',String(scene.id),()=>openSceneDetails(scene.id))};
  $$('[data-scene-ref-delete]').forEach(button=>button.onclick=async()=>{await withStructuralUndo('Remove Scene link',()=>api(`/api/references/${button.dataset.sceneRefDelete}`,{method:'DELETE'}),{view:'manuscript',chapterId:state.chapter?.id});openSceneDetails(scene.id)});
}
async function deleteScene(sceneId){const scene=sceneById(sceneId);if(!scene)return;if(!await confirmDialog('Delete Scene',i18nText('confirm.delete_scene',{title:scene.title}),{confirmLabel:'Delete Scene',danger:true}))return;keepStructureVisibleAfterDelete('scenes',scene.tab_id||state.activeDocumentId);await withStructuralUndo(`Delete scene: ${scene.title}`,()=>api(`/api/scenes/${sceneId}`,{method:'DELETE'}),{view:'manuscript',chapterId:state.chapter?.id});await refresh(false);await reloadManuscriptView();renderChapterTree();toast('Scene deleted')}
async function cutScene(sceneId){const scene=sceneById(sceneId);if(!scene)return;if(!await confirmDialog('Cut Scene',i18nText('confirm.cut_scene',{title:scene.title}),{confirmLabel:'Cut Scene',danger:true}))return;keepStructureVisibleAfterDelete('scenes',scene.tab_id||state.activeDocumentId);await flushSave();await withStructuralUndo(`Cut scene: ${scene.title}`,()=>api(`/api/scenes/${sceneId}/cut`,{method:'POST'}),{view:'manuscript',chapterId:state.chapter?.id,documentId:scene.tab_id||state.activeDocumentId,manuscriptViewMode:state.viewMode});await refresh(false);if(state.viewMode==='scene')await openWholeBook(false,false);else await reloadManuscriptView();renderChapterTree();await notifyCutsChanged();toast('Scene moved to Cuts');await showCutsOnboardingOnce()}
async function createSceneBelow(sceneId){
  const anchor=sceneById(sceneId);if(!anchor)return;let scene;await flushSave();await withStructuralUndo(`Create Scene below: ${anchor.title}`,async()=>{scene=await api('/api/scenes',{method:'POST',body:JSON.stringify({tab_id:anchor.tab_id,after_scene_id:+anchor.id,default_title_prefix:i18nSource('Scene')})})},{view:'manuscript',chapterId:anchor.last_chapter_id||anchor.first_chapter_id||state.chapter?.id,documentId:anchor.tab_id});updateManuscriptTabPrefs({sceneMarkers:true},anchor.tab_id);await refresh(false);syncViewScenesControl();renderChapterTree();await selectScene(scene.id,false);requestAnimationFrame(()=>{const prose=$('.manuscript-block');if(prose)placeCaret(prose,0)});toast(i18nText('entity.created',{title:scene.title}));
}
function sceneMenuItems(sceneId){return [
  {label:'Add Scene Below',action:()=>createSceneBelow(sceneId)},
  {label:bookmarkExactScene(sceneId)?'Remove Bookmark':'Bookmark',action:()=>toggleSceneBookmark(sceneById(sceneId))},
  {label:'Details',tooltip:'Edit the Scene name and details, or manage its links.',action:()=>openSceneDetails(sceneId)},
  {label:'Cut',danger:true,tooltip:'Move this Scene’s manuscript contents to Cuts and remove the Scene.',action:()=>cutScene(sceneId)},
  {label:'Delete',danger:true,tooltip:'Remove the Scene and its links without changing the manuscript text.',action:()=>deleteScene(sceneId)}
]}
function openSceneMenu(event,sceneId){
  const scene=sceneById(sceneId);if(!scene)return;hideSceneDetailsHover();event.preventDefault();event.stopPropagation();const items=sceneMenuItems(sceneId);
  if(event.type==='contextmenu')openContextMenu(event.clientX,event.clientY,items);
  else{const rect=event.currentTarget.getBoundingClientRect();openContextMenu(rect.left,rect.bottom+4,items,{anchorRect:rect})}
}
async function goScene(sceneId){
  const scene=sceneById(sceneId);if(!scene)return;
  if(state.viewMode==='book'){
    const el=$(`.manuscript-block-wrap[data-scene-id="${sceneId}"]`);el?.scrollIntoView({behavior:'smooth',block:'center'});return;
  }
  if(state.viewMode==='scene'||state.viewMode==='scene-gap'){await selectScene(sceneId);return}
  if(scene.first_chapter_id)await selectChapter(+scene.first_chapter_id,false);
  requestAnimationFrame(()=>{const wrap=$(`.manuscript-block-wrap[data-scene-id="${sceneId}"]`);if(wrap)revealOutlineTarget(wrap);($(`.ghost-scene-title[data-scene-id="${sceneId}"]`)||wrap)?.scrollIntoView({behavior:'smooth',block:'center'})});
}

