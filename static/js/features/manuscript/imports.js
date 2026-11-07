// -------------------- append document tabs --------------------
function beginDocumentImport(doc){
  const input=$('#documentImportInput');input.value='';input.dataset.afterDocumentId=String(doc.id);input.dataset.afterDocumentTitle=doc.title||'Tab';input.click();
}
$('#documentImportInput').oncancel=()=>{delete $('#documentImportInput').dataset.afterDocumentId;delete $('#documentImportInput').dataset.afterDocumentTitle};
$('#documentImportInput').onchange=async event=>{
  const input=event.target,file=input.files[0],afterDocumentId=entityId(input.dataset.afterDocumentId),afterDocumentTitle=input.dataset.afterDocumentTitle||'this tab';
  delete input.dataset.afterDocumentId;delete input.dataset.afterDocumentTitle;
  if(!file||!afterDocumentId){input.value='';return}
  const isWord=/\.docx?$/i.test(file.name);let preferredHeading='heading1';
  if(isWord){try{const saved=await api('/api/ui-state/import-chapter-heading');if(/^heading[1-6]$/.test(String(saved.value||'')))preferredHeading=String(saved.value)}catch(error){console.warn('Could not restore import chapter marker',error)}}
  const headingOptions=[1,2,3,4,5,6].map(level=>`<option value="heading${level}" ${preferredHeading===`heading${level}`?'selected':''}>${escapeHtml(i18nText('import.heading',{level:i18nNumber(level)}))}</option>`).join('');
  showModal('Import Tabs',`<p><b>${escapeHtml(file.name)}</b></p><p class="small-muted">${escapeHtml(i18nText('import.tabs_append_after',{title:afterDocumentTitle}))}</p>${isWord?`<div class="field"><label>{{i18n:dynamic.html.text.label.58994380}}</label><select id="documentImportChapterHeading">${headingOptions}</select></div><p class="small-muted">{{i18n:dynamic.html.text.p.8c525689}}</p>`:''}`,`<button id="confirmDocumentImport">{{i18n:dynamic.html.text.button.1da0fe67}}</button>`);
  $('#confirmDocumentImport').onclick=async()=>{
    const button=$('#confirmDocumentImport'),owner=importDialogOwner();if(button.disabled)return;button.disabled=true;
    const chapterHeading=$('#documentImportChapterHeading')?.value||'heading1';
    try{
      if(!await confirmExistingDocumentImport('Import Tabs',i18nText('import.append_warning'),owner))return;
      if(isWord)await api('/api/ui-state/import-chapter-heading',{method:'PUT',body:JSON.stringify({value:chapterHeading})});
      if(!ownsImportDialog(owner))return;
      try{
        if(hostedManuscriptRevisions())await runManuscriptImportWithProgress(file,'Preparing Tabs…',progress=>performDocumentTabImport(file,chapterHeading,afterDocumentId,progress),{resumable:true});
        else{currentModalDismiss=null;dismissModalTree();await performDocumentTabImport(file,chapterHeading,afterDocumentId)}
      }finally{input.value=''}
    }finally{if(button.isConnected)button.disabled=false}
  };
};
async function performDocumentTabImport(file,chapterHeading,afterDocumentId,progress=null,resumeLastAppend=false){
  const projectId=currentProjectIdentity();await flushSave();progress?.assertCurrent();const fd=new FormData();fd.append('file',file);setSaveState('Importing…');
  try{
    const label=`Import tabs: ${file.name}`;
    const result=await withStructuralUndo(label,()=>hostedManuscriptRevisions()?performHostedManuscriptRevision(file,chapterHeading,fd,{mode:'append',destination:afterDocumentId,label,progress,resumeLastAppend}):api(`/api/documents/import?after_document_id=${encodeURIComponent(afterDocumentId)}&chapter_heading=${encodeURIComponent(chapterHeading)}`,{method:'POST',body:fd}),{view:'manuscript',preserveUncertainAction:true,preservePublishedRevision:true,quietRollbackCodes:['HOSTED_IMPORT_CANCELLED','HOSTED_IMPORT_DIALOG_CHANGED']});
    progress?.assertCurrent();progress?.complete?.();
    await refresh(false);progress?.assertCurrent();
    if(result.first_document_id&&state.documents.some(doc=>entityId(doc.id)===entityId(result.first_document_id))){
      state.activeDocumentId=entityId(result.first_document_id);renderDocuments();renderChapterTree();const first=visibleChapters(state.activeDocumentId)[0];
      if(state.viewMode==='book'||(!first&&progress))await openWholeBook(false);else if(first)await selectChapter(first.id,false);
    }
    toast(i18nText('import.summary',{tabs:i18nCount(result.tabs,'import.tabs.one','import.tabs.many'),chapters:result.chapters?` · ${i18nCount(result.chapters,'import.chapters.one','import.chapters.many')}`:'',images:result.images?` · ${i18nCount(result.images,'import.images.one','import.images.many')}`:''}));return result;
  }catch(error){await refreshPublishedManuscriptRevision(error,progress);throw error}
  finally{if(projectId===currentProjectIdentity())setSaveState('Saved')}
}

