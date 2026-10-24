// Manuscript dirty-state, autosave, conflict handling, and status counts.

async function captureAutoHistory(source='quiet'){
  clearTimeout(state.historyQuietTimer);state.historyQuietTimer=null;
  await flushSave();
  const historyLabel=state.lastRevisionLabel||i18nSource('Saved from automatic History.');
  const result=await api(`/api/autocommit?label=${encodeURIComponent(historyLabel)}&source=${encodeURIComponent(source)}`,{method:'POST'});
  state.lastHistoryCaptureAt=Date.now();return result;
}
function scheduleQuietHistory(){
  clearTimeout(state.historyQuietTimer);
  state.historyQuietTimer=setTimeout(()=>captureAutoHistory('quiet').catch(err=>console.warn('Quiet History save failed',err)),90*1000);
}
// Active-editor work only: one bounded request at a time, no periodic polling,
// persisted manuscript cache, or dependency of a successful save on maintenance.
const hostedReadCacheQueue=new Map(),hostedReadCacheCompleted=new Map();
let hostedReadCacheScheduled=false,hostedReadCacheRunning=false;
function queueHostedReadCache(chapterId,revision){
  if(!runtimeSupports('hosted_persistence')||!chapterId)return;
  const project=String(currentProjectIdentity()||'');if(!project)return;
  const key=`${project}:${chapterId}`,version=String(revision||'');
  for(const [id,job] of hostedReadCacheQueue)if(job.project!==project)hostedReadCacheQueue.delete(id);
  if(hostedReadCacheCompleted.get(key)===version)return;
  if(hostedReadCacheQueue.get(key)?.revision!==version)hostedReadCacheQueue.set(key,{project,chapterId,revision:version});
  while(hostedReadCacheQueue.size>64)hostedReadCacheQueue.delete(hostedReadCacheQueue.keys().next().value);
  kickHostedReadCache();
}
function kickHostedReadCache(){
  if(hostedReadCacheScheduled||hostedReadCacheRunning||!hostedReadCacheQueue.size||state.dirty||state.saveInFlight||document.visibilityState==='hidden')return;
  hostedReadCacheScheduled=true;
  const run=()=>{void drainHostedReadCache()};
  if(typeof window.requestIdleCallback==='function')window.requestIdleCallback(run,{timeout:1500});else setTimeout(run,150);
}
async function drainHostedReadCache(){
  hostedReadCacheScheduled=false;
  if(hostedReadCacheRunning||state.dirty||state.saveInFlight||document.visibilityState==='hidden')return;
  const entry=hostedReadCacheQueue.entries().next().value;if(!entry)return;
  const [key,job]=entry;
  if(String(currentProjectIdentity()||'')!==job.project){hostedReadCacheQueue.delete(key);kickHostedReadCache();return}
  hostedReadCacheRunning=true;
  try{
    const result=await api(`/api/chapters/${encodeURIComponent(job.chapterId)}/read-cache`,{method:'POST'});
    if(hostedReadCacheQueue.get(key)===job&&!result.more){
      hostedReadCacheQueue.delete(key);hostedReadCacheCompleted.set(key,job.revision);
      while(hostedReadCacheCompleted.size>64)hostedReadCacheCompleted.delete(hostedReadCacheCompleted.keys().next().value);
    }
  }catch(error){
    // An outage/rate limit cannot fail a saved manuscript or cause an idle retry
    // loop. A later open/save receives fresh server hints and resumes the work.
    if(hostedReadCacheQueue.get(key)===job)hostedReadCacheQueue.delete(key);
    console.warn('Read cache maintenance deferred; manuscript save is unaffected',error);
  }finally{hostedReadCacheRunning=false;kickHostedReadCache()}
}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')kickHostedReadCache()});

