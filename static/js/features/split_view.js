// -------------------- split view --------------------
$('#closeSplitBtn').onclick=closeSplitView;
$('#viewBtn')?.addEventListener('pointerdown',e=>e.preventDefault());
$('#viewBtn')?.addEventListener('click',e=>{e.stopPropagation();toggleViewPopover()});
$('#viewFocusBtn')?.addEventListener('click',()=>{setFocusMode(!document.body.classList.contains('focus'));closeViewPopover()});
$('#viewSplitBtn')?.addEventListener('click',()=>{closeViewPopover();openSplitViewChooser().catch(error=>{console.error('SPLIT_VIEW_001',error);toast(error.message||'Split View could not be opened.')})});
$('#viewChaptersBtn')?.addEventListener('click',()=>{setChapterMarkersVisible(!chapterMarkersVisible());syncViewChaptersControl()});
$('#viewScenesBtn')?.addEventListener('click',()=>{setSceneMarkersVisible(!sceneMarkersVisible());syncViewScenesControl()});
$('#viewFindReplaceBtn')?.addEventListener('click',()=>{closeViewPopover();openFind()});
$('#manuscriptTextSize')?.addEventListener('input',e=>applyManuscriptTextSize(e.currentTarget.value));
$('#manuscriptFont')?.addEventListener('change',e=>applyManuscriptFont(e.currentTarget.value));
window.addEventListener('resize',positionViewPopover,{passive:true});
window.addEventListener('scroll',positionViewPopover,true);
window.addEventListener('resize',positionCanvasViewPopover,{passive:true});
window.addEventListener('scroll',positionCanvasViewPopover,true);
$('.document-toolbar')?.addEventListener('scroll',closeViewPopover,{passive:true});
function clearCheckpointLiveHighlights(){$$('.checkpoint-live-change').forEach(el=>el.classList.remove('checkpoint-live-change'))}
let checkpointSplitScrollCleanup=null,checkpointSplitRenderTimer=null;
let historyWorkspace=null,historySplitScrollCleanup=null,historySplitRenderTimer=null;
function closeCheckpointComparisonUI(){
  clearTimeout(checkpointSplitRenderTimer);checkpointSplitRenderTimer=null;clearCheckpointLiveHighlights();checkpointWorkspace&&(checkpointWorkspace.changeIndex=-1,checkpointWorkspace.changeCount=0,checkpointWorkspace.changeTargets=[]);
  checkpointSplitScrollCleanup?.();checkpointSplitScrollCleanup=null;$('#checkpointCompareToolbar')?.classList.add('hidden');
}
const SPLIT_RATIO_KEY='breadmaker.splitRatio.v1';
let splitPaneRatio=.5,splitResizeActive=false,splitLayoutFrame=0;
try{const saved=Number(localStorage.getItem(SPLIT_RATIO_KEY));if(Number.isFinite(saved)&&saved>.15&&saved<.85)splitPaneRatio=saved}catch{}
function splitVisiblePanelWidth(){
  const view=$('#manuscriptView');if(!view)return 0;let width=0;
  for(const panel of $$('.right-panel',view)){if(panel.classList.contains('hidden'))continue;const style=getComputedStyle(panel);if(style.position==='absolute'||style.position==='fixed')continue;width+=panel.getBoundingClientRect().width}
  return width;
}
function splitUsableWidth(){
  const view=$('#manuscriptView'),nav=view?.querySelector('.book-nav'),grip=$('#splitResizer');if(!view)return 0;
  const navWidth=nav&&getComputedStyle(nav).display!=='none'?nav.getBoundingClientRect().width:0,gripWidth=grip&&!grip.classList.contains('hidden')?grip.getBoundingClientRect().width:0;
  return Math.max(0,view.getBoundingClientRect().width-navWidth-splitVisiblePanelWidth()-gripWidth);
}
function clampSplitRatio(value,available=splitUsableWidth()){
  if(!available)return .5;const minimum=Math.min(.42,Math.max(.2,240/available));return Math.max(minimum,Math.min(1-minimum,Number(value)||.5));
}
function applySplitRatio(value=splitPaneRatio,{persist=false}={}){
  const pane=$('#splitPane');if(!pane||pane.classList.contains('hidden'))return;const available=splitUsableWidth();if(!available)return;splitPaneRatio=clampSplitRatio(value,available);pane.style.flex=`0 0 ${Math.round(available*splitPaneRatio)}px`;
  if(persist)try{localStorage.setItem(SPLIT_RATIO_KEY,String(splitPaneRatio))}catch{}
}
function scheduleSplitLayout(){cancelAnimationFrame(splitLayoutFrame);splitLayoutFrame=requestAnimationFrame(()=>{applySplitRatio();splitLayoutFrame=requestAnimationFrame(()=>applySplitRatio())})}
function openSplitLayout(){const grip=$('#splitResizer');grip?.classList.remove('hidden');scheduleSplitLayout()}
function closeSplitLayout(){const grip=$('#splitResizer'),pane=$('#splitPane');grip?.classList.add('hidden');grip?.classList.remove('dragging');document.body.classList.remove('split-resizing');splitResizeActive=false;if(pane)pane.style.flex=''}
function resetSplitRatio(){splitPaneRatio=.5;applySplitRatio(.5,{persist:true})}
function wireSplitResizer(){
  const grip=$('#splitResizer');if(!grip)return;
  grip.addEventListener('pointerdown',event=>{if($('#splitPane')?.classList.contains('hidden'))return;splitResizeActive=true;grip.classList.add('dragging');document.body.classList.add('split-resizing');grip.setPointerCapture?.(event.pointerId);event.preventDefault()});
  grip.addEventListener('pointermove',event=>{if(!splitResizeActive)return;const pane=$('#splitPane'),available=splitUsableWidth();if(!pane||!available)return;const right=pane.getBoundingClientRect().right,gripWidth=grip.getBoundingClientRect().width,splitWidth=Math.max(0,right-event.clientX-gripWidth/2);applySplitRatio(splitWidth/available);event.preventDefault()});
  const finish=event=>{if(!splitResizeActive)return;splitResizeActive=false;grip.classList.remove('dragging');document.body.classList.remove('split-resizing');try{if(grip.hasPointerCapture?.(event.pointerId))grip.releasePointerCapture(event.pointerId)}catch{}applySplitRatio(splitPaneRatio,{persist:true})};
  grip.addEventListener('pointerup',finish);grip.addEventListener('pointercancel',finish);grip.addEventListener('dblclick',event=>{event.preventDefault();resetSplitRatio()});
  grip.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();const delta=event.key==='ArrowLeft'?.025:-.025;applySplitRatio(splitPaneRatio+delta,{persist:true})}else if(event.key==='Home'){event.preventDefault();resetSplitRatio()}});
  window.addEventListener('resize',scheduleSplitLayout,{passive:true});
  const observer=new MutationObserver(()=>{if(!splitResizeActive&&!$('#splitPane')?.classList.contains('hidden'))scheduleSplitLayout()});
  for(const id of ['checkpointPanel','cutsPanel','commentsPanel','markupPanel','manuscriptImagePanel','manuscriptReviewsPanel']){const panel=$('#'+id);if(panel)observer.observe(panel,{attributes:true,attributeFilter:['class']})}
}
wireSplitResizer();
function closeSplitView(){closeCheckpointComparisonUI();closeHistoryComparisonUI();state.split=null;$('#splitTaskAdd')?.classList.add('hidden');$('#splitPane').classList.add('hidden');closeSplitLayout();$('#splitBody').classList.remove('checkpoint-split-body');$('#splitBody').innerHTML=''}
function showSplit(title,html,type,id){closeCheckpointComparisonUI();closeHistoryComparisonUI();state.split={type,id};$('#splitTitle').textContent=i18nSource(title);$('#splitTaskAdd')?.classList.toggle('hidden',type!=='tasks');$('#splitBody').classList.remove('checkpoint-split-body');$('#splitBody').innerHTML=html;bindReadOnlyImageDescriptions($('#splitBody'));$('#splitPane').classList.remove('hidden');openSplitLayout()}
function splitManuscriptBlockHtml(block){
  if(block.block_type!=='image')return `<div class="split-manuscript-block ${escapeHtml(block.semantic_style||'body')}">${escapeHtml(block.text_content||'')}</div>`;
  const width=Math.max(5,Math.min(100,Number(block.image_width)||100)),footer=String(block.image_footer||'').trim();
  const rowId=String(block.image_row_id||'').trim(),rowAttrs=rowId?` data-image-row-id="${escapeHtml(rowId)}" data-image-row-width="${width}"`:'';
  return `<figure class="split-image-figure"${rowAttrs}><img class="split-image" style="width:${rowId?100:width}%" src="${escapeHtml(projectAssetDisplayUrl(block.image_path))}" alt="${escapeHtml(block.image_description||'')}">${footer?`<figcaption>${escapeHtml(block.image_footer||'')}</figcaption>`:''}</figure>`;
}
function splitTabRows(){return [...(state.documents||[])].sort((a,b)=>(+a.position||0)-(+b.position||0)||compareEntityIds(a.id,b.id))}
function splitSceneRows(tabId){return (state.scenes||[]).filter(scene=>entityId(scene.tab_id)===entityId(tabId)).sort((a,b)=>(+a.position||0)-(+b.position||0)||compareEntityIds(a.id,b.id))}
async function openSplitViewChooser(){
  await ensureCheckpointWorkspace(false);
  const tabs=splitTabRows();
  let chooserTabId=tabs.some(tab=>entityId(tab.id)===entityId(state.activeDocumentId))?entityId(state.activeDocumentId):(entityId(tabs[0]?.id)||0);
  const option=(value,label,selected=false)=>`<option value="${escapeHtml(value)}"${selected?' selected':''}>${escapeHtml(label)}</option>`;
  const typeOptions=[
    option('chapter',i18nSource('Chapter')),
    option('scene',i18nSource('Scene')),
    option('tab',i18nText('split.full_tab')),
    option('checkpoint',i18nSource('Checkpoint')),
    option('history',i18nSource('History')),
    option('canvas',i18nSource('Canvas')),
    option('map',i18nSource('Map')),
    option('tasks',i18nSource('Tasks')),
  ].join('');
  showModal('Split View',`<div class="field"><label>{{i18n:dynamic.html.text.label.7579a345}}</label><select id="splitType">${typeOptions}</select></div><div id="splitTarget"></div>`,'<button id="openSplitConfirm">{{i18n:dynamic.html.text.button.63c52603}}</button>');

  const tabOptions=()=>tabs.map(tab=>option(tab.id,tab.title||i18nSource('Untitled'),entityId(tab.id)===entityId(chooserTabId))).join('');
  const emptyTarget=()=>`<p class="small-muted">${escapeHtml(i18nSource('Nothing available.'))}</p>`;
  const entityOptions=(rows,labeler,selectedId=null)=>rows.map(row=>option(row.id,labeler(row),selectedId!=null&&entityId(row.id)===entityId(selectedId))).join('');
  const field=(label,body)=>`<div class="field"><label>${escapeHtml(label)}</label>${body}</div>`;

  const refresh=async()=>{
    const type=$('#splitType').value,box=$('#splitTarget');
    if(type==='tasks'){box.innerHTML='';return}

    if(type==='chapter'||type==='scene'){
      const rows=type==='chapter'?visibleChapters(chooserTabId):splitSceneRows(chooserTabId);
      const targetLabel=i18nSource(type==='chapter'?'Chapter':'Scene');
      const selectedId=entityId(chooserTabId)===entityId(state.activeDocumentId)?(type==='chapter'?state.chapter?.id:state.scene?.id):null;
      box.innerHTML=field(i18nSource('Tab'),`<select id="splitTabSelect">${tabOptions()}</select>`)+field(targetLabel,rows.length?`<select id="splitTargetSelect">${entityOptions(rows,row=>row.title||i18nSource('Untitled'),selectedId)}</select>`:emptyTarget());
      $('#splitTabSelect').onchange=()=>{chooserTabId=entityId($('#splitTabSelect').value);refresh()};
      return;
    }

    if(type==='tab'){
      box.innerHTML=field(i18nSource('Tab'),tabs.length?`<select id="splitTargetSelect">${tabOptions()}</select>`:emptyTarget());
      $('#splitTargetSelect')?.addEventListener('change',event=>{chooserTabId=entityId(event.currentTarget.value)});
      return;
    }

    let rows=[],label='';
    if(type==='canvas'){rows=state.canvases;label='Canvas'}
    else if(type==='map'){rows=state.maps;label='Map'}
    else if(type==='history'){
      if(!historyWorkspace)historyWorkspace={items:[],selectedId:null,currentGeneration:null,showDiff:false,linkedScroll:true,changeIndex:-1,changeCount:0,changeTargets:[],scope:null,chapterId:null};
      if(!historyWorkspace.items.length){const history=await api('/api/history/v2');historyWorkspace.items=history.revisions||[];historyWorkspace.currentGeneration=+history.current_generation||null}
      rows=historyWorkspace.items;label='History';
    }else{rows=checkpointWorkspace.items;label='Checkpoint'}
    const labeler=row=>type==='checkpoint'?checkpointOptionLabel(row):type==='history'?historyDisplayName(row):(row.title||row.name||i18nSource('Untitled'));
    const emptyMessage=type==='checkpoint'?'No checkpoints have been saved yet.':type==='history'?'No automatic History is available yet.':'Nothing available.';
    box.innerHTML=field(i18nSource(label),rows.length?`<select id="splitTargetSelect">${entityOptions(rows,labeler)}</select>`:`<p class="small-muted">${escapeHtml(i18nSource(emptyMessage))}</p>`);
  };

  $('#splitType').onchange=refresh;
  await refresh();
  $('#openSplitConfirm').onclick=async()=>{
    const type=$('#splitType').value,raw=$('#splitTargetSelect')?.value||'',id=type==='history'?String(raw):entityId(raw);
    if(type!=='tasks'&&!id)return toast('Choose something to open');
    dismissModalTree();
    if(type==='chapter')await openChapterSplit(id);
    else if(type==='scene')await openSceneSplit(id);
    else if(type==='tab')await openTabSplit(id);
    else if(type==='checkpoint')await openCheckpointSplit(id);
    else if(type==='history')await openHistorySplit(id);
    else if(type==='canvas')await openCanvasSplit(id);
    else if(type==='map')await openMapSplit(id);
    else await openTasksSplit();
  };
}
async function openChapterSplit(chapterId){await flushSave();const data=await api(`/api/chapters/${chapterId}`);showSplit(data.chapter.title,`<article class="split-paper"><h2>${escapeHtml(data.chapter.title)}</h2>${data.blocks.map(splitManuscriptBlockHtml).join('')}</article>`,'chapter',chapterId)}
async function openSceneSplit(sceneId){await flushSave();const data=await api(`/api/scenes/${sceneId}/manuscript`);showSplit(data.scene.title,`<article class="split-paper"><h2>${escapeHtml(data.scene.title)}</h2>${data.blocks.map(splitManuscriptBlockHtml).join('')}</article>`,'scene',sceneId)}
async function openTabSplit(tabId){await flushSave();const data=await api(`/api/book?tab_id=${encodeURIComponent(tabId)}`),blocksByChapter=new Map();for(const block of data.blocks||[]){const id=entityId(block.chapter_id);if(!blocksByChapter.has(id))blocksByChapter.set(id,[]);blocksByChapter.get(id).push(block)}const body=(data.chapters||[]).map(chapter=>`${chapter.is_implicit?'':`<h3 class="split-tab-chapter-title">${escapeHtml(chapter.title||i18nSource('Untitled'))}</h3>`}${(blocksByChapter.get(entityId(chapter.id))||[]).map(splitManuscriptBlockHtml).join('')}`).join('');showSplit(data.tab.title,`<article class="split-paper split-tab-paper"><h2>${escapeHtml(data.tab.title)}</h2>${body}</article>`,'tab',tabId)}
async function openCanvasSplit(canvasId){
  const data=await api(`/api/canvases/${canvasId}`),developmental=data.canvas.canvas_type==='developmental',allNodes=data.nodes||[],allEdges=data.edges||[];
  const isStructure=n=>['chapter','scene'].includes(n.node_type)&&((n.node_type==='chapter'&&n.chapter_id)||(n.node_type==='scene'&&n.scene_id)),isPlanning=n=>n.node_type==='free',inLane=n=>developmental&&Number(n.developmental_hidden||0)!==1&&n.lane_position!==null&&n.lane_position!==undefined&&(isStructure(n)||isPlanning(n));
  const laneNodes=developmental?allNodes.filter(inLane).sort((a,b)=>(Number(a.lane_position)-Number(b.lane_position))||(a.id-b.id)):[],nodes=developmental?allNodes.filter(n=>!inLane(n)&&Number(n.developmental_hidden||0)!==1):allNodes;
  const laneHtml=developmental?`<div class="split-developmental-lane"><div class="split-developmental-lane-label">{{i18n:dynamic.html.text.div.ee7fca73}}</div><div class="split-developmental-lane-track">${laneNodes.map(n=>`<div class="split-developmental-tile"><small>${escapeHtml(i18nSource(n.node_type==='scene'?'Scene':n.node_type==='chapter'?'Chapter':'Card'))}</small><b>${escapeHtml(n.title||i18nSource('Untitled'))}</b></div>`).join('')||'<span class="small-muted">{{i18n:dynamic.html.text.span.6a728fe6}}</span>'}</div></div>`:'';
  const stageHtml='<div id="splitCanvasStage" class="split-canvas-stage"><svg id="splitCanvasEdges" class="split-canvas-edges"></svg><div id="splitCanvasNodes"></div></div>',body=developmental?`<div class="split-developmental-canvas">${laneHtml}<div class="split-developmental-workspace">${stageHtml}</div></div>`:stageHtml;
  showSplit(data.canvas.name,body,'canvas',canvasId);
  const stage=$('#splitCanvasStage'),nodeExtent=n=>{const size=n.node_type==='routing'?{w:12,h:12}:n.node_type==='label'?{w:40,h:40}:{w:Number(n.w)||220,h:Number(n.h)||125};return{x:(Number(n.x)||0)+size.w,y:(Number(n.y)||0)+size.h}},width=Math.max(520,...nodes.map(n=>nodeExtent(n).x+80)),height=Math.max(420,...nodes.map(n=>nodeExtent(n).y+80));stage.style.width=`${width}px`;stage.style.height=`${height}px`;
  const nodeBox=$('#splitCanvasNodes');for(const n of nodes){const el=document.createElement('div'),special=n.node_type==='routing'?' routing routing-node':n.node_type==='label'?' label-node':n.node_type==='image'?' image-object':'';el.className=`split-canvas-node ${n.shape||'rect'}${special}`;el.style.left=`${Number(n.x)||0}px`;el.style.top=`${Number(n.y)||0}px`;if(n.node_type==='routing'){el.style.width='12px';el.style.height='12px';el.innerHTML='<span class="routing-glyph" aria-hidden="true"></span>'}else if(n.node_type==='label'){el.style.width='40px';el.style.height='40px';if(!indexUsesThemeColors(n)){if(n.color)el.style.background=n.color;if(n.text_color)el.style.color=n.text_color}el.innerHTML=`<span class="label-value" style="font-size:${labelFontSize(n.title)}px">${escapeHtml(n.title||'')}</span>`}else{el.style.width=`${Number(n.w)||220}px`;el.style.height=`${Number(n.h)||125}px`;if(n.shape!=='text'&&n.node_type!=='image'&&n.color)el.style.background=n.color;const splitText=(n.node_type==='free'||['chapter','scene'].includes(n.node_type))?automaticCanvasTextColor(n):n.text_color;if(splitText)el.style.color=splitText;if(n.image_path)el.innerHTML=`<img src="${escapeHtml(projectAssetDisplayUrl(n.image_path))}" alt="">`;else{const mode=n.node_type==='free'?cardDisplayMode(n):'full';el.classList.add(`card-display-${mode}`);const title=mode==='body'?'':`<b>${escapeHtml((n.icon?`${n.icon} `:'')+(n.title||''))}</b>`,bodyText=mode==='header'?'':(n.body?`<p>${escapeHtml(n.body)}</p>`:'');el.innerHTML=title+bodyText}}nodeBox.appendChild(el)}
  const visibleIds=new Set(nodes.map(n=>n.id)),edges=allEdges.filter(edge=>visibleIds.has(edge.from_node_id)&&visibleIds.has(edge.to_node_id)&&Number(edge.developmental_hidden||0)!==1),svg=$('#splitCanvasEdges');svg.setAttribute('width',width);svg.setAttribute('height',height);for(const edge of edges){const a=nodes.find(n=>n.id===edge.from_node_id),b=nodes.find(n=>n.id===edge.to_node_id);if(!a||!b)continue;const geometry=edgeGeometry(a,b,edge.from_port,edge.to_port,edge.route_style||'curved'),path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('class','edge-line');path.setAttribute('d',edgeVisualPath(geometry,edge,b));path.setAttribute('stroke',edgeVisualColor(edge));path.style.strokeWidth=String(Number(edge.line_width)||1.5);const dash=edgeDashArray(edge);if(dash)path.setAttribute('stroke-dasharray',dash);svg.appendChild(path);const label=String(edge.label||'').trim();if(label){const[x,y]=edgeMidPoint(geometry),text=document.createElementNS('http://www.w3.org/2000/svg','text');text.setAttribute('class','edge-label');text.setAttribute('x',x);text.setAttribute('y',y-10);text.setAttribute('text-anchor','middle');text.setAttribute('fill',edgeVisualColor(edge));text.textContent=label;svg.appendChild(text)}const end=edgeEndElement(edge,b,geometry.endTangent);if(end)svg.appendChild(end)}
}

