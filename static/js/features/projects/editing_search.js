// One-shot English editing searches. Hosted capability is server-controlled;
// native mode needs no flag, model or background scan.
const EDITING_SEARCHES=['telling','filter','adverbs','qualifiers','wordy','redundancies','emotions'];
function experimentalEditingSearchMarkup(){
  if(state.runtime?.mode!=='local'&&!runtimeSupports('editor_highlights'))return '';
  return `<section class="project-settings-section">${projectSettingsRow('experimentalEditorHighlights',i18nText('editing_search.title'),i18nText('editing_search.entry_help'),{translated:true})}</section>`;
}
function openEditorHighlights(onDismiss=null){
  if(state.runtime?.mode!=='local'&&!runtimeSupports('editor_highlights'))return;
  let cleanup=()=>{};
  showModal(i18nText('editing_search.title'),`<div class="editing-searches"><p class="small-muted">${escapeHtml(i18nText('editing_search.help'))}</p>${EDITING_SEARCHES.map(scan=>`<button class="project-settings-row" type="button" data-editing-search="${scan}"><span><b>${escapeHtml(i18nText(`editing_search.${scan}`))}</b><small>${escapeHtml(i18nText(`editing_search.${scan}_help`))}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>`).join('')}<p id="editingSearchStatus" class="small-muted" role="status" aria-live="polite"></p></div>`,'',()=>{cleanup();onDismiss?.()});
  cleanup=wireExperimentalEditingSearches();
}
function wireExperimentalEditingSearches(){
  const buttons=$$('[data-editing-search]');if(!buttons.length)return ()=>{};
  const pid=currentProjectIdentity(),documentId=state.activeDocumentId,status=$('#editingSearchStatus');
  let active=true,busy=false;
  const current=()=>active&&pid===currentProjectIdentity()&&String(documentId)===String(state.activeDocumentId);
  const available=currentView()==='manuscript'&&!!documentId;
  if(!available)status.textContent=i18nText('editing_search.open_document');
  const setBusy=value=>{busy=value;buttons.forEach(node=>node.disabled=value||!available)};
  const run=async(scan,action)=>{
      if(busy||!current())return;
      setBusy(true);status.textContent=i18nText('editing_search.running');
      try{
        await flushSave();if(!current())return;
        const result=await withStructuralUndo(i18nText('editing_search.undo'),async()=>{
          if(!current())throw Error('EDITING_SEARCH_CONTEXT_CHANGED');
          return api('/api/annotations/editing-search',{method:'POST',headers:{'X-Breadmaker-Project':pid},body:JSON.stringify({scan,action,tab_id:documentId})});
        },{view:'manuscript',documentId,chapterId:state.chapter?.id,manuscriptViewMode:state.viewMode});
        if(!current())return;
        await refresh(false);await reloadManuscriptView();if(!current())return;
        if(state.find.navigatingMarkup)closeFind();
        if(action==='clear'||action==='remove'){
          status.textContent=i18nText(action==='clear'?'editing_search.cleared':'editing_search.removed');
          undoToast(status.textContent);return;
        }
        if(!result.matches){status.textContent=i18nText('editing_search.none');return}
        status.textContent=i18nText(result.matches===1?'editing_search.done_one':'editing_search.done',{count:i18nNumber(result.matches),name:result.marker.name});
        undoToast(status.textContent);
      }catch(error){
        if(current())status.textContent=i18nText(['EDITING_SEARCH_TOO_LARGE','EDITING_SEARCH_TOO_MANY_MATCHES'].includes(error.message)?'editing_search.too_large':'editing_search.failed');
      }finally{if(current())setBusy(false);else busy=false}
  };
  buttons.forEach(button=>{
    button.disabled=!available;
    const choose=async event=>{
      event.preventDefault();if(busy||!current())return;
      const scan=button.dataset.editingSearch,rect=button.getBoundingClientRect();
      setBusy(true);let previous;
      try{previous=(await api(`/api/annotations/editing-search?tab_id=${encodeURIComponent(documentId)}`,{headers:{'X-Breadmaker-Project':pid}})).searches?.[scan]}
      catch{if(current())status.textContent=i18nText('editing_search.failed');return}
      finally{if(current())setBusy(false);else busy=false}
      if(!current())return;
      if(!previous?.marker){await run(scan,'scan');return}
      openContextMenu(rect.left,rect.bottom+4,[
        {label:i18nText('editing_search.clear'),tooltip:i18nText('editing_search.clear_help'),action:()=>run(scan,'clear')},
        {label:i18nText('editing_search.remove'),danger:true,action:async()=>{
          if(!current())return;
          const confirmed=await confirmDialog(i18nText('editing_search.remove'),i18nText('editing_search.remove_confirm',{name:previous.marker.name}),{confirmLabel:i18nText('editing_search.remove'),danger:true});
          if(confirmed&&current())await run(scan,'remove');
        }},
        {label:i18nText('editing_search.recheck'),tooltip:i18nText('editing_search.recheck_help'),action:()=>run(scan,'recheck')},
      ],{anchorRect:rect});
    };
    button.onclick=choose;button.oncontextmenu=choose;
  });
  return ()=>{active=false};
}