function resetDirtyState(){state.dirty=false;state.dirtyChapters.clear();state.chapterDirtyGeneration.clear();state.chapterSavedGeneration.clear();}
function manuscriptMutationBarrier(on){state.mutationBarrierDepth=Math.max(0,state.mutationBarrierDepth+(on?1:-1));document.body.classList.toggle('manuscript-mutating',state.mutationBarrierDepth>0);}
async function withManuscriptMutationBarrier(fn){manuscriptMutationBarrier(true);try{return await fn()}finally{manuscriptMutationBarrier(false)}}
$('#manuscriptPaper')?.addEventListener('beforeinput',event=>{if(state.mutationBarrierDepth>0){event.preventDefault();event.stopImmediatePropagation();toast('Finishing the current manuscript change…',1200)}},true);
function markDirty(source){
  state.bookCache.clear();
  let chapterId=null,wrap=null;if(typeof source==='number'||typeof source==='string')chapterId=entityId(source);else if(source?.target){wrap=source.target.closest?.('.manuscript-block-wrap');chapterId=wrap?entityId(wrap.dataset.chapterId):null}if(!chapterId&&state.viewMode==='chapter')chapterId=state.chapter?.id;if(wrap&&outlineStyleLevel(wrap.dataset.style)){const text=wrap.querySelector('.manuscript-block')?.innerText||'';const item=state.chapterOutlines.find(row=>String(row.id)===String(wrap.dataset.blockId));if(item)item.text_content=text;const label=$(`.outline-nav[data-block-id="${CSS.escape(String(wrap.dataset.blockId))}"] .outline-nav-label`);if(label)label.textContent=text||i18nSource('Untitled section')}
  const wasClean=!state.dirty&&!state.dirtyChapters.size;const generation=++state.editGeneration;if(chapterId){state.dirtyChapters.add(chapterId);state.chapterDirtyGeneration.set(entityId(chapterId),generation)}state.dirty=true;if(wasClean)ensureEditBaseline().catch(error=>console.warn('Edit baseline failed',error));state.lastRevisionLabel=`Edited ${visibleChapterById(chapterId)?.title||'manuscript'}`;setSaveState('Saving…');scheduleCrashJournal();clearTimeout(state.saveTimer);state.saveTimer=setTimeout(()=>flushSave().catch(()=>{}),700);scheduleQuietHistory();scheduleWordCount();
}

