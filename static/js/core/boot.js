const LOCAL_MANUSCRIPT_IMAGE_ACCEPT='image/png,image/jpeg,image/webp,image/gif,image/avif,image/bmp,.png,.jpg,.jpeg,.webp,.gif,.avif,.bmp';
const HOSTED_SAFE_IMAGE_ACCEPT='image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp';
function applyRuntimeImageInputPolicy(){
  const hosted=runtimeSupports('hosted_projects'),manuscript=$('#manuscriptImageFileInput'),canvas=$('#imageInput');
  if(manuscript)manuscript.accept=hosted?HOSTED_SAFE_IMAGE_ACCEPT:LOCAL_MANUSCRIPT_IMAGE_ACCEPT;
  if(canvas)canvas.accept=hosted?HOSTED_SAFE_IMAGE_ACCEPT:'image/*';
}


const HOSTED_FIRST_DRAFT_KEY='breadmaker-hosted-first-draft';
let hostedFirstProjectCreating=false;
function hostedBlankDraftSnapshot(){
  return {title:String($('#chapterTitle')?.innerText||'Chapter 1').replace(/\n+/g,' ').trim()||'Chapter 1',text:String($('#editor')?.innerText||'')};
}
async function createHostedProjectFromBlankEditor(){
  if(hostedFirstProjectCreating)return;hostedFirstProjectCreating=true;
  const draft=hostedBlankDraftSnapshot();$('#manuscriptPaper').contentEditable='false';setSaveState('Creating…');
  try{
    const result=await api('/api/projects',{method:'POST',body:JSON.stringify({name:'Untitled Book'})});
    const projectId=String(result?.project?.id||'');if(!projectId)throw new Error('PROJECT_CREATE_ID_MISSING');
    try{sessionStorage.setItem(HOSTED_FIRST_DRAFT_KEY,JSON.stringify({project_id:projectId,...draft}))}catch{}
    openWorkspaceProject(projectId,{replace:true});
  }catch(error){hostedFirstProjectCreating=false;$('#manuscriptPaper').contentEditable='true';setSaveState('Not saved');toast(error.message||'Could not create project',5000)}
}
function bootHostedBlankProject(){
  state.hostedZeroProject=true;state.project=null;state.documents=[];state.chapters=[];state.chapter=null;state.chapterData=null;state.activeDocumentId=null;state.viewMode='chapter';
  document.body.classList.add('hosted-zero-project');$('#chapterTitle').textContent='Chapter 1';$('#editor').innerHTML='';$('#manuscriptPaper').contentEditable='true';
  $$('.view').forEach(v=>v.classList.remove('active'));$('#manuscriptView').classList.add('active');$$('.main-tab').forEach(tab=>tab.classList.toggle('active',tab.dataset.view==='manuscript'));
  setSaveState('Not saved');document.documentElement.removeAttribute('data-initial-view');
  const paper=$('#manuscriptPaper');
  paper.addEventListener('beforeinput',event=>{if(state.hostedZeroProject&&!hostedFirstProjectCreating)event.stopImmediatePropagation()},true);
  const capture=event=>{if(!state.hostedZeroProject||hostedFirstProjectCreating)return;event.stopImmediatePropagation();queueMicrotask(()=>{const draft=hostedBlankDraftSnapshot();if(draft.text.trim()||draft.title!=='Chapter 1')createHostedProjectFromBlankEditor()})};
  paper.addEventListener('input',capture,true);queueMicrotask(()=>$('#editor')?.focus());
}
async function restoreHostedFirstDraft(){
  if(!runtimeSupports('hosted_projects')||!state.project)return;let raw=null;try{raw=JSON.parse(sessionStorage.getItem(HOSTED_FIRST_DRAFT_KEY)||'null')}catch{}
  if(!raw||String(raw.project_id||'')!==String(currentProjectIdentity()||''))return;try{sessionStorage.removeItem(HOSTED_FIRST_DRAFT_KEY)}catch{}
  const title=String(raw.title||'Chapter 1').trim()||'Chapter 1',text=String(raw.text||'');
  if(state.viewMode!=='chapter'||!state.chapter)return;$('#chapterTitle').textContent=title;const prose=$('.manuscript-block');if(prose)prose.textContent=text;
  if(title!==String(state.chapter.title||'')||text.trim()){markDirty(+state.chapter.id);await flushSave()}
}