// -------------------- import manuscript --------------------
$('#importInput').oncancel=()=>{if($('#importInput').dataset.returnToProjects==='1'){delete $('#importInput').dataset.returnToProjects;openProjectCenter()}};
function importedProjectName(file){const fallback=i18nSource('Imported Book');return String(file?.name||fallback).replace(/\.(?:docx?|txt)$/i,'').trim()||fallback}
async function preferredImportChapterHeading(file){
  if(!/\.docx?$/i.test(file?.name||''))return 'heading1';
  try{const saved=await api('/api/ui-state/import-chapter-heading');if(/^heading[1-6]$/.test(String(saved.value||'')))return String(saved.value)}catch(error){console.warn('Could not restore import chapter marker',error)}
  return 'heading1';
}
function importHeadingControl(file,preferredHeading,id='importChapterHeading'){
  if(!/\.docx?$/i.test(file?.name||''))return '';
  const options=[1,2,3,4,5,6].map(level=>`<option value="heading${level}" ${preferredHeading===`heading${level}`?'selected':''}>${escapeHtml(i18nText('import.heading',{level:i18nNumber(level)}))}</option>`).join('');
  return `<div class="field"><label>{{i18n:dynamic.html.text.label.58994380}}</label><select id="${id}">${options}</select></div><p class="small-muted">{{i18n:dynamic.html.text.p.6a871b9d}}</p>`;
}
async function rememberImportHeading(file,chapterHeading){if(/\.docx?$/i.test(file?.name||''))await api('/api/ui-state/import-chapter-heading',{method:'PUT',body:JSON.stringify({value:chapterHeading})})}
async function importReadiness(){try{return await api('/api/import/readiness')}catch(error){console.warn('Could not inspect manuscript import readiness',error);return {manuscript_empty:false,reason:'unknown'}}}

function importDialogOwner(){return {node:$('#modalBody').firstElementChild,revision:modalRevision,project:currentProjectIdentity(),actor:currentUserIdentity()}}
function ownsImportDialog(owner,checkRevision=false){return owner.project===currentProjectIdentity()&&owner.actor===currentUserIdentity()&&owner.node?.isConnected&&$('#modalBody').contains(owner.node)&&!$('#modal').classList.contains('hidden')&&(!checkRevision||owner.revision===modalRevision)}
async function confirmExistingDocumentImport(title,message,owner=importDialogOwner()){
  if(!ownsImportDialog(owner,true))return false;
  await flushSave();if(!ownsImportDialog(owner,true))return false;
  const current=await importReadiness();if(!ownsImportDialog(owner,true))return false;
  const confirmed=current.manuscript_empty&&(state.documents||[]).length<=1
    ?await confirmDialog(title,message,{confirmLabel:'Import',danger:title==='Replace Main Manuscript'})
    :(await dangerCodeDialog(title,typedConfirmationCode(5),i18nSource('Import'),{message,nested:true}))!==null;
  return confirmed&&ownsImportDialog(owner);
}

