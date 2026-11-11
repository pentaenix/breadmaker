// Project creation, rename, import, metadata, destructive actions, project list, and trash.

async function openExperimentalFeatures(onDismiss=null){
  let saved={};try{saved=(await api('/api/ui-state/experimental-features')).value||{}}catch{}
  const enabled=saved.map_renderer===true;
  showModal('Experimental Features',`<p class="small-muted">{{i18n:dynamic.html.text.p.5f0fd4a2}}</p><div class="project-settings-switch-row"><span class="project-settings-switch-copy"><b>{{i18n:dynamic.html.text.b.1a61e715}}</b><small>{{i18n:dynamic.html.text.small.9731353c}}</small></span><button id="experimentalMapRenderer" class="structure-switch ${enabled?'active':''}" type="button" role="switch" aria-checked="${enabled?'true':'false'}" aria-label="{{i18n:dynamic.html.attr.aria.label.187f3524}}"><span aria-hidden="true"></span></button></div>${experimentalEditingSearchMarkup()}`, '', onDismiss);
  const highlights=$('#experimentalEditorHighlights');
  if(highlights)highlights.onclick=()=>{closeModal(false);openEditorHighlights(()=>openExperimentalFeatures(onDismiss))};
  const toggle=$('#experimentalMapRenderer');toggle.onclick=async()=>{const next=toggle.getAttribute('aria-checked')!=='true';toggle.setAttribute('aria-checked',String(next));toggle.classList.toggle('active',next);state.mapExperimentalFeatures={...(state.mapExperimentalFeatures||{}),map_renderer:next};await api('/api/ui-state/experimental-features',{method:'PUT',body:JSON.stringify({value:state.mapExperimentalFeatures})});applyFeatureFlags()};
}
// Compatibility return target for older import flows; the former Project Center is now Project Settings.
async function openProjectCenter(){ return openProjectSettings(); }

function createProjectDialog(onDismiss=null) {
  showModal('New Project', `<div class="field"><label>{{i18n:dynamic.html.text.label.020d3868}}</label><input id="newProjectName" autofocus value="${escapeHtml(i18nSource('Untitled Book'))}"></div>`, '<button id="createProjectConfirm">{{i18n:dynamic.html.text.button.742354ce}}</button>', onDismiss);
  $('#createProjectConfirm').onclick = async () => {
    const name = $('#newProjectName').value.trim(); if (!name) return;
    const result=await api('/api/projects', {method:'POST', body:JSON.stringify({name})});
    openWorkspaceProject(result?.project?.id||'');
  };
}

function renameCurrentProject(onDismiss=null) {
  const active = state.workspace.projects.find(p => p.id === state.workspace.active_project_id);
  if (!active) return;
  showModal('Rename Project', `<div class="field"><label>{{i18n:dynamic.html.text.label.450ca425}}</label><input id="renameProjectName" value="${escapeHtml(active.name)}"></div>`, '<button id="renameProjectConfirm">{{i18n:dynamic.html.text.button.e1458dad}}</button>', onDismiss);
  $('#renameProjectConfirm').onclick = async () => {
    const name=$('#renameProjectName').value.trim(); if(!name)return;
    if(!await commitWorkspaceProjectRename(active,name)||runtimeSupports('hosted_persistence'))return;
    await refresh(false); state.workspace=await api('/api/workspace'); closeModal();
  };
}

async function commitWorkspaceProjectRename(active,name){
  return withManuscriptMutationBarrier(async()=>{
    await flushSave();
    if(String(currentProjectIdentity())!==String(active.id))return false;
    await api(`/api/projects/${active.id}`,{method:'PATCH',body:JSON.stringify({name})});
    active.name=name;
    if(String(currentProjectIdentity())!==String(active.id))return false;
    document.title=name;
    if(runtimeSupports('hosted_persistence')){
      // Rename advances the project's write-CAS. Reopen from current authority;
      // never attach its new revision to the old manuscript or accept more input
      // while the old page is navigating. Native chapter revisions are separate.
      clearManuscriptReadCache();
      openWorkspaceProject(active.id,{replace:true,view:currentView()});
    }
    return true;
  });
}

