// Playback/highlights only. Project cache and browser inference are separate adapters.
const narrationPreview={passage:true,word:true};
const narration={records:[],selected:null,selection:[],busy:false,stopRequested:false,queueLoading:false,audio:new window.BreadmakerNarrationPlayback(),url:null,engine:null,project:null,token:0,gutters:[],queue:[],early:null};
let narrationStore={
  async list(pid){return (await api('/api/narrations',{headers:{'X-Breadmaker-Project':pid}})).records},
  async save(pid,record,blob){const body=new FormData();body.append('metadata',JSON.stringify(record));body.append('file',blob,'narration.wav');return api('/api/narrations',{method:'POST',headers:{'X-Breadmaker-Project':pid},body})},
  async audio(pid,id){return (await api(`/api/narrations/${id}/audio`,{headers:{'X-Breadmaker-Project':pid}})).blob()},
  async remove(pid,id){return api(`/api/narrations/${id}`,{method:'DELETE',headers:{'X-Breadmaker-Project':pid}})},
  async restore(pid,id,token){return api(`/api/narrations/${id}/restore`,{method:'POST',headers:{'X-Breadmaker-Project':pid},body:JSON.stringify({undo_token:token})})}
};
let narrationContextEpoch=0,narrationSessionEpoch=0,narrationSessionChannel=null;
function narrationHosted(){return state.runtime?.mode!=='local'&&runtimeSupports('hosted_persistence')}
function narrationSnapshot(){
  const pid=currentProjectIdentity(),transport=narrationHosted()?narrationStore.capture(pid):undefined;
  return {pid,account:currentUserIdentity(),epoch:narrationContextEpoch,transport};
}
function narrationCurrent(snapshot){return !!snapshot&&snapshot.pid===currentProjectIdentity()&&snapshot.account===currentUserIdentity()&&snapshot.epoch===narrationContextEpoch}
function requireNarrationContext(snapshot){if(!narrationCurrent(snapshot)){if(snapshot?.epoch===narrationContextEpoch&&narrationHosted())clearHostedNarrationContext();throw Error('NARRATION_ACCOUNT_CONTEXT_CHANGED')}return snapshot}
function resetNarrationPrivateUI(){
  narrationContextEpoch++;narration.token++;narration.stopRequested=true;
  narration.audio.pause();narration.audio.clearStream();narration.audio.src='';
  narration.engine?.dispose();narration.engine=null;narration.device=null;if(narration.url)URL.revokeObjectURL(narration.url);narration.url=null;
  narration.records=[];narration.selected=null;narration.selection=[];narration.queue=[];narration.early=null;narration.project=null;narration.busy=false;narration.queueLoading=false;narration.loading=false;
  clearNarrationPreviewHighlights();narration.gutters.forEach(node=>node.remove());narration.gutters=[];
  $('#narrationRecordings')?.replaceChildren();if($('#narrationPreviewExcerpt'))$('#narrationPreviewExcerpt').textContent='';
  $('#narrationPanel .narration-player')?.classList.add('hidden');$('#narrationPanel')?.classList.add('hidden');$('#narrationBtn')?.classList.remove('active');$('#narrationBtn')?.setAttribute('aria-pressed','false');
  narrationPreferences.project=null;narrationPreferences.loading=null;narrationPreferences.value=normalizeNarrationPreferences(null);
  narrationVoiceCatalog.value=null;narrationVoiceCatalog.pending=null;
  window.dispatchEvent(new Event('breadmaker-narration-context-reset'));
}
function clearHostedNarrationContext(broadcast=false){
  if(!narrationHosted())return;
  narrationSessionEpoch++;if(typeof narrationStore.clear==='function')narrationStore.clear();else resetNarrationPrivateUI();
  if(broadcast)narrationSessionChannel?.postMessage('session-changed');
}
async function prepareNarrationSources(sources,snapshot,{remember=false}={}){
  requireNarrationContext(snapshot);
  const result=narrationHosted()?await narrationStore.sources(snapshot.pid,sources,{remember,snapshot:snapshot.transport}):sources;
  requireNarrationContext(snapshot);return result;
}
async function refreshNarrationSourceHashes(snapshot){
  if(!narrationHosted())return;
  const targets=narrationVisibleRecords().map(record=>{const prose=narrationProse(record);return prose?{source_text:narrationSourceText(record,prose)}:null}).filter(Boolean);
  await prepareNarrationSources(targets,snapshot,{remember:true});
}
function narrationOpen(){return !$('#narrationPanel').classList.contains('hidden')}
function narrationParagraphs(){return $$('#editor .manuscript-block').filter(prose=>prose.textContent.trim()&&prose.getClientRects().length)}
function narrationSourceProse(record){return record.target_type==='chapter_title'?chapterTitleTextElement(record.chapter_id):$(`#editor .manuscript-block-wrap[data-block-id="${CSS.escape(String(record.block_id))}"] .manuscript-block`)}
function narrationSourceText(record,prose){if(record.target_type!=='chapter_title')return prose.textContent;const copy=prose.cloneNode(true);copy.querySelectorAll('button,.note-gutter-marker,.markup-gutter').forEach(node=>node.remove());return copy.textContent}
function narrationGutterWrap(record,prose){return record.target_type==='chapter_title'?(prose.closest('.book-chapter-heading')||prose):prose.closest('.manuscript-block-wrap')}
function narrationProse(record){const prose=narrationSourceProse(record);return prose?.getClientRects().length?prose:null}
function narrationFresh(record){const prose=narrationProse(record);if(!prose)return false;const text=narrationSourceText(record,prose);return record.thin?narrationStore.fresh(record,text):text===record.source_text}
function narrationGenerationFresh(source){const prose=narrationSourceProse(source);return prose?narrationSourceText(source,prose)===source.source_text:source.background===true}
function narrationListContext(){return `${state.viewMode}:${state.activeDocumentId}:${state.viewMode==='book'?'*':state.chapter?.id||narrationChapterSources()[0]?.chapter_id||''}`}
function narrationVisibleRecords(){const chapterId=String(state.chapter?.id||narrationChapterSources()[0]?.chapter_id||''),targets=$$('#manuscriptPaper #chapterTitle,#editor .book-chapter-title,#editor .manuscript-block').filter(prose=>prose.getClientRects().length&&(state.viewMode==='book'||String(prose.closest('.manuscript-block-wrap,.book-chapter-heading')?.dataset.chapterId||state.chapter?.id||'')===chapterId)),order=new Map(targets.map((prose,index)=>[prose.matches('#chapterTitle,.book-chapter-title')?'chapter-title:'+(prose.closest('.book-chapter-heading')?.dataset.chapterId||state.chapter?.id):prose.closest('.manuscript-block-wrap').dataset.blockId,index]));return narration.records.filter(record=>order.has(record.block_id)).sort((a,b)=>order.get(a.block_id)-order.get(b.block_id)||a.start-b.start||a.end-b.end)}
function narrationTitleSource(chapterId,text){return {target_type:'chapter_title',chapter_id:String(chapterId),block_id:'chapter-title:'+chapterId,source_text:text,text,start:0,end:text.length,background:true}}
function narrationRecreationSource(record){const prose=narrationProse(record);if(!prose)return null;if(record.target_type==='chapter_title')return narrationTitleSource(record.chapter_id,narrationSourceText(record,prose));if(record.start===0&&record.end===(record.thin?record.source_length:record.source_text.length))return narrationSource(prose);return narrationFresh(record)?{...narrationSource(prose,record.start,record.end)}:null}
function clearNarrationPreviewHighlights(){if(window.CSS?.highlights){CSS.highlights.delete('narration-preview-passage');CSS.highlights.delete('narration-preview-word')}}
function narrationRange(prose,start,end){const range=document.createRange(),walker=document.createTreeWalker(prose,NodeFilter.SHOW_TEXT);let node,offset=0,found=false;while((node=walker.nextNode())){const next=offset+node.length;if(!found&&start<next){range.setStart(node,start-offset);found=true}if(found&&end<=next){range.setEnd(node,end-offset);return range}offset=next}return null}
function paintNarrationPreview(){
  clearNarrationPreviewHighlights();const record=narration.selected,prose=record&&narrationProse(record);if(!narrationOpen()||!prose||!narrationFresh(record)||!window.CSS?.highlights||!window.Highlight)return;
  const passage=narrationRange(prose,record.start,record.end);if(narrationPreview.passage&&passage)CSS.highlights.set('narration-preview-passage',new Highlight(passage));
  const time=narration.audio.currentTime,word=(record.timings||[]).find(item=>time>=item.time&&time<item.time+item.duration);
  if(narrationPreview.word&&word){const range=narrationRange(prose,record.start+word.start,record.start+word.end);if(range)CSS.highlights.set('narration-preview-word',new Highlight(range))}
}
function narrationLabel(node,key){if(node.__breadmakerI18nAttrs)delete node.__breadmakerI18nAttrs['aria-label'];node.setAttribute('aria-label',`{{i18n:${key}}}`);applyI18n(node)}
function narrationText(node,key){if(node.dataset.narrationText===key)return;node.dataset.narrationText=key;node.textContent=`{{i18n:${key}}}`;applyI18n(node)}
function narrationVoiceLabel(voice){const item=narrationVoiceCatalog.value?.voices.find(item=>item.id===voice);return item?narrationVoiceOptionLabel(item):[...$('#narrationVoice').options].find(option=>option.value===voice)?.textContent||(voice==='af_bella'?i18nText('narration.voice_bella'):voice)}
function syncNarrationPlayControl(){const playing=!narration.audio.paused||narration.early?.waitingNext;$('#narrationPlay').innerHTML=playing?'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>':'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg>';narrationLabel($('#narrationPlay'),playing?'narration.pause':'narration.play')}
function narrationTime(){const f=value=>`${Math.floor(value/60)}:${String(Math.floor(value%60)).padStart(2,'0')}`,duration=narration.selected?.duration||0;$('#narrationTime').textContent=`${f(narration.audio.currentTime)} / ${f(duration)}`+((narration.audio.waiting||narration.early?.waitingNext)?' · '+i18nText('narration.buffering'):'');$('#narrationSeek').value=String(duration?narration.audio.currentTime/duration*100:0);paintNarrationPreview()}
function cancelNarrationEarly(){narration.early=null;if(narration.selected?.id==='pending-narration'){narration.audio.pause();narration.audio.clearStream();narration.selected=null;$('#narrationPanel .narration-player').classList.add('hidden')}}
function closeNarrationPanel(){cancelNarrationEarly();narration.audio.pause();narration.token++;clearNarrationPreviewHighlights();narration.gutters.forEach(node=>node.remove());narration.gutters=[];$('#narrationPanel').classList.add('hidden');$('#narrationBtn').classList.remove('active');$('#narrationBtn').setAttribute('aria-pressed','false')}
function syncNarrationBackend(){const engine=narration.engine;if(!engine)return;$('#narrationProgressBar').dataset.cpuThreads=String(engine.timing.threads);const status=$('#narrationSettingsBackend');if(status)status.textContent=!engine.ready?i18nText('narration.backend_preparing'):engine.backend==='webgpu'?i18nText('narration.backend_gpu'):i18nText(engine.fallbackReason?'narration.backend_fallback':'narration.backend_cpu',{threads:engine.timing.threads})}
function applyNarrationPreferences(value){
  narrationPreferences.value=normalizeNarrationPreferences(value);
  if(narration.engine&&narration.engine.allowGPU!==value.gpu){cancelNarrationEarly();narration.audio.pause();if(narration.audio.stream){narration.audio.clearStream();narration.selected=null;$('#narrationPanel .narration-player').classList.add('hidden');clearNarrationPreviewHighlights()}narration.engine.dispose();narration.engine=null}
  narrationPreview.passage=value.passage;narrationPreview.word=value.word;
  for(const [id,key] of [['viewNarrationPassage','passage'],['viewNarrationWord','word']])$('#'+id)?.setAttribute('aria-checked',String(value[key]));
  syncNarrationVoiceChoices();$('#narrationSpeed').value=String(value.speed);narration.audio.playbackRate=value.speed;narration.audio.setPitch(value.pitch).catch(narrationFail);paintNarrationPreview();
}
async function narrationDevice(snapshot){
  requireNarrationContext(snapshot);if(narration.device)return narration.device;
  const {HostedNarrationDevice}=await import('/static/vendor/narration/hosted-device.mjs');requireNarrationContext(snapshot);
  const response=await fetch('/static/vendor/narration/installation.json',{cache:'no-store',credentials:'same-origin',signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw Error('NARRATION_RUNTIME_UNAVAILABLE');
  const bytes=await response.arrayBuffer();if(bytes.byteLength>4096)throw Error('NARRATION_RUNTIME_INVALID');
  const installed=JSON.parse(new TextDecoder().decode(bytes));requireNarrationContext(snapshot);
  if(installed.hosted!==true)throw Error('NARRATION_RUNTIME_INVALID');
  const device=new HostedNarrationDevice({project:snapshot.pid,account:snapshot.account,client:CLIENT_ID,manifest:installed.manifest,
    check:()=>requireNarrationContext(snapshot),onContextLost:()=>{if(narrationCurrent(snapshot))clearHostedNarrationContext()},
    consentGPU:size=>window.confirm(i18nText('narration.gpu_download_confirm',{size:Math.ceil(size/1024**2)}))});
  if(!narration.device)narration.device=device;return narration.device;
}
function narrationEngine(){
  if(!narration.engine){
    const snapshot=narrationSnapshot(),hosted=narrationHosted()?{project:snapshot.pid,account:snapshot.account,client:CLIENT_ID,
      check:()=>requireNarrationContext(snapshot),device:()=>narrationDevice(snapshot),
      invalidate:()=>{if(narrationCurrent(snapshot))clearHostedNarrationContext()},
      pronounce:data=>narrationStore.call(snapshot.transport,'/api/narration-pronunciation',{method:'POST',body:JSON.stringify(data)})}:null;
    narration.engine=new window.BreadmakerKokoroEngine({backend:narrationPreferences.value.gpu?'auto':'wasm',hosted});narration.engine.allowGPU=narrationPreferences.value.gpu;
  }return narration.engine;
}
function receiveNarrationChunk(early,source,part){
  if(narration.early!==early||early.context&&!narrationCurrent(early.context)||early.pid!==currentProjectIdentity()||!narrationOpen()||!narrationFresh(source))return;
  if(part.reset){narration.audio.pause();narration.audio.clearStream();early.started=false;early.record=null;narration.selected=null;$('#narrationPanel .narration-player').classList.add('hidden');return}
  const first=!early.started;
  if(first){early.started=true;early.record={...source,id:'pending-narration',voice:early.voice,timings:[],duration:0};narration.selected=early.record;narration.queue=early.queue;narration.audio.startStream(part.context);narration.audio.playbackRate=Number($('#narrationSpeed').value);$('#narrationPreviewExcerpt').textContent=source.text;$('#narrationPanel .narration-player').classList.remove('hidden')}
  early.record.timings=part.timings;early.record.duration=part.duration;narration.audio.appendStream(part.buffer);
  if(first&&early.autoplay)narration.audio.play().catch(error=>{if(error.name!=='NotAllowedError')narrationFail(error);syncNarrationPlayControl()});narrationTime();
}
function narrationSource(prose,start=0,end=prose.textContent.length){return {block_id:prose.closest('.manuscript-block-wrap').dataset.blockId,chapter_id:prose.closest('.manuscript-block-wrap').dataset.chapterId,source_text:prose.textContent,text:prose.textContent.slice(start,end),start,end}}
function narrationChapterSources(){const paragraphs=narrationParagraphs(),current=activeManuscriptProse()||paragraphs.find(prose=>prose.getBoundingClientRect().bottom>$('#documentScroller').getBoundingClientRect().top)||paragraphs[0],chapter=current?.closest('.manuscript-block-wrap').dataset.chapterId;return paragraphs.filter(prose=>prose.closest('.manuscript-block-wrap').dataset.chapterId===chapter).map(prose=>narrationSource(prose))}
function captureNarrationSelection(){
  const selection=getSelection();if(!selection?.rangeCount)return;const range=selection.getRangeAt(0);
  if(!range.collapsed){const sources=[];for(const prose of narrationParagraphs()){if(!range.intersectsNode(prose))continue;const part=range.cloneRange();if(!prose.contains(part.startContainer))part.setStart(prose,0);if(!prose.contains(part.endContainer))part.setEnd(prose,prose.childNodes.length);const start=offsetIn(prose,part.startContainer,part.startOffset),end=offsetIn(prose,part.endContainer,part.endOffset);if(end>start)sources.push(narrationSource(prose,start,end))}if(sources.length)narration.selection=sources}
  else if($('#editor').contains(selection.anchorNode))narration.selection=[];syncNarrationActions();
}
function syncNarrationActions(){const live=narration.project===currentProjectIdentity()&&!narration.loading,book=state.viewMode==='book';const save=$('#narrationSettingsSave');if(save&&!save.dataset.saving)save.disabled=narration.busy;$('#narrationSettingsBusy')?.classList.toggle('hidden',!narration.busy);$('#narrationSelection').disabled=narration.busy||narration.queueLoading||!live||!narration.selection.length;$('#narrationChapter').disabled=narration.busy||narration.queueLoading||!live||!(book?state.bookData?.blocks?.length:narrationChapterSources().length);narrationText($('#narrationChapter'),book?'narration.book':'narration.chapter');$('#narrationSelectionOptions').disabled=$('#narrationSelection').disabled;$('#narrationScopeOptions').disabled=narration.busy||narration.queueLoading||!live;$('#narrationVoice').disabled=narration.busy||narration.queueLoading||!live;$('#narrationProgress').classList.toggle('hidden',!narration.busy);$('#narrationStop').disabled=narration.stopRequested;narrationText($('#narrationStop'),narration.stopRequested?'narration.stopping':'narration.stop_generation')}
function openNarrationActionMenu(kind,event){
  event.preventDefault();event.stopPropagation();const context=narrationSnapshot(),pid=context.pid,button=$(kind==='selection'?'#narrationSelection':'#narrationChapter'),sources=narration.selection.map(source=>({...source})),book=state.viewMode==='book';
  const target={scope:book?'book':'chapter',chapterId:String(state.chapter?.id||narrationChapterSources()[0]?.chapter_id||''),documentId:state.activeDocumentId};
  const items=[{label:i18nText(kind==='selection'?'narration.generate_selection':book?'narration.generate_book':'narration.generate_chapter'),testId:'narration-generate-only',disabled:button.disabled,action:()=>{if(!narrationCurrent(context))return;(kind==='selection'?generateNarrations(sources,false,{generateOnly:true,context}):narrateScope({generateOnly:true,target,context})).catch(narrationFail)}}];
  items.push({label:i18nText(kind==='selection'?'narration.regenerate_section':book?'narration.regenerate_book':'narration.regenerate_chapter'),testId:'narration-regenerate',disabled:button.disabled,action:()=>{if(!narrationCurrent(context))return;(kind==='selection'?generateNarrations(sources,true,{generateOnly:true,context}):narrateScope({generateOnly:true,regenerate:true,target,context})).catch(narrationFail)}});
  if(kind==='scope')items.push({separator:true},{label:`${book||narrationPreferences.value.continueChapters?'✓ ':''}${i18nText('narration.continue_chapters')}`,testId:'narration-continue-chapters',disabled:book||narration.busy||narration.queueLoading,action:()=>{if(narrationCurrent(context))saveNarrationPreferences({continueChapters:!narrationPreferences.value.continueChapters}).catch(narrationFail)}});
  if(kind==='scope')items.push({separator:true},{label:i18nText('narration.delete_all'),testId:'narration-delete-all',danger:true,disabled:narration.busy||narration.queueLoading,action:()=>{if(narrationCurrent(context))deleteNarrationScope(target,context).catch(narrationFail)}});
  const rect=event.currentTarget.getBoundingClientRect(),rightClick=event.type==='contextmenu';openContextMenu(rightClick?event.clientX:rect.left,rightClick?event.clientY:rect.bottom,items,{anchorRect:rect});
  const toggle=$('[data-testid="narration-continue-chapters"]');if(toggle){toggle.setAttribute('role','menuitemcheckbox');toggle.setAttribute('aria-checked',String(book||narrationPreferences.value.continueChapters))}
}
function narrationReplacementSources(sources){const voice=$('#narrationVoice').value;return sources.map(source=>{if(source.replaces)return source;const full=source.start===0&&source.end===source.source_text.length,candidates=narration.records.filter(record=>String(record.block_id)===String(source.block_id)&&(full?record.start===0&&record.end===(record.thin?record.source_length:record.source_text.length):(record.thin?!!source.source_hash&&record.source_hash===source.source_hash:record.source_text===source.source_text)&&record.start===source.start&&record.end===source.end)).sort((a,b)=>Number(b.voice===voice)-Number(a.voice===voice)||String(a.id).localeCompare(String(b.id)));return candidates.length?{...source,replaces:candidates[0].id}:{...source}})}
async function narrateScope({generateOnly=false,regenerate=false,target=null,context=null}={}){
  if(narration.busy||narration.queueLoading)return;const snapshot=requireNarrationContext(context||narrationSnapshot()),pid=snapshot.pid,documentId=state.activeDocumentId,token=++narration.token;
  const scope=target?.scope||(state.viewMode==='book'?'book':'chapter'),chapterId=String(target?.chapterId||state.chapter?.id||narrationChapterSources()[0]?.chapter_id||'');
  if(target&&target.documentId!==documentId)return;
  cancelNarrationEarly();narration.audio.pause();narration.queueLoading=true;syncNarrationActions();
  let sources,records,savedTail=[];
  try{
    await loadNarrationPreferences();requireNarrationContext(snapshot);await flushSave();requireNarrationContext(snapshot);
    const book=await loadBookData(documentId,{force:true});
    requireNarrationContext(snapshot);records=await narrationStore.list(pid,snapshot.transport);requireNarrationContext(snapshot);
    if(pid!==currentProjectIdentity()||documentId!==state.activeDocumentId||token!==narration.token||!narrationOpen())return;
    const chapters=(book.chapters||[]).map(chapter=>String(chapter.id)),start=chapters.indexOf(chapterId),continueChapters=!generateOnly&&!regenerate&&scope==='chapter'&&narrationPreferences.value.continueChapters;
    if(scope==='chapter'&&start<0)return;
    // Continuation is playback-only. A chapter action must never synthesize
    // another chapter, even when a previously saved continuation setting is on.
    const allowed=new Set(scope==='book'?chapters:[chapterId]);
    const live=new Map($$('#editor .manuscript-block').map(prose=>{const source=narrationSource(prose);return [String(source.block_id),source]}));
    const chapterSources=chapter=>[...(!chapter.is_implicit&&String(chapter.title||'').trim()?[narrationTitleSource(chapter.id,chapter.title)]:[]),...(book.blocks||[]).filter(block=>String(block.chapter_id)===String(chapter.id)&&block.block_type!=='image'&&String(block.text_content||'').trim()).map(block=>({...((live.get(String(block.id)))||{block_id:block.id,chapter_id:block.chapter_id,source_text:block.text_content,text:block.text_content,start:0,end:block.text_content.length}),background:true}))];
    sources=(book.chapters||[]).filter(chapter=>allowed.has(String(chapter.id))).flatMap(chapterSources);
    sources=await prepareNarrationSources(sources,snapshot,{remember:true});
    if(continueChapters){for(const source of await prepareNarrationSources((book.chapters||[]).slice(start+1).flatMap(chapterSources),snapshot)){const record=window.findNarrationRecording(source,records,$('#narrationVoice').value,true);if(!record)break;savedTail.push(record)}}
    narration.records=records;
  }finally{if(narrationCurrent(snapshot)){narration.queueLoading=false;syncNarrationActions()}}
  if(!sources?.length||!narrationCurrent(snapshot)||token!==narration.token)return;
  const queue=sources.map(source=>window.findNarrationRecording(source,records,$('#narrationVoice').value,true));
  if(!regenerate&&queue.every(Boolean)){if(!generateOnly)await playNarration(queue[0],[...queue,...savedTail]);return}
  if(scope==='book')toast(i18nText('narration.overnight'),8000);
  await generateNarrations(regenerate?narrationReplacementSources(sources):sources,regenerate,{reuseAnyVoice:true,generateOnly,savedTail,context:snapshot});
}
async function deleteNarrationScope(target,context=null){
  if(narration.busy||narration.queueLoading||target.documentId!==state.activeDocumentId)return;const snapshot=requireNarrationContext(context||narrationSnapshot()),pid=snapshot.pid;narration.queueLoading=true;syncNarrationActions();const deleted=[];let failed=false;
  try{
    const book=await loadBookData(target.documentId,{force:true}),chapters=(book.chapters||[]).filter(chapter=>target.scope==='book'||String(chapter.id)===target.chapterId),ids=new Set(chapters.map(chapter=>String(chapter.id))),blocks=new Set((book.blocks||[]).filter(block=>ids.has(String(block.chapter_id))).map(block=>String(block.id)));
    for(const id of ids)blocks.add('chapter-title:'+id);
    requireNarrationContext(snapshot);const records=await narrationStore.list(pid,snapshot.transport);requireNarrationContext(snapshot);
    for(const record of records.filter(record=>blocks.has(String(record.block_id)))){
      requireNarrationContext(snapshot);
      const {undo_token}=await narrationStore.remove(pid,record.id,snapshot.transport);requireNarrationContext(snapshot);deleted.push({record,undo_token});narration.records=narration.records.filter(item=>item.id!==record.id);narration.queue=narration.queue.filter(item=>item.id!==record.id);
      if(narration.selected?.id===record.id){cancelNarrationEarly();narration.token++;narration.audio.pause();narration.audio.clearStream();narration.selected=null;$('#narrationPanel .narration-player').classList.add('hidden');clearNarrationPreviewHighlights()}
    }
  }catch(error){failed=true;console.error('NARRATION_DELETE_ALL',error);if(!deleted.length)throw error}
  finally{if(narrationCurrent(snapshot)){narration.queueLoading=false;renderNarrations()}}
  if(deleted.length&&narrationCurrent(snapshot))toast(i18nText('narration.deleted_all',{count:deleted.length})+(failed?' '+i18nText('narration.failed'):''),10000,{label:i18nText('action.undo'),onClick:async()=>{const restored=[];try{requireNarrationContext(snapshot);for(const item of deleted)restored.push(await narrationStore.restore(pid,item.record.id,item.undo_token,snapshot.transport))}catch(error){narrationFail(error)}finally{if(narrationCurrent(snapshot)){for(const record of restored){narration.records=narration.records.filter(item=>item.id!==record.id);narration.records.push(record)}renderNarrations()}}}});
}
function narrationFail(error){console.error('NARRATION_001',error);toast(i18nText('narration.failed'),5000)}
async function deleteNarration(record,context=null){
  if(narration.busy||narration.queueLoading)return;const snapshot=requireNarrationContext(context||narrationSnapshot()),pid=snapshot.pid,{undo_token}=await narrationStore.remove(pid,record.id,snapshot.transport);
  if(narrationCurrent(snapshot)){
    narration.records=narration.records.filter(item=>item.id!==record.id);narration.queue=narration.queue.filter(item=>item.id!==record.id);
    if(narration.selected?.id===record.id){narration.token++;narration.audio.pause();narration.selected=null;$('#narrationPanel .narration-player').classList.add('hidden');clearNarrationPreviewHighlights()}
    renderNarrations();
    toast(i18nText('narration.deleted'),10000,{label:i18nText('action.undo'),onClick:async()=>{try{requireNarrationContext(snapshot);const restored=await narrationStore.restore(pid,record.id,undo_token,snapshot.transport);requireNarrationContext(snapshot);narration.records=narration.records.filter(item=>item.id!==restored.id);narration.records.push(restored);renderNarrations()}catch(error){narrationFail(error)}}});
  }
}
async function playNarration(record,queue=[record],continuation=false){
  if(!continuation)cancelNarrationEarly();
  const snapshot=narrationSnapshot(),pid=snapshot.pid,token=++narration.token;narration.audio.pause();
  if(record.thin){record=await narrationStore.metadata(pid,record,snapshot.transport);requireNarrationContext(snapshot)}
  const blob=await narrationStore.audio(pid,record.id,snapshot.transport);if(token!==narration.token||!narrationCurrent(snapshot)||!narrationOpen())return;
  const autoplay=!continuation||!narration.early||narration.early.autoplay;
  if(narration.url)URL.revokeObjectURL(narration.url);narration.url=URL.createObjectURL(blob);narration.selected=record;narration.queue=queue;narration.audio.src=narration.url;narration.audio.playbackRate=Number($('#narrationSpeed').value);$('#narrationPanel .narration-player').classList.remove('hidden');$('#narrationPreviewExcerpt').textContent=record.text;if(autoplay)try{await narration.audio.play()}catch(error){if(error.name!=='NotAllowedError')throw error}if(narrationCurrent(snapshot))renderNarrations();
}
async function resumeNarrationPlayback(){
  if(!narration.selected)return;
  const snapshot=narrationSnapshot(),selected=narration.selected,token=narration.token;
  if(narrationHosted()){
    if(selected.id==='pending-narration')await (await narrationDevice(snapshot)).authorize();
    else{const manifest=await narrationStore.manifest(snapshot.transport,selected.id);if(selected.generation&&selected.generation!==manifest.generation)throw Error('NARRATION_RECORD_CHANGED')}
  }
  requireNarrationContext(snapshot);if(token!==narration.token||selected!==narration.selected||!narrationOpen())return;
  await narration.audio.play();
}
function renderNarrations(){
  const context=narrationSnapshot();narration.listContext=narrationListContext();
  const list=$('#narrationRecordings');list.replaceChildren();narration.gutters.forEach(node=>node.remove());narration.gutters=[];const visible=narrationVisibleRecords();
  if(!visible.length){const empty=document.createElement('div');empty.className='narration-empty';empty.innerHTML='<b>{{i18n:narration.empty_title}}</b><p>{{i18n:narration.empty_hint}}</p>';list.append(empty);applyI18n(empty)}
  for(const record of visible){
    const row=document.createElement('div');row.className='narration-recording';row.classList.toggle('active',narration.selected?.id===record.id);const main=document.createElement('button');main.type='button';main.className='narration-recording-main';main.onclick=()=>{if(narrationCurrent(context))playNarration(record).catch(narrationFail)};
    const text=document.createElement('span');text.className='narration-recording-excerpt';text.textContent=record.text;const meta=document.createElement('small');meta.textContent=`${Math.floor(record.duration/60)}:${String(Math.floor(record.duration%60)).padStart(2,'0')} · ${narrationVoiceLabel(record.voice)} ${narrationFresh(record)?'':'· '+i18nText('narration.changed')}`;main.append(text,meta);
    const more=document.createElement('button');more.className='bare-button';more.textContent='⋮';narrationLabel(more,'narration.options');more.onclick=event=>{event.stopPropagation();if(!narrationCurrent(context))return;const rect=more.getBoundingClientRect(),source=narrationRecreationSource(record);openContextMenu(rect.left,rect.bottom,[{label:i18nText('narration.recreate'),disabled:narration.busy||narration.queueLoading||!source,action:()=>generateNarrations([{...source,replaces:record.id}],true,{context}).catch(narrationFail)},{label:i18nText('narration.delete'),danger:true,disabled:narration.busy||narration.queueLoading,action:()=>deleteNarration(record,context).catch(narrationFail)}])};row.append(main,more);list.append(row);
    if(narrationOpen()&&narrationFresh(record)){const prose=narrationProse(record),wrap=narrationGutterWrap(record,prose);if(wrap.querySelector('.narration-preview-gutter'))continue;const gutter=document.createElement('button');gutter.type='button';gutter.className='bare-button narration-preview-gutter';gutter.contentEditable='false';gutter.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 4 12 8-12 8Z"/></svg>';narrationLabel(gutter,'narration.play');gutter.onclick=event=>{event.preventDefault();event.stopPropagation();if(narration.selected?.id===record.id&&!narration.audio.paused)narration.audio.pause();else playNarration(record).catch(narrationFail)};wrap.append(gutter);narration.gutters.push(gutter)}
  }
  syncNarrationActions();paintNarrationPreview();
}
async function generateNarrations(sources,recreate=false,{reuseAnyVoice=false,generateOnly=false,savedTail=[],context=null}={}){
  if(narration.busy||narration.queueLoading||!sources.length)return;const snapshot=requireNarrationContext(context||narrationSnapshot()),pid=snapshot.pid,voice=$('#narrationVoice').value,pace=narrationPreferences.value.generationPace,punctuationPauses=narrationPreferences.value.punctuationPauses;narration.busy=true;narration.stopRequested=false;syncNarrationActions();const queue=[];let success=false,progress;
  cancelNarrationEarly();narration.audio.pause();const early={pid,context:snapshot,voice,queue,started:false,autoplay:!generateOnly&&narrationPreferences.value.autoplay,waitingNext:false,record:null};narration.early=generateOnly?null:early;syncNarrationPlayControl();
  try{
  sources=await prepareNarrationSources(sources,snapshot,{remember:true});if(recreate)sources=narrationReplacementSources(sources);narrationEngine();
  const progressSources=sources.map(source=>({...source,cached:!recreate&&!!window.findNarrationRecording(source,narration.records,voice,reuseAnyVoice)}));
  progress=new window.BreadmakerNarrationProgress(narration.engine,progressSources,voice,status=>{if(!narrationCurrent(snapshot))return;const label=i18nText('narration.generating',{current:status.current,total:status.total}),hint=status.overrun?i18nText('narration.longer'):status.phase==='saving'?i18nText('narration.saving'):(status.phase==='loading'?i18nText('narration.loading_voice')+' · ':'')+i18nText('narration.remaining',{seconds:status.remainingSeconds});if($('#narrationProgressLabel').textContent!==label)$('#narrationProgressLabel').textContent=label;$('#narrationProgressBar').value=status.fraction;if($('#narrationProgressEstimate').textContent!==hint)$('#narrationProgressEstimate').textContent=hint});
  await flushSave();requireNarrationContext(snapshot);for(let i=0;i<sources.length;i++){
    requireNarrationContext(snapshot);if(narration.stopRequested)break;const source=sources[i];if(!source.text.trim())continue;if(!narrationGenerationFresh(source))throw Error('NARRATION_TEXT_CHANGED');
    let record=!recreate&&window.findNarrationRecording(source,narration.records,voice,reuseAnyVoice);const generated=!record;
    if(!record){const output=await narration.engine.generate(source.text,voice,status=>{if(narrationCurrent(snapshot)){progress.event(i,status);syncNarrationBackend()}},!generateOnly&&i===0&&narrationPreferences.value.earlyPlayback?part=>receiveNarrationChunk(early,source,part):()=>{},pace,punctuationPauses);requireNarrationContext(snapshot);progress.event(i,{phase:'saving'});record=await narrationStore.save(pid,{...source,voice,generation_pace:pace,punctuation_pauses:punctuationPauses,backend:output.metrics.backend,timings:output.timings},output.blob,snapshot.transport);requireNarrationContext(snapshot);narration.records=narration.records.filter(item=>item.id!==record.id&&item.id!==source.replaces);narration.records.push(record)}progress.completeSource(i);queue.push(record);
    if(i===sources.length-1&&!narration.stopRequested)queue.push(...savedTail);
    if(narration.early===early&&narrationOpen()){
      if(i===0&&early.started&&early.record){Object.assign(early.record,record.thin?{id:record.id,duration:record.duration}:record);narration.audio.finishStream()}
      else if(!early.started){early.started=true;early.record=record;await playNarration(record,queue,true)}
      else if(early.waitingNext&&early.autoplay){early.waitingNext=false;await playNarration(record,queue,true)}
    }if(generated||i===0||i===sources.length-1)renderNarrations();
  }success=narrationCurrent(snapshot)&&!narration.stopRequested}finally{progress?.stop(success);if(narrationCurrent(snapshot)){narration.busy=false;if(narration.early===early){early.waitingNext=false;if(!success&&narration.selected?.id==='pending-narration'){narration.audio.pause();narration.audio.clearStream();narration.selected=null;$('#narrationPanel .narration-player').classList.add('hidden');clearNarrationPreviewHighlights()}}syncNarrationActions();syncNarrationPlayControl();narrationTime()}}
}
async function openNarrationPanel(){
  if(narrationOpen()){closeNarrationPanel();return}
  closeCheckpointPanel();closeCutsPanel();closeCommentsPanel();closeMarkupPanel();closeManuscriptImagePanel();closeManuscriptReviewsPanel();
  $('#narrationPanel').classList.remove('hidden');$('#narrationBtn').classList.add('active');$('#narrationBtn').setAttribute('aria-pressed','true');
  const snapshot=narrationSnapshot();narration.project=snapshot.pid;const pid=narration.project,token=narration.token;
  narration.loading=true;syncNarrationActions();
  try{
    await loadNarrationPreferences();requireNarrationContext(snapshot);const records=await narrationStore.list(pid,snapshot.transport);
    if(token!==narration.token||!narrationCurrent(snapshot)||!narrationOpen())return;
    narration.records=records;await refreshNarrationSourceHashes(snapshot);requireNarrationContext(snapshot);renderNarrations();narrationEngine();syncNarrationBackend();
    narration.engine.load().then(syncNarrationBackend).catch(error=>{syncNarrationBackend();console.debug('Narration warmup unavailable',error)});
  }finally{if(narrationCurrent(snapshot)&&token===narration.token){narration.loading=false;syncNarrationActions()}}
}
async function initializeNarrationUI(){
  if(state.runtime?.mode!=='local'){
    if(!narrationHosted()||!runtimeSupports('narration'))return;
    const {HostedNarrationStore}=await import('/static/vendor/narration/hosted-store.mjs');
    narrationStore=new HostedNarrationStore({request:api,context:()=>({account:currentUserIdentity(),project:currentProjectIdentity(),sessionEpoch:narrationSessionEpoch}),onInvalidated:resetNarrationPrivateUI});
    if(typeof BroadcastChannel==='function'){narrationSessionChannel=new BroadcastChannel('breadmaker-auth-session');narrationSessionChannel.onmessage=event=>{if(event.data==='session-changed')clearHostedNarrationContext()}}
    window.addEventListener('pagehide',()=>clearHostedNarrationContext());
  }
  narrationLabel($('#narrationBtn'),'narration.title');$('#narrationBtn').classList.remove('hidden');$('#narrationViewOptions').classList.remove('hidden');$('#narrationBtn').onclick=()=>openNarrationPanel().catch(narrationFail);$('#closeNarrationPanel').onclick=closeNarrationPanel;
  $('#narrationSelection').onclick=()=>generateNarrations(narration.selection.map(item=>({...item}))).catch(narrationFail);$('#narrationChapter').onclick=()=>narrateScope().catch(narrationFail);
  $('#narrationStop').onclick=()=>{narration.stopRequested=true;syncNarrationActions()};
  for(const [kind,id] of [['selection','narrationSelectionOptions'],['scope','narrationScopeOptions']]){$('#'+id).onclick=event=>openNarrationActionMenu(kind,event);$(`[data-narration-action="${kind}"]`).oncontextmenu=event=>openNarrationActionMenu(kind,event)}
  $('#narrationVoice').onchange=()=>saveNarrationPreferences({voice:$('#narrationVoice').value}).catch(narrationFail);
  $('#narrationPlay').onclick=()=>{if(!narration.selected)return;const early=narration.early;if(!narration.audio.paused||early?.waitingNext){narration.token++;if(early){early.autoplay=false;early.waitingNext=false}narration.audio.pause();syncNarrationPlayControl();return}if(early)early.autoplay=true;const index=narration.queue.findIndex(record=>record.id===narration.selected?.id),next=narration.queue[index+1];if(narration.audio.ended&&next)playNarration(next,narration.queue,true).catch(narrationFail);else resumeNarrationPlayback().catch(narrationFail)};$('#narrationPrevious').onclick=()=>{if(narration.early)narration.early.waitingNext=false;narration.audio.currentTime=0};$('#narrationNext').onclick=()=>{const continuous=narration.queue.length>1,records=continuous?narration.queue:narrationVisibleRecords(),index=records.findIndex(record=>record.id===narration.selected?.id);if(records[index+1])playNarration(records[index+1],continuous?records:[records[index+1]]).catch(narrationFail)};
  $('#narrationRewind').onclick=()=>{narration.audio.currentTime=Math.max(0,narration.audio.currentTime-5)};$('#narrationForward').onclick=()=>{narration.audio.currentTime=Math.min(narration.selected?.duration||0,narration.audio.currentTime+5)};$('#narrationSeek').oninput=event=>{narration.audio.currentTime=Number(event.target.value)/100*(narration.selected?.duration||0)};$('#narrationSpeed').onchange=()=>{narration.audio.playbackRate=Number($('#narrationSpeed').value);saveNarrationPreferences({speed:Number($('#narrationSpeed').value)}).catch(narrationFail)};
  let highlightFrame=null;const followWord=()=>{if(narration.audio.paused||!narrationOpen())return;paintNarrationPreview();highlightFrame=requestAnimationFrame(followWord)};
  narration.audio.ontimeupdate=narrationTime;narration.audio.onplay=()=>{syncNarrationPlayControl();cancelAnimationFrame(highlightFrame);followWord()};narration.audio.onpause=()=>{cancelAnimationFrame(highlightFrame);syncNarrationPlayControl()};narration.audio.onended=()=>{const index=narration.queue.findIndex(record=>record.id===narration.selected?.id),next=narration.queue[index+1];if(next)playNarration(next,narration.queue,true).catch(narrationFail);else if(narration.busy&&narration.early?.autoplay)narration.early.waitingNext=true;syncNarrationPlayControl();narrationTime()};document.addEventListener('selectionchange',captureNarrationSelection);
  $('#manuscriptPaper').addEventListener('input',()=>{cancelNarrationEarly();narration.audio.pause();clearNarrationPreviewHighlights();if(narrationOpen()){const snapshot=narrationSnapshot();refreshNarrationSourceHashes(snapshot).then(()=>{if(narrationCurrent(snapshot)&&narrationOpen())renderNarrations()}).catch(narrationFail)}});
  let queued=false;new MutationObserver(changes=>{if(queued||!narrationOpen()||changes.every(change=>$('#narrationPanel').contains(change.target)))return;queued=true;requestAnimationFrame(()=>{queued=false;syncNarrationActions();if(['checkpointPanel','cutsPanel','commentsPanel','markupPanel','manuscriptImagePanel','manuscriptReviewsPanel'].some(id=>!$('#'+id).classList.contains('hidden'))){closeNarrationPanel();return}if(narration.listContext!==narrationListContext()||narration.gutters.some(node=>!node.isConnected)){const snapshot=narrationSnapshot();refreshNarrationSourceHashes(snapshot).then(()=>{if(narrationCurrent(snapshot)&&narrationOpen())renderNarrations()}).catch(narrationFail)}else if(narration.records.some(record=>narrationFresh(record)&&!narrationGutterWrap(record,narrationProse(record)).querySelector('.narration-preview-gutter')))renderNarrations()})}).observe($('#manuscriptView'),{subtree:true,childList:true,attributes:true,attributeFilter:['class']});attachManuscriptToolTooltip('narrationBtn','narrationTooltip');
}
window.addEventListener('load',()=>window.__breadmakerBootPromise?.then(initializeNarrationUI).catch(()=>{}));
