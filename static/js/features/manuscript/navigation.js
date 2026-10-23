// -------------------- documents and chapter navigation --------------------
function activeDocument(){ return state.documents.find(d => d.id === state.activeDocumentId) || null; }
function sceneById(id){ return state.scenes.find(s => entityId(s.id) === entityId(id)) || null; }
function chapterScenes(chapterId){ return state.scenes.filter(s => entityId(s.first_chapter_id) === entityId(chapterId)); }

function manuscriptTabPrefs(docId=state.activeDocumentId){
  const key=String(entityId(docId)||'');if(!key)return {};
  const current=state.manuscriptTabPresentation?.[key];return current&&typeof current==='object'?current:{};
}
function visibleChapters(docId=null){return state.chapters.filter(ch=>!ch.is_implicit&&(docId==null||entityId(ch.tab_id)===entityId(docId)))}
function visibleChapterById(id){return visibleChapters().find(ch=>entityId(ch.id)===entityId(id))||null}
// Persistence-only escape hatch. Normal UI code must use visibleChapters()/visibleChapterById().
function storageChapterById(id){return state.chapters.find(ch=>entityId(ch.id)===entityId(id))||null}
function manuscriptTabChapterRows(docId=state.activeDocumentId){return visibleChapters(docId)}
function manuscriptTabSceneRows(docId=state.activeDocumentId){return state.scenes.filter(scene=>entityId(scene.tab_id)===entityId(docId))}
function autoShowChapters(docId=state.activeDocumentId){return manuscriptTabChapterRows(docId).length>0}
function manuscriptTabVisibility(docId=state.activeDocumentId){
  const prefs=manuscriptTabPrefs(docId),chapters=typeof prefs.chapters==='boolean'?prefs.chapters:autoShowChapters(docId),scenes=typeof prefs.scenes==='boolean'?prefs.scenes:manuscriptTabSceneRows(docId).length>0;
  return {chapters,scenes};
}
function chapterMarkersVisible(docId=state.activeDocumentId){const prefs=manuscriptTabPrefs(docId);return prefs.chapterMarkers!==false}
function sceneMarkersVisible(docId=state.activeDocumentId){const prefs=manuscriptTabPrefs(docId);return prefs.sceneMarkers!==false}
function normalizeManuscriptNavMode(){
  const visible=manuscriptTabVisibility(),prefs=manuscriptTabPrefs();let mode=prefs.mode==='scenes'?'scenes':prefs.mode==='chapters'?'chapters':state.manuscriptNavMode;
  if(mode==='chapters'&&!visible.chapters&&visible.scenes)mode='scenes';
  if(mode==='scenes'&&!visible.scenes&&visible.chapters)mode='chapters';
  state.manuscriptNavMode=mode==='scenes'?'scenes':'chapters';return visible;
}
let manuscriptTabPresentationSaveTimer=null;
function persistManuscriptTabPresentation(){
  clearTimeout(manuscriptTabPresentationSaveTimer);manuscriptTabPresentationSaveTimer=setTimeout(()=>api('/api/ui-state/manuscript-tab-presentation',{method:'PUT',body:JSON.stringify({value:state.manuscriptTabPresentation||{}})}).catch(console.warn),80);
}
function updateManuscriptTabPrefs(patch,docId=state.activeDocumentId,{persist=true}={}){
  const key=String(entityId(docId)||'');if(!key)return;const current={...manuscriptTabPrefs(docId),...patch};state.manuscriptTabPresentation={...(state.manuscriptTabPresentation||{}),[key]:current};if(persist)persistManuscriptTabPresentation();
}
function keepStructureVisibleAfterDelete(kind,docId=state.activeDocumentId){const prefs=manuscriptTabPrefs(docId);if(typeof prefs[kind]!=='boolean')updateManuscriptTabPrefs({[kind]:true},docId)}
function syncViewChaptersControl(){const button=$('#viewChaptersBtn');if(!button)return;const visible=chapterMarkersVisible();button.setAttribute('aria-checked',String(visible));button.classList.toggle('active',visible)}
function syncViewScenesControl(){const button=$('#viewScenesBtn');if(!button)return;const visible=sceneMarkersVisible();button.setAttribute('aria-checked',String(visible));button.classList.toggle('active',visible)}
function syncManuscriptViewControls(){syncViewChaptersControl();syncViewScenesControl()}
function rerenderCurrentManuscriptSurface(){const snapshot=captureManuscriptViewport();if(state.viewMode==='book')renderBookEditor();else if(state.viewMode==='scene')renderSceneEditor();else if(state.viewMode==='scene-gap')renderSceneGapEditor();else if(state.chapterData)renderEditor();restoreManuscriptViewport(snapshot)}
function setChapterMarkersVisible(visible){updateManuscriptTabPrefs({chapterMarkers:!!visible});syncViewChaptersControl();if(['book','scene','scene-gap'].includes(state.viewMode))rerenderCurrentManuscriptSurface()}
function setSceneMarkersVisible(visible){updateManuscriptTabPrefs({sceneMarkers:!!visible});syncViewScenesControl();if(state.viewMode!=='scene')rerenderCurrentManuscriptSurface()}
function setNavigatorVisibility(kind,visible){
  updateManuscriptTabPrefs({[kind]:!!visible});const vis=normalizeManuscriptNavMode();
  if(visible&&((kind==='chapters'&&!vis.scenes)||(kind==='scenes'&&!vis.chapters)))state.manuscriptNavMode=kind;
  updateManuscriptTabPrefs({mode:state.manuscriptNavMode});renderChapterTree();
}
async function createNavigatorItem(){
  if(state.manuscriptNavMode==='scenes'){
    const afterSceneId=state.viewMode==='scene'&&entityId(state.scene?.tab_id)===entityId(state.activeDocumentId)?entityId(state.scene.id):null;
    let scene;await flushSave();await withStructuralUndo('Create Scene',async()=>{scene=await api('/api/scenes',{method:'POST',body:JSON.stringify({tab_id:state.activeDocumentId,after_scene_id:afterSceneId,default_title_prefix:i18nSource('Scene')})})},{view:'manuscript',chapterId:state.chapter?.id});updateManuscriptTabPrefs({sceneMarkers:true});await refresh(false);syncViewScenesControl();renderChapterTree();await selectScene(scene.id,false);requestAnimationFrame(()=>{const prose=$('.manuscript-block');if(prose){prose.focus();const range=document.createRange();range.selectNodeContents(prose);range.collapse(false);const sel=getSelection();sel.removeAllRanges();sel.addRange(range)}});toast(i18nText('entity.created',{title:scene.title}));return;
  }
  const count=manuscriptTabChapterRows(state.activeDocumentId).length,afterChapterId=state.viewMode==='chapter'&&entityId(state.chapter?.tab_id)===entityId(state.activeDocumentId)?entityId(state.chapter.id):null;let chapter;await withStructuralUndo('Create chapter',async()=>{chapter=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:i18nText('chapter.default_name',{number:i18nNumber(count+1)}),tab_id:state.activeDocumentId,after_chapter_id:afterChapterId})})},{view:'manuscript'});await refresh(false);await selectChapter(chapter.id,false);
}
function navigatorMenuItems(){
  const visible=manuscriptTabVisibility(),sceneMode=state.manuscriptNavMode==='scenes';return [
    {label:sceneMode?'Add Scene':'Add Chapter',testId:sceneMode?'navigator-add-scene':'navigator-add-chapter',action:()=>createNavigatorItem()},
    {separator:true},
    {label:`${visible.chapters?'✓ ':''}${i18nSource('Show Chapters')}`,testId:'navigator-toggle-chapters',action:()=>setNavigatorVisibility('chapters',!visible.chapters)},
    {label:`${visible.scenes?'✓ ':''}${i18nSource('Show Scenes')}`,testId:'navigator-toggle-scenes',action:()=>setNavigatorVisibility('scenes',!visible.scenes)}
  ];
}
function openNavigatorMenu(event){
  event.preventDefault();event.stopPropagation();const items=navigatorMenuItems();
  if(event.type==='contextmenu')openContextMenu(event.clientX,event.clientY,items);
  else{const rect=event.currentTarget.getBoundingClientRect();openContextMenu(rect.left,rect.bottom+4,items,{anchorRect:rect})}
}