let projectPackageImportIntent=null;
function ownsProjectPackageImport(intent){
  return projectPackageImportIntent===intent&&!intent.dismissed&&intent.actor===currentUserIdentity()&&intent.project===currentProjectIdentity()&&intent.navigation===workspaceNavigation&&intent.view===currentView()&&intent.path===location.pathname&&intent.revision===modalRevision&&intent.node?.isConnected&&$('#modalBody').contains(intent.node)&&!$('#modal').classList.contains('hidden');
}
function packageImportText(key,params={}){return i18nText(`package_import.${key}`,params)}
function packageImportSave(intent,{start=false}={}){
  // Metadata only. In particular, never persist the archive or decoded content.
  if(intent.pending&&intent.storageKey){
    let current=null;try{current=JSON.parse(sessionStorage.getItem(intent.storageKey)||'null')}catch{}
    // A late response from a dismissed attempt cannot replace a newer attempt
    // deliberately begun after confirmed cancellation.
    if(!start&&current?.key&&current.key!==intent.pending.key)return;
    sessionStorage.setItem(intent.storageKey,JSON.stringify(intent.pending));
  }
}
function packageImportRecord(intent,job){
  if(!job?.job_id)throw new Error(packageImportText('invalid_response'));
  if(intent.pending.job&&intent.pending.job!==job.job_id)throw new Error(packageImportText('invalid_response'));
  if(job.source_sha256&&job.source_sha256!==intent.pending.sha256)throw new Error(packageImportText('invalid_response'));
  intent.job=job;intent.pending.job=job.job_id;intent.pending.phase=job.phase;
  if(job.phase==='completed'){
    if(!/^[0-9a-f]{32}$/.test(String(job.project?.id||'')))throw new Error(packageImportText('invalid_response'));
    intent.pending.project=job.project.id;intent.pending.publishing=false;
  }
  if(job.phase==='cancelled')intent.pending.publishing=false;
  packageImportSave(intent);
  return job;
}
function packageImportRender(intent,label,detail=''){
  if(!ownsProjectPackageImport(intent))return;
  const job=intent.job,completed=job?.phase==='completed',cancelled=job?.phase==='cancelled';
  $('#projectPackageImportStatus').textContent=label;
  $('#projectPackageImportError').textContent=detail;
  $('#projectPackageImportError').hidden=!detail;
  intent.node.classList.toggle('failed',!!detail);
  intent.node.setAttribute('aria-busy',String(intent.busy));
  const track=$('#projectPackageImportTrack'),bar=$('#projectPackageImportBar');
  const total=Number(job?.total_records||0)+Number(job?.total_images||0),done=Number(job?.completed_records||0)+Number(job?.completed_images||0);
  track.setAttribute('aria-valuetext',label);
  if(job&&total>0){track.setAttribute('aria-valuemax',String(total));track.setAttribute('aria-valuenow',String(Math.min(total,done)));bar.style.width=`${Math.min(100,100*done/total)}%`}
  else{track.removeAttribute('aria-valuenow');track.removeAttribute('aria-valuemax');bar.style.width=completed?'100%':'0%'}
  const counts=$('#projectPackageImportCounts');
  counts.textContent=job?packageImportText('counts',{records:i18nNumber(job.completed_records||0),totalRecords:i18nNumber(job.total_records||0),images:i18nNumber(job.completed_images||0),totalImages:i18nNumber(job.total_images||0)}):'';
  const cancel=$('#cancelProjectPackageImport');
  cancel.disabled=!job||completed||cancelled||intent.cancelRequested||intent.pending?.publishing;
  cancel.textContent=packageImportText(intent.cancelRequested&&!completed&&!cancelled?'cancelling':'cancel');
  const resume=$('#resumeProjectPackageImport');
  resume.hidden=intent.busy||cancelled;
  resume.disabled=intent.busy;
  resume.textContent=packageImportText(completed?'open':intent.errorCode==='HOSTED_IMPORT_EXPIRED'?'restart':intent.pending?.publishing?'check':'resume');
}
function packageImportStatus(intent){
  const job=intent.job;
  return packageImportText(job?.phase==='completed'?'completed':job?.phase==='cancelled'?'cancelled':intent.cancelRequested?'cancelling':intent.pending?.publishing?'publishing':job?.phase==='preparing'?'uploading':job?.ready?'ready':'staging');
}
async function packageImportPrepare(intent){
  packageImportRender(intent,packageImportText('uploading'));
  const previous=intent.job;
  let job;
  if(previous?.prepare_available&&intent.pending.job){
    job=await api(`/api/projects/import/jobs/${encodeURIComponent(intent.pending.job)}/prepare`,{method:'POST'});
  }else{
    const fd=new FormData();fd.append('file',intent.file);
    job=await api('/api/projects/import?resumable=true&preparation_version=3',{method:'POST',body:fd,idempotencyKey:intent.pending.key});
  }
  // Continue only a successful, advancing durable preparation response. HTTP
  // failures and stalled/malformed responses pause for explicit Resume; never
  // retry them automatically or discard the existing source/operation binding.
  if(job?.phase==='preparing'){
    const prior=previous?.prepared_batches||0;
    let advances=job.prepared_batches>prior;
    if(job.preparation_version===3){
      if(job.prepare_available!==true||!Number.isSafeInteger(job.prepared_source_chunks)||!Number.isSafeInteger(job.total_source_chunks)||job.total_source_chunks<1||job.prepared_source_chunks<0||job.prepared_source_chunks>job.total_source_chunks||job.prepared_source_chunks<(previous?.prepared_source_chunks||0))throw new Error(packageImportText('invalid_response'));
      advances=advances||!previous?.prepare_available||job.prepared_source_chunks>(previous?.prepared_source_chunks||0);
    }
    if(!Number.isSafeInteger(job.prepared_batches)||!Number.isSafeInteger(job.total_batches)||job.prepared_batches<prior||job.prepared_batches>job.total_batches||job.total_batches<1||!advances)throw new Error(packageImportText('invalid_response'));
  }
  return packageImportRecord(intent,job);
}
async function packageImportReadStatus(intent){
  return packageImportRecord(intent,await api(`/api/projects/import/jobs/${encodeURIComponent(intent.pending.job)}`));
}
async function packageImportCancel(intent){
  try{return packageImportRecord(intent,await api(`/api/projects/import/jobs/${encodeURIComponent(intent.pending.job)}/cancel`,{method:'POST'}))}
  catch(error){
    // A timed-out cancellation can race publication. Only server status can
    // establish cancellation; neither a request nor a 409 proves it succeeded.
    if(intent.actor!==currentUserIdentity())throw error;
    try{const job=await packageImportReadStatus(intent);if(['completed','cancelled'].includes(job.phase))return job}catch{}
    throw error;
  }
}
async function runProjectPackageImport(intent,{checkOnly=false}={}){
  if(intent.busy||!ownsProjectPackageImport(intent))return;
  intent.busy=true;
  packageImportRender(intent,packageImportText(intent.pending?'checking':'hashing'));
  try{
    await flushSave();if(!ownsProjectPackageImport(intent))return;
    if(!intent.pending){
      if(intent.file.size>64*1024*1024)throw new Error(packageImportText('size_limit'));
      const sha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await intent.file.arrayBuffer())),byte=>byte.toString(16).padStart(2,'0')).join('');
      if(!ownsProjectPackageImport(intent))return;
      const signature=JSON.stringify([intent.actor,sha256,intent.file.name]);
      intent.storageKey=`breadmaker-pending-package-import:${encodeURIComponent(intent.actor)}:${sha256}:${encodeURIComponent(intent.file.name)}`;
      let pending=null;try{pending=JSON.parse(sessionStorage.getItem(intent.storageKey)||'null')}catch{}
      intent.pending=pending?.signature===signature?pending:{actor:intent.actor,signature,sha256,key:uid(),created:Date.now()};
      // A confirmed cancellation may be imported again on a new file selection.
      // Missing/expired routes and unknown publication never discard this key.
      if(intent.pending.phase==='cancelled')intent.pending={actor:intent.actor,signature,sha256,key:uid(),created:Date.now()};
      packageImportSave(intent,{start:true});
    }
    if(!ownsProjectPackageImport(intent))return;
    if(intent.pending.job)await packageImportReadStatus(intent);
    if(!intent.pending.job){
      if(!ownsProjectPackageImport(intent))return;
      await packageImportPrepare(intent);
    }
    if(!ownsProjectPackageImport(intent))return;
    if(checkOnly&&intent.job.phase!=='completed'&&intent.job.phase!=='cancelled'){
      // Do not mistake a still-running finish for a failed publication. The
      // next explicit Resume may finish this same job; never upload again.
      intent.pending.publishing=false;packageImportSave(intent);return;
    }
    while(!['completed','cancelled'].includes(intent.job.phase)){
      if(!ownsProjectPackageImport(intent))return;
      packageImportRender(intent,packageImportStatus(intent));
      if(intent.cancelRequested){await packageImportCancel(intent);break}
      if(intent.job.phase==='preparing'){
        await new Promise(resolve=>requestAnimationFrame(resolve));
        if(!ownsProjectPackageImport(intent))return;
        if(intent.cancelRequested)continue;
        await packageImportPrepare(intent);
        continue;
      }
      if(intent.job.ready){
        intent.pending.publishing=true;packageImportSave(intent);packageImportRender(intent,packageImportText('publishing'));
        try{packageImportRecord(intent,await api(`/api/projects/import/jobs/${encodeURIComponent(intent.pending.job)}/finish`,{method:'POST'}))}
        catch(error){
          if(intent.actor!==currentUserIdentity())throw error;
          try{await packageImportReadStatus(intent)}catch{throw error}
          if(!['completed','cancelled'].includes(intent.job.phase))throw error;
        }
        break;
      }
      const next=intent.job.next_batch;
      packageImportRecord(intent,await api(`/api/projects/import/jobs/${encodeURIComponent(intent.pending.job)}/step?batch=${encodeURIComponent(next)}`,{method:'POST'}));
      // Yield to paint and cancellation between bounded, durable batches.
      await new Promise(resolve=>requestAnimationFrame(resolve));
    }
    if(!ownsProjectPackageImport(intent))return;
    if(intent.job.phase==='completed'&&!intent.cancelRequested){
      const projectId=intent.job.project.id;intent.dismissed=true;currentModalDismiss=null;closeModal(false);openWorkspaceProject(projectId);
    }
  }catch(error){
    if(ownsProjectPackageImport(intent)){
      intent.error=error?.message||String(error);
      // Hosted compatibility routes use an exact string detail; newer routes
      // may expose the same authority code as a structured error instead.
      intent.errorCode=error?.code||(error?.message==='HOSTED_IMPORT_EXPIRED'?'HOSTED_IMPORT_EXPIRED':'');
      // An uncertain failure keeps all recovery metadata, including a published
      // job pointer. Generic 404s on mixed deployments are not a restart signal.
    }
  }finally{
    intent.busy=false;
    if(ownsProjectPackageImport(intent))packageImportRender(intent,intent.error?packageImportText('paused'):packageImportStatus(intent),intent.error||'');
  }
}
async function importProjectPackage(event) {
  const input=event.target,file=input.files?.[0];
  // File inputs can emit the same selection twice. Lock synchronously before
  // hashing, upload, or rendering; stale async completions never own new UI.
  if(projectPackageImportIntent?.busy&&ownsProjectPackageImport(projectPackageImportIntent)){input.value='';return}
  const returnToProjects=input.dataset.returnToProjects==='1';delete input.dataset.returnToProjects;input.value='';
  if(!file){if(returnToProjects)openProjectCenter();return}
  const intent={file,returnToProjects,actor:currentUserIdentity(),project:currentProjectIdentity(),navigation:workspaceNavigation,view:currentView(),path:location.pathname,busy:false,dismissed:false,cancelRequested:false,pending:null,job:null};
  projectPackageImportIntent=intent;
  showModal(packageImportText('title'),`<div id="projectPackageImportProgress" class="manuscript-import-progress"><p class="manuscript-import-file"><b>${escapeHtml(file.name)}</b></p><p id="projectPackageImportStatus" class="small-muted" role="status" aria-live="polite"></p><div id="projectPackageImportTrack" class="manuscript-import-progress-track" role="progressbar" aria-label="${escapeHtml(packageImportText('title'))}" aria-valuemin="0"><span id="projectPackageImportBar"></span></div><p id="projectPackageImportCounts" class="small-muted"></p><p id="projectPackageImportError" class="manuscript-import-error" hidden></p></div>`,`<button id="backProjectPackageImport" type="button">${escapeHtml(packageImportText('back'))}</button><button id="cancelProjectPackageImport" type="button" disabled>${escapeHtml(packageImportText('cancel'))}</button><button id="resumeProjectPackageImport" type="button" hidden>${escapeHtml(packageImportText('resume'))}</button>`,()=>{intent.dismissed=true});
  intent.node=$('#projectPackageImportProgress');intent.revision=modalRevision;
  $('#backProjectPackageImport').onclick=()=>{if(!ownsProjectPackageImport(intent))return;intent.dismissed=true;currentModalDismiss=null;closeModal(false);if(returnToProjects)openProjectCenter()};
  $('#cancelProjectPackageImport').onclick=()=>{
    if(!ownsProjectPackageImport(intent)||$('#cancelProjectPackageImport').disabled)return;
    intent.cancelRequested=true;intent.error='';packageImportRender(intent,packageImportText('cancelling'));
    if(!intent.busy)void runProjectPackageImport(intent);
  };
  $('#resumeProjectPackageImport').onclick=()=>{
    if(!ownsProjectPackageImport(intent)||intent.busy)return;
    if(intent.job?.phase==='completed'){const pid=intent.job.project.id;intent.dismissed=true;currentModalDismiss=null;closeModal(false);openWorkspaceProject(pid);return}
    if(intent.errorCode==='HOSTED_IMPORT_EXPIRED'){
      // This exact server code guarantees nonpublication. Starting another
      // attempt is an explicit action, never a fallback for generic 404/5xx.
      const {actor,signature,sha256}=intent.pending;
      intent.pending={actor,signature,sha256,key:uid(),created:Date.now()};intent.job=null;
      try{packageImportSave(intent,{start:true})}catch(error){packageImportRender(intent,packageImportText('paused'),error.message);return}
    }
    const checkOnly=!!intent.pending?.publishing;intent.cancelRequested=false;intent.error='';intent.errorCode='';void runProjectPackageImport(intent,{checkOnly});
  };
  if(runtimeSupports('hosted_persistence')&&runtimeSupports('project_package_import_resumable'))return runProjectPackageImport(intent);
  // Local SQLite and older hosted servers retain the one-shot package route.
  // Its upload is indeterminate, not a fabricated percentage or cancellation.
  intent.busy=true;packageImportRender(intent,packageImportText('uploading'));
  try{
    await flushSave();if(!ownsProjectPackageImport(intent))return;
    const fd=new FormData();fd.append('file',file);const result=await api('/api/projects/import',{method:'POST',body:fd});
    if(ownsProjectPackageImport(intent)){const pid=result?.project?.id;if(!pid)throw new Error(packageImportText('invalid_response'));intent.dismissed=true;currentModalDismiss=null;closeModal(false);openWorkspaceProject(pid)}
  }catch(error){if(ownsProjectPackageImport(intent)){intent.busy=false;packageImportRender(intent,packageImportText('paused'),error?.message||String(error));$('#resumeProjectPackageImport').hidden=true}}
  finally{intent.busy=false}
}