// -------------------- lease / boot --------------------
let leaseLost = false;
function showLeaseLost(initial=false) {
  if (leaseLost) return;
  leaseLost = true;
  clearTimeout(state.saveTimer);
  $('#editor').contentEditable = 'false';
  $('#chapterTitle').contentEditable = 'false';
  $('#manuscriptPaper').contentEditable = 'false';
  const message = initial
    ? 'This project is already being edited in another Breadmaker browser tab.'
    : 'This tab lost editing control because another Breadmaker tab became active.';
  showModal('Project already open', `<p>${escapeHtml(i18nSource(message))}</p><p class="small-muted">{{i18n:dynamic.html.text.p.549806ae}}</p>`, '<button id="reloadLease">{{i18n:dynamic.html.text.button.959be4d5}}</button>');
  $('#reloadLease').onclick = () => location.reload();
}
async function heartbeatLease() {
  try { await api('/api/lease', {method:'POST'}); }
  catch (error) { if (error.status === 409) showLeaseLost(false); else console.warn('Lease heartbeat failed', error); }
}
window.addEventListener('pagehide', e => {
  if (e.persisted) return;
  try { const pid=currentProjectIdentity(),uid=runtimeSupports('account_sessions')?'':currentUserIdentity();fetch('/api/lease/release', {method:'POST', headers:{'X-Breadmaker-Client':CLIENT_ID,...(pid?{'X-Breadmaker-Project':pid}:{}),...(uid?{'X-Breadmaker-User':uid}:{})}, keepalive:true}).catch(()=>{}); } catch {}
});

const BOOT_UI_STATE_KEYS=['tabs-expanded','book-nav-collapsed','manuscript-outline-collapse','manuscript-tab-presentation','canvas-tree','canvas-rail','canvas-active','map-tree','map-rail','experimental-features','map-active','task-filter','review-rail','review-active','manuscript-reviews','manuscript-last-location'];
async function loadBootUIState(){
  try{return (await api(`/api/ui-state?keys=${encodeURIComponent(BOOT_UI_STATE_KEYS.join(','))}`)).values||{}}
  catch(error){console.warn('Could not batch-load UI state',error);return{}}
}

const LOCAL_RUNTIME_FALLBACK={
  mode:'local',
  capabilities:{offline:true,local_profiles:true,native_filesystem:true,portable_project_export:true,removable_backups:true,direct_remote_backups:true,request_scoped_projects:true,request_scoped_actors:true,request_scoped_recovery:true,project_scoped_editor_leases:true,hosted_projects:false,account_sessions:false,test_auth_bootstrap:false,account_authentication:false,hosted_collaboration:false,realtime_collaboration:false},
  metadata:{persistence:'local_workspace',identity:'local_profiles',production_ready:true},
  auth:{enabled:false,auth_mode:'none',server_side:false},
  project_header:'X-Breadmaker-Project',
  actor_header:'X-Breadmaker-User',
};
async function loadRuntimeBootstrap(){
  try{return await api('/api/runtime')}
  catch(error){
    const localHost=['127.0.0.1','localhost','::1'].includes(String(location.hostname||'').toLowerCase());
    if(![404,500].includes(Number(error?.status))||!localHost)throw error;
    // Desktop releases prior to the bootstrap seam have always exposed /api/health.
    // A local-only fallback is safe only after that endpoint attests that the process
    // is Breadmaker itself and that its runtime is local. Hosted/account modes never
    // guess capabilities from a missing endpoint.
    const health=await api('/api/health');
    if(health?.app!=='Breadmaker Editor'||health?.runtime!=='local')throw error;
    const backendVersion=String(health?.version||'');
    console.warn('Breadmaker runtime bootstrap endpoint was unavailable; continuing with attested local capabilities.',{frontend:'10.5.41',backend:backendVersion||'unknown'});
    return {...LOCAL_RUNTIME_FALLBACK,fallback:true,backend_version:backendVersion};
  }
}