async function openMapSplit(mapId){const data=await api(`/api/maps/${mapId}`);showSplit(data.map.name,'<div class="split-map-stage"><div class="split-map-world"><svg id="splitMapSvg" class="map-svg" xmlns="http://www.w3.org/2000/svg"></svg></div></div>','map',mapId);const svg=$('#splitMapSvg'),stage=svg.closest('.split-map-stage');applyMapTheme(stage,data);const layout=mapLogicalLayout(data,1),zoom=Math.max(.25,Math.min(1,Math.min((stage.clientWidth-20)/layout.width,(stage.clientHeight-20)/layout.height)));renderMapSvg(svg,data,{interactive:false,zoom})}
function splitTasksEmptyHtml(){return '<div class="split-task-empty"><span>{{i18n:dynamic.html.text.span.731abf02}}</span></div>'}
const splitTaskExpandedMilestones=new Set();
function splitTaskHasOpenMilestones(task){return Math.max(0,(+task?.milestone_count||0)-(+task?.milestone_completed_count||0))>0}
async function completeTaskFromSplit(task,row){
  if(!task||!row||row.dataset.completing==='1')return;row.dataset.completing='1';const checkbox=row.querySelector('.split-task-main .split-task-check input');if(checkbox)checkbox.disabled=true;
  try{
    await withStructuralUndo(`Complete task: ${task.title}`,()=>api(`/api/tasks/${task.id}`,{method:'PATCH',body:JSON.stringify({status:'done'})}),{view:currentView(),chapterId:state.chapter?.id});
    splitTaskExpandedMilestones.delete(entityId(task.id));row.remove();if(!$('#splitBody [data-split-task]'))$('#splitBody').innerHTML=splitTasksEmptyHtml();if($('#tasksView').classList.contains('active'))await loadTasks();
  }catch(error){if(checkbox){checkbox.checked=false;checkbox.disabled=false}row.dataset.completing='';toast(error.message||'Task could not be completed.')}
}
function renderSplitMilestoneRows(task,row,milestones){
  const box=row?.querySelector('[data-split-milestones]');if(!box)return;const openMilestones=(milestones||[]).filter(m=>!m.completed);
  if(!openMilestones.length){box.innerHTML='';box.classList.add('hidden');splitTaskExpandedMilestones.delete(entityId(task.id));const toggle=row.querySelector('[data-split-milestone-toggle]');if(toggle)toggle.remove();return}
  box.classList.remove('hidden');box.innerHTML=openMilestones.map(m=>`<div class="split-task-milestone" data-split-milestone="${m.id}"><label class="split-task-milestone-check" title="{{i18n:dynamic.html.attr.title.bccd31d1}}"><input type="checkbox" aria-label="${escapeHtml(i18nText('tasks.complete_named',{title:m.title}))}"></label><button type="button" class="split-task-milestone-title">${escapeHtml(m.title)}</button></div>`).join('');
  for(const milestoneRow of box.querySelectorAll('[data-split-milestone]')){const milestone=openMilestones.find(m=>entityId(m.id)===entityId(milestoneRow.dataset.splitMilestone));if(!milestone)continue;milestoneRow.querySelector('.split-task-milestone-title').onclick=e=>{e.stopPropagation();openTaskDetails(task.id)};const check=milestoneRow.querySelector('input');check.onchange=async e=>{e.stopPropagation();if(!e.target.checked)return;e.target.disabled=true;milestoneRow.classList.add('completing');try{await withStructuralUndo(`Complete milestone: ${milestone.title}`,()=>api(`/api/task-milestones/${milestone.id}`,{method:'PATCH',body:JSON.stringify({completed:true})}),{view:currentView(),chapterId:state.chapter?.id});milestoneRow.remove();task.milestone_completed_count=(+task.milestone_completed_count||0)+1;if(!box.querySelector('[data-split-milestone]')){box.classList.add('hidden');splitTaskExpandedMilestones.delete(entityId(task.id));row.querySelector('[data-split-milestone-toggle]')?.remove()}if($('#tasksView').classList.contains('active'))await loadTasks()}catch(error){e.target.checked=false;e.target.disabled=false;milestoneRow.classList.remove('completing');toast(error.message||'Milestone could not be completed.')}}}
}
async function expandSplitTaskMilestones(task,row){
  const toggle=row?.querySelector('[data-split-milestone-toggle]'),box=row?.querySelector('[data-split-milestones]');if(!toggle||!box)return;const id=entityId(task.id);
  if(splitTaskExpandedMilestones.has(id)){splitTaskExpandedMilestones.delete(id);toggle.setAttribute('aria-expanded','false');row.classList.remove('milestones-expanded');box.classList.add('hidden');box.innerHTML='';return}
  splitTaskExpandedMilestones.add(id);toggle.setAttribute('aria-expanded','true');row.classList.add('milestones-expanded');box.classList.remove('hidden');box.innerHTML='<div class="split-task-milestone-loading">{{i18n:dynamic.html.text.div.6f4f7e1f}}</div>';
  try{const milestones=await apiList(`/api/tasks/${task.id}/milestones`,'milestones');if(state.split?.type!=='tasks'||!document.body.contains(row)||!splitTaskExpandedMilestones.has(id))return;renderSplitMilestoneRows(task,row,milestones)}catch(error){splitTaskExpandedMilestones.delete(id);toggle.setAttribute('aria-expanded','false');row.classList.remove('milestones-expanded');box.classList.add('hidden');box.innerHTML='';toast(error.message||'Milestones could not be loaded.')}
}
async function renderTasksSplitBody(){
  if(state.split?.type!=='tasks')return;const tasks=(await getTasks()).filter(t=>t.status==='open'),body=$('#splitBody');if(!body)return;const liveIds=new Set(tasks.map(t=>entityId(t.id)));for(const id of [...splitTaskExpandedMilestones])if(!liveIds.has(id))splitTaskExpandedMilestones.delete(id);
  body.innerHTML=tasks.length?`<div class="split-reference-list">${tasks.map(t=>{const hasMilestones=splitTaskHasOpenMilestones(t),expanded=hasMilestones&&splitTaskExpandedMilestones.has(entityId(t.id));return `<article class="split-task-row ${expanded?'milestones-expanded':''}" data-split-task="${t.id}"><div class="split-task-main" tabindex="0"><label class="split-task-check" title="{{i18n:dynamic.html.attr.title.9623e95b}}"><input type="checkbox" aria-label="${escapeHtml(i18nText('tasks.complete_named',{title:t.title}))}"></label><button type="button" class="split-task-body"><b>${escapeHtml(t.title)}</b>${t.description?`<p>${escapeHtml(t.description)}</p>`:''}</button>${hasMilestones?`<button type="button" class="split-task-milestone-toggle" data-split-milestone-toggle aria-label="${escapeHtml(i18nText('tasks.toggle_milestones',{action:i18nSource(expanded?'Collapse':'Show'),title:t.title}))}" aria-expanded="${expanded?'true':'false'}"><span aria-hidden="true">›</span></button>`:''}</div><div class="split-task-milestones ${expanded?'':'hidden'}" data-split-milestones>${expanded?'<div class="split-task-milestone-loading">{{i18n:dynamic.html.text.div.6f4f7e1f}}</div>':''}</div></article>`}).join('')}</div>`:splitTasksEmptyHtml();
  for(const row of body.querySelectorAll('[data-split-task]')){const task=tasks.find(t=>entityId(t.id)===entityId(row.dataset.splitTask));if(!task)continue;const open=()=>openTaskDetails(task.id),main=row.querySelector('.split-task-main'),taskBody=row.querySelector('.split-task-body');main.onclick=e=>{if(e.target.closest('input,label,[data-split-milestone-toggle]'))return;open()};taskBody.onclick=e=>{e.stopPropagation();open()};main.onkeydown=e=>{if((e.key==='Enter'||e.key===' ')&&!e.target.closest('input,button')){e.preventDefault();open()}};const check=row.querySelector('.split-task-main .split-task-check input');if(check)check.onchange=e=>{e.stopPropagation();if(e.target.checked)completeTaskFromSplit(task,row)};const toggle=row.querySelector('[data-split-milestone-toggle]');if(toggle)toggle.onclick=e=>{e.stopPropagation();expandSplitTaskMilestones(task,row)};if(splitTaskExpandedMilestones.has(entityId(task.id))&&toggle){try{const milestones=await apiList(`/api/tasks/${task.id}/milestones`,'milestones');if(state.split?.type==='tasks'&&document.body.contains(row)&&splitTaskExpandedMilestones.has(entityId(task.id)))renderSplitMilestoneRows(task,row,milestones)}catch(error){splitTaskExpandedMilestones.delete(entityId(task.id));toggle.setAttribute('aria-expanded','false');row.classList.remove('milestones-expanded');row.querySelector('[data-split-milestones]')?.classList.add('hidden')}}}
}
async function openTasksSplit(){splitTaskExpandedMilestones.clear();showSplit('Tasks','<div class="split-task-empty"><span>{{i18n:dynamic.html.text.span.a5f99487}}</span></div>','tasks',null);await renderTasksSplitBody()}
$('#splitTaskAdd').onclick=()=>openTaskDetails(null);