function currentJsonImportContext(extra={}){
  return {view:currentView(),document_id:state.activeDocumentId||null,chapter_id:state.chapter?.id||null,canvas_id:state.currentCanvas?.id||null,map_id:state.currentMap?.id||null,map_line_id:state.currentMapLineId||null,task_id:state.selectedTaskId||null,...extra};
}
function beginJsonImport(extra={}){
  const input=$('#projectJsonImportInput');input.dataset.importContext=JSON.stringify(currentJsonImportContext(extra));input.value='';input.click();
}
async function importJson(event) {
  const input=event.target,file=input.files?.[0];if(!file)return;
  let context={};try{context=JSON.parse(input.dataset.importContext||'{}')}catch{}delete input.dataset.importContext;
  const previewData=new FormData();previewData.append('file',file);previewData.append('context',JSON.stringify(context));
  try{
    const preview=await api('/api/json-import/preview',{method:'POST',body:previewData}),s=preview.summary||{},warnings=preview.warnings||[];
    const count=(n,label)=>`${i18nNumber(n)} ${i18nSource(label)}`;
    const warningHtml=warnings.length?`<div class="project-json-warnings"><b>${escapeHtml(i18nText(warnings.length===1?'import.warning.one':'import.warning.many',{count:i18nNumber(warnings.length)}))}</b>${warnings.slice(0,8).map(w=>`<span>${escapeHtml(w)}</span>`).join('')}${warnings.length>8?`<span>${escapeHtml(i18nText('import.warning.more',{count:i18nNumber(warnings.length-8)}))}</span>`:''}</div>`:'';
    if(preview.kind==='project'){
      showModal('Import JSON Project',`<div class="project-json-preview"><p><b>${escapeHtml(preview.name||file.name)}</b></p><p class="small-muted">{{i18n:dynamic.html.text.p.21d64229}}</p><div class="project-json-summary"><span>${count(s.tabs,'tabs')}</span><span>${count(s.chapters,'chapters')}</span><span>${count(s.scenes,'scenes')}</span><span>${count(s.markers,'markers')}</span><span>${count(s.canvases,'canvases')}</span><span>${count(s.cards,'cards')}</span><span>${count(s.maps,'maps')}</span><span>${count(s.tasks,'tasks')}</span></div>${warningHtml}</div>`,`<button id="cancelJsonImport" class="bare-button">{{i18n:dynamic.html.text.button.92113f72}}</button><button id="confirmJsonImport">{{i18n:dynamic.html.text.button.b4df2223}}</button>`);
    }else{
      const otherCanvasObjects=Math.max(0,Number(s.canvas_objects||0)-Number(s.cards||0));
      const summaryRows=[['markers','markers'],['tabs','tabs'],['chapters','chapters'],['scenes','scenes'],['canvases','canvases'],['cards','cards'],['other_canvas_objects','other canvas objects'],['connectors','connectors'],['maps','maps'],['map_lines','map lines'],['map_items','map items'],['tasks','tasks'],['subtasks','subtasks'],['cuts','cuts']].filter(([key])=>Number((key==='other_canvas_objects'?otherCanvasObjects:s[key])||0)>0);
      const targets=(preview.targets||[]).map(t=>{const options=t.candidates||[];if(!t.needs_choice)return `<div class="fragment-target-row"><span>${escapeHtml(i18nText('import.add_to',{type:i18nSource(t.type.replace('_',' '))}))}</span><b>${escapeHtml(t.resolved_label||i18nSource('Current'))}</b></div>`;return `<div class="field fragment-target-choice"><label>${escapeHtml(i18nText('import.choose_destination',{type:i18nSource(t.type.replace('_',' '))}))}</label><select data-fragment-target="${t.index}" ${options.length?'':'disabled'}>${options.length?`<option value="">{{i18n:dynamic.html.text.option.a2f5a254}}</option>${options.map(c=>`<option value="${c.id}">${escapeHtml(c.label)}</option>`).join('')}`:'<option>{{i18n:dynamic.html.text.option.61c26621}}</option>'}</select></div>`}).join('');
      showModal('Import JSON Fragment',`<div class="project-json-preview fragment-json-preview"><p><b>${escapeHtml(preview.name||file.name)}</b></p><p class="small-muted">{{i18n:dynamic.html.text.p.b3ce6986}}</p><div class="project-json-summary">${summaryRows.map(([key,label])=>`<span>${count(key==='other_canvas_objects'?otherCanvasObjects:s[key],label)}</span>`).join('')}</div>${targets?`<div class="fragment-targets">${targets}</div>`:''}${warningHtml}</div>`,`<button id="cancelJsonImport" class="bare-button">{{i18n:dynamic.html.text.button.92113f72}}</button><button id="confirmJsonImport">{{i18n:dynamic.html.text.button.1da0fe67}}</button>`);
      if((preview.targets||[]).some(t=>t.needs_choice&&!(t.candidates||[]).length))$('#confirmJsonImport').disabled=true;
    }
    $('#cancelJsonImport').onclick=()=>{closeModal();input.value=''};
    $('#confirmJsonImport').onclick=async()=>{
      const button=$('#confirmJsonImport');button.disabled=true;button.textContent=i18nSource(preview.kind==='project'?'Creating…':'Importing…');
      const choices={};$$('[data-fragment-target]').forEach(select=>{if(select.value)choices[select.dataset.fragmentTarget]=entityId(select.value)});
      if(preview.kind==='fragment'&&$$('[data-fragment-target]').some(select=>!select.value)){button.disabled=false;button.textContent=i18nSource('Import');toast('Choose a destination for every fragment item.');return}
      const fd=new FormData();fd.append('file',file);fd.append('context',JSON.stringify(context));fd.append('target_choices',JSON.stringify(choices));
      try{
        if(preview.kind==='project'){const result=await api('/api/json-import',{method:'POST',body:fd});openWorkspaceProject(result?.project?.id||'');return}
        await withStructuralUndo(`Import JSON: ${preview.name||'Fragment'}`,()=>api('/api/json-import',{method:'POST',body:fd}),{view:context.view||currentView(),documentId:context.document_id,chapterId:context.chapter_id,canvasId:context.canvas_id,mapId:context.map_id});
        closeModal();input.value='';await refresh(false);toast(i18nText('import.fragment_imported',{name:preview.name||i18nSource('JSON fragment')}));
      }catch(error){button.disabled=false;button.textContent=i18nSource(preview.kind==='project'?'Create Project':'Import');throw error}
    };
  }catch(error){input.value='';throw error}
}