let manuscriptImportProgressTimer=null;
function stopManuscriptImportProgress(){if(manuscriptImportProgressTimer){clearInterval(manuscriptImportProgressTimer);manuscriptImportProgressTimer=null}}
function setManuscriptImportProgress(value,label){
  const pct=Math.max(0,Math.min(100,Math.round(+value||0))),bar=$('#manuscriptImportProgressBar'),status=$('#manuscriptImportProgressStatus'),shell=$('#manuscriptImportProgress');
  if(bar){bar.style.width=`${pct}%`;bar.parentElement?.setAttribute('aria-valuenow',String(pct));bar.parentElement?.setAttribute('aria-valuetext',i18nSource(label||'Importing…'))}
  if(status)status.textContent=i18nSource(label||'Importing…');
  shell?.style.setProperty('--import-progress',`${pct}%`);
}
function beginManuscriptImportProgress(file,label='Preparing document…',{bounded=false}={}){
  stopManuscriptImportProgress();
  $('#modalTitle').textContent=i18nSource('Importing Manuscript');
  $('#modalBody').innerHTML=`<div id="manuscriptImportProgress" class="manuscript-import-progress"><p class="manuscript-import-file"><b>${escapeHtml(file?.name||'Document')}</b></p><p id="manuscriptImportProgressStatus" class="small-muted">${escapeHtml(i18nSource(label))}</p><div class="manuscript-import-progress-track" role="progressbar" aria-label="{{i18n:dynamic.html.attr.aria.label.6733b843}}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="6"><span id="manuscriptImportProgressBar" style="width:6%"></span></div></div>`;
  $('#modalActions').innerHTML='<button id="cancelImportManuscript" type="button" disabled title="{{i18n:dynamic.html.attr.title.186b2109}}">{{i18n:dynamic.html.text.button.92113f72}}</button>';
  const close=$('#modalClose');if(close){close.disabled=true;close.setAttribute('aria-disabled','true')}
  let pct=bounded?0:6,tick=0;setManuscriptImportProgress(pct,label);
  if(bounded){$('#cancelImportManuscript').title=i18nSource('Cancel is available after the import is prepared.');return}
  manuscriptImportProgressTimer=setInterval(()=>{
    tick+=1;if(pct<88)pct=Math.min(88,pct+(pct<38?5:pct<68?3:1));
    const phase=i18nSource(pct<28?'Preparing document…':pct<56?'Reading document…':pct<78?'Importing content…':'Finishing import…');
    setManuscriptImportProgress(pct,phase);
  },180);
}
async function completeManuscriptImportProgress(){
  stopManuscriptImportProgress();setManuscriptImportProgress(100,'Import complete');
  await new Promise(resolve=>setTimeout(resolve,180));
}
function failManuscriptImportProgress(error,{bounded=false}={}){
  stopManuscriptImportProgress();const close=$('#modalClose');if(close){close.disabled=false;close.removeAttribute('aria-disabled')}
  const shell=$('#manuscriptImportProgress');shell?.classList.add('failed');
  const published=error?.code==='HOSTED_IMPORT_UNDO_UNCONFIRMED';
  setManuscriptImportProgress(published?100:bounded?Number($('#manuscriptImportProgressBar')?.parentElement?.getAttribute('aria-valuenow')):100,published?'Import complete; Undo needs attention':'Import could not be completed');
  const detail=document.createElement('p');detail.className='manuscript-import-error';detail.textContent=error?.message||i18nSource('The document could not be imported.');shell?.appendChild(detail);
  $('#modalActions').innerHTML='<button id="closeFailedImport" type="button">{{i18n:dynamic.html.text.button.972112a0}}</button>';$('#closeFailedImport').onclick=()=>closeModal();
}
function restoreImportModalClose(){const close=$('#modalClose');if(close){close.disabled=false;close.removeAttribute('aria-disabled')}}
function hostedManuscriptRevisions(){return runtimeSupports('hosted_persistence')&&runtimeSupports('hosted_manuscript_revisions')}
function manuscriptRevisionProgress(){
  const owner=importDialogOwner(),button=$('#cancelImportManuscript');
  const progress={job:null,cancelRequested:false,cancelPromise:null,publishing:false,published:false,
    isCurrent:()=>ownsImportDialog(owner,true),
    assertCurrent(){if(!this.isCurrent())throw new BreadmakerError('The import dialog or project changed. Reopen Import to continue.',0,'HOSTED_IMPORT_DIALOG_CHANGED')},
    setJob(job){this.assertCurrent();this.job=job;button.disabled=!job||this.cancelRequested||this.publishing||this.published;if(!button.disabled)button.removeAttribute('title')},
    disableCancel(){if(this.isCurrent()){button.disabled=true;button.onclick=null}},
    update(value,label){this.assertCurrent();setManuscriptImportProgress(value,label)}
  };
  button.onclick=()=>{if(button.disabled||!progress.isCurrent()||!progress.job||progress.publishing||progress.published)return;progress.cancelRequested=true;button.disabled=true;button.textContent=i18nSource('Cancelling…')};
  return progress;
}
async function runManuscriptImportWithProgress(file,label,operation,{reload=false,resumable=false}={}){
  beginManuscriptImportProgress(file,label,{bounded:resumable});
  const progress=resumable?manuscriptRevisionProgress():null;
  try{
    const result=await operation(progress);progress?.assertCurrent();await completeManuscriptImportProgress();progress?.assertCurrent();restoreImportModalClose();currentModalDismiss=null;dismissModalTree();if(progress?.undoUnavailable)toast('Import complete. Undo is no longer available for this import.',6000);if(reload){if(runtimeSupports('hosted_projects')&&result?.project?.id)openWorkspaceProject(result.project.id);else location.reload()}return {ok:true,result};
  }catch(error){
    if(progress&&!progress.isCurrent())return {ok:false,error};
    progress?.disableCancel();
    if(error.confirmedCancelled){stopManuscriptImportProgress();restoreImportModalClose();dismissModalTree();toast('Import cancelled');return {ok:false,cancelled:true}}
    console.error('Manuscript import failed',error);failManuscriptImportProgress(error,{bounded:resumable});return {ok:false,error};
  }
}