async function boot() {
  // Locale and runtime metadata are independent network reads. Start them together so
  // desktop launch does not pay one full request/parse round trip before beginning the
  // next. Neither is project scoped.
  const [,runtimePayload]=await Promise.all([ensureI18n(),loadRuntimeBootstrap()]);
  // Focus/Reviewer shells are transient session modes and must never suppress the
  // application chrome during a fresh document boot.
  document.body.classList.remove('focus','reviewer-mode-active');
  state.runtime=runtimePayload;
  applyRuntimeImageInputPolicy();
  restoreManuscriptTextSize();restoreManuscriptFont();restoreMapRulerScale();restoreMapTextSize();restoreMapFont();setTheme(localStorage.getItem('breadmaker-theme')||'light');

  // Profile, detached Review status, and workspace selection are mutually independent
  // bootstrap reads. The server resolves the active local/account identity when these
  // requests do not yet carry an explicit browser-side actor header.
  const profilePromise=loadUserProfile();
  const portablePromise=api('/api/portable-review/status').catch(()=>({active:false}));
  const workspacePromise=api('/api/workspace');
  const [,portable,workspace]=await Promise.all([profilePromise,portablePromise,workspacePromise]);
  if(portable?.active){if(portable.locked){const unlocked=await unlockPortableBreadReview(portable);if(!unlocked)return;await openPortableReviewerMode(unlocked.payload,{temporary:!!unlocked.temporary,filename:unlocked.filename||''});return}await openPortableReviewerMode(portable.payload,{temporary:!!portable.temporary,filename:portable.filename||''});return}

  // Resolve the profile's actual project before any project-scoped request. Startup
  // then hydrates bootstrap + collaborator UI preferences concurrently instead of
  // serially opening a transaction/request for every workspace preference.
  state.workspace=workspace;
  const routedProject=projectRouteIdentity();
  if(runtimeSupports('hosted_projects')&&routedProject){
    const allowed=(state.workspace?.projects||[]).some(project=>String(project.id)===routedProject);
    if(!allowed)throw new Error('Project not found or you do not have access.');
    if(String(state.workspace?.active_project_id||'')!==routedProject){await api(`/api/projects/${routedProject}/activate`,{method:'POST'});state.workspace=await api('/api/workspace')}
  }
  if(state.workspace?.runtime&&state.workspace.runtime!==state.runtime?.mode)throw new Error('Breadmaker runtime mismatch. Reload the application.');
  if(state.workspace?.capabilities)state.runtime={...state.runtime,mode:state.workspace.runtime||state.runtime.mode,capabilities:{...state.workspace.capabilities}};
  applyRuntimeImageInputPolicy();
  if(state.workspace?.recovery){await openRecoveryBoot();return}
  if(runtimeSupports('hosted_projects')&&!(state.workspace?.projects||[]).length){bootHostedBlankProject();return}
  if(runtimeSupports('hosted_projects')&&!routedProject&&state.workspace?.active_project_id){history.replaceState({breadmakerView:'manuscript'},'',hostedProjectRoute(state.workspace.active_project_id,'manuscript'))}

  const bootView=viewFromRoute();
  // Lease acquisition is required before editing, but it does not have to serialize
  // read-only bootstrap/UI-state hydration. Start all of them together, then refuse to
  // expose an editable workspace if the lease was lost.
  const leasePromise=api('/api/lease',{method:'POST'}).then(()=>true).catch(error=>{if(error.status===409)return false;throw error});
  const [leaseGranted,bootstrap,bootUI,bootCrashDrafts]=await Promise.all([
    leasePromise,
    api('/api/bootstrap'),
    loadBootUIState(),
    crashJournalForProject(currentProjectIdentity()),
  ]);
  if(!leaseGranted){showLeaseLost(true);return}
  setInterval(heartbeatLease,5000);
  state.bootUIState=bootUI;
  invalidateTaskCache();invalidateQuickOpenDetailIndex();applyBootstrapData(bootstrap);

  // Hydrate all lightweight navigation state without painting intermediate defaults.
  // Only the routed heavy workspace is loaded below; inactive Canvas/Map/Manuscript
  // payloads stay cold until the user actually visits them.
  await Promise.all([
    restoreCanvasUIState(bootUI,{render:false}),
    restoreManuscriptUIState(bootUI,{render:false}),
    restoreMapsUIState(bootUI,{render:false}),
    restoreTaskUIState(bootUI),
    restoreReviewUIState(bootUI,{render:false}),
    restoreManuscriptReviewsUI(bootUI),
  ]);
  renderDocuments();renderChapterTree();applyOutlineVisibility();syncBookmarkToolbar();renderCanvasRail();renderMarkers();renderMapSwitcher();renderReviews();

  if(bootView==='manuscript'||bootView==='canvas'){
    await hydratePrimaryWorkspace(bootView);
  }else if(bootView==='maps'&&state.maps.length){
    const rememberedMapId=state.currentMap?.id&&state.maps.some(map=>+map.id===+state.currentMap.id)?+state.currentMap.id:+state.maps[0].id;
    if(rememberedMapId)await selectMap(rememberedMapId,{persist:false});
  }

  setView(bootView,{historyMode:'replace'});
  await restoreHostedFirstDraft();
  // Normal application boot must never finish inside a transient Focus/Reviewer shell.
  // Reviewer Mode returns earlier above, so this is safe and keeps global profile chrome
  // reachable even if stale transient UI state leaked into the document during restore.
  if(!state.reviewerSession)document.body.classList.remove('focus','reviewer-mode-active');
  // Prime Full Text only after the routed workspace is interactive. This keeps boot
  // light while making the first Book/Full Text switch a cache hit in normal use.
  if(bootView==='manuscript'&&state.activeDocumentId){const prefetch=()=>loadBookData(state.activeDocumentId).catch(()=>{});if('requestIdleCallback'in window)requestIdleCallback(prefetch,{timeout:1800});else setTimeout(prefetch,700)}
  if(bootView==='manuscript'&&manuscriptReviewsWorkspace.panelOpen){
    manuscriptReviewsWorkspace.panelOpen=false;
    await openManuscriptReviewsPanel();
  }
  setInterval(()=>captureAutoHistory('continuous').catch(err=>console.warn('Automatic history save failed',err)),5*60*1000);
  setTimeout(()=>api('/api/integrity/deep').then(result=>{if(!result.ok)toast(i18nText('recovery.integrity_warning'),7000);else if(result.disk?.warning)toast(i18nText('recovery.low_disk_warning'),7000)}).catch(console.warn),2500);
  setTimeout(()=>checkCrashJournal(bootCrashDrafts).catch(console.warn),500);
  setTimeout(()=>{if(typeof resumeAccountSecurityAction==='function')resumeAccountSecurityAction().catch(console.warn)},120);
}