async function importCutsPackage(event) {
  const input=event.target,returnToProjects=input.dataset.returnToProjects==='1';delete input.dataset.returnToProjects;
  const file=input.files[0];if(!file){if(returnToProjects)openProjectCenter();return}
  const fd=new FormData();fd.append('file',file);
  try{
    let result;await withStructuralUndo('Import Cuts',async()=>{result=await api('/api/cuts/import',{method:'POST',body:fd})},{view:'manuscript',chapterId:state.chapter?.id});
    await notifyCutsChanged();
    toast(i18nText(result.imported===1?'import.cuts.one':'import.cuts.many',{count:i18nNumber(result.imported)}));
    if(returnToProjects)await openProjectCenter();
  }catch(error){if(returnToProjects)await openProjectCenter();throw error}
  finally{input.value=''}
}

async function openProjectMetadata(onDismiss=null){
  showModal('Project Metadata','<div class="verify-loading"><div class="verify-pulse" aria-hidden="true"></div><div><b>{{i18n:dynamic.html.text.b.824d4922}}</b></div></div><div id="projectMetadataBody" class="metadata-sections"><div class="verify-skeleton"></div></div>','',onDismiss);
  await new Promise(resolve=>requestAnimationFrame(resolve));
  try{
    const data=await api('/api/project/metadata'),m=data.manuscript||{},st=data.structure||{},cv=data.canvas||{},maps=data.maps||{},pr=data.safety||{},proj=data.project||{};
    const row=(label,value)=>`<div class="metadata-row"><span>${escapeHtml(i18nSource(label))}</span><b>${i18nNumber(value)}</b></div>`;
    $('#projectMetadataBody').innerHTML=`
      <section class="metadata-section"><div class="metadata-title">{{i18n:dynamic.html.text.div.3bdcb845}}</div>${row('Words',m.words)}${row('Characters',m.characters)}${row('Characters without spaces',m.characters_no_spaces)}${row('Chapters',m.chapters)}${row('Scenes',m.scenes)}${row('Manuscript blocks',m.blocks)}${row('Images',m.images)}</section>
      <section class="metadata-section"><div class="metadata-title">{{i18n:dynamic.html.text.div.c61fb2fe}}</div>${row('Tabs',st.tabs)}${row('Annotations',st.annotations)}${row('Markers',st.markers)}${row('Tasks',st.tasks)}${row('Milestones',st.milestones)}</section>
      <section class="metadata-section"><div class="metadata-title">{{i18n:dynamic.html.text.div.4d02d73a}}</div>${row('Canvases',cv.canvases)}${row('Cards',cv.nodes)}${row('Connections',cv.connections)}</section>
      <section class="metadata-section"><div class="metadata-title">{{i18n:dynamic.html.text.div.13c6b760}}</div>${row('Maps',maps.maps)}${row('Lines',maps.lines)}${row('Stations / spans',maps.items)}${row('Connections',maps.connections)}</section>
      <section class="metadata-section"><div class="metadata-title">{{i18n:dynamic.html.text.div.80661c8b}}</div>${row('Checkpoints',pr.checkpoints)}${row('Cuts',pr.cuts)}${row('History states',pr.history)}${row('Session Undo states',pr.undo)}</section>
      <section class="metadata-section metadata-project-info"><div class="metadata-title">{{i18n:dynamic.html.text.div.1cf975e3}}</div><div class="metadata-row"><span>{{i18n:dynamic.html.text.span.b8e48f23}}</span><b>${proj.created_at?i18nDateTime(proj.created_at):'—'}</b></div><div class="metadata-row"><span>{{i18n:dynamic.html.text.span.edde1090}}</span><b>${proj.updated_at?i18nDateTime(proj.updated_at):'—'}</b></div></section>`;
  }catch(error){$('#projectMetadataBody').innerHTML=`<p class="danger">${escapeHtml(error.message||String(error))}</p>`}
}