function documentMenuItems(doc){return [
  {label:'Create canvas from tab…',action:()=>documentToCanvas(doc)},
  {label:'Import JSON…',action:()=>beginJsonImport({view:'manuscript',document_id:doc.id})},
  ...(doc.is_manuscript?[]:[{label:'Set as Main Manuscript',action:()=>setMainManuscript(doc)}]),
  {label:'Details',action:()=>configureDocument(doc)},
  {label:'Delete',danger:true,tooltip:'Delete this Tab and all manuscript content inside it.',action:()=>deleteDocument(doc)}
]}
function openDocumentMenu(event,doc){
  event.preventDefault();event.stopPropagation();const items=documentMenuItems(doc);
  if(event.type==='contextmenu')openContextMenu(event.clientX,event.clientY,items);
  else{const rect=event.currentTarget.getBoundingClientRect();openContextMenu(rect.left,rect.bottom+4,items,{anchorRect:rect})}
}
function renderDocuments() {
  const list=$('#documentList');list.innerHTML='';list.classList.toggle('hidden',!state.tabsExpanded);
  for(const doc of state.documents){
    const row=document.createElement('div');row.className='document-row'+(entityId(doc.id)===entityId(state.activeDocumentId)?' active':'');row.dataset.documentId=doc.id;
    row.innerHTML=`<button type="button" class="document-row-main"><span class="document-title">${escapeHtml(doc.title)}</span></button><button type="button" class="document-more manuscript-item-more" aria-label="${escapeHtml(i18nText('tab.actions_for',{name:doc.title}))}"><span aria-hidden="true">⋮</span></button>`;
    row.querySelector('.document-row-main').onclick=async()=>{await flushSave();state.activeDocumentId=doc.id;normalizeManuscriptNavMode();syncManuscriptViewControls();renderDocuments();renderChapterTree();const first=visibleChapters(doc.id)[0];if(state.viewMode==='book')await openWholeBook(false);else if(first)await selectChapter(first.id,false);else await openWholeBook(false)};
    row.querySelector('.document-more').onclick=e=>openDocumentMenu(e,doc);row.oncontextmenu=e=>openDocumentMenu(e,doc);row.onmouseenter=()=>queueChapterDetailsHover(doc,row);row.onmouseleave=hideChapterDetailsHover;
    list.appendChild(row);
  }
  syncTabsToggle();
  const title=activeDocument()?.title||i18nSource('Manuscript');
  $('#activeDocumentTitle').textContent=title;
  const navTitle=$('#selectedDocumentNavTitle');if(navTitle)navTitle.textContent=title.toLocaleUpperCase(i18nLocale());
}
function syncTabsToggle(){const button=$('#tabsToggleBtn');if(!button)return;button.innerHTML=`<span aria-hidden="true">${state.tabsExpanded?'▾':'▸'}</span>`;button.title=i18nSource(state.tabsExpanded?'Hide Tabs':'Show Tabs');button.setAttribute('aria-label',button.title);button.setAttribute('aria-expanded',String(state.tabsExpanded))}
function toggleTabsVisibility(){
  state.tabsExpanded=!state.tabsExpanded;renderDocuments();syncTabsToggle();api('/api/ui-state/tabs-expanded',{method:'PUT',body:JSON.stringify({value:state.tabsExpanded})}).catch(console.warn);
}
function tabsMenuItems(){return [{label:'New Tab',action:()=>createTab()},{label:'Import Tabs…',action:()=>beginDocumentImport(activeDocument()||state.documents.at(-1))}]}
function openTabsMenu(event){
  event.preventDefault();event.stopPropagation();const items=tabsMenuItems();
  if(event.type==='contextmenu')openContextMenu(event.clientX,event.clientY,items);
  else{const rect=event.currentTarget.getBoundingClientRect();openContextMenu(rect.left,rect.bottom+4,items,{anchorRect:rect})}
}
function createTab(){
  showModal('New Tab',`<div class="field"><label>{{i18n:dynamic.html.text.label.450ca425}}</label><input id="newDocumentName" value="${escapeHtml(i18nSource('Notes'))}" autofocus></div><div class="field"><label>{{i18n:dynamic.html.text.label.d1ac14a8}}</label><textarea id="newDocumentDescription" rows="3" placeholder="{{i18n:dynamic.html.attr.placeholder.b0207dce}}"></textarea></div>`,'<button id="createDocumentConfirm">{{i18n:dynamic.html.text.button.742354ce}}</button>');
  $('#createDocumentConfirm').onclick=async()=>{const title=$('#newDocumentName').value.trim();if(!title)return;let doc;await withStructuralUndo(`Create tab: ${title}`,async()=>{doc=await api('/api/documents',{method:'POST',body:JSON.stringify({title,description:$('#newDocumentDescription').value})})},{view:'manuscript'});dismissModalTree();await refresh(false);state.activeDocumentId=doc.id;renderDocuments();renderChapterTree();await openWholeBook(false)};
}
$('#tabsMenuBtn').onclick=openTabsMenu;$('#tabsToggleBtn').onclick=e=>{e.preventDefault();e.stopPropagation();toggleTabsVisibility()};$('.tabs-head').oncontextmenu=openTabsMenu;
function configureDocument(doc){
  showModal('Tab Details',`<div class="field"><label>{{i18n:dynamic.html.text.label.450ca425}}</label><input id="tabConfigName" value="${escapeHtml(doc.title)}"></div><div class="field"><label>{{i18n:dynamic.html.text.label.d1ac14a8}}</label><textarea id="tabConfigDescription" rows="4">${escapeHtml(doc.description||'')}</textarea></div>`,`<button id="deleteDocumentFromDetails" class="danger">{{i18n:dynamic.html.text.button.190c9e1f}}</button><button id="saveTabConfig">{{i18n:dynamic.html.text.button.9faf5fce}}</button>`);
  $('#deleteDocumentFromDetails').onclick=async()=>{if(await deleteDocument(doc))dismissModalTree()};
  $('#saveTabConfig').onclick=async()=>{const title=$('#tabConfigName').value.trim();if(!title)return;await withStructuralUndo(`Edit tab details: ${doc.title}`,()=>api(`/api/documents/${doc.id}`,{method:'PATCH',body:JSON.stringify({title,description:$('#tabConfigDescription').value})}),{view:'manuscript'});dismissModalTree();await refresh(false);renderDocuments();renderChapterTree()};
}
async function deleteDocument(doc){
  if(state.documents.length<=1){toast('A project must keep at least one Tab.');return false}
  if(!await confirmDialog('Delete Tab',i18nText('confirm.delete_tab',{title:doc.title}),{confirmLabel:'Delete Tab',danger:true}))return false;
  let result;await withStructuralUndo(`Delete tab: ${doc.title}`,async()=>{result=await api(`/api/documents/${doc.id}`,{method:'DELETE'})},{view:'manuscript'});
  await refresh(false);state.activeDocumentId=entityId(result.next_document_id)||state.documents[0]?.id||null;normalizeManuscriptNavMode();syncManuscriptViewControls();renderDocuments();renderChapterTree();await openWholeBook(false,false);toast('Tab deleted');return true;
}
async function renameDocument(doc){configureDocument(doc)}
$('#editActiveDocumentBtn').onclick=()=>{const doc=activeDocument();if(doc)configureDocument(doc)};
async function setMainManuscript(doc){
  if(doc.is_manuscript)return;
  await flushSave();
  await withStructuralUndo(`Set main manuscript: ${doc.title}`,()=>api(`/api/documents/${doc.id}/make-manuscript`,{method:'POST'}),{view:'manuscript'});
  await refresh(false);state.activeDocumentId=doc.id;normalizeManuscriptNavMode();syncManuscriptViewControls();renderDocuments();renderChapterTree();toast(i18nText('manuscript.set_main',{title:doc.title}));
}
async function documentToCanvas(doc){
  let cv;
  await withStructuralUndo(`Create canvas for tab: ${doc.title}`,async()=>{
    cv=await api('/api/canvases',{method:'POST',body:JSON.stringify({name:doc.title,description:doc.description||'',canvas_type:'blank'})});
    const node=await api(`/api/canvases/${cv.id}/nodes`,{method:'POST',body:JSON.stringify({shape:'rect',title:doc.title,body:i18nSource('Linked tab'),x:120,y:100,w:240,h:120})});
    await api('/api/references',{method:'POST',body:JSON.stringify({source_type:'node',source_id:String(node.id),target_type:'document',target_id:String(doc.id),relationship:'source document'})});
  },{view:'canvas'});
  await refresh(false);setView('canvas');await selectCanvas(cv.id);toast('Canvas created and linked to the Tab.');
}
function chapterRailNumber(ch,index){const match=String(ch.title||'').match(/^(?:Chapter\s+)?(\d+)\s*:/i);return match?match[1]:String(index+1)}
function chapterOutlineItems(chapterId){return (state.chapterOutlines||[]).filter(item=>entityId(item.chapter_id)===entityId(chapterId)&&/^heading[2-4]$/.test(item.semantic_style||'')).sort((a,b)=>(+a.position||0)-(+b.position||0)||String(a.id).localeCompare(String(b.id)))}
function chapterOutlineLevel(item){const match=/^heading([2-4])$/.exec(String(item?.semantic_style||''));return match?+match[1]:0}
function outlineHasChildren(items,index){const level=chapterOutlineLevel(items[index]);if(!level)return false;const next=items[index+1];return !!next&&chapterOutlineLevel(next)>level}
function outlineAncestorCollapsed(items,index){const stack=[];for(let i=0;i<index;i++){const level=chapterOutlineLevel(items[i]);while(stack.length&&stack.at(-1).level>=level)stack.pop();stack.push({level,id:String(items[i].id)})}const currentLevel=chapterOutlineLevel(items[index]);while(stack.length&&stack.at(-1).level>=currentLevel)stack.pop();return stack.some(entry=>state.manuscriptCollapsedHeadings.has(entry.id))}
let chapterDetailsHoverTimer=null,chapterDetailsHoverSourceId=null;
function hideChapterDetailsHover(){clearTimeout(chapterDetailsHoverTimer);chapterDetailsHoverTimer=null;chapterDetailsHoverSourceId=null;$('#chapterDetailsHover')?.remove()}
function queueChapterDetailsHover(chapter,row){
  hideChapterDetailsHover();const details=String(chapter?.description||'').trim();if(!details)return;const id=entityId(chapter.id);chapterDetailsHoverSourceId=id;
  chapterDetailsHoverTimer=setTimeout(()=>{if(chapterDetailsHoverSourceId!==id||!row.isConnected||!row.matches(':hover'))return;const card=document.createElement('div');card.id='chapterDetailsHover';card.className='chapter-details-hover-card';card.innerHTML=`<div>${escapeHtml(details).replace(/\n/g,'<br>')}</div>`;document.body.appendChild(card);const r=row.getBoundingClientRect(),b=card.getBoundingClientRect(),gap=8,edge=8;let left=r.right+gap;if(left+b.width>innerWidth-edge)left=Math.max(edge,r.left-b.width-gap);const top=Math.max(edge,Math.min(innerHeight-b.height-edge,r.top+(r.height-b.height)/2));card.style.left=`${Math.round(left)}px`;card.style.top=`${Math.round(top)}px`},300)
}
async function goToOutlineBlock(chapterId,blockId){
  if(state.viewMode==='book'){
    const wrap=$(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(blockId))}"]`);wrap?.scrollIntoView({behavior:'auto',block:'center'});wrap?.querySelector('.manuscript-block')?.focus();pulseBookmarkTarget(wrap);return;
  }
  if(entityId(state.chapter?.id)!==entityId(chapterId))await selectChapter(entityId(chapterId));
  requestAnimationFrame(()=>{const wrap=$(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(blockId))}"]`);wrap?.scrollIntoView({behavior:'auto',block:'center'});wrap?.querySelector('.manuscript-block')?.focus();pulseBookmarkTarget(wrap)});
}
function sceneRailLabel(scene){
  const title=String(scene?.title||'').trim();
  const defaultMatch=title.match(/^Scene\s+(\d+)$/i);
  if(defaultMatch)return defaultMatch[1];
  const first=Array.from(title).find(ch=>/[\p{L}\p{N}]/u.test(ch));
  return (first||'S').toLocaleUpperCase();
}
function sceneLocationLabel(scene){
  const chapter=visibleChapterById(scene.first_chapter_id);
  if(Number(scene.chapter_count||0)>1)return i18nText('scene.location_chapters',{chapter:chapter?.title||i18nSource('Manuscript'),count:i18nNumber(scene.chapter_count)});
  return chapter?.title||i18nSource('Written scene');
}
let sceneGapHoverTimer=null;
function hideSceneGapHover(){clearTimeout(sceneGapHoverTimer);sceneGapHoverTimer=null;$('#sceneGapHover')?.remove()}
function showSceneGapHover(target,gap){hideSceneGapHover();sceneGapHoverTimer=setTimeout(()=>{if(!target.isConnected||!target.matches(':hover'))return;const flyer=document.createElement('div');flyer.id='sceneGapHover';flyer.className='scene-gap-hover';flyer.textContent=i18nCount(gap.word_count,'scene_gap.words.one','scene_gap.words.many');document.body.appendChild(flyer);const r=target.getBoundingClientRect(),b=flyer.getBoundingClientRect(),margin=8;flyer.style.left=`${Math.max(margin,Math.min(innerWidth-b.width-margin,r.left+(r.width-b.width)/2))}px`;flyer.style.top=`${Math.max(margin,r.top-b.height-6)}px`},300)}
let sceneDetailsHoverTimer=null,sceneDetailsHoverSourceId=null;
function hideSceneDetailsHover(){clearTimeout(sceneDetailsHoverTimer);sceneDetailsHoverTimer=null;sceneDetailsHoverSourceId=null;$('#sceneDetailsHover')?.remove()}
function queueSceneDetailsHover(scene,row){
  hideSceneDetailsHover();const details=String(scene?.description||'').trim();if(!details)return;const id=entityId(scene.id);sceneDetailsHoverSourceId=id;
  sceneDetailsHoverTimer=setTimeout(()=>{if(sceneDetailsHoverSourceId!==id||!row.isConnected||!row.matches(':hover'))return;const card=document.createElement('div');card.id='sceneDetailsHover';card.className='chapter-details-hover-card scene-details-hover-card';card.innerHTML=`<div>${escapeHtml(details).replace(/\n/g,'<br>')}</div>`;document.body.appendChild(card);const r=row.getBoundingClientRect(),b=card.getBoundingClientRect(),gap=8,edge=8;let left=r.right+gap;if(left+b.width>innerWidth-edge)left=Math.max(edge,r.left-b.width-gap);const top=Math.max(edge,Math.min(innerHeight-b.height-edge,r.top+(r.height-b.height)/2));card.style.left=`${Math.round(left)}px`;card.style.top=`${Math.round(top)}px`},300)
}
async function goToSceneGap(gap){if(!gap?.first_block_id)return;if(state.viewMode==='scene'||state.viewMode==='scene-gap'){await selectSceneGap(gap);return}if(state.viewMode==='book'&&entityId(state.activeDocumentId)===entityId(gap.tab_id)){$(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(gap.first_block_id))}"]`)?.scrollIntoView({behavior:'smooth',block:'center'});return}state.activeDocumentId=entityId(gap.tab_id);await selectChapter(entityId(gap.first_chapter_id),false);requestAnimationFrame(()=>$(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(gap.first_block_id))}"]`)?.scrollIntoView({behavior:'smooth',block:'center'}))}
async function moveSceneOutline(sceneId,{beforeSceneId=null,afterSceneId=null,targetBlockId=null}={}){
  const scene=sceneById(sceneId);if(!scene)return;
  await withStructuralUndo(`Move scene: ${scene.title}`,()=>api(`/api/scenes/${sceneId}/move`,{method:'POST',body:JSON.stringify({before_scene_id:beforeSceneId,after_scene_id:afterSceneId,target_block_id:targetBlockId})}),{view:'manuscript',documentId:state.activeDocumentId,manuscriptViewMode:state.viewMode,sceneId:state.viewMode==='scene'?state.scene?.id:null,chapterId:state.chapter?.id});
  await refresh(false);if(state.viewMode==='scene'&&state.scene?.id)await selectScene(state.scene.id,false);else await reloadManuscriptView();renderChapterTree();
}
function sceneGapForTab(){return (state.sceneGaps||[]).filter(g=>entityId(g.tab_id)===entityId(state.activeDocumentId))}
function appendSceneGap(tree,gap){
  const row=document.createElement('div');row.className='scene-gap-row'+(state.viewMode==='scene-gap'&&String(state.sceneGap?.first_block_id)===String(gap.first_block_id)?' active':'');row.dataset.firstBlockId=gap.first_block_id;row.setAttribute('role','button');row.tabIndex=0;row.setAttribute('aria-label',i18nCount(gap.word_count,'scene_gap.words.one','scene_gap.words.many'));row.innerHTML='<span class="scene-gap-dashes" aria-hidden="true"></span>';
  if(bookmarkShowsOnSceneGap(gap)){const mark=document.createElement('button');mark.type='button';mark.className='sidebar-bookmark-icon scene-gap-bookmark-icon';mark.setAttribute('aria-label',i18nSource('Bookmark inside this text outside Scenes'));wireSidebarBookmarkNavigation(mark);row.appendChild(mark)}
  row.onmouseenter=()=>showSceneGapHover(row,gap);row.onmouseleave=hideSceneGapHover;row.onclick=e=>{if(e.target.closest('.sidebar-bookmark-icon'))return;clearTimeout(sceneNavClickTimer);sceneNavClickTimer=setTimeout(()=>goToSceneGap(gap),220)};row.ondblclick=e=>{clearTimeout(sceneNavClickTimer);if(e.target.closest('.sidebar-bookmark-icon'))return;e.preventDefault();e.stopPropagation();selectSceneGap(gap)};row.onkeydown=e=>{if(e.target.closest('.sidebar-bookmark-icon'))return;if(e.key==='Enter'||e.key===' '){e.preventDefault();goToSceneGap(gap)}};
  row.ondragover=e=>{if(!state.dragSceneId)return;e.preventDefault();row.classList.add('drop-target');hideSceneGapHover()};row.ondragleave=()=>row.classList.remove('drop-target');
  row.ondrop=async e=>{e.preventDefault();row.classList.remove('drop-target');const sid=state.dragSceneId;state.dragSceneId=null;if(!sid)return;try{await moveSceneOutline(sid,{beforeSceneId:gap.after_scene_id||null,afterSceneId:gap.before_scene_id||null,targetBlockId:gap.first_block_id})}catch(error){toast(error.message||'Scene could not be moved.',4500)}};
  tree.appendChild(row);
}
let sceneNavClickTimer=null;
function renderSceneTree(tree){
  hideSceneGapHover();hideSceneDetailsHover();
  const scenes=[...state.scenes].filter(scene=>entityId(scene.tab_id)===entityId(state.activeDocumentId)).sort((a,b)=>(+a.position||0)-(+b.position||0)||compareEntityIds(a.id,b.id));
  const gaps=sceneGapForTab();
  if(!scenes.length){tree.innerHTML='<div class="scene-nav-empty small-muted">{{i18n:dynamic.html.text.div.d5f2327a}}</div>';return}
  const leading=gaps.filter(g=>!g.before_scene_id&&g.after_scene_id);for(const gap of leading)appendSceneGap(tree,gap);
  for(const [sceneIndex,scene] of scenes.entries()){
    const row=document.createElement('div');row.className='scene-outline-row'+(state.viewMode==='scene'&&entityId(state.scene?.id)===entityId(scene.id)?' active':'');row.dataset.sceneId=scene.id;row.draggable=true;
    row.innerHTML=`<span class="scene-grip" aria-hidden="true">⋮⋮</span><button type="button" class="scene-outline-main"><span class="scene-short">${escapeHtml(sceneRailLabel(scene))}</span><span class="scene-outline-copy"><span class="scene-outline-title">${escapeHtml(scene.title||i18nSource('Scene'))}</span><small>${escapeHtml(sceneLocationLabel(scene))}</small></span></button>${bookmarkShowsOnScene(scene.id)?`<button type="button" class="sidebar-bookmark-icon scene-bookmark-icon" aria-label="${escapeHtml(i18nSource(bookmarkExactScene(scene.id)?'Bookmarked Scene':'Bookmark inside this Scene'))}"></button>`:''}<button type="button" class="scene-more manuscript-item-more" aria-label="${escapeHtml(i18nText('scene.actions',{title:scene.title||i18nSource('Scene')}))}"><span aria-hidden="true">⋮</span></button>`;
    row.onclick=e=>{if(e.target.closest('.scene-more,.sidebar-bookmark-icon'))return;clearTimeout(sceneNavClickTimer);sceneNavClickTimer=setTimeout(()=>goScene(scene.id),220)};row.ondblclick=e=>{if(e.target.closest('.scene-more,.sidebar-bookmark-icon'))return;clearTimeout(sceneNavClickTimer);e.preventDefault();e.stopPropagation();selectScene(scene.id)};row.querySelector('.scene-more').onclick=e=>openSceneMenu(e,scene.id);const sceneBookmark=row.querySelector('.sidebar-bookmark-icon');if(sceneBookmark)wireSidebarBookmarkNavigation(sceneBookmark);row.oncontextmenu=e=>{if(e.target.closest('.sidebar-bookmark-icon'))return;openSceneMenu(e,scene.id)};row.onmouseenter=()=>queueSceneDetailsHover(scene,row);row.onmouseleave=hideSceneDetailsHover;
    row.ondragstart=e=>{state.dragSceneId=entityId(scene.id);row.classList.add('dragging');e.dataTransfer?.setData('text/plain',String(scene.id));e.dataTransfer&&(e.dataTransfer.effectAllowed='move')};
    row.ondragend=()=>{state.dragSceneId=null;row.classList.remove('dragging');$$('.scene-outline-row,.scene-gap-row').forEach(x=>x.classList.remove('drop-target'))};
    row.ondragover=e=>{if(!state.dragSceneId||entityId(state.dragSceneId)===entityId(scene.id))return;e.preventDefault();row.classList.add('drop-target')};row.ondragleave=()=>row.classList.remove('drop-target');
    row.ondrop=async e=>{e.preventDefault();row.classList.remove('drop-target');const sid=state.dragSceneId;state.dragSceneId=null;if(!sid||entityId(sid)===entityId(scene.id))return;try{await moveSceneOutline(sid,{beforeSceneId:entityId(scene.id)})}catch(error){toast(error.message||'Scene could not be moved.',4500)}};
    tree.appendChild(row);
    for(const gap of gaps.filter(g=>entityId(g.before_scene_id)===entityId(scene.id)))appendSceneGap(tree,gap);
  }
}

function chapterMenuItems(ch){return [{label:bookmarkExactChapter(ch.id)?'Remove Bookmark':'Bookmark',action:()=>toggleChapterBookmark(ch)},{label:'Split View',action:()=>openChapterSplit(ch.id)},{label:'Add Chapter Below',action:()=>createChapterBelow(ch)},{label:'Details',action:()=>configureChapterDetails(ch)},{label:'Cut',danger:true,tooltip:'Move this Chapter and all of its contents to Cuts.',action:()=>cutChapter(ch)},{label:'Delete',danger:true,tooltip:'Delete this Chapter and all of its contents without creating a Cut.',action:()=>deleteChapterPermanently(ch)}]}
function openChapterMenu(event,ch){
  event.preventDefault();event.stopPropagation();hideChapterDetailsHover();const items=chapterMenuItems(ch);
  if(event.type==='contextmenu')openContextMenu(event.clientX,event.clientY,items);
  else{const rect=event.currentTarget.getBoundingClientRect();openContextMenu(rect.left,rect.bottom+4,items,{anchorRect:rect})}
}

function clearChapterDropIndicator(tree=$('#chapterTree')){
  state.dragChapterDropBeforeId=null;
  if(!tree)return;
  tree.querySelectorAll('.chapter-row.drop-before,.chapter-row.drop-after').forEach(row=>row.classList.remove('drop-before','drop-after'));
}
function chapterDropBeforeIdAtY(tree,clientY){
  const movingId=entityId(state.dragChapterId);
  const rows=[...tree.querySelectorAll('.chapter-row')].filter(row=>entityId(row.dataset.id)!==movingId);
  for(const row of rows){const rect=row.getBoundingClientRect();if(clientY<rect.top+rect.height/2)return entityId(row.dataset.id)}
  return null;
}
function showChapterDropIndicator(tree,beforeId){
  tree.querySelectorAll('.chapter-row.drop-before,.chapter-row.drop-after').forEach(row=>row.classList.remove('drop-before','drop-after'));
  state.dragChapterDropBeforeId=beforeId==null?null:entityId(beforeId);
  const rows=[...tree.querySelectorAll('.chapter-row')].filter(row=>entityId(row.dataset.id)!==entityId(state.dragChapterId));
  if(beforeId==null){rows.at(-1)?.classList.add('drop-after');return}
  rows.find(row=>entityId(row.dataset.id)===entityId(beforeId))?.classList.add('drop-before');
}
function optimisticallyApplyChapterOrder(documentId,ids){
  const slots=[];const byId=new Map();
  state.chapters.forEach((chapter,index)=>{if(!chapter.is_implicit&&entityId(chapter.tab_id)===entityId(documentId)){slots.push(index);byId.set(entityId(chapter.id),chapter)}});
  if(slots.length!==ids.length||ids.some(id=>!byId.has(entityId(id))))return false;
  ids.forEach((id,index)=>{const chapter=byId.get(entityId(id));chapter.position=index;state.chapters[slots[index]]=chapter});
  return true;
}
function applySceneOutlineOrder(sceneIds){
  if(!Array.isArray(sceneIds))return;
  const rank=new Map(sceneIds.map((id,index)=>[entityId(id),index]));
  state.scenes.forEach(scene=>{if(rank.has(entityId(scene.id)))scene.position=rank.get(entityId(scene.id))});
}
async function finishChapterDrop(tree,siblings){
  const movingId=entityId(state.dragChapterId);if(!movingId)return;
  const beforeId=state.dragChapterDropBeforeId;
  const oldIds=siblings.map(ch=>entityId(ch.id)),ids=oldIds.filter(id=>id!==movingId);
  const to=beforeId==null?ids.length:ids.indexOf(entityId(beforeId));if(to<0){clearChapterDropIndicator(tree);return}
  ids.splice(to,0,movingId);clearChapterDropIndicator(tree);state.dragChapterId=null;
  if(ids.every((id,index)=>id===oldIds[index])){renderChapterTree();return}
  // Make the navigator obey the line immediately. Persistence/Undo continues in the
  // background; if the structural preflight rejects the move we restore server truth.
  optimisticallyApplyChapterOrder(state.activeDocumentId,ids);renderChapterTree();
  try{
    const result=await withStructuralUndo('Reorder chapters',()=>api('/api/chapters/reorder',{method:'POST',body:JSON.stringify({ids})}),{view:'manuscript',chapterId:movingId});
    applySceneOutlineOrder(result?.scene_ids);
    if(state.manuscriptNavMode==='scenes')renderChapterTree();
  }catch(error){
    await refresh(false);renderChapterTree();toast(error?.message||'This Chapter could not be moved.',5200);
  }
}
function renderChapterTree(){
  hideSceneGapHover();hideSceneDetailsHover();
  $('#bookViewBtn').classList.toggle('active',state.viewMode==='book');$('#fullDocumentControl')?.classList.toggle('active',state.viewMode==='book');$('#collapsedFullTextBtn')?.classList.toggle('active',state.viewMode==='book');$('.book-nav').classList.toggle('collapsed',state.bookNavCollapsed);
  const visible=normalizeManuscriptNavMode(),modeControl=$('#manuscriptNavMode'),navToolbar=$('#manuscriptNavToolbar'),visibleCount=Number(visible.chapters)+Number(visible.scenes);
  if(modeControl){modeControl.classList.toggle('hidden',visibleCount<2);modeControl.classList.remove('single')}
  navToolbar?.classList.toggle('hidden',visibleCount<2);
  $('#chaptersNavMode')?.classList.toggle('hidden',!visible.chapters);$('#scenesNavMode')?.classList.toggle('hidden',!visible.scenes);
  $('#chaptersNavMode')?.classList.toggle('active',state.manuscriptNavMode==='chapters');$('#scenesNavMode')?.classList.toggle('active',state.manuscriptNavMode==='scenes');
  const tree=$('#chapterTree');tree.innerHTML='';tree.ondragover=null;tree.ondrop=null;
  if(!visibleCount)return;
  if(state.manuscriptNavMode==='scenes'&&visible.scenes){renderSceneTree(tree);return}
  if(!visible.chapters)return;
  const siblings=manuscriptTabChapterRows(state.activeDocumentId);
  if(!siblings.length){tree.innerHTML='<div class="scene-nav-empty small-muted">{{i18n:dynamic.html.text.div.840b9f00}}</div>';return}
  siblings.forEach((ch,index)=>{
    const outline=chapterOutlineItems(ch.id),hasOutline=outline.length>0,chapterCollapsed=hasOutline&&state.manuscriptCollapsedChapters.has(entityId(ch.id));
    const row=document.createElement('div');row.className='chapter-row'+(state.viewMode==='chapter'&&state.chapter?.id===ch.id?' active':'');row.draggable=true;row.dataset.id=ch.id;row.setAttribute('aria-label',ch.title||i18nText('chapter.default_name',{number:i18nNumber(index+1)}));
    row.innerHTML=`${hasOutline?`<span class="chapter-outline-toggle" role="button" tabindex="0" aria-label="${escapeHtml(i18nText('outline.toggle_chapter',{action:i18nSource(chapterCollapsed?'Expand':'Collapse'),title:ch.title}))}" aria-expanded="${String(!chapterCollapsed)}">${chapterCollapsed?'▸':'▾'}</span>`:'<span class="chapter-outline-toggle placeholder" aria-hidden="true"></span>'}<span class="chapter-grip" aria-hidden="true">⋮⋮</span><span class="chapter-short">${escapeHtml(chapterRailNumber(ch,index))}</span><span class="chapter-full">${escapeHtml(ch.title)}</span>${bookmarkShowsOnChapter(ch.id)?`<button type="button" class="sidebar-bookmark-icon chapter-bookmark-icon" aria-label="${escapeHtml(i18nSource(bookmarkExactChapter(ch.id)?'Bookmarked Chapter':'Bookmark inside this Chapter'))}"></button>`:''}<button type="button" class="chapter-more manuscript-item-more" aria-label="${escapeHtml(i18nText('chapter.actions',{title:ch.title}))}"><span aria-hidden="true">⋮</span></button>`;
    row.onclick=e=>{if(e.target.closest('.chapter-outline-toggle:not(.placeholder),.sidebar-bookmark-icon,.chapter-more'))return;state.viewMode==='book'?scrollBookChapter(ch.id):selectChapter(ch.id)};row.ondblclick=e=>{if(!e.target.closest('.chapter-outline-toggle,.sidebar-bookmark-icon,.chapter-more'))selectChapter(ch.id)};row.onmouseenter=()=>queueChapterDetailsHover(ch,row);row.onmouseleave=hideChapterDetailsHover;
    const toggle=row.querySelector('.chapter-outline-toggle:not(.placeholder)');if(toggle){const act=e=>{e.preventDefault();e.stopPropagation();toggleOutlineChapter(ch.id)};toggle.onclick=act;toggle.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){act(e)}}}
    row.querySelector('.chapter-more').onclick=e=>openChapterMenu(e,ch);const chapterBookmark=row.querySelector('.sidebar-bookmark-icon');if(chapterBookmark)wireSidebarBookmarkNavigation(chapterBookmark);row.oncontextmenu=e=>{if(e.target.closest('.sidebar-bookmark-icon'))return;openChapterMenu(e,ch)};
    row.ondragstart=e=>{state.dragChapterId=entityId(ch.id);state.dragChapterDropBeforeId=null;row.classList.add('dragging');if(e.dataTransfer){e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',String(ch.id))}};
    row.ondragend=()=>{row.classList.remove('dragging');clearChapterDropIndicator(tree);state.dragChapterId=null};
    tree.appendChild(row);
    if(!state.bookNavCollapsed&&!chapterCollapsed){
      outline.forEach((item,itemIndex)=>{
        if(outlineAncestorCollapsed(outline,itemIndex))return;const level=chapterOutlineLevel(item),hasChildren=outlineHasChildren(outline,itemIndex),collapsed=hasChildren&&state.manuscriptCollapsedHeadings.has(String(item.id));
        const sr=document.createElement('button');sr.className=`outline-nav outline-level-${level}`;sr.dataset.blockId=item.id;sr.style.paddingLeft=`${25+(level-2)*14}px`;sr.title=item.text_content||'';
        sr.innerHTML=`${hasChildren?`<span class="outline-nav-toggle" aria-hidden="true">${collapsed?'▸':'▾'}</span>`:'<span class="outline-nav-toggle placeholder" aria-hidden="true"></span>'}<span class="outline-nav-label">${escapeHtml(item.text_content||i18nSource('Untitled section'))}</span>`;
        sr.onclick=e=>{if(e.target.closest('.outline-nav-toggle:not(.placeholder)')){e.preventDefault();e.stopPropagation();toggleOutlineHeading(String(item.id));return}goToOutlineBlock(ch.id,item.id)};tree.appendChild(sr);
      });
    }
  });
  // One drop controller owns both the line and the eventual insertion slot. Native
  // row-level drag/drop can report a different target at release time when children
  // or scrolling are involved; storing the slot here makes the visible line authoritative.
  tree.ondragover=e=>{if(!state.dragChapterId)return;e.preventDefault();if(e.dataTransfer)e.dataTransfer.dropEffect='move';const beforeId=chapterDropBeforeIdAtY(tree,e.clientY);if(beforeId!==state.dragChapterDropBeforeId||!tree.querySelector('.chapter-row.drop-before,.chapter-row.drop-after'))showChapterDropIndicator(tree,beforeId)};
  tree.ondrop=e=>{if(!state.dragChapterId)return;e.preventDefault();e.stopPropagation();finishChapterDrop(tree,siblings)};
}
function setManuscriptNavMode(mode){state.manuscriptNavMode=mode==='scenes'?'scenes':'chapters';updateManuscriptTabPrefs({mode:state.manuscriptNavMode});renderChapterTree()}
$('#chaptersNavMode').onclick=()=>setManuscriptNavMode('chapters');
$('#scenesNavMode').onclick=()=>setManuscriptNavMode('scenes');
$('#manuscriptNavMode').oncontextmenu=openNavigatorMenu;
$('#navigatorMenuBtn').onclick=openNavigatorMenu;
$('#navigatorMenuBtn').oncontextmenu=openNavigatorMenu;
$('#chapterTree').oncontextmenu=e=>{if(e.target.closest('.chapter-row,.scene-outline-row,.outline-nav,.scene-gap-row'))return;openNavigatorMenu(e)};

$('#bookViewBtn').onclick=()=>openWholeBook().catch(error=>toast(error.message||'Full Text could not be loaded.',5000));$('#collapsedFullTextBtn').onclick=()=>openWholeBook().catch(error=>toast(error.message||'Full Text could not be loaded.',5000));
$('#toggleBookNavBtn').onclick=e=>{e.stopPropagation();state.bookNavCollapsed=!state.bookNavCollapsed;renderChapterTree();api('/api/ui-state/book-nav-collapsed',{method:'PUT',body:JSON.stringify({value:state.bookNavCollapsed})}).catch(console.warn)};
async function configureChapterDetails(chapter){
  const current=visibleChapterById(chapter.id)||chapter;showModal('Chapter Details',`<div class="field"><label>{{i18n:dynamic.html.text.label.d1ac14a8}}</label><textarea id="chapterDescription" rows="6" placeholder="{{i18n:dynamic.html.attr.placeholder.7d612628}}">${escapeHtml(current.description||'')}</textarea></div>`,'<button id="saveChapterDescription">{{i18n:dynamic.html.text.button.9faf5fce}}</button>');
  $('#saveChapterDescription').onclick=async()=>{await withStructuralUndo(`Edit chapter details: ${current.title}`,()=>api(`/api/chapters/${current.id}`,{method:'PATCH',body:JSON.stringify({description:$('#chapterDescription').value})}),{view:'manuscript',chapterId:current.id});closeModal();await refresh(false);renderChapterTree();toast('Chapter details saved')};
}
async function createChapterBelow(chapter){
  const current=visibleChapterById(chapter.id)||chapter;if(!current)return;const documentId=entityId(current.tab_id)||entityId(state.activeDocumentId),count=manuscriptTabChapterRows(documentId).length;let created;
  await flushSave();await withStructuralUndo(`Create chapter below: ${current.title}`,async()=>{created=await api('/api/chapters',{method:'POST',body:JSON.stringify({title:i18nText('chapter.default_name',{number:i18nNumber(count+1)}),tab_id:documentId,after_chapter_id:entityId(current.id)})})},{view:'manuscript',chapterId:entityId(current.id),documentId});
  await refresh(false);await selectChapter(created.id,false,false);clearManuscriptSelection();requestAnimationFrame(()=>{const prose=$('.manuscript-block');if(prose)placeCaret(prose,0)});toast(i18nText('entity.created',{title:created.title}));
}


function chapterAfterRemovalTarget(chapter){
  const rows=manuscriptTabChapterRows(chapter?.tab_id||state.activeDocumentId),index=rows.findIndex(ch=>entityId(ch.id)===entityId(chapter?.id));if(index<0)return null;return rows[index-1]||rows[index+1]||null;
}
function clearManuscriptSelection(){
  try{getSelection()?.removeAllRanges()}catch{}state.lastManuscriptRange=null;state.selectionToolbarRange=null;state.selectionToolbarItems=[];state.selectionToolbarHeaderChapterId=null;hideSelectionToolbar();hideSelectionHighlightPicker();
}

async function cutChapter(chapter){
  if(!await confirmDialog('Cut Chapter',i18nText('confirm.cut_chapter',{title:chapter.title}),{confirmLabel:'Cut Chapter',danger:true}))return;
  keepStructureVisibleAfterDelete('chapters',chapter.tab_id||state.activeDocumentId);
  await withStructuralUndo(`Cut chapter: ${chapter.title}`,()=>api(`/api/chapters/${chapter.id}`,{method:'DELETE'}),{view:'manuscript'});await refresh(false);await notifyCutsChanged();
  const next=manuscriptTabChapterRows(state.activeDocumentId)[0];if(next)await selectChapter(next.id,false);else await openWholeBook(false);await showCutsOnboardingOnce();
}
async function deleteChapterPermanently(chapter){
  if(!await confirmDialog('Delete Chapter',i18nText('confirm.delete_chapter',{title:chapter.title}),{confirmLabel:'Delete Chapter',danger:true}))return;
  const documentId=chapter.tab_id||state.activeDocumentId,target=chapterAfterRemovalTarget(chapter);clearManuscriptSelection();keepStructureVisibleAfterDelete('chapters',documentId);
  await withStructuralUndo(`Delete chapter: ${chapter.title}`,()=>api(`/api/chapters/${chapter.id}/permanent`,{method:'DELETE'}),{view:'manuscript'});await refresh(false);
  if(target&&visibleChapterById(target.id))await selectChapter(target.id,false,false);else{const fallback=manuscriptTabChapterRows(documentId)[0];if(fallback)await selectChapter(fallback.id,false,false);else await openWholeBook(false,false)}clearManuscriptSelection();toast('Chapter deleted');
}

async function dissolveChapterBoundary(chapterId,{stayInBook=state.viewMode==='book',titleStyle=null}={}){
  chapterId=entityId(chapterId);const chapter=visibleChapterById(chapterId);if(!chapter)return;
  await flushSave();clearManuscriptSelection();keepStructureVisibleAfterDelete('chapters',chapter.tab_id||state.activeDocumentId);
  let result;
  try{const suffix=titleStyle?`?title_style=${encodeURIComponent(titleStyle)}`:'';result=await withStructuralUndo(`${titleStyle?'Change chapter header style':'Remove chapter boundary'}: ${chapter.title}`,()=>api(`/api/chapters/${chapterId}/dissolve${suffix}`,{method:'POST'}),{view:'manuscript',chapterId,documentId:chapter.tab_id||state.activeDocumentId,manuscriptViewMode:state.viewMode})}
  catch(error){toast(error?.message||'That Chapter boundary could not be removed.',5000);return}
  await refresh(false);
  const focusBlockId=result.converted_block_id||result.first_block_id;
  if(stayInBook||result.target_is_implicit){await openWholeBook(false,false);requestAnimationFrame(()=>{const moved=focusBlockId?$(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(focusBlockId))}"] .manuscript-block`):null;if(moved){moved.scrollIntoView({behavior:'auto',block:'center'});placeCaret(moved,0)}})}
  else if(visibleChapterById(result.target_chapter_id)){await selectChapter(result.target_chapter_id,false,false);requestAnimationFrame(()=>{const moved=focusBlockId?$(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(focusBlockId))}"] .manuscript-block`):null;if(moved)placeCaret(moved,0)})}
  else await openWholeBook(false,false);
  if(titleStyle)toast(i18nText('chapter.header_changed',{style:i18nSource(titleStyle==='body'?'Body':titleStyle.replace('heading','H')),target:result.target_title||i18nSource('Full Text')}));else toast(result.target_is_implicit?i18nSource('Chapter header removed. The prose remains in Full Text.'):i18nText('chapter.removed_into',{target:result.target_title||i18nSource('the Chapter above')}));
}

async function deleteFocusedChapterSelection(chapterId){
  chapterId=entityId(chapterId);const chapter=visibleChapterById(chapterId);if(!chapter)return;
  const documentId=chapter.tab_id||state.activeDocumentId,target=chapterAfterRemovalTarget(chapter);
  await flushSave();clearManuscriptSelection();keepStructureVisibleAfterDelete('chapters',documentId);
  try{await withStructuralUndo(`Delete chapter: ${chapter.title}`,()=>api(`/api/chapters/${chapterId}/permanent`,{method:'DELETE'}),{view:'manuscript',chapterId,documentId,manuscriptViewMode:'chapter'})}
  catch(error){toast(error?.message||'That Chapter could not be deleted.',5000);return}
  await refresh(false);
  if(target&&visibleChapterById(target.id))await selectChapter(target.id,false,false);else{const remaining=manuscriptTabChapterRows(documentId);if(remaining.length)await selectChapter(remaining[0].id,false,false);else await openWholeBook(false,false)}clearManuscriptSelection();
  toast('Chapter and contents deleted.');
}

function scrollBookChapter(chapterId){const heading=$(`.book-chapter-heading[data-chapter-id="${chapterId}"]`),firstBlock=$(`.manuscript-block-wrap[data-chapter-id="${chapterId}"]`);(heading||firstBlock)?.scrollIntoView({behavior:'auto',block:'start'});}