async function openRecoveryBoot(){
  const info=state.workspace?.recovery||{},projects=state.workspace?.projects||[],backups=await api('/api/backups').catch(()=>[]);
  const backupRows=backups.filter(b=>b.verified).map(b=>{const title=b.created_at?i18nDateTime(b.created_at):i18nText('backup.recovery_unknown');return `<button class="project-settings-row recovery-boot-backup" data-recovery-backup="${escapeHtml(b.file)}"><span><b>${escapeHtml(title)}</b><small>${escapeHtml(i18nText('verify.verified_check'))} · ${formatBytes(b.bytes||0)}</small></span><span>›</span></button>`}).join('');
  const body=`<p>${escapeHtml(i18nText('recovery.boot_body'))}</p>${info.diagnostic?`<p class="small-muted">${escapeHtml(i18nText('recovery.diagnostic',{id:info.diagnostic}))}</p>`:''}<div class="recovery-boot-list">${projects.filter(p=>p.id!==info.active_project_id).map(p=>`<button class="project-settings-row" data-recovery-project="${escapeHtml(p.id)}"><span><b>${escapeHtml(p.name)}</b><small>${escapeHtml(p.path)}</small></span><span>›</span></button>`).join('')}${backupRows}</div>`;
  const actions=`<button id="recoveryProjectSettings" type="button">${escapeHtml(i18nSource('Project Settings…'))}</button>`;
  showModal(i18nText('recovery.boot_title'),body,actions);
  $('#recoveryProjectSettings').onclick=()=>{closeModal(false);openProjectSettings()};
  $$('[data-recovery-project]').forEach(b=>b.onclick=async()=>{await api(`/api/projects/${b.dataset.recoveryProject}/activate`,{method:'POST'});openWorkspaceProject(b.dataset.recoveryProject)});
  $$('[data-recovery-backup]').forEach(b=>b.onclick=async()=>{const result=await api(`/api/backups/${encodeURIComponent(b.dataset.recoveryBackup)}/restore-as-project`,{method:'POST'});openWorkspaceProject(result?.project?.id||'')});
}

