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
window.__breadmakerBootPromise.then(()=>{document.documentElement.dataset.breadmakerBootReady='true'}).catch(error=>{console.error('BREADMAKER_BOOT_001',error);showModal('Breadmaker could not open this project',`<p>${escapeHtml(error?.message||String(error))}</p><p class="small-muted">{{i18n:dynamic.html.text.p.4fa5b320}}</p>`)});