$('#importInput').onchange=async event=>{
  const input=event.target,returnToProjects=input.dataset.returnToProjects==='1';delete input.dataset.returnToProjects;
  const file=input.files[0];if(!file){if(returnToProjects)openProjectCenter();return}
  const preferredHeading=await preferredImportChapterHeading(file),readiness=await importReadiness();
  const hostedImport=runtimeSupports('hosted_persistence');
  const finish=()=>{input.value='';if(returnToProjects)openProjectCenter()};
  const dismissImport=()=>{stopManuscriptImportProgress();restoreImportModalClose();finish()};
  const cancelActions='<button id="cancelImportManuscript" type="button">{{i18n:dynamic.html.text.button.92113f72}}</button>';
  if(readiness.manuscript_empty&&!hostedImport){
    showModal('Import Manuscript',`<p><b>${escapeHtml(file.name)}</b></p><p class="small-muted">{{i18n:dynamic.html.text.p.45ac775d}}</p>${importHeadingControl(file,preferredHeading)}`,`${cancelActions}<button id="confirmImportManuscript">{{i18n:dynamic.html.text.button.d74537c8}}</button>`,dismissImport);
    $('#cancelImportManuscript').onclick=()=>closeModal();
    $('#confirmImportManuscript').onclick=async()=>{const chapterHeading=$('#importChapterHeading')?.value||'heading1';await rememberImportHeading(file,chapterHeading);const outcome=await runManuscriptImportWithProgress(file,'Preparing document…',()=>performManuscriptImport(file,chapterHeading));if(outcome.ok)finish()};
    return;
  }
  showModal('Import Manuscript',`
    <p><b>${escapeHtml(file.name)}</b></p>
    ${importHeadingControl(file,preferredHeading)}
    ${hostedImport&&!hostedManuscriptRevisions()?`<p class="small-muted">${escapeHtml(i18nText('import.existing_hosted_limit'))}</p>`:''}
    <div class="import-destination-list">
      <button id="importDestinationReplace" class="project-settings-row import-destination-row" type="button"><span><b>{{i18n:dynamic.html.text.b.68a52141}}</b><small>{{i18n:dynamic.html.text.small.eb38cc20}}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>
      <button id="importDestinationNew" class="project-settings-row import-destination-row" type="button"><span><b>{{i18n:dynamic.html.text.b.4fd07995}}</b><small>Create a separate project named “${escapeHtml(importedProjectName(file))}” and import the document there. This project stays untouched.</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>
      <button id="importDestinationTabs" class="project-settings-row import-destination-row" type="button"><span><b>{{i18n:dynamic.html.text.b.d7612017}}</b><small>{{i18n:dynamic.html.text.small.da7c50c5}}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>
    </div>`, cancelActions, dismissImport);
  $('#cancelImportManuscript').onclick=()=>closeModal();
  const heading=()=>$('#importChapterHeading')?.value||'heading1';
  let destinationBusy=false;
  const activate=async action=>{
    if(destinationBusy)return;destinationBusy=true;
    const owner=importDialogOwner(),buttons=$$('.import-destination-row');buttons.forEach(button=>button.disabled=true);
    try{await action(owner)}finally{destinationBusy=false;buttons.forEach(button=>{if(button.isConnected)button.disabled=false})}
  };
  $('#importDestinationReplace').onclick=()=>activate(async owner=>{
    const chapterHeading=heading();await rememberImportHeading(file,chapterHeading);
    const confirmed=await confirmExistingDocumentImport('Replace Main Manuscript',i18nText(hostedImport?'import.replace_hosted_warning':'confirm.replace_main_manuscript',{file:file.name}),owner);
    if(!confirmed)return;
    const outcome=await runManuscriptImportWithProgress(file,'Preparing replacement…',progress=>performManuscriptImport(file,chapterHeading,true,progress),{resumable:hostedManuscriptRevisions()});if(outcome.ok)finish();
  });
  $('#importDestinationNew').onclick=()=>activate(async owner=>{
    const chapterHeading=heading();await rememberImportHeading(file,chapterHeading);if(!ownsImportDialog(owner,true))return;
    const outcome=await runManuscriptImportWithProgress(file,'Preparing new project…',()=>performManuscriptImportAsNewProject(file,chapterHeading),{reload:true});if(outcome.ok)input.value='';
  });
  $('#importDestinationTabs').onclick=()=>activate(async owner=>{
    const chapterHeading=heading();if(!await confirmExistingDocumentImport('Import Tabs',i18nText('import.append_warning'),owner))return;
    await rememberImportHeading(file,chapterHeading);if(!ownsImportDialog(owner))return;
    const docs=[...(state.documents||[])].sort((a,b)=>(+a.position||0)-(+b.position||0)||compareEntityIds(a.id,b.id)),last=docs.at(-1);
    if(!last)return toast('No Tab is available to append after.');
    const outcome=await runManuscriptImportWithProgress(file,'Preparing Tabs…',progress=>performDocumentTabImport(file,chapterHeading,last.id,progress,true),{resumable:hostedManuscriptRevisions()});if(outcome.ok)finish();
  });
};
async function performManuscriptImport(file,chapterHeading,confirmed=false,progress=null){
  const projectId=currentProjectIdentity();await flushSave();progress?.assertCurrent();const fd=new FormData();fd.append('file',file);setSaveState('Importing…');
  try{
    const label='Import manuscript';
    const result=await withStructuralUndo(label,()=>hostedManuscriptRevisions()?performHostedManuscriptRevision(file,chapterHeading,fd,{mode:'replace',confirmed,label,progress}):api(`/api/import?chapter_heading=${encodeURIComponent(chapterHeading)}&confirmed=${confirmed}`,{method:'POST',body:fd}),{view:'manuscript',preserveUncertainAction:true,preservePublishedRevision:true,quietRollbackCodes:['HOSTED_IMPORT_CANCELLED','HOSTED_IMPORT_DIALOG_CHANGED']});
    progress?.assertCurrent();progress?.complete?.();await refresh(false);progress?.assertCurrent();const primary=state.documents.find(d=>d.is_manuscript)||state.documents[0];state.activeDocumentId=primary?.id||null;renderDocuments();renderChapterTree();const first=visibleChapters(state.activeDocumentId)[0];if(first)await selectChapter(first.id,false);else if(progress)await openWholeBook(false);toast(i18nText('import.chapter_summary',{chapters:i18nCount(result.chapters,'import.chapters.one','import.chapters.many'),images:result.images?` · ${i18nCount(result.images,'import.images.one','import.images.many')}`:''}));return result;
  }catch(error){await refreshPublishedManuscriptRevision(error,progress);throw error}
  finally{if(projectId===currentProjectIdentity())setSaveState('Saved')}
}
async function performManuscriptImportAsNewProject(file,chapterHeading){
  await flushSave();const fd=new FormData();fd.append('file',file);setSaveState('Importing…');
  try{
    if(!runtimeSupports('hosted_persistence'))return await api(`/api/import/new-project?chapter_heading=${encodeURIComponent(chapterHeading)}&project_name=${encodeURIComponent(importedProjectName(file))}`,{method:'POST',body:fd});
    return await performHostedProjectImport(file,chapterHeading,fd);
  }finally{setSaveState('Saved')}
}

