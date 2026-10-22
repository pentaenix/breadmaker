// Canvas keyboard operations are handled by the global Canvas shortcut handler above.
// Normal Find must always restore its controls after using the same navigator for markup.
const _openFindBase=openFind;
openFind=function(){
  $('#findInput').disabled=false;$('#findOptions').classList.remove('hidden');if(state.find.navigatingMarkup){$('#findInput').value='';state.find={hits:[],index:-1,query:'',navigatingMarkup:false};}
  _openFindBase();
};

// Start only after every event handler above exists.
installBreadReviewDropTarget();
window.__breadmakerBootPromise=boot();
window.__breadmakerBootPromise.then(()=>{clearTimeout(window.__breadmakerStartupDeadline);delete document.documentElement.dataset.breadmakerBootFailed;window.breadmakerStartupProgress?.(100);document.title=String(state.project?.name||state.project?.title||'Breadmaker Studio');document.documentElement.dataset.breadmakerBootReady='true';const app=document.getElementById('app');app.inert=false;app.removeAttribute('aria-hidden');app.setAttribute('aria-busy','false')}).catch(error=>{clearTimeout(window.__breadmakerStartupDeadline);console.error('BREADMAKER_BOOT_001',error);window.breadmakerStartupFailed?.()});