function dangerFirstDialog(title,message,{allowExternalErase=false}={}){
  return new Promise(resolve=>{let settled=false;const settle=value=>{if(settled)return;settled=true;currentModalDismiss=null;resolve(value);closeModal(false)};const option=allowExternalErase?`<label class="danger-extra-option"><input id="dangerEraseExternal" type="checkbox"><span>${escapeHtml(i18nText('danger.erase_external_backups'))}</span></label><p class="small-muted">${escapeHtml(i18nText('danger.erase_external_backups_help'))}</p>`:'';showNestedModal(title,`<p class="dialog-message">${escapeHtml(i18nSource(message))}</p>${option}`,`<button id="dialogCancel">${escapeHtml(i18nText('dynamic.html.text.button.92113f72'))}</button><a id="dangerDownloadData" class="quiet-button danger-download-data" href="/api/data/export" download target="_blank" rel="noopener">${escapeHtml(i18nText('release.download_data_zip'))}</a><button id="dialogConfirm" class="danger">${escapeHtml(i18nText('dynamic.html.text.button.d7cf19d2'))}</button>`,()=>{if(!settled){settled=true;resolve({confirmed:false,eraseExternal:false})}});$('#dialogCancel').onclick=()=>settle({confirmed:false,eraseExternal:false});$('#dialogConfirm').onclick=()=>settle({confirmed:true,eraseExternal:!!$('#dangerEraseExternal')?.checked})});
}
function typedConfirmationCode(length=5){
  const alphabet='abcdefghijklmnopqrstuvwxyz0123456789',values=crypto.getRandomValues(new Uint8Array(length));
  return Array.from(values,value=>alphabet[value%alphabet.length]).join('');
}
function dangerCodeDialog(title,code,actionLabel,{message='',nested=false}={}){
  return new Promise(resolve=>{
    let settled=false;const settle=value=>{if(settled)return;settled=true;currentModalDismiss=null;resolve(value);closeModal(false)};
    (nested?showNestedModal:showModal)(title,`<p class="danger-confirm-copy">${escapeHtml(i18nText('danger.type_code',{count:code.length}))}</p>${message?`<p class="small-muted">${escapeHtml(message)}</p>`:''}<div class="danger-confirm-code" aria-label="{{i18n:dynamic.html.attr.aria.label.99ea43b0}}">${escapeHtml(code)}</div><div class="field"><label>{{i18n:dynamic.html.text.label.5d464b5e}}</label><input id="dangerConfirmInput" autocomplete="off" autocapitalize="none" spellcheck="false" maxlength="${code.length}" inputmode="text"></div>`,`<button id="dangerCodeCancel">{{i18n:dynamic.html.text.button.92113f72}}</button><button id="dangerCodeConfirm" class="danger" disabled>${escapeHtml(actionLabel)}</button>`,()=>{if(!settled){settled=true;resolve(null)}});
    const input=$('#dangerConfirmInput'),button=$('#dangerCodeConfirm'),shown=$('.danger-confirm-code');
    ['copy','cut','contextmenu','dragstart','selectstart'].forEach(type=>shown.addEventListener(type,e=>e.preventDefault()));
    input.addEventListener('paste',e=>e.preventDefault());
    const sync=()=>{button.disabled=input.value!==code};input.oninput=sync;
    input.onkeydown=e=>{if(e.key==='Enter'&&!button.disabled){e.preventDefault();settle(input.value)}};
    $('#dangerCodeCancel').onclick=()=>settle(null);button.onclick=()=>{if(input.value===code)settle(input.value)};queueMicrotask(()=>input.focus());
  });
}