function terminalManuscriptRevisionError(error){
  return ['HOSTED_REPLAY_SUPERSEDED','HOSTED_IMPORT_EXPIRED','HOSTED_IMPORT_CANCELLED','HOSTED_GENERATION_CONFLICT','HOSTED_IMPORT_REQUEST_CHANGED','HOSTED_IMPORT_PLAN_CHANGED','HOSTED_IMPORT_NOT_FOUND'].includes(error?.code||error?.message)||error?.status===404||error?.status===410;
}
async function refreshPublishedManuscriptRevision(error,progress){
  if(error?.code!=='HOSTED_IMPORT_UNDO_UNCONFIRMED'||!progress?.isCurrent())return;
  try{
    await refresh(false);progress.assertCurrent();
    const doc=state.documents.find(item=>entityId(item.id)===entityId(error.publishedResult?.first_document_id))||state.documents.find(item=>item.is_manuscript)||state.documents[0];
    state.activeDocumentId=doc?.id||null;renderDocuments();renderChapterTree();
    const first=visibleChapters(state.activeDocumentId)[0];if(first)await selectChapter(first.id,false);else await openWholeBook(false);
  }catch(refreshError){console.error('Published import refresh failed',refreshError);error.message+=` ${i18nSource('Reload this project to see the imported manuscript.')}`}
}
async function performHostedManuscriptRevision(file,chapterHeading,fd,{mode,destination='',confirmed=false,label,progress,resumeLastAppend=false}){
  if(file.size>64*1024*1024)throw new Error('The document exceeds the 64 MiB import limit.');
  if(!progress)throw new Error('Reopen Import to start this import safely.');
  progress.assertCurrent();
  const projectId=currentProjectIdentity(),digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),b=>b.toString(16).padStart(2,'0')).join('');
  progress.assertCurrent();
  const actor=currentUserIdentity();
  // The main chooser's implicit "last tab" changes after a successful append.
  // Check a unique matching pending import at its original destination instead
  // of duplicating a commit whose response was lost. Explicit tab-menu imports
  // always retain the writer's chosen destination.
  if(mode==='append'&&resumeLastAppend){
    const candidates=[];
    for(const key of Object.keys(sessionStorage)){
      if(!key.startsWith('breadmaker-pending-manuscript-revision:'))continue;
      try{const value=JSON.parse(sessionStorage.getItem(key)),scope=JSON.parse(value.signature);if(value.key&&key===`breadmaker-pending-manuscript-revision:${value.signature}`&&scope[0]===projectId&&scope[1]===actor&&scope[2]===mode&&scope[4]===digest&&scope[5]===chapterHeading)candidates.push(scope)}catch{}
    }
    if(candidates.length>1)throw new Error('Several imports of this document are paused. Use Import Tabs from the original destination tab’s menu to resume one.');
    if(candidates.length===1)destination=candidates[0][3];
  }
  const signature=JSON.stringify([projectId,actor,mode,String(destination),digest,chapterHeading]);
  const storageKey=`breadmaker-pending-manuscript-revision:${signature}`;
  let pending=null;
  try{pending=JSON.parse(sessionStorage.getItem(storageKey)||'null')}catch{}
  if(pending?.signature!==signature||!pending.key)pending={signature,key:uid()};
  const persist=()=>sessionStorage.setItem(storageKey,JSON.stringify(pending));
  const clear=()=>sessionStorage.removeItem(storageKey);
  const request=async(url,options={})=>{progress.assertCurrent();const result=await api(url,{...options,headers:{'X-Breadmaker-Project':String(projectId)}});progress.assertCurrent();return result};
  const jobUrl=()=>`/api/import/revisions/${encodeURIComponent(pending.job)}`;
  const completed=async status=>{
    const result=status.result;
    progress.published=true;progress.disableCancel();
    if(!result?.undo_token){const error=new BreadmakerError('The import completed, but its Undo result could not be verified.',0,'HOSTED_IMPORT_UNDO_UNCONFIRMED');error.publishedResult=result;throw error}
    progress.undoUnavailable=result.undo_available===false;
    if(!progress.undoUnavailable&&result.undo_token!==hostedStructuralActionToken&&!state.undoStack.some(item=>item.kind==='structural'&&item.token===result.undo_token)){
      try{await finishStructuralUndo(result.undo_token,label,{view:'manuscript',projectId});progress.assertCurrent()}
      catch(error){const failure=new BreadmakerError('The import completed, but Undo could not be confirmed.',0,'HOSTED_IMPORT_UNDO_UNCONFIRMED');failure.publishedResult=result;throw failure}
    }
    progress.complete=clear;
    return result;
  };
  const cancelIfRequested=async status=>{
    if(!progress.cancelRequested||status.completed)return status;
    progress.disableCancel();
    if(!progress.cancelPromise)progress.cancelPromise=(async()=>{
      let result;
      try{result=await request(`${jobUrl()}/cancel`,{method:'POST'})}
      catch(error){
        if((error.code||error.message)!=='HOSTED_IMPORT_ALREADY_PUBLISHED_USE_UNDO')throw error;
        let checked;
        try{checked=await request(jobUrl())}catch(statusError){if(terminalManuscriptRevisionError(statusError))throw statusError}
        if(checked?.completed)return checked;
        throw new BreadmakerError('The server reports that this import was published, but its result could not be confirmed. Reopen Import with the same document and destination to check it.',0,'HOSTED_IMPORT_PUBLICATION_UNCERTAIN');
      }
      if(result.completed)return result;
      if(result.cancelled!==true)throw new Error('Cancellation could not be confirmed. Reopen Import with the same document to check it.');
      clear();const error=new BreadmakerError('Import cancelled',0,'HOSTED_IMPORT_CANCELLED');error.confirmedCancelled=true;throw error;
    })();
    return progress.cancelPromise;
  };
  try{
    persist();let status;
    if(pending.job)status=await request(jobUrl());
    else{
      const url=mode==='append'?`/api/documents/import?resumable=true&after_document_id=${encodeURIComponent(destination)}&chapter_heading=${encodeURIComponent(chapterHeading)}`:`/api/import?resumable=true&chapter_heading=${encodeURIComponent(chapterHeading)}&confirmed=${confirmed}`;
      status=await request(url,{method:'POST',body:fd,idempotencyKey:pending.key});
      if(!status.job_id)throw new Error('The import job could not be verified. Reopen Import with the same document to retry.');
      pending.job=status.job_id;persist();
    }
    progress.setJob(pending.job);
    while(true){
      status=await cancelIfRequested(status);
      if(status.completed)return await completed(status);
      if(status.ready)break;
      if(!Number.isInteger(status.next_batch)||status.next_batch<0)throw new Error('The import progress could not be verified. Reopen Import to retry.');
      // next_batch is an opaque, replay-safe request cursor. Adoption and image
      // work advance it too; only content_batch measures paragraph progress.
      if(status.phase==='adopting'||status.phase==='checking'){
        const checked=status.adoption?.records_validated;
        progress.update(0,Number.isInteger(checked)?`Checking the existing manuscript: ${i18nNumber(checked)} records`:'Checking the existing manuscript…');
      }else if(status.phase==='images'){
        const done=status.image_next,total=status.image_total;
        if(!Number.isInteger(done)||!Number.isInteger(total)||done<0||total<1||done>total)throw new Error('The import image progress could not be verified. Reopen Import to retry.');
        progress.update(15*done/total,`Preparing images: ${i18nNumber(done)} / ${i18nNumber(total)}`);
      }else{
        const done=status.content_batch??status.next_batch,total=status.content_batches??status.total_batches;
        if(!Number.isInteger(done)||!Number.isInteger(total)||done<0||total<1||done>total)throw new Error('The import progress could not be verified. Reopen Import to retry.');
        progress.update(15+75*done/total,`Importing content: ${i18nNumber(done)} / ${i18nNumber(total)}`);
      }
      try{status=await request(`${jobUrl()}/step?batch=${status.next_batch}`,{method:'POST'})}
      catch(error){if(!progress.cancelRequested)throw error;status=await cancelIfRequested(status)}
    }
    // No event may start cancellation after publication begins. All accepted
    // cancellation work is settled first, including a cancel that found a commit.
    status=await cancelIfRequested(status);
    if(status.completed)return await completed(status);
    progress.publishing=true;progress.disableCancel();progress.update(95,'Publishing completed manuscript…');
    let result;
    try{result=await request(`${jobUrl()}/finish`,{method:'POST'})}
    catch(error){
      if(terminalManuscriptRevisionError(error))throw error;
      // A failed response is not proof that publication failed. Never roll back
      // or republish an unknown outcome; the durable job remains the authority.
      let checked;
      try{checked=await request(jobUrl())}
      catch(statusError){if(terminalManuscriptRevisionError(statusError))throw statusError}
      if(checked?.completed)return await completed(checked);
      if(checked&&error.status>=400&&error.status<500)throw error;
      throw new BreadmakerError('Publication could not be confirmed. Before editing, reopen Import with the same document and destination to check the result.',0,'HOSTED_IMPORT_PUBLICATION_UNCERTAIN');
    }
    return await completed({completed:true,result});
  }catch(error){
    if(terminalManuscriptRevisionError(error)){
      clear();if(error.confirmedCancelled)throw error;
      throw new BreadmakerError('This import can no longer be resumed. Close this dialog, choose the document again and confirm a new import.',error.status,error.code||error.message);
    }
    if(!['HOSTED_IMPORT_PUBLICATION_UNCERTAIN','HOSTED_IMPORT_DIALOG_CHANGED'].includes(error.code)&&!progress.published&&error.status!==401&&error.status!==403)error.message+=` ${i18nSource('Choose the same document, destination and heading again to resume this import.')}`;
    throw error;
  }
}

