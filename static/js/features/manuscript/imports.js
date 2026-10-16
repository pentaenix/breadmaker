// -------------------- append document tabs --------------------
function beginDocumentImport(doc){
  const input=$('#documentImportInput');input.value='';input.dataset.afterDocumentId=String(doc.id);input.dataset.afterDocumentTitle=doc.title||'Tab';input.click();
}
$('#documentImportInput').oncancel=()=>{delete $('#documentImportInput').dataset.afterDocumentId;delete $('#documentImportInput').dataset.afterDocumentTitle};
$('#documentImportInput').onchange=async event=>{
  const input=event.target,file=input.files[0],afterDocumentId=+input.dataset.afterDocumentId,afterDocumentTitle=input.dataset.afterDocumentTitle||'this tab';
  delete input.dataset.afterDocumentId;delete input.dataset.afterDocumentTitle;
  if(!file||!afterDocumentId){input.value='';return}
  const isWord=/\.docx?$/i.test(file.name);let preferredHeading='heading1';
  if(isWord){try{const saved=await api('/api/ui-state/import-chapter-heading');if(/^heading[1-6]$/.test(String(saved.value||'')))preferredHeading=String(saved.value)}catch(error){console.warn('Could not restore import chapter marker',error)}}
  const headingOptions=[1,2,3,4,5,6].map(level=>`<option value="heading${level}" ${preferredHeading===`heading${level}`?'selected':''}>${escapeHtml(i18nText('import.heading',{level:i18nNumber(level)}))}</option>`).join('');
  showModal('Import Tabs',`<p><b>${escapeHtml(file.name)}</b></p><p class="small-muted">${escapeHtml(i18nText('import.tabs_append_after',{title:afterDocumentTitle}))}</p>${isWord?`<div class="field"><label>{{i18n:dynamic.html.text.label.58994380}}</label><select id="documentImportChapterHeading">${headingOptions}</select></div><p class="small-muted">{{i18n:dynamic.html.text.p.8c525689}}</p>`:''}`,`<button id="confirmDocumentImport">{{i18n:dynamic.html.text.button.1da0fe67}}</button>`);
  $('#confirmDocumentImport').onclick=async()=>{const chapterHeading=$('#documentImportChapterHeading')?.value||'heading1';if(isWord)await api('/api/ui-state/import-chapter-heading',{method:'PUT',body:JSON.stringify({value:chapterHeading})});currentModalDismiss=null;dismissModalTree();try{await performDocumentTabImport(file,chapterHeading,afterDocumentId)}finally{input.value=''}};
};
async function performDocumentTabImport(file,chapterHeading,afterDocumentId){
  await flushSave();const fd=new FormData();fd.append('file',file);setSaveState('Importing…');
  try{
    const result=await withStructuralUndo(`Import tabs: ${file.name}`,()=>api(`/api/documents/import?after_document_id=${encodeURIComponent(afterDocumentId)}&chapter_heading=${encodeURIComponent(chapterHeading)}`,{method:'POST',body:fd}),{view:'manuscript'});
    await refresh(false);
    if(result.first_document_id&&state.documents.some(doc=>+doc.id===+result.first_document_id)){
      state.activeDocumentId=+result.first_document_id;renderDocuments();renderChapterTree();const first=visibleChapters(state.activeDocumentId)[0];
      if(state.viewMode==='book')await openWholeBook(false);else if(first)await selectChapter(first.id,false);
    }
    toast(i18nText('import.summary',{tabs:i18nCount(result.tabs,'import.tabs.one','import.tabs.many'),chapters:result.chapters?` · ${i18nCount(result.chapters,'import.chapters.one','import.chapters.many')}`:'',images:result.images?` · ${i18nCount(result.images,'import.images.one','import.images.many')}`:''}));
  }finally{setSaveState('Saved')}
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

let manuscriptImportProgressTimer=null;
function stopManuscriptImportProgress(){if(manuscriptImportProgressTimer){clearInterval(manuscriptImportProgressTimer);manuscriptImportProgressTimer=null}}
function setManuscriptImportProgress(value,label){
  const pct=Math.max(0,Math.min(100,Math.round(+value||0))),bar=$('#manuscriptImportProgressBar'),status=$('#manuscriptImportProgressStatus'),shell=$('#manuscriptImportProgress');
  if(bar){bar.style.width=`${pct}%`;bar.parentElement?.setAttribute('aria-valuenow',String(pct))}
  if(status)status.textContent=i18nSource(label||'Importing…');
  shell?.style.setProperty('--import-progress',`${pct}%`);
}
function beginManuscriptImportProgress(file,label='Preparing document…'){
  stopManuscriptImportProgress();
  $('#modalTitle').textContent=i18nSource('Importing Manuscript');
  $('#modalBody').innerHTML=`<div id="manuscriptImportProgress" class="manuscript-import-progress"><p class="manuscript-import-file"><b>${escapeHtml(file?.name||'Document')}</b></p><p id="manuscriptImportProgressStatus" class="small-muted">${escapeHtml(i18nSource(label))}</p><div class="manuscript-import-progress-track" role="progressbar" aria-label="{{i18n:dynamic.html.attr.aria.label.6733b843}}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="6"><span id="manuscriptImportProgressBar" style="width:6%"></span></div></div>`;
  $('#modalActions').innerHTML='<button id="cancelImportManuscript" type="button" disabled title="{{i18n:dynamic.html.attr.title.186b2109}}">{{i18n:dynamic.html.text.button.92113f72}}</button>';
  const close=$('#modalClose');if(close){close.disabled=true;close.setAttribute('aria-disabled','true')}
  let pct=6,tick=0;setManuscriptImportProgress(pct,label);
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
function failManuscriptImportProgress(error){
  stopManuscriptImportProgress();const close=$('#modalClose');if(close){close.disabled=false;close.removeAttribute('aria-disabled')}
  const shell=$('#manuscriptImportProgress');shell?.classList.add('failed');
  setManuscriptImportProgress(100,'Import could not be completed');
  const detail=document.createElement('p');detail.className='manuscript-import-error';detail.textContent=error?.message||i18nSource('The document could not be imported.');shell?.appendChild(detail);
  $('#modalActions').innerHTML='<button id="closeFailedImport" type="button">{{i18n:dynamic.html.text.button.972112a0}}</button>';$('#closeFailedImport').onclick=()=>closeModal();
}
function restoreImportModalClose(){const close=$('#modalClose');if(close){close.disabled=false;close.removeAttribute('aria-disabled')}}
async function runManuscriptImportWithProgress(file,label,operation,{reload=false}={}){
  beginManuscriptImportProgress(file,label);
  try{
    const result=await operation();await completeManuscriptImportProgress();restoreImportModalClose();currentModalDismiss=null;dismissModalTree();if(reload)location.reload();return {ok:true,result};
  }catch(error){console.error('Manuscript import failed',error);failManuscriptImportProgress(error);return {ok:false,error}}
}

$('#importInput').onchange=async event=>{
  const input=event.target,returnToProjects=input.dataset.returnToProjects==='1';delete input.dataset.returnToProjects;
  const file=input.files[0];if(!file){if(returnToProjects)openProjectCenter();return}
  const preferredHeading=await preferredImportChapterHeading(file),readiness=await importReadiness();
  const finish=()=>{input.value='';if(returnToProjects)openProjectCenter()};
  const dismissImport=()=>{stopManuscriptImportProgress();restoreImportModalClose();finish()};
  const cancelActions='<button id="cancelImportManuscript" type="button">{{i18n:dynamic.html.text.button.92113f72}}</button>';
  if(readiness.manuscript_empty){
    showModal('Import Manuscript',`<p><b>${escapeHtml(file.name)}</b></p><p class="small-muted">{{i18n:dynamic.html.text.p.45ac775d}}</p>${importHeadingControl(file,preferredHeading)}`,`${cancelActions}<button id="confirmImportManuscript">{{i18n:dynamic.html.text.button.d74537c8}}</button>`,dismissImport);
    $('#cancelImportManuscript').onclick=()=>closeModal();
    $('#confirmImportManuscript').onclick=async()=>{const chapterHeading=$('#importChapterHeading')?.value||'heading1';await rememberImportHeading(file,chapterHeading);const outcome=await runManuscriptImportWithProgress(file,'Preparing document…',()=>performManuscriptImport(file,chapterHeading));if(outcome.ok)finish()};
    return;
  }
  showModal('Import Manuscript',`
    <p><b>${escapeHtml(file.name)}</b></p>
    ${importHeadingControl(file,preferredHeading)}
    <div class="import-destination-list">
      <button id="importDestinationReplace" class="project-settings-row import-destination-row" type="button"><span><b>{{i18n:dynamic.html.text.b.68a52141}}</b><small>{{i18n:dynamic.html.text.small.eb38cc20}}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>
      <button id="importDestinationNew" class="project-settings-row import-destination-row" type="button"><span><b>{{i18n:dynamic.html.text.b.4fd07995}}</b><small>Create a separate project named “${escapeHtml(importedProjectName(file))}” and import the document there. This project stays untouched.</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>
      <button id="importDestinationTabs" class="project-settings-row import-destination-row" type="button"><span><b>{{i18n:dynamic.html.text.b.d7612017}}</b><small>{{i18n:dynamic.html.text.small.da7c50c5}}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>
    </div>`, cancelActions, dismissImport);
  $('#cancelImportManuscript').onclick=()=>closeModal();
  const heading=()=>$('#importChapterHeading')?.value||'heading1';
  $('#importDestinationReplace').onclick=async()=>{
    const chapterHeading=heading();await rememberImportHeading(file,chapterHeading);
    const confirmed=await confirmDialog('Replace Main Manuscript',i18nText('confirm.replace_main_manuscript',{file:file.name}),{confirmLabel:'Replace Main Manuscript',danger:true});
    if(!confirmed)return;
    const outcome=await runManuscriptImportWithProgress(file,'Preparing replacement…',()=>performManuscriptImport(file,chapterHeading));if(outcome.ok)finish();
  };
  $('#importDestinationNew').onclick=async()=>{const chapterHeading=heading();await rememberImportHeading(file,chapterHeading);const outcome=await runManuscriptImportWithProgress(file,'Preparing new project…',()=>performManuscriptImportAsNewProject(file,chapterHeading),{reload:true});if(outcome.ok)input.value=''};
  $('#importDestinationTabs').onclick=async()=>{const chapterHeading=heading();await rememberImportHeading(file,chapterHeading);const docs=[...(state.documents||[])].sort((a,b)=>(+a.position||0)-(+b.position||0)||(+a.id)-(+b.id)),last=docs.at(-1);if(!last)return toast('No Tab is available to append after.');const outcome=await runManuscriptImportWithProgress(file,'Preparing Tabs…',()=>performDocumentTabImport(file,chapterHeading,last.id));if(outcome.ok)finish()};
};
async function performManuscriptImport(file,chapterHeading){
  await flushSave();const fd=new FormData();fd.append('file',file);setSaveState('Importing…');
  try{let result;await withStructuralUndo('Import manuscript',async()=>{result=await api(`/api/import?chapter_heading=${encodeURIComponent(chapterHeading)}`,{method:'POST',body:fd})},{view:'manuscript'});await refresh(false);const primary=state.documents.find(d=>d.is_manuscript)||state.documents[0];state.activeDocumentId=primary?.id||null;renderDocuments();renderChapterTree();const first=visibleChapters(state.activeDocumentId)[0];if(first)await selectChapter(first.id,false);toast(i18nText('import.chapter_summary',{chapters:i18nCount(result.chapters,'import.chapters.one','import.chapters.many'),images:result.images?` · ${i18nCount(result.images,'import.images.one','import.images.many')}`:''}))}finally{setSaveState('Saved')}
}
async function performManuscriptImportAsNewProject(file,chapterHeading){
  await flushSave();const fd=new FormData();fd.append('file',file);setSaveState('Importing…');
  try{return await api(`/api/import/new-project?chapter_heading=${encodeURIComponent(chapterHeading)}&project_name=${encodeURIComponent(importedProjectName(file))}`,{method:'POST',body:fd})}finally{setSaveState('Saved')}
}