let deleteAllProgressTimer=null;
function stopDeleteAllProgress(){if(deleteAllProgressTimer){clearInterval(deleteAllProgressTimer);deleteAllProgressTimer=null}}
function setDeleteAllProgress(value,label){
  const pct=Math.max(0,Math.min(100,Math.round(+value||0))),bar=$('#deleteAllProgressBar'),status=$('#deleteAllProgressStatus');
  if(bar){bar.style.width=`${pct}%`;bar.parentElement?.setAttribute('aria-valuenow',String(pct))}
  if(status)status.textContent=i18nText(label);
}
function beginDeleteAllProgress(erasingBackups=false){
  stopDeleteAllProgress();currentModalDismiss=null;modalStack.length=0;
  showModal(i18nText('danger.delete_all_progress_title'),`<div id="deleteAllProgress" class="danger-delete-progress"><p id="deleteAllProgressStatus" class="small-muted"></p><div class="danger-delete-progress-track" role="progressbar" aria-label="${escapeHtml(i18nText('danger.delete_all_progress_title'))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="8"><span id="deleteAllProgressBar" style="width:8%"></span></div><p class="small-muted danger-delete-progress-note">${escapeHtml(i18nText('danger.delete_all_progress_note'))}</p></div>`,'');
  const close=$('#modalClose');if(close){close.disabled=true;close.setAttribute('aria-disabled','true')}
  let pct=8;setDeleteAllProgress(pct,erasingBackups?'danger.delete_all_progress_backups':'danger.delete_all_progress_local');
  deleteAllProgressTimer=setInterval(()=>{if(pct<90)pct=Math.min(90,pct+(pct<40?5:pct<70?3:1));setDeleteAllProgress(pct,erasingBackups&&pct<52?'danger.delete_all_progress_backups':pct<82?'danger.delete_all_progress_local':'danger.delete_all_progress_finishing')},180);
}
async function completeDeleteAllProgress(){
  stopDeleteAllProgress();setDeleteAllProgress(100,'danger.delete_all_progress_complete');
  await new Promise(resolve=>setTimeout(resolve,180));
}
function failDeleteAllProgress(error){
  stopDeleteAllProgress();const close=$('#modalClose');if(close){close.disabled=false;close.removeAttribute('aria-disabled')}
  const shell=$('#deleteAllProgress');shell?.classList.add('failed');setDeleteAllProgress(100,'danger.delete_all_progress_failed');
  const detail=document.createElement('p');detail.className='danger-delete-progress-error';detail.textContent=error?.message||i18nText('danger.delete_all_progress_failed');shell?.appendChild(detail);
  $('#modalActions').innerHTML=`<button id="closeFailedDeleteAll" type="button">${escapeHtml(i18nText('ui.close'))}</button>`;$('#closeFailedDeleteAll').onclick=()=>closeModal();
}

