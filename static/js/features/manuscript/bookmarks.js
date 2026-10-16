// Manuscript bookmark ownership.

// 4.19 — one deliberate manuscript bookmark. This is project state (Undo/Redo),
// unlike last-location restoration, which is lightweight UI navigation state.
const BOOKMARK_ICON_SVG='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3.5h10v17l-5-3.4-5 3.4z"/></svg>';
let bookmarkRemoveArm={button:null,until:0,toastTimer:null,expiryTimer:null};
function clearBookmarkRemoveArm(){clearTimeout(bookmarkRemoveArm.toastTimer);clearTimeout(bookmarkRemoveArm.expiryTimer);bookmarkRemoveArm={button:null,until:0,toastTimer:null,expiryTimer:null}}
function bookmarkExactChapter(id){return state.bookmark?.target_type==='chapter'&&+state.bookmark.chapter_id===+id}
function bookmarkExactScene(id){return state.bookmark?.target_type==='scene'&&+state.bookmark.scene_id===+id}
function bookmarkShowsOnChapter(id){const b=state.bookmark;return !!b&&(bookmarkExactChapter(id)||(b.owner_chapter_id!=null&&+b.owner_chapter_id===+id))}
function bookmarkShowsOnScene(id){const b=state.bookmark;return !!b&&(bookmarkExactScene(id)||(b.owner_scene_id!=null&&+b.owner_scene_id===+id))}
function bookmarkShowsOnSceneGap(gap){const b=state.bookmark;if(!b||!gap||+b.tab_id!==+gap.tab_id||!b.location_block_id)return false;return (gap.block_ids||[]).some(id=>String(id)===String(b.location_block_id))}
function bookmarkRangeMatches(items){
  const b=state.bookmark;if(!b||b.target_type!=='range'||!items?.length)return false;const first=items[0],last=items.at(-1);
  return String(b.start_block_id)===String(first.block_id)&&String(b.end_block_id)===String(last.block_id)&&+b.start_offset===+first.start_offset&&+b.end_offset===+last.end_offset;
}
function bookmarkContext(){return {view:'manuscript',chapterId:state.chapter?.id||null,sceneId:state.scene?.id||null,documentId:state.activeDocumentId||null,manuscriptViewMode:state.viewMode}}
function bookmarkDestinationTooltip(bookmark=state.bookmark){
  const b=bookmark;if(!b)return i18nText('manuscript.bookmark_go_tooltip');
  if(b.target_type==='chapter'){const title=b.target_title||storageChapterById(+b.chapter_id)?.title;if(title)return i18nText('manuscript.bookmark_go_chapter',{title});}
  if(b.target_type==='scene'){const title=b.target_title||sceneById(+b.scene_id)?.title;if(title)return i18nText('manuscript.bookmark_go_scene',{title});}
  if(b.target_type==='range'){const scene=b.owner_scene_id?sceneById(+b.owner_scene_id):null;if(scene?.title)return i18nText('manuscript.bookmark_go_passage_scene',{title:scene.title});const chapter=b.owner_chapter_id?storageChapterById(+b.owner_chapter_id):null;if(chapter?.title)return i18nText('manuscript.bookmark_go_passage_chapter',{title:chapter.title});return i18nText('manuscript.bookmark_go_passage');}
  return i18nText('manuscript.bookmark_go_tooltip');
}
function syncBookmarkToolbar(){
  const button=$('#bookmarkBtn'),text=$('#bookmarkTooltipText');if(!button)return;const has=!!state.bookmark,tooltip=bookmarkDestinationTooltip();button.classList.toggle('hidden',!has);button.classList.toggle('has-bookmark',has);button.setAttribute('aria-label',has?tooltip:i18nSource('Go to bookmark'));if(text)text.textContent=tooltip;
}
async function refreshBookmarkPresentation({preserveViewport=true}={}){
  await refresh(false);renderChapterTree();
  if(currentView()==='manuscript'){
    if(preserveViewport)await reloadManuscriptView();else if(state.viewMode==='book')renderBookEditor();else if(state.viewMode==='scene')renderSceneEditor();else if(state.viewMode==='scene-gap')renderSceneGapEditor();else if(state.viewMode==='chapter')renderEditor();
  }
  // Reveal the viewport bookmark only after the manuscript layout is stable. Showing it
  // before reloadManuscriptView() can move the pointer off the sticky control and cancel
  // its delayed hover tooltip while the async bookmark mutation is still settling.
  syncBookmarkToolbar();
}
async function setBookmarkTarget(payload,label='Bookmark'){
  const moving=!!state.bookmark;await withStructuralUndo(moving?'Move bookmark':label,()=>api('/api/bookmark',{method:'PUT',body:JSON.stringify(payload)}),bookmarkContext());
  clearBookmarkRemoveArm();await refreshBookmarkPresentation();toast(moving?'Bookmark moved':'Bookmarked');
}
async function removeBookmark(){
  if(!state.bookmark)return;await withStructuralUndo('Remove bookmark',()=>api('/api/bookmark',{method:'DELETE'}),bookmarkContext());clearBookmarkRemoveArm();await refreshBookmarkPresentation();undoToast('Bookmark removed');
}
function armBookmarkRemoval(button){
  const now=Date.now();if(bookmarkRemoveArm.button===button&&now<bookmarkRemoveArm.until){clearBookmarkRemoveArm();removeBookmark().catch(error=>toast(error.message||'Bookmark could not be removed.'));return}
  clearBookmarkRemoveArm();bookmarkRemoveArm.button=button;bookmarkRemoveArm.until=now+2200;
  bookmarkRemoveArm.toastTimer=setTimeout(()=>{if(bookmarkRemoveArm.button===button&&Date.now()<bookmarkRemoveArm.until)toast('Click again to remove bookmark')},180);
  bookmarkRemoveArm.expiryTimer=setTimeout(clearBookmarkRemoveArm,2250);
}
function wireBookmarkRemovalButton(button,label='Bookmark'){
  if(!button)return;button.innerHTML=BOOKMARK_ICON_SVG;button.title=i18nText('bookmark.remove_tooltip',{label:i18nSource(label)});button.onpointerdown=e=>{e.preventDefault();e.stopPropagation()};button.onclick=e=>{e.preventDefault();e.stopPropagation();armBookmarkRemoval(button)};button.ondblclick=e=>{e.preventDefault();e.stopPropagation()};
}
function wireSidebarBookmarkNavigation(button){
  if(!button)return;button.innerHTML=BOOKMARK_ICON_SVG;const tooltip=bookmarkDestinationTooltip();button.title=tooltip;button.setAttribute('aria-label',tooltip);button.onpointerdown=e=>{e.stopPropagation()};button.onclick=e=>{e.preventDefault();e.stopPropagation();goToBookmark().catch(error=>toast(error.message||'Bookmark could not be opened.'))};button.ondblclick=e=>{e.preventDefault();e.stopPropagation()};
}
async function toggleChapterBookmark(chapter){if(bookmarkExactChapter(chapter.id))return removeBookmark();return setBookmarkTarget({target_type:'chapter',chapter_id:chapter.id},'Bookmark Chapter')}
async function toggleSceneBookmark(scene){if(bookmarkExactScene(scene.id))return removeBookmark();return setBookmarkTarget({target_type:'scene',scene_id:scene.id},'Bookmark Scene')}
async function toggleTextBookmark(items){
  if(!items?.length)return;if(bookmarkRangeMatches(items))return removeBookmark();await ensureSavedSelection(items);const first=items[0],last=items.at(-1);
  return setBookmarkTarget({target_type:'range',start_block_id:first.block_id,end_block_id:last.block_id,start_offset:first.start_offset,end_offset:last.end_offset},'Bookmark selection');
}
function makeInlineBookmarkButton(label='Bookmark'){
  const button=document.createElement('button');button.type='button';button.className='inline-bookmark-button';button.contentEditable='false';button.setAttribute('aria-label',i18nSource(label));wireBookmarkRemovalButton(button,label);return button;
}
function renderBookmarkMarker(wrap,blockId){
  const b=state.bookmark;if(!b||b.target_type!=='range'||String(b.start_block_id)!==String(blockId))return;const button=makeInlineBookmarkButton('Bookmarked text');button.classList.add('text-bookmark-marker');wrap.appendChild(button);
}
function addChapterBookmarkToHeading(heading,chapterId){if(!bookmarkShowsOnChapter(chapterId))return;const exact=bookmarkExactChapter(chapterId),button=makeInlineBookmarkButton(exact?'Bookmarked Chapter':'Bookmark inside this Chapter');button.classList.add('heading-bookmark-marker');heading.prepend(button)}
function addSceneBookmarkToHeading(heading,sceneId){if(!bookmarkShowsOnScene(sceneId))return;const exact=bookmarkExactScene(sceneId),button=makeInlineBookmarkButton(exact?'Bookmarked Scene':'Bookmark inside this Scene');button.classList.add('heading-bookmark-marker');heading.prepend(button)}
function syncFocusedChapterBookmark(){const button=$('#chapterTitleBookmark');if(!button)return;const show=state.viewMode==='chapter'&&state.chapter&&bookmarkShowsOnChapter(state.chapter.id);button.classList.toggle('hidden',!show);if(show)wireBookmarkRemovalButton(button,bookmarkExactChapter(state.chapter.id)?'Bookmarked Chapter':'Bookmark inside this Chapter')}
function syncSelectionBookmarkAction(items,headerChapterId=null){const button=$('#selectionToolbar [data-selection-action="bookmark"]'),title=$('#selectionBookmarkTooltipTitle'),text=$('#selectionBookmarkTooltipText');if(!button)return;const header=+headerChapterId||0,active=header?bookmarkExactChapter(header):bookmarkRangeMatches(items),label=i18nSource(header?(active?'Remove Chapter bookmark':'Bookmark Chapter'):(active?'Remove bookmark':'Bookmark selection'));button.classList.toggle('active',active);button.setAttribute('aria-label',label);button.removeAttribute('title');if(title)title.textContent=label;if(text)text.textContent=i18nText(active?'manuscript.bookmark_remove_tooltip':'manuscript.bookmark_selection_tooltip')}