async function saveOneChapter(chapterId){
  const wraps=wrapsForChapter(chapterId),blocks=wraps.map(serializeWrap),ch=storageChapterById(chapterId);if(!ch)return;
  await ensureEditBaseline();let revision=state.chapterRevisions.get(entityId(chapterId))||ch.revision||'',title=visibleChapterTitleText(chapterId);
  if(title!==ch.title){const titled=await api(`/api/chapters/${chapterId}/title`,{method:'PATCH',body:JSON.stringify({title,expected_revision:revision})});revision=titled.revision||revision;state.chapterRevisions.set(entityId(chapterId),revision);updateChapterTitleState(chapterId,title,revision)}
  const result=await api(`/api/chapters/${chapterId}/blocks`,{method:'PUT',body:JSON.stringify({blocks,expected_revision:revision})});state.chapterRevisions.set(entityId(chapterId),result.revision||revision);ch.revision=result.revision||revision;queueHostedReadCache(chapterId,result.revision||revision);await clearCrashDraft(chapterId);let outlineIdsChanged=false;wraps.forEach((wrap,index)=>{if(wrap.isConnected&&result.ids[index]&&wrap.dataset.blockId.startsWith('tmp_')){const previous=wrap.dataset.blockId;wrap.dataset.blockId=result.ids[index];if(state.manuscriptCollapsedHeadings.delete(previous)){state.manuscriptCollapsedHeadings.add(result.ids[index]);outlineIdsChanged=true}}});state.chapterOutlines=state.chapterOutlines.filter(item=>entityId(item.chapter_id)!==entityId(chapterId));wraps.forEach((wrap,index)=>{if(outlineStyleLevel(wrap.dataset.style))state.chapterOutlines.push({id:wrap.dataset.blockId,chapter_id:entityId(chapterId),position:index,semantic_style:wrap.dataset.style,text_content:wrap.querySelector('.manuscript-block')?.innerText||''})});renderChapterTree();if(outlineIdsChanged)persistManuscriptOutlineState();
}
async function saveSceneView(){
  if(state.viewMode!=='scene'||!state.scene)return;await ensureEditBaseline();const wraps=$$('.manuscript-block-wrap'),payload=wraps.map(serializeSceneWrap),chapterIds=[...new Set(payload.map(b=>entityId(b.chapter_id)))],expected_revisions=Object.fromEntries(chapterIds.map(id=>[String(id),state.chapterRevisions.get(id)||'']));
  for(const chapterId of chapterIds){const ch=storageChapterById(chapterId),title=visibleChapterTitleText(chapterId);if(ch&&title!==ch.title){const titled=await api(`/api/chapters/${chapterId}/title`,{method:'PATCH',body:JSON.stringify({title,expected_revision:expected_revisions[String(chapterId)]||''})});const revision=titled.revision||expected_revisions[String(chapterId)]||'';expected_revisions[String(chapterId)]=revision;state.chapterRevisions.set(entityId(chapterId),revision);updateChapterTitleState(chapterId,title,revision)}}
  const result=await api(`/api/scenes/${state.scene.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:payload,expected_revisions})});
  for(const [cid,rev] of Object.entries(result.chapter_revisions||{})){state.chapterRevisions.set(entityId(cid),rev);const ch=storageChapterById(entityId(cid));if(ch)ch.revision=rev;await clearCrashDraft(entityId(cid))}wraps.forEach((wrap,index)=>{if(wrap.isConnected&&result.ids?.[index]&&wrap.dataset.blockId.startsWith('tmp_'))wrap.dataset.blockId=result.ids[index]});
}
async function flushSave(){
  if(state.saveInFlight){await state.saveInFlight;if(state.dirty||state.dirtyChapters.size)return flushSave();return}
  if(!state.dirty&&!state.dirtyChapters.size)return;
  const ids=[...state.dirtyChapters];if(!ids.length&&state.viewMode==='chapter'&&state.chapter)ids.push(state.chapter.id);
  if(!ids.length&&state.viewMode!=='scene')throw new Error('MANUSCRIPT_DIRTY_CHAPTER_MISSING');
  const pending=ids.map(id=>[entityId(id),state.chapterDirtyGeneration.get(entityId(id))||state.editGeneration]);
  const sceneGeneration=state.editGeneration;
  state.saveInFlight=(async()=>{try{
    if(state.viewMode==='scene'&&state.scene)await saveSceneView();else for(const [id] of pending)await saveOneChapter(id);
    for(const [id,generation] of pending){state.chapterSavedGeneration.set(id,Math.max(state.chapterSavedGeneration.get(id)||0,generation));if((state.chapterDirtyGeneration.get(id)||0)<=generation){state.dirtyChapters.delete(id);state.chapterDirtyGeneration.delete(id)}}
    // A focused Scene save serializes every visible Scene block, potentially across
    // several Chapters. Only generations that existed when this request began may
    // be cleared; newer input remains dirty.
    if(state.viewMode==='scene'){for(const id of [...state.dirtyChapters]){if((state.chapterDirtyGeneration.get(id)||0)<=sceneGeneration){state.dirtyChapters.delete(id);state.chapterDirtyGeneration.delete(id);state.chapterSavedGeneration.set(id,sceneGeneration)}}}
    state.dirty=state.dirtyChapters.size>0;if(!state.dirty)setSaveState('Saved');else setSaveState('Saving…');renderChapterTree();
  }catch(error){for(const [id,generation] of pending){state.dirtyChapters.add(id);state.chapterDirtyGeneration.set(id,Math.max(state.chapterDirtyGeneration.get(id)||0,generation))}state.dirty=true;setSaveState('Save failed');toast('Save failed. Your text remains in this window.',5000);throw error}finally{state.saveInFlight=null}})();
  try{await state.saveInFlight}catch(error){clearTimeout(state.saveTimer);if(error.code==='MANUSCRIPT_CONFLICT_001')showManuscriptConflict(error);else if(error.status===409)showLeaseLost(false);else state.saveTimer=setTimeout(()=>flushSave().catch(()=>{}),1500);throw error}
  if(state.dirty||state.dirtyChapters.size)return flushSave();
  kickHostedReadCache();
  if(state.split?.type==='checkpoint')scheduleCheckpointSplitRender();if(state.split?.type==='history')scheduleHistorySplitRender();
}


function showManuscriptConflict(error){clearTimeout(state.saveTimer);showModal(i18nText('recovery.conflict_title'),`<p>${escapeHtml(i18nText('recovery.conflict_body'))}</p><p class="small-muted">${escapeHtml(i18nText('recovery.conflict_preserved'))}</p>`,`<button id="conflictKeep">${escapeHtml(i18nText('recovery.keep_draft'))}</button><button id="conflictReload">${escapeHtml(i18nText('recovery.load_newer'))}</button>`);$('#conflictKeep').onclick=()=>closeModal();$('#conflictReload').onclick=()=>location.reload()}


$('#manuscriptPaper').addEventListener('input',event=>{const title=$('#chapterTitle'),editor=$('#editor'),sel=selectionElement();if(!title||!editor){console.error('BREADMAKER_MANUSCRIPT_SHELL_001: native editing removed the manuscript shell');toast('The manuscript view recovered from an unsafe browser edit.');reloadManuscriptView().catch(error=>console.error('BREADMAKER_MANUSCRIPT_SHELL_002',error));return}if(state.viewMode==='chapter'&&(event.target?.closest?.('#chapterTitle')||sel?.closest?.('#chapterTitle'))){if(title.innerText.includes('\n'))title.textContent=title.innerText.replace(/\n+/g,' ');markDirty(state.chapter?.id);return}const headingTitle=event.target?.closest?.('.book-chapter-title')||sel?.closest?.('.book-chapter-title');if(headingTitle){normalizedChapterTitleElementText(headingTitle);const heading=headingTitle.closest('.book-chapter-heading');if(heading?.dataset.chapterId)markDirty(entityId(heading.dataset.chapterId));return}const wrap=event.target?.closest?.('.manuscript-block-wrap')||activeManuscriptWrap();if(wrap){markDirty(entityId(wrap.dataset.chapterId));scheduleManuscriptReviewAnnotations(wrap.dataset.blockId)}else if(state.viewMode==='chapter')markDirty(state.chapter?.id)});
$('#manuscriptPaper').addEventListener('keydown',event=>{const title=event.target.closest?.('#chapterTitle');if(!title)return;if(event.key==='Enter'){event.preventDefault();const first=$('.manuscript-block');if(first)placeCaret(first,0);return}if(event.key==='Backspace'||event.key==='Delete'){const sel=getSelection();if(!sel?.rangeCount||!sel.isCollapsed)return;const range=sel.getRangeAt(0),offset=offsetIn(title,range.startContainer,range.startOffset),len=title.innerText.length;if((event.key==='Backspace'&&offset===0)||(event.key==='Delete'&&offset>=len))event.preventDefault()}});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState!=='hidden')return;if(state.dirty||state.dirtyChapters.size)flushSave().catch(()=>{});if(state.currentCanvas?.id)persistCurrentCanvasViewport().catch(()=>{});if(state.currentMap?.id)persistMapViewportNow().catch(()=>{})});
window.addEventListener('beforeunload',e=>{if(state.dirty||state.dirtyChapters.size){e.preventDefault();e.returnValue=''}});
let wordCountTimer=null;function scheduleWordCount(){clearTimeout(wordCountTimer);wordCountTimer=setTimeout(countWords,140)}
function countWords(){const visibleWraps=$$('.manuscript-block-wrap').filter(w=>!w.classList.contains('scene-gap-hidden')),text=visibleWraps.map(w=>w.querySelector('.manuscript-block')?.textContent||'').join(' ').trim();const count=text?text.split(/\s+/).length:0;$('#wordCount').textContent=i18nCount(count,'status.words.one','status.words.many');const scenes=new Set(visibleWraps.map(w=>w.dataset.sceneId).filter(Boolean));$('#sceneStatus').textContent=scenes.size?i18nCount(scenes.size,'status.scenes.one','status.scenes.many'):'';}