async function trashHostedProject(active){
  return withManuscriptMutationBarrier(async()=>{
    const pid=String(active.id);
    if(String(currentProjectIdentity())!==pid)return;
    await flushSave();
    if(String(currentProjectIdentity())!==pid)return;
    // The server transaction checks current owner/CAS and only tombstones the
    // project. The confirmed recoverable action needs no global-purge challenge.
    await api(`/api/projects/${pid}`,{method:'DELETE'});
    if(String(currentProjectIdentity())!==pid)return;
    clearManuscriptReadCache();
    location.replace('/');
  });
}

async function runDangerDelete(action,active,onReturn=openProjectSettings){
  const deletingAll=action==='delete_all_data',hostedTrash=runtimeSupports('hosted_projects')&&action==='delete_project';
  if(!active&&!deletingAll)return;
  const firstMessage=deletingAll
    ?'Delete every Breadmaker-managed project, Recovery Backup, History state, Checkpoint, Cut, export, log, and local preference on this computer? External files you exported elsewhere are not touched.'
    :i18nText('trash.delete_project_confirm',{name:active.name});
  const first=await dangerFirstDialog(deletingAll?'Delete All Breadmaker Data':'Delete Project',firstMessage,{allowExternalErase:deletingAll});
  if(!first.confirmed){if(onReturn)await onReturn();return}
  try{
    if(hostedTrash){
      const typed=await dangerCodeDialog('Delete Project',typedConfirmationCode(5),'Delete Project');
      if(typed===null){if(onReturn)await onReturn();return}
      await trashHostedProject(active);return;
    }
    const challenge=await api('/api/danger/challenge',{method:'POST',body:JSON.stringify({action,...(!deletingAll&&active?.id?{project_id:active.id}:{})})});
    const typed=await dangerCodeDialog(deletingAll?'Final Confirmation':'Delete Project',challenge.code,deletingAll?'Delete All Data':'Delete Project');
    if(typed===null){if(onReturn)await onReturn();return}
    const payloadData={challenge_id:challenge.challenge_id,code:typed,replacement_name:i18nSource('Untitled Book'),erase_external_backups:false};
    if(deletingAll&&first.eraseExternal){
      const warned=await confirmDialog(i18nText('danger.external_warning_title'),i18nText('danger.external_warning_body'),{confirmLabel:i18nText('danger.external_warning_confirm'),danger:true});
      if(!warned){if(onReturn)await onReturn();return}
      const external=await api('/api/danger/challenge',{method:'POST',body:JSON.stringify({action:'delete_external_backups'})});
      const externalTyped=await dangerCodeDialog(i18nText('danger.external_final_title'),external.code,i18nText('danger.external_final_action'));
      if(externalTyped===null){if(onReturn)await onReturn();return}
      payloadData.erase_external_backups=true;payloadData.external_warning_acknowledged=true;payloadData.external_challenge_id=external.challenge_id;payloadData.external_code=externalTyped;
    }
    const payload=JSON.stringify(payloadData);
    if(deletingAll){
      beginDeleteAllProgress(payloadData.erase_external_backups);
      try{
        await api('/api/danger/delete-all-data',{method:'POST',body:payload});
        await completeDeleteAllProgress();
        location.replace('/');
      }catch(error){
        console.error('Delete All failed',error);failDeleteAllProgress(error);
      }
      return;
    }
    const result=await api(`/api/projects/${active.id}`,{method:'DELETE',body:payload});
    const next=String(result?.active_project_id||'');if(runtimeSupports('hosted_projects'))location.href=next?hostedProjectRoute(next,'manuscript'):'/';else location.href='/';
  }catch(error){toast(error.message||'Delete action failed.',5000);if(onReturn&&!hostedTrash)await onReturn()}
}

