// Application-wide Undo/Redo and structural Undo coordination.

const HISTORY_MEMORY_LIMIT=40*1024*1024;
function historyWeight(snapshot){try{return JSON.stringify(snapshot).length*2}catch{return 0}}
function historyClear(kind){state[`${kind}Stack`]=[];state[`${kind}Bytes`]=0;updateUndoButtons();}
function historyPush(kind,snapshot){const stack=state[`${kind}Stack`],bytesKey=`${kind}Bytes`;snapshot._bytes=historyWeight(snapshot);stack.push(snapshot);state[bytesKey]+=snapshot._bytes;while((stack.length>100||state[bytesKey]>HISTORY_MEMORY_LIMIT)&&stack.length>1){const old=stack.shift();state[bytesKey]-=old._bytes||0}updateUndoButtons();}
function historyPeek(kind){const stack=state[`${kind}Stack`];return stack.length?stack[stack.length-1]:null}
function historyPop(kind){const stack=state[`${kind}Stack`],snap=stack.pop();if(snap)state[`${kind}Bytes`]=Math.max(0,state[`${kind}Bytes`]-(snap._bytes||0));updateUndoButtons();return snap;}
function updateUndoButtons(){const busy=state.historyBusy;const undo=$('#undoBtn'),redo=$('#redoBtn'),undoUnavailable=busy||!state.undoStack.length,redoUnavailable=busy||!state.redoStack.length;if(undo){undo.disabled=false;undo.classList.toggle('is-disabled',undoUnavailable);undo.setAttribute('aria-disabled',String(undoUnavailable))}if(redo){redo.disabled=false;redo.classList.toggle('is-disabled',redoUnavailable);redo.setAttribute('aria-disabled',String(redoUnavailable))}}