async function performHostedProjectImport(file,chapterHeading,fd){
  if(file.size>64*1024*1024)throw new Error('The document exceeds the 64 MiB import limit.');
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),b=>b.toString(16).padStart(2,'0')).join('');
  const storageKey='breadmaker-pending-document-import';let pending=null;
  try{pending=JSON.parse(sessionStorage.getItem(storageKey)||'null')}catch{}
  const signature=JSON.stringify([digest,file.name,chapterHeading]);
  if(pending?.signature!==signature||Date.now()-Number(pending.created)>86400000){pending={signature,key:uid(),created:Date.now()};sessionStorage.setItem(storageKey,JSON.stringify(pending))}
  let progress;
  if(pending.job){
    try{progress=await api(`/api/import/jobs/${encodeURIComponent(pending.job)}`)}
    catch(error){if(error.status!==404&&!(error.status===409&&(error.code||error.message)==='HOSTED_IMPORT_RESTART_REQUIRED'))throw error;pending={signature,key:uid(),created:Date.now()};sessionStorage.setItem(storageKey,JSON.stringify(pending))}
  }
  if(!progress){
    progress=await api(`/api/import/new-project?resumable=true&chapter_heading=${encodeURIComponent(chapterHeading)}&project_name=${encodeURIComponent(importedProjectName(file))}`,{method:'POST',body:fd,idempotencyKey:pending.key});
    pending.job=progress.job_id;sessionStorage.setItem(storageKey,JSON.stringify(pending));
  }
  stopManuscriptImportProgress();
  while(!progress.ready){
    setManuscriptImportProgress(10+85*progress.next_batch/progress.total_batches,`Importing content: ${progress.next_batch} / ${progress.total_batches}`);
    progress=await api(`/api/import/jobs/${encodeURIComponent(progress.job_id)}/step?batch=${progress.next_batch}`,{method:'POST'});
  }
  setManuscriptImportProgress(97,'Publishing completed manuscript…');
  const result=await api(`/api/import/jobs/${encodeURIComponent(progress.job_id)}/finish`,{method:'POST'});
  sessionStorage.removeItem(storageKey);return result;
}