async function openAllProjects(onDismiss=null){
  try{state.workspace=await api('/api/workspace')}catch{}
  const projects=[...(state.workspace?.projects||[])];
  const render=(query='')=>{
    const q=query.trim().toLowerCase(),rows=projects.filter(p=>!q||`${p.name} ${p.path||''} ${p.id||''}`.toLowerCase().includes(q));
    $('#allProjectsList').innerHTML=rows.map(p=>`<article class="project-explorer-card ${p.id===state.workspace.active_project_id?'active':''}"><div class="project-explorer-card-head"><b title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</b>${p.id===state.workspace.active_project_id?'<span class="project-open-pill">{{i18n:dynamic.html.text.span.7f381b07}}</span>':''}</div><small>${escapeHtml(i18nText('projects.last_opened',{date:i18nDateTime(p.last_opened_at)}))}</small>${p.path?`<span class="project-explorer-card-path" title="${escapeHtml(p.path)}">${escapeHtml(p.path)}</span>`:''}${p.id===state.workspace.active_project_id?'<button class="quiet-button" disabled>{{i18n:dynamic.html.text.button.05cf43cc}}</button>':`<button class="quiet-button" data-explorer-open="${p.id}">{{i18n:dynamic.html.text.button.63c52603}}</button>`}</article>`).join('')||'<div class="project-list-empty">{{i18n:dynamic.html.text.div.794cd043}}</div>';
    $$('[data-explorer-open]').forEach(button=>button.onclick=async()=>{await flushSave();await api(`/api/projects/${button.dataset.explorerOpen}/activate`,{method:'POST'});openWorkspaceProject(button.dataset.explorerOpen)});
  };
  showModal('All Projects',`<div class="project-explorer"><input id="projectExplorerSearch" class="project-explorer-search" placeholder="{{i18n:dynamic.html.attr.placeholder.907bdbea}}" aria-label="{{i18n:dynamic.html.attr.aria.label.6ef555b3}}"><div id="allProjectsList" class="project-explorer-grid"></div></div>`,'',onDismiss);
  render();$('#projectExplorerSearch').oninput=e=>render(e.target.value);queueMicrotask(()=>$('#projectExplorerSearch')?.focus());
}

async function openProjectTrash(onDismiss=null){const ws=await api('/api/workspace'),items=ws.trash||[];const body=`<p class="small-muted">${escapeHtml(i18nText('trash.explainer'))}</p><div class="history-list">${items.map(item=>`<div class="history-row"><div><b>${escapeHtml(item.name)}</b><small>${escapeHtml(i18nText('trash.deleted_at',{date:i18nDateTime(item.deleted_at)}))}</small></div><button class="quiet-button" data-trash-restore="${escapeHtml(item.trash_id)}">${escapeHtml(i18nText('trash.restore'))}</button></div>`).join('')||`<p class="small-muted">${escapeHtml(i18nText('trash.empty'))}</p>`}</div>`;showModal(i18nText('trash.title'),body,'',onDismiss);$$('[data-trash-restore]').forEach(button=>button.onclick=async()=>{const result=await api(`/api/trash/${encodeURIComponent(button.dataset.trashRestore)}/restore`,{method:'POST'});openWorkspaceProject(result?.project?.id||'')})}

