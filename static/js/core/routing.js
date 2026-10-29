function reportViewRenderError(view,error) {
  console.error(`BREADMAKER_VIEW_${String(view).toUpperCase()}_001`, error);
  toast(i18nText('runtime.render_failed',{view:i18nSource(view[0].toUpperCase()+view.slice(1))}));
}
const VIEW_ROUTES={manuscript:'',canvas:'/canvas',maps:'/maps',tasks:'/tasks',reviews:'/reviews'};
const PROJECT_ROUTE_RE=/^\/p\/([0-9a-f]{32})(?:\/(canvas|maps|tasks|reviews))?\/?$/;
function projectRouteIdentity(pathname=location.pathname){const match=PROJECT_ROUTE_RE.exec(String(pathname||''));return match?match[1]:''}
function projectRouteView(pathname=location.pathname){const match=PROJECT_ROUTE_RE.exec(String(pathname||''));return match?(match[2]||'manuscript'):null}
function currentView(){return $('.view.active')?.id?.replace('View','')||'manuscript'}
function viewFromRoute(pathname=location.pathname){
  const projectView=projectRouteView(pathname);if(projectView)return projectView;
  const normalized=String(pathname||'/').replace(/\/+$/,'')||'/';
  return Object.entries(VIEW_ROUTES).find(([,suffix])=>(suffix||'/')===normalized)?.[0]||'manuscript';
}
function hostedProjectRoute(projectId,view='manuscript'){
  const pid=String(projectId||'').toLowerCase();if(!/^[0-9a-f]{32}$/.test(pid))return '/';
  return `/p/${pid}${VIEW_ROUTES[view]||''}`;
}
function openWorkspaceProject(projectId,{replace=false,view='manuscript'}={}){
  let path='/';
  if(runtimeSupports('hosted_projects')){
    const pid=String(projectId||'').toLowerCase();
    if(!/^[0-9a-f]{32}$/.test(pid))throw new Error('PROJECT_ROUTE_ID_INVALID');
    path=hostedProjectRoute(pid,view);
  }
  location[replace?'replace':'assign'](path);
}
function syncViewRoute(view,{replace=false}={}){
  if(!['http:','https:'].includes(location.protocol))return;
  const projectId=currentProjectIdentity();
  const path=runtimeSupports('hosted_projects')&&projectId?hostedProjectRoute(projectId,view):(VIEW_ROUTES[view]||'/');
  if(location.pathname===path)return;
  const method=replace?'replaceState':'pushState';
  try{history[method]({breadmakerView:view},'',`${path}${location.search}${location.hash}`)}catch(error){console.warn('Could not update Breadmaker route',error)}
}
function setView(view,{historyMode='push'}={}) {
  $$('.view').forEach(v => v.classList.remove('active'));
  const target=$(`#${view}View`);
  if(!target){reportViewRenderError(view,new Error(`Missing view shell #${view}View`));return}
  target.classList.add('active');
  $$('.main-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.view === view));
  if(historyMode!=='none')syncViewRoute(view,{replace:historyMode==='replace'});
  if (view === 'canvas') { drawEdges(); applyPendingCanvasViewport(); }
  if (view === 'maps') renderMapsWorkspace().catch(error=>reportViewRenderError('maps',error));
  if (view === 'tasks') Promise.resolve(loadTasks()).catch(error=>reportViewRenderError('tasks',error));
  if (view === 'reviews') renderReviews();
  document.documentElement.removeAttribute('data-initial-view');
}
let workspaceNavigation=0;
async function hydratePrimaryWorkspace(view){
  if(view==='manuscript'&&state.manuscriptWorkspaceHydrated&&runtimeSupports('hosted_projects')&&(currentView()!=='manuscript'||state.manuscriptNeedsValidation)){
    // Paint the retained workspace immediately, but keep edits locked until
    // current authority/revision validation completes. Never rebase old text.
    const navigation=workspaceNavigation;await flushSave();if(navigation!==workspaceNavigation)return;
    setView('manuscript');const pane=$('#manuscriptPrimaryPane');pane.inert=true;pane.setAttribute('aria-busy','true');state.manuscriptNeedsValidation=true;
    try{await reloadManuscriptView({keepLoaded:true});state.manuscriptNeedsValidation=false;pane.inert=false}
    catch(error){toast(error.message||i18nSource('Loading…'),5000)}
    finally{pane.setAttribute('aria-busy','false')}
    return;
  }
  if(view==='manuscript'&&!state.manuscriptWorkspaceHydrated){
    if(state.hostedChapterFirst){
      // A cold legacy book can mean thousands of object reads. Open a complete,
      // editable chapter, never a partial book that could later overwrite text.
      const last=state.bootUIState?.['manuscript-last-location'];
      if(last?.view==='chapter'||last?.view==='scene'){
        if(await restoreLastManuscriptLocation(state.bootUIState)){state.manuscriptWorkspaceHydrated=true;syncBookmarkToolbar();return}
      }
      const chapterId=last?.viewport?.chapter_id||state.bookmark?.owner_chapter_id||state.bookmark?.chapter_id;
      const chapter=state.chapters.find(row=>String(row.id)===String(chapterId))||visibleChapters(state.activeDocumentId)[0];
      if(chapter){await selectChapter(chapter.id,false,false);if(last?.viewport)requestAnimationFrame(()=>restoreManuscriptViewport(last.viewport))}
      state.manuscriptWorkspaceHydrated=true;syncBookmarkToolbar();return;
    }
    let restored=await restoreLastManuscriptLocation(state.bootUIState);
    if(!restored&&state.bookmark)restored=await goToBookmark({activateView:false,silent:true});
    if(!restored&&state.activeDocumentId)await openWholeBook(false,false);
    state.manuscriptWorkspaceHydrated=true;syncBookmarkToolbar();return;
  }
  if(view==='canvas'&&!state.currentCanvas&&state.canvases.length){
    const remembered=state.lastCanvasId&&state.canvases.some(canvas=>entityId(canvas.id)===entityId(state.lastCanvasId))?entityId(state.lastCanvasId):entityId(state.canvases[0].id);
    if(remembered)await selectCanvas(remembered,{persist:false});
  }
}

$$('.main-tab').forEach(tab => tab.addEventListener('click', async () => {
  const view=tab.dataset.view,navigation=++workspaceNavigation;
  if(state.hostedZeroProject){
    if(view!=='manuscript'||hostedFirstProjectCreating)await createHostedProjectFromBlankEditor({view});
    return;
  }
  if(view!=='manuscript')cancelFullTextPreview();
  if(view!=='manuscript')await flushSave();
  await hydratePrimaryWorkspace(view);
  if(navigation===workspaceNavigation)setView(view);
}));
window.addEventListener('popstate',async()=>{
  const view=viewFromRoute(),navigation=++workspaceNavigation;
  if(state.hostedZeroProject){
    if(view!=='manuscript'||hostedFirstProjectCreating)await createHostedProjectFromBlankEditor({view});
    return;
  }
  if(view!=='manuscript')cancelFullTextPreview();
  if(view!=='manuscript')await flushSave();
  await hydratePrimaryWorkspace(view);
  if(navigation===workspaceNavigation)setView(view,{historyMode:'none'});
});