function applyCanvasConfig(){
  const size=Number(state.canvasConfig?.node_typography?.content_font_size_pt);
  if(Number.isFinite(size)&&size>=8&&size<=36)document.documentElement.style.setProperty('--canvas-node-font-size',`${size}pt`);
  else document.documentElement.style.removeProperty('--canvas-node-font-size');
}

function applyBootstrapData(bootstrap,{oldDoc=null}={}){
  Object.assign(state,{project:bootstrap.project,documents:bootstrap.tabs,chapters:bootstrap.chapters,chapterOutlines:bootstrap.chapter_outlines||[],canvases:bootstrap.canvases,scenes:bootstrap.scenes,sceneGaps:bootstrap.scene_gaps||[],bookmark:bootstrap.bookmark||null,markers:bootstrap.labels,iconMarkers:bootstrap.icon_markers||[],maps:bootstrap.maps||[],reviews:bootstrap.reviews||[],features:bootstrap.features||{},metroConfig:bootstrap.metro_view_config||{},canvasConfig:bootstrap.canvas_config||{}});
  state.chapterRevisions=new Map((state.chapters||[]).map(ch=>[+ch.id,ch.revision||'']));
  applyFeatureFlags();applyCanvasConfig();
  state.activeDocumentId=oldDoc&&state.documents.some(d=>d.id===oldDoc)?oldDoc:(state.documents.find(d=>d.is_manuscript)?.id||state.documents[0]?.id||null);
}
async function refresh(preserve=true) {
  const bootstrap = await api('/api/bootstrap');invalidateTaskCache();invalidateQuickOpenDetailIndex();state.bookCache.clear();
  const oldChapter = preserve ? state.chapter?.id : null;
  const oldScene = preserve && state.viewMode==='scene' ? state.scene?.id : null;
  const oldSceneGap = preserve && state.viewMode==='scene-gap' ? state.sceneGap?.first_block_id : null;
  const oldCanvas = preserve ? state.currentCanvas?.id : null;
  const oldMap = preserve ? state.currentMap?.id : null;
  const oldDoc = state.activeDocumentId;
  applyBootstrapData(bootstrap,{oldDoc});
  renderDocuments();
  renderChapterTree();
  syncBookmarkToolbar();
  renderCanvasRail();
  renderMarkers();
  state.mapTreeCache.clear();
  if(oldMap&&state.maps.some(map=>map.id===oldMap)){state.currentMap=state.maps.find(map=>map.id===oldMap);state.mapData=null}else if(!preserve){state.currentMap=null;state.mapData=null;state.currentMapLineId=null;state.selectedMapItemId=null}
  renderMapSwitcher();
  if(state.currentReviewId&&!state.reviews.some(review=>review.id===state.currentReviewId))state.currentReviewId=null;
  renderReviews();
  if (oldScene && state.scenes.some(scene => +scene.id === +oldScene)) await selectScene(oldScene, false);
  else if(oldSceneGap){const gap=state.sceneGaps.find(item=>String(item.first_block_id)===String(oldSceneGap));if(gap)await selectSceneGap(gap,false)}
  else if (oldChapter && visibleChapters().some(ch => ch.id === oldChapter)) await selectChapter(oldChapter, false);
  if (oldCanvas && state.canvases.some(c => c.id === oldCanvas)) await selectCanvas(oldCanvas);
}