async function performUndo(){
  if(!state.undoStack.length){toast('Nothing to undo');return}clearTimeout(state.saveTimer);if(state.saveInFlight)await state.saveInFlight;const target=historyPeek('undo');
  if(!target)return;
  if(target.kind==='manuscript'){
    const redo=await currentSnapshotForIds(target.chapters.map(c=>c.id));await flushPendingCanvasNodeSaves();await flushSave();await restoreEditSnapshot(target);historyPop('undo');historyPush('redo',redo);
  }else if(target.kind==='scene_edit'){
    const redo=await currentSceneEditSnapshot(target.scene.id);await flushPendingCanvasNodeSaves();await flushSave();await restoreSceneEditSnapshot(target);historyPop('undo');historyPush('redo',redo);
  }else if(target.kind==='structural'){
    await flushPendingCanvasNodeSaves();await flushSave();
    await api(`/api/undo/apply/${target.token}?direction=undo`,{method:'POST'});
    historyPop('undo');historyPush('redo',target);await refresh(false);await reopenAfterStructuralRestore(target);await notifyCutsChanged();await notifyCommentsChanged();
  }
  state.undoGroup={key:'',at:0};toast('Undone',4200,{label:i18nText('action.redo'),onClick:()=>appRedo()});
}
async function performRedo(){
  if(!state.redoStack.length){toast('Nothing to redo');return}clearTimeout(state.saveTimer);if(state.saveInFlight)await state.saveInFlight;const target=historyPeek('redo');
  if(!target)return;
  if(target.kind==='manuscript'){
    await flushPendingCanvasNodeSaves();const undo=await currentSnapshotForIds(target.chapters.map(c=>c.id));await restoreEditSnapshot(target);historyPop('redo');historyPush('undo',undo);
  }else if(target.kind==='scene_edit'){
    await flushPendingCanvasNodeSaves();const undo=await currentSceneEditSnapshot(target.scene.id);await restoreSceneEditSnapshot(target);historyPop('redo');historyPush('undo',undo);
  }else if(target.kind==='structural'){
    await flushPendingCanvasNodeSaves();
    await api(`/api/undo/apply/${target.token}?direction=redo`,{method:'POST'});
    historyPop('redo');historyPush('undo',target);await refresh(false);await reopenAfterStructuralRestore(target);await notifyCutsChanged();await notifyCommentsChanged();
  }
  state.undoGroup={key:'',at:0};toast('Redone',4200,{label:i18nText('action.undo'),onClick:()=>appUndo()});
}
function requestHistory(kind,source='ui'){
  const now=performance.now();
  if(source==='beforeinput'&&state.historyShortcutKind===kind&&now<state.historyShortcutUntil)return state.historyChain;
  if(source==='shortcut'){state.historyShortcutKind=kind;state.historyShortcutUntil=now+500;}
  state.historyChain=state.historyChain.then(async()=>{
    // Structural actions record their before/after revisions asynchronously. If the
    // user presses Undo immediately (especially after Move to Cuts), wait for that
    // action to finish recording before inspecting the Undo stack.
    await state.structuralChain;
    state.historyBusy=true;updateUndoButtons();
    try{
      // Undo/Redo are edits to the current working state, not navigation. Preserve
      // the writer's live manuscript anchor across the refresh/reopen sequence.
      if(currentView()==='manuscript')await withManuscriptMutationBarrier(()=>preserveManuscriptViewport(()=>kind==='undo'?performUndo():performRedo()));
      else if(kind==='undo')await performUndo();else await performRedo();
    }
    finally{state.historyBusy=false;updateUndoButtons()}
  }).catch(error=>{state.historyBusy=false;updateUndoButtons();console.error('HISTORY_COMMAND_001',error);toast('Undo/redo could not be completed safely. The action is still available—please try again.',6000);});
  return state.historyChain;
}
function appUndo(){return requestHistory('undo','ui')}
function appRedo(){return requestHistory('redo','ui')}
async function reopenAfterStructuralRestore(target){
  if(target.view==='canvas'&&target.canvasId&&state.canvases.some(c=>c.id===target.canvasId)){setView('canvas');await selectCanvas(target.canvasId);return}
  if(target.view==='maps'&&target.mapId&&state.maps.some(m=>m.id===target.mapId)){setView('maps');await selectMap(target.mapId);return}
  if(target.view==='tasks'){setView('tasks');await loadTasks();return}
  if(target.view==='reviews'){setView('reviews');renderReviews();return}
  setView('manuscript');
  if(target.documentId&&state.documents.some(d=>d.id===target.documentId))state.activeDocumentId=target.documentId;
  if(target.manuscriptViewMode==='book'){await openWholeBook(false);return}
  if(target.manuscriptViewMode==='scene'&&target.sceneId&&state.scenes.some(scene=>entityId(scene.id)===entityId(target.sceneId))){await selectScene(entityId(target.sceneId),false);return}
  const chapterId=target.chapterId&&!!visibleChapterById(target.chapterId)?target.chapterId:visibleChapters(state.activeDocumentId)[0]?.id;if(chapterId)await selectChapter(chapterId,false);
}
async function beginStructuralUndo(label,scope='full',scopeId=null){await flushPendingCanvasNodeSaves();await flushSave();const suffix=scopeId!==null&&scopeId!==undefined?`&scope_id=${encodeURIComponent(scopeId)}`:'';const started=await api(`/api/undo/begin?label=${encodeURIComponent(label)}&scope=${encodeURIComponent(scope||'full')}${suffix}`,{method:'POST'});return started.token}
async function finishStructuralUndo(token,label,context={}){
  const finished=await api(`/api/undo/finish/${token}`,{method:'POST'});
  if(!finished.changed)return false;
  historyPush('undo',{kind:'structural',token,label,view:context.view||currentView(),chapterId:context.chapterId||state.chapter?.id||null,sceneId:context.sceneId||state.scene?.id||null,documentId:context.documentId||null,manuscriptViewMode:context.manuscriptViewMode||null,canvasId:context.canvasId||state.currentCanvas?.id||null,mapId:context.mapId||state.currentMap?.id||null});historyClear('redo');return true;
}
function withStructuralUndo(label,fn,context={}){
  const run=async()=>withManuscriptMutationBarrier(async()=>{
    const scope=context.undoScope||'full',scopeId=scope==='canvas'?(context.canvasId||state.currentCanvas?.id||null):scope==='maps'?(context.mapId||state.currentMap?.id||null):null;const before=await beginStructuralUndo(label,scope,scopeId);
    try{
      if(runtimeSupports('hosted_persistence'))hostedStructuralActionToken=before;
      const result=await fn();
      hostedStructuralActionToken=null;
      await finishStructuralUndo(before,label,context);
      invalidateTaskCache();invalidateQuickOpenDetailIndex();
      return result;
    }catch(error){
      hostedStructuralActionToken=null;
      // Treat a multi-step user action as one application-level transaction. API
      // calls inside fn may each be valid SQLite transactions, but a later failure
      // must not strand an earlier successful step or destructive DOM mutation.
      try{
        clearTimeout(state.saveTimer);
        await api(`/api/undo/rollback/${before}`,{method:'POST'});
        resetDirtyState();setSaveState('Saved');
        const reopen=async()=>{await refresh(false);await reopenAfterStructuralRestore({kind:'structural',view:context.view||currentView(),chapterId:context.chapterId||null,canvasId:context.canvasId||null,mapId:context.mapId||null})};
        if(currentView()==='manuscript')await preserveManuscriptViewport(reopen);else await reopen();
        if(!(context.quietRollbackCodes||[]).includes(error?.code))toast(i18nText('action.rollback_restored',{label:i18nSource(label)}),4200);
      }catch(rollbackError){
        console.error('STRUCTURAL_ROLLBACK_001', {label,error,rollbackError});
        toast('The action failed and automatic rollback could not be confirmed. Stop editing and reopen Breadmaker before continuing.',8000);
      }
      throw error;
    }
  });
  // Serialize structural operations. More importantly, expose the in-flight promise
  // so an immediate Ctrl/Cmd+Z waits for the action's Undo revision to exist.
  const operation=state.structuralChain.then(run,run);
  state.structuralChain=operation.catch(()=>{});
  return operation;
}
function recordStructuralUndo(){/* v4 structural operations use withStructuralUndo() */}
$('#undoBtn').onclick=event=>{if(event.currentTarget.getAttribute('aria-disabled')==='true')return;requestHistory('undo','button')};$('#redoBtn').onclick=event=>{if(event.currentTarget.getAttribute('aria-disabled')==='true')return;requestHistory('redo','button')};updateUndoButtons();
