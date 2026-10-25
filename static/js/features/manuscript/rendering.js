// Manuscript rendering, visible annotation presentation, view selection, and viewport/location state.

// -------------------- manuscript rendering --------------------
function newTempId(){return `tmp_${uid().replaceAll('-','')}`;}
function visibleAnnotations(){return state.viewMode==='book'||state.viewMode==='scene-gap'?(state.bookData?.annotations||[]):state.viewMode==='scene'?(state.sceneData?.annotations||[]):(state.chapterData?.annotations||[]);}
function annotationMap(){const map=new Map();for(const a of visibleAnnotations()){if(!a.block_id)continue;if(!map.has(a.block_id))map.set(a.block_id,[]);map.get(a.block_id).push(a)}return map;}
function chapterTitleAnnotations(chapterId){return visibleAnnotations().filter(a=>!a.block_id&&entityId(a.chapter_id)===entityId(chapterId))}
function chapterTitleTextElement(chapterId){if(state.viewMode==='chapter'&&entityId(state.chapter?.id)===entityId(chapterId))return $('#chapterTitle');return $(`.book-chapter-heading[data-chapter-id="${chapterId}"] > .book-chapter-title`)}
function normalizedChapterTitleElementText(element){if(!element)return'';const normalized=element.innerText.replace(/\n+/g,' ');if(normalized!==element.innerText)element.textContent=normalized;return normalized.trim()||i18nSource('Untitled Chapter')}
function visibleChapterTitleText(chapterId){const element=chapterTitleTextElement(chapterId);return element&&element.isConnected?normalizedChapterTitleElementText(element):(storageChapterById(chapterId)?.title||i18nSource('Untitled Chapter'))}
function updateChapterTitleState(chapterId,title,revision=null){const ch=storageChapterById(chapterId);if(ch){ch.title=title;if(revision)ch.revision=revision}if(state.chapter&&entityId(state.chapter.id)===entityId(chapterId))state.chapter.title=title;for(const collection of [state.bookData?.chapters,state.sceneData?.chapters])for(const row of collection||[])if(entityId(row.id)===entityId(chapterId))row.title=title}
function renderChapterTitleAnnotations(container,chapterId,noteGroups){if(!container)return;$$(':scope > .note-gutter-marker,:scope > .markup-gutter',container).forEach(x=>x.remove());const rows=chapterTitleAnnotations(chapterId),highlights=rows.filter(a=>a.annotation_type==='highlight'&&a.label_id&&a.label_color);renderMarkupGutterSwatches(container,`chapter:${chapterId}`,highlights);renderNoteGutterMarker(container,null,rows,noteGroups,{chapterId})}
function makeInlineChapterHeading(chapter,{className='book-chapter-heading',allowBoundaryDelete=false,noteGroups=null}={}){
  const heading=document.createElement('div');heading.className=className;heading.contentEditable='false';heading.tabIndex=0;heading.dataset.chapterId=chapter.id;
  const title=document.createElement('span');title.className='book-chapter-title';title.contentEditable='true';title.spellcheck=true;title.textContent=chapter.title||i18nSource('Untitled Chapter');title.setAttribute('aria-label',i18nSource('Chapter title'));heading.appendChild(title);
  addChapterBookmarkToHeading(heading,chapter.id);
  heading.onclick=e=>{if(e.target.closest('.book-chapter-title,.inline-bookmark-button,.note-gutter-marker,.markup-gutter'))return;heading.focus()};
  heading.onkeydown=e=>{if(e.target.closest('.book-chapter-title'))return;if(allowBoundaryDelete&&(e.key==='Backspace'||e.key==='Delete')&&!e.metaKey&&!e.ctrlKey&&!e.altKey){e.preventDefault();e.stopPropagation();dissolveChapterBoundary(chapter.id,{stayInBook:true}).catch(error=>toast(error.message||String(error)))}};
  title.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();const first=wrapsForChapter(chapter.id)[0]?.querySelector('.manuscript-block');if(first)placeCaret(first,0);return}if(e.key==='Backspace'||e.key==='Delete'){const selection=getSelection();if(!selection?.rangeCount||!selection.isCollapsed)return;const range=selection.getRangeAt(0),offset=offsetIn(title,range.startContainer,range.startOffset),length=title.innerText.length;if((e.key==='Backspace'&&offset===0)||(e.key==='Delete'&&offset>=length))e.preventDefault()}};
  if(noteGroups)renderChapterTitleAnnotations(heading,chapter.id,noteGroups);return heading;
}

function makeBlockWrap(block, chapterId=null){
  const wrap=document.createElement('div'); wrap.className='manuscript-block-wrap';wrap.dataset.testid='manuscript-block-wrap';
  wrap.dataset.blockId=block.id||newTempId(); wrap.dataset.chapterId=chapterId||block.chapter_id||state.chapter?.id||''; wrap.dataset.sceneId=block.scene_id||''; wrap.dataset.type=block.block_type||'paragraph'; wrap.dataset.style=block.semantic_style||'body';
  if(block.block_type==='image'){
    wrap.contentEditable='false';wrap.classList.add('manuscript-image-wrap');wrap.dataset.imagePath=block.image_path||'';wrap.dataset.imageWidth=String(Math.max(5,Math.min(100,Number(block.image_width)||100)));wrap.dataset.imageFooter=block.image_footer||'';wrap.dataset.imageDescription=block.image_description||'';wrap.dataset.imageRowId=block.image_row_id||'';
    const figure=document.createElement('figure');figure.className='manuscript-image-figure';figure.draggable=true;figure.tabIndex=0;figure.setAttribute('role','group');figure.setAttribute('aria-label',i18nText('manuscript.image_selected'));
    const img=document.createElement('img');img.className='manuscript-image';img.src=projectAssetDisplayUrl(block.image_path);img.alt=wrap.dataset.imageDescription;img.draggable=false;img.style.width=wrap.dataset.imageRowId?'100%':`${wrap.dataset.imageWidth}%`;figure.appendChild(img);
    const caption=document.createElement('figcaption');caption.className='manuscript-image-footer';caption.textContent=wrap.dataset.imageFooter;figure.appendChild(caption);wrap.appendChild(figure);bindManuscriptImageWrap(wrap);bindManuscriptImageDropTarget(wrap);return wrap;
  }
  const prose=document.createElement('div');prose.className='manuscript-block';prose.dataset.testid='manuscript-block';prose.dataset.semanticStyle=block.semantic_style||'body';prose.innerHTML=block.content_html||escapeHtml(block.text_content||'');wrap.appendChild(prose);bindManuscriptImageDropTarget(wrap);return wrap;
}

function outlineStyleLevel(style){const match=/^heading([2-4])$/.exec(String(style||''));return match?+match[1]:0}
function persistManuscriptOutlineState(){
  clearTimeout(state.manuscriptOutlineSaveTimer);state.manuscriptOutlineSaveTimer=setTimeout(()=>{
    const headings=[...state.manuscriptCollapsedHeadings].filter(id=>id&&!String(id).startsWith('tmp_'));
    const chapters=[...state.manuscriptCollapsedChapters].map(entityId).filter(isEntityId);
    api('/api/ui-state/manuscript-outline-collapse',{method:'PUT',body:JSON.stringify({value:{headings,chapters}})}).catch(console.warn);
  },80);
}
// Manuscript prose itself is never collapsible. Outline state belongs exclusively
// to the left navigation tree; this function only guarantees old hidden classes or
// legacy gutter controls cannot survive a rerender.
function applyOutlineVisibility(){
  $$('.manuscript-block-wrap.outline-hidden,.ghost-scene-title.outline-hidden').forEach(node=>node.classList.remove('outline-hidden'));
  $$('.outline-toggle,.book-chapter-toggle').forEach(node=>node.remove());
}
function toggleOutlineHeading(blockId){
  const id=String(blockId);if(state.manuscriptCollapsedHeadings.has(id))state.manuscriptCollapsedHeadings.delete(id);else state.manuscriptCollapsedHeadings.add(id);renderChapterTree();persistManuscriptOutlineState();
}
function toggleOutlineChapter(chapterId){
  chapterId=entityId(chapterId);if(!chapterOutlineItems(chapterId).length)return;if(state.manuscriptCollapsedChapters.has(chapterId))state.manuscriptCollapsedChapters.delete(chapterId);else state.manuscriptCollapsedChapters.add(chapterId);renderChapterTree();persistManuscriptOutlineState();
}
function revealOutlineTarget(wrap){
  if(!wrap)return;const chapterId=entityId(wrap.dataset.chapterId);let changed=state.manuscriptCollapsedChapters.delete(chapterId),stack=[];const targetId=String(wrap.dataset.blockId);
  for(const item of chapterOutlineItems(chapterId)){if(String(item.id)===targetId)break;const level=chapterOutlineLevel(item);while(stack.length&&stack.at(-1).level>=level)stack.pop();stack.push({level,id:String(item.id)})}
  for(const entry of stack)if(state.manuscriptCollapsedHeadings.delete(entry.id))changed=true;
  if(changed){renderChapterTree();persistManuscriptOutlineState()}
}

function appendSceneGhost(container,block,chapterId,previousScene,{book=false}={}){
  if(!sceneMarkersVisible()||!block.scene_id||entityId(block.scene_id)===entityId(previousScene))return;
  const scene=sceneById(block.scene_id); if(!scene)return;
  if(book&&String(scene.first_block_id||'')!==String(block.id))return;
  const ghost=document.createElement('div');ghost.className='ghost-scene-title';ghost.contentEditable='false';ghost.dataset.sceneId=scene.id;const continued=!book&&entityId(scene.first_chapter_id)!==entityId(chapterId);ghost.classList.toggle('continued',continued);ghost.innerHTML=`<span>${escapeHtml(scene.title)}${continued?` · ${i18nSource('continued')}`:''}</span>`;addSceneBookmarkToHeading(ghost,scene.id);if(continued&&!bookmarkShowsOnScene(scene.id))ghost.title='{{i18n:dynamic.html.attr.title.3849ee66}}';ghost.ondblclick=e=>{if(e.target.closest('.inline-bookmark-button'))return;selectScene(scene.id)};ghost.oncontextmenu=e=>{if(e.target.closest('.inline-bookmark-button'))return;e.preventDefault();openSceneMenu(e,scene.id)};container.appendChild(ghost);
}
function appendSceneEndGhost(container,block){
  if(!sceneMarkersVisible()||!block?.scene_id)return;const scene=sceneById(block.scene_id);if(!scene||String(scene.last_block_id)!==String(block.id))return;
  const ghost=document.createElement('div');ghost.className='ghost-scene-end';ghost.contentEditable='false';ghost.dataset.sceneId=scene.id;ghost.innerHTML=`<span>${escapeHtml(i18nText('scene.end_label',{title:scene.title}))}</span>`;ghost.title=i18nText('scene.end_tooltip',{title:scene.title});ghost.ondblclick=()=>configureScene(scene.id);ghost.oncontextmenu=e=>{e.preventDefault();openSceneMenu(e,scene.id)};container.appendChild(ghost);
}

function visibleCommentThreadGroups(){
  const rows=visibleAnnotations().filter(a=>a.annotation_type==='comment'&&a.thread_id&&a.status!=='resolved'&&!a.stale),groups=new Map();
  for(const a of rows){const id=entityId(a.thread_id);if(!groups.has(id))groups.set(id,[]);groups.get(id).push(a)}
  return groups;
}
function renderBlockAnnotations(wrap,blockId,map,noteGroups){
  const annotations=map.get(blockId)||[],highlights=annotations.filter(a=>a.annotation_type==='highlight'&&a.label_id&&a.label_color);
  renderMarkupGutterSwatches(wrap,blockId,highlights);
  renderBookmarkMarker(wrap,blockId);
  renderNoteGutterMarker(wrap,blockId,annotations,noteGroups);
}

let markupHoverTimer=null,markupHoverSourceKey=null;
function hideMarkupHover(){clearTimeout(markupHoverTimer);markupHoverTimer=null;markupHoverSourceKey=null;$('#markupHoverCard')?.classList.add('hidden')}
function positionMarkupHoverCard(card,target){
  const rect=target.getBoundingClientRect(),gap=9,margin=8;card.classList.remove('hidden');const box=card.getBoundingClientRect();
  let left=rect.left-box.width-gap;if(left<margin)left=rect.right+gap;
  left=Math.max(margin,Math.min(left,innerWidth-box.width-margin));
  const top=Math.max(margin,Math.min(rect.top+(rect.height-box.height)/2,innerHeight-box.height-margin));
  card.style.left=`${Math.round(left)}px`;card.style.top=`${Math.round(top)}px`;
}
function queueMarkupHover(marker,target,blockId){
  clearTimeout(markupHoverTimer);const key=`${marker.id}@${blockId}`;markupHoverSourceKey=key;$('#markupHoverCard')?.classList.add('hidden');
  markupHoverTimer=setTimeout(()=>{if(markupHoverSourceKey!==key)return;const card=$('#markupHoverCard');if(!card)return;card.innerHTML=`<span class="markup-hover-dot" style="background:${escapeHtml(marker.color)}"></span><span>${escapeHtml(marker.name)}</span>`;positionMarkupHoverCard(card,target)},300);
}
function renderMarkupGutterSwatches(wrap,blockId,highlights){
  if(!highlights.length)return;const seen=new Set(),items=[];
  for(const annotation of highlights){const id=entityId(annotation.label_id);if(!id||seen.has(id))continue;seen.add(id);items.push({id,name:annotation.label_name||'Marker',color:annotation.label_color})}
  if(!items.length)return;const gutter=document.createElement('div');gutter.className='markup-gutter';gutter.contentEditable='false';gutter.dataset.blockId=blockId;
  for(const marker of items){const swatch=document.createElement('button');swatch.type='button';swatch.className='markup-gutter-swatch';swatch.style.background=marker.color;swatch.setAttribute('aria-label',i18nText('marker.highlight_aria',{name:marker.name}));swatch.onpointerdown=e=>{e.preventDefault();e.stopPropagation()};swatch.onmouseenter=()=>queueMarkupHover(marker,swatch,blockId);swatch.onmouseleave=()=>{clearTimeout(markupHoverTimer);if(markupHoverSourceKey===`${marker.id}@${blockId}`)hideMarkupHover()};swatch.onclick=e=>{e.stopPropagation();hideMarkupHover();navigateMarker(marker.id)};gutter.appendChild(swatch)}
  wrap.appendChild(gutter);
}

let commentHoverHideTimer=null;
function visibleNoteThreadGroups(){
  const rows=visibleAnnotations().filter(a=>a.annotation_type==='comment'&&a.thread_id&&a.status!=='resolved'&&!a.stale),groups=new Map();
  for(const a of rows){const id=entityId(a.thread_id);if(!groups.has(id))groups.set(id,[]);groups.get(id).push(a)}
  return groups;
}
function scheduleCommentRailLayout(){/* 4.15.4: block-relative Note markers reflow with the manuscript automatically. */}
function cancelCommentRailHoverHide(){clearTimeout(commentHoverHideTimer);commentHoverHideTimer=null}
function scheduleCommentRailHoverHide(){cancelCommentRailHoverHide();commentHoverHideTimer=setTimeout(()=>hideCommentHover(),120)}
function noteThreadFromMarker(item){return {id:item.id,anchors:item.anchors,first_message:item.body,chapter_title:item.chapter,scene_title:item.scene}}
function positionCommentHoverCard(card,rect,target=null,interactive=false){
  // 4.15.5: Notes are margin notes. Prefer the space immediately left of the
  // marker, even when that overlaps Breadmaker's chapter/navigation sidebar.
  // Only the browser viewport edge is a reason to fall back to the right.
  const margin=8,gap=9,minUsefulWidth=138,preferredWidth=238;card.classList.remove('hidden');card.style.width='';
  const markerRect=target?.getBoundingClientRect?.()||rect;
  const leftRoom=Math.max(0,markerRect.left-margin-gap);let placeLeft=leftRoom>=minUsefulWidth;
  if(placeLeft)card.style.width=`${Math.floor(Math.min(preferredWidth,leftRoom))}px`;
  let box=card.getBoundingClientRect(),left;
  if(placeLeft)left=markerRect.left-box.width-gap;
  else{
    const rightRoom=Math.max(0,innerWidth-markerRect.right-margin-gap);
    card.style.width=`${Math.floor(Math.min(preferredWidth,Math.max(minUsefulWidth,rightRoom)))}px`;
    box=card.getBoundingClientRect();left=markerRect.right+gap;
  }
  left=Math.max(margin,Math.min(left,innerWidth-box.width-margin));
  const top=Math.max(margin,Math.min(markerRect.top-(box.height-markerRect.height)/2,innerHeight-box.height-margin));
  card.style.left=`${Math.round(left)}px`;card.style.top=`${Math.round(top)}px`;
}
function commentMarkerHoverKey(items,target){return `${target?.dataset?.commentThreads||items.map(x=>x.id).join(',')}@${target?.dataset?.blockId||''}`}
function queueCommentMarkerHover(items,target){
  cancelCommentRailHoverHide();clearTimeout(commentHoverTimer);const key=commentMarkerHoverKey(items,target),card=$('#commentHoverCard');
  if(commentHoverSourceKey!==key){commentHoverSourceKey=key;if(card){card.onmouseleave=null;card.classList.add('hidden')}cancelCommentRailHoverHide();if(commentsPanelOpen())paintSelectedCommentFocus();else clearCommentFocus()}
  commentHoverTimer=setTimeout(()=>{if(commentHoverSourceKey===key)showCommentMarkerHover(items,target,key)},300)
}
function showCommentMarkerHover(items,target,key=commentMarkerHoverKey(items,target)){
  cancelCommentRailHoverHide();if(commentHoverSourceKey!==key)return;const card=$('#commentHoverCard');if(!card||!items.length)return;const heading=items.length>1?`<div class="comment-hover-heading">${escapeHtml(i18nText('notes.count',{count:i18nNumber(items.length)}))}</div>`:'';
  card.innerHTML=heading+items.map(t=>`<button type="button" class="comment-hover-thread" data-hover-comment="${t.id}"><span>${escapeHtml(t.body||'Note')}</span></button>`).join('');
  $$('[data-hover-comment]',card).forEach(button=>{const item=items.find(t=>entityId(t.id)===entityId(button.dataset.hoverComment));button.onmouseenter=()=>paintCommentFocus(noteThreadFromMarker(item));button.onfocus=()=>paintCommentFocus(noteThreadFromMarker(item));button.onclick=()=>{hideCommentHover();openCommentsForThread(entityId(button.dataset.hoverComment))}});
  const interactive=items.length>1;card.classList.toggle('comment-hover-picker',interactive);card.classList.toggle('comment-hover-preview',!interactive);
  // Both preview and picker receive pointer input in 4.15.5 so long Notes can
  // be scrolled. Entering the flyer owns the hover until the pointer leaves it.
  card.onmouseenter=()=>{cancelCommentRailHoverHide();clearTimeout(commentHoverTimer)};
  card.onmouseleave=scheduleCommentRailHoverHide;positionCommentHoverCard(card,target.getBoundingClientRect(),target,interactive);
  if(items.length===1)paintCommentFocus(noteThreadFromMarker(items[0]));
}
function noteMarkerItemsForBlock(blockAnnotations,groups){
  const ids=[...new Set(blockAnnotations.filter(a=>a.annotation_type==='comment'&&a.thread_id&&a.status!=='resolved'&&!a.stale).map(a=>entityId(a.thread_id)))];
  return ids.map(id=>{const anchors=groups.get(id)||[];const first=anchors[0]||blockAnnotations.find(a=>entityId(a.thread_id)===id)||{};return{id,anchors,body:first.body||i18nSource('Note'),chapter:first.chapter_title||visibleChapterById(entityId(first.chapter_id))?.title||'',scene:first.scene_title||''}}).filter(x=>x.anchors.length);
}
function renderNoteGutterMarker(wrap,blockId,blockAnnotations,groups,{chapterId=null}={}){
  const items=noteMarkerItemsForBlock(blockAnnotations,groups);if(!items.length)return;
  const marker=document.createElement('button');marker.type='button';marker.className='note-gutter-marker';marker.contentEditable='false';if(blockId)marker.dataset.blockId=blockId;if(chapterId)marker.dataset.chapterId=chapterId;marker.dataset.commentThreads=items.map(x=>x.id).join(',');marker.setAttribute('aria-label',items.length===1?i18nSource('Open note'):i18nText('notes.choose_count',{count:i18nNumber(items.length)}));
  marker.innerHTML=`<span class="note-gutter-pencil" aria-hidden="true">✎</span><span class="note-gutter-count" aria-hidden="true">${items.length}</span>`;
  marker.onpointerdown=e=>{e.preventDefault();e.stopPropagation()};marker.onmouseenter=()=>queueCommentMarkerHover(items,marker);marker.onmouseleave=()=>{clearTimeout(commentHoverTimer);scheduleCommentRailHoverHide()};marker.onclick=e=>{e.stopPropagation();if(items.length===1){hideCommentHover();openCommentsForThread(items[0].id)}else{commentHoverSourceKey=commentMarkerHoverKey(items,marker);showCommentMarkerHover(items,marker,commentHoverSourceKey)}};wrap.appendChild(marker);
}

function renderEditor(){
  clearSearchPaint();const editor=$('#editor');editor.innerHTML='';$('#chapterTitle').classList.remove('hidden');const map=annotationMap(),noteGroups=visibleNoteThreadGroups();let previousScene=null;
  for(const block of state.chapterData?.blocks||[]){appendSceneGhost(editor,block,state.chapter.id,previousScene);const wrap=makeBlockWrap(block,state.chapter.id);editor.appendChild(wrap);renderBlockAnnotations(wrap,block.id,map,noteGroups);appendSceneEndGhost(editor,block);previousScene=block.scene_id||null;}
  if(!(state.chapterData?.blocks||[]).length)editor.appendChild(makeBlockWrap({id:newTempId(),block_type:'paragraph',semantic_style:'body',content_html:'',text_content:''},state.chapter.id));
  layoutManuscriptImageRows(editor);renderChapterTitleAnnotations($('.chapter-title-row'),state.chapter.id,noteGroups);syncFocusedChapterBookmark();applyOutlineVisibility();countWords();paintMarkerHighlights();scheduleCommentRailLayout();scheduleManuscriptReviewAnnotations();
}

function renderBookEditor(){
  clearSearchPaint();const editor=$('#editor');editor.innerHTML='';$('#chapterTitle').classList.add('hidden');$('#chapterTitleBookmark')?.classList.add('hidden');const map=annotationMap(),noteGroups=visibleNoteThreadGroups(),byChapter=new Map(),fragment=document.createDocumentFragment();
  for(const block of state.bookData?.blocks||[]){if(!byChapter.has(entityId(block.chapter_id)))byChapter.set(entityId(block.chapter_id),[]);byChapter.get(entityId(block.chapter_id)).push(block)}
  for(const ch of state.bookData?.chapters||[]){
    if(chapterMarkersVisible()&&!ch.is_implicit)fragment.appendChild(makeInlineChapterHeading(ch,{allowBoundaryDelete:true,noteGroups}));
    let previousScene=null;const blocks=byChapter.get(entityId(ch.id))||[];
    if(!blocks.length)fragment.appendChild(makeBlockWrap({id:newTempId(),block_type:'paragraph',semantic_style:'body',content_html:'',text_content:''},ch.id));
    for(const block of blocks){appendSceneGhost(fragment,block,ch.id,previousScene,{book:true});const wrap=makeBlockWrap(block,ch.id);fragment.appendChild(wrap);renderBlockAnnotations(wrap,block.id,map,noteGroups);appendSceneEndGhost(fragment,block);previousScene=block.scene_id||null;}
  }
  editor.appendChild(fragment);layoutManuscriptImageRows(editor);applyOutlineVisibility();countWords();paintMarkerHighlights();scheduleCommentRailLayout();scheduleManuscriptReviewAnnotations();
}

function renderSceneEditor(){
  clearSearchPaint();const editor=$('#editor');editor.innerHTML='';$('#chapterTitle').classList.add('hidden');$('#chapterTitleBookmark')?.classList.add('hidden');const map=annotationMap(),noteGroups=visibleNoteThreadGroups(),scene=state.scene||state.sceneData?.scene;
  if(scene){const start=document.createElement('div');start.className='ghost-scene-title scene-view-boundary';start.contentEditable='false';start.dataset.sceneId=scene.id;start.innerHTML=`<span>${escapeHtml(scene.title||'Scene')}</span>`;addSceneBookmarkToHeading(start,scene.id);start.oncontextmenu=e=>{if(e.target.closest('.inline-bookmark-button'))return;e.preventDefault();openSceneMenu(e,scene.id)};editor.appendChild(start)}
  let previousChapter=null;for(const block of state.sceneData?.blocks||[]){if(entityId(block.chapter_id)!==entityId(previousChapter)){const ch=visibleChapterById(block.chapter_id);if(chapterMarkersVisible()&&ch&&!ch.is_implicit)editor.appendChild(makeInlineChapterHeading(ch,{className:'book-chapter-heading scene-focused-chapter-heading'}));previousChapter=entityId(block.chapter_id)}const wrap=makeBlockWrap(block,block.chapter_id);editor.appendChild(wrap);renderBlockAnnotations(wrap,block.id,map,noteGroups)}
  if(scene){const end=document.createElement('div');end.className='ghost-scene-end scene-view-boundary';end.contentEditable='false';end.dataset.sceneId=scene.id;end.innerHTML=`<span>${escapeHtml(i18nText('scene.end_named',{name:scene.title||i18nSource('Scene')}))}</span>`;end.oncontextmenu=e=>{e.preventDefault();openSceneMenu(e,scene.id)};editor.appendChild(end)}
  layoutManuscriptImageRows(editor);applyOutlineVisibility();countWords();paintMarkerHighlights();scheduleCommentRailLayout();scheduleManuscriptReviewAnnotations();
}
function orderedBookBlocks(data=state.bookData){const by=new Map();for(const block of data?.blocks||[]){if(!by.has(entityId(block.chapter_id)))by.set(entityId(block.chapter_id),[]);by.get(entityId(block.chapter_id)).push(block)}const out=[];for(const ch of data?.chapters||[])out.push(...(by.get(entityId(ch.id))||[]));return out}
function renderSceneGapEditor(){
  clearSearchPaint();const editor=$('#editor');editor.innerHTML='';$('#chapterTitle').classList.add('hidden');$('#chapterTitleBookmark')?.classList.add('hidden');const map=annotationMap(),noteGroups=visibleNoteThreadGroups(),ordered=orderedBookBlocks(),anchor=ordered.findIndex(block=>String(block.id)===String(state.sceneGap?.first_block_id));
  if(anchor<0){editor.innerHTML='<div class="scene-view-empty"><b>{{i18n:dynamic.html.text.b.2611c45b}}</b><span>{{i18n:dynamic.html.text.span.a1195ffa}}</span></div>';return}
  let start=anchor,end=anchor;while(start>0&&ordered[start-1].scene_id==null)start--;while(end+1<ordered.length&&ordered[end+1].scene_id==null)end++;const visibleIds=new Set(ordered.slice(start,end+1).map(block=>String(block.id)));
  const begin=document.createElement('div');begin.className='ghost-scene-title scene-view-boundary scene-gap-view-boundary';begin.contentEditable='false';begin.innerHTML='<span>{{i18n:dynamic.html.text.span.2cbdacb4}}</span>';editor.appendChild(begin);
  const byChapter=new Map();for(const block of state.bookData?.blocks||[]){if(!byChapter.has(entityId(block.chapter_id)))byChapter.set(entityId(block.chapter_id),[]);byChapter.get(entityId(block.chapter_id)).push(block)}
  for(const ch of state.bookData?.chapters||[]){const blocks=byChapter.get(entityId(ch.id))||[],hasVisible=blocks.some(block=>visibleIds.has(String(block.id)));if(hasVisible&&chapterMarkersVisible()&&!ch.is_implicit)editor.appendChild(makeInlineChapterHeading(ch,{className:'book-chapter-heading scene-focused-chapter-heading'}));for(const block of blocks){const wrap=makeBlockWrap(block,ch.id);if(!visibleIds.has(String(block.id)))wrap.classList.add('scene-gap-hidden');editor.appendChild(wrap);if(visibleIds.has(String(block.id)))renderBlockAnnotations(wrap,block.id,map,noteGroups)}}
  const finish=document.createElement('div');finish.className='ghost-scene-end scene-view-boundary scene-gap-view-boundary';finish.contentEditable='false';finish.innerHTML='<span>{{i18n:dynamic.html.text.span.9105cc6e}}</span>';editor.appendChild(finish);layoutManuscriptImageRows(editor);applyOutlineVisibility();countWords();paintMarkerHighlights();scheduleCommentRailLayout();scheduleManuscriptReviewAnnotations();
}
async function selectScene(id,save=true,refreshSearch=true){
  if(save)await flushSave();const data=await api(`/api/scenes/${id}/manuscript`);for(const [cid,rev] of Object.entries(data.chapter_revisions||{}))state.chapterRevisions.set(entityId(cid),rev);state.viewMode='scene';state.scene=data.scene;state.sceneData=data;state.sceneGap=null;state.chapter=null;state.chapterData=null;state.bookData=null;
  const meta=sceneById(id);if(meta?.tab_id||meta?.first_tab_id)state.activeDocumentId=entityId(meta.tab_id||meta.first_tab_id);normalizeManuscriptNavMode();syncManuscriptViewControls();resetDirtyState();setSaveState('Saved');renderDocuments();renderChapterTree();renderSceneEditor();$('#documentScroller').scrollTop=0;if(refreshSearch)refreshFindForContext();syncCheckpointPanelContext();syncCheckpointComparisonContext();syncHistoryComparisonContext();scheduleCommentsWorkspaceSync();schedulePersistManuscriptLocation();
}
async function loadBookData(tabId,{preview=null}={}){
  const id=entityId(tabId);if(state.bookCache.has(id))return state.bookCache.get(id);
  if(!state.hostedChapterFirst){const data=await api(`/api/book?tab_id=${id}`);state.bookCache.set(id,data);for(const cid of data.cache_refresh_chapters||[])queueHostedReadCache(cid,data.project?.generation);return data}
  const project=currentProjectIdentity(),editGeneration=state.editGeneration,data=await api(`/api/book?tab_id=${encodeURIComponent(id)}&manifest=true`);
  function active(){if(preview?.cancelled)throw Object.assign(new Error('Full Text loading cancelled'),{cancelled:true})}
  active();preview?.initialize(data.chapters);
  const results=new Array(data.chapters.length);let next=0,completed=0,failure=null;
  async function worker(){
    while(!failure){
      const index=next++;if(index>=data.chapters.length)return;
      try{
        active();const cached=state.chapterCache?.get(`${project}:${data.chapters[index].id}`)||state.chapterData;
        const row=cached?.next_block_offset===null&&String(cached.chapter?.id)===String(data.chapters[index].id)&&String(cached.revision)===String(data.project.generation)
          ?cached:await loadChapterData(data.chapters[index].id);
        if(String(row.revision)!==String(data.project.generation))throw new Error('The manuscript changed while loading. Please reopen Full Text.');
        active();results[index]=row.blocks;completed++;preview?.chapter(index,row.blocks,completed);setSaveState(`Loading Full Text ${completed}/${data.chapters.length}…`);
      }catch(error){failure=error;return}
    }
  }
  try{
    // Bounded chapter reads keep every request below the gateway deadline.
    // Do not publish/cache/render ANY partial book or rebase stale revisions.
    await Promise.all([worker(),worker()]);if(failure)throw failure;active();
    const changed=await api(`/api/book?tab_id=${encodeURIComponent(id)}&manifest=true`,{headers:{'If-None-Match':data.read_tag},acceptNotModified:true});
    active();if(changed!==null||String(currentProjectIdentity())!==String(project)||state.editGeneration!==editGeneration||state.dirty||state.saveInFlight)throw new Error('The manuscript changed while loading. Please reopen Full Text.');
    data.blocks=results.flat();state.bookCache.set(id,data);return data;
  }finally{setSaveState(state.dirty?'Not saved':'Saved')}
}
async function selectSceneGap(gap,save=true,refreshSearch=true){
  if(!gap?.first_block_id)return;if(save)await flushSave();const data=await loadBookData(gap.tab_id);state.viewMode='scene-gap';state.sceneGap={...gap};state.scene=null;state.sceneData=null;state.chapter=null;state.chapterData=null;state.bookData=data;state.activeDocumentId=entityId(gap.tab_id);normalizeManuscriptNavMode();syncManuscriptViewControls();resetDirtyState();setSaveState('Saved');renderDocuments();renderChapterTree();renderSceneGapEditor();$('#documentScroller').scrollTop=0;if(refreshSearch)refreshFindForContext();syncCheckpointPanelContext();syncCheckpointComparisonContext();syncHistoryComparisonContext();scheduleCommentsWorkspaceSync();schedulePersistManuscriptLocation();
}

function findIsOpen(){return !$('#findPopover').classList.contains('hidden')&&!state.find.navigatingMarkup&&!!$('#findInput').value}
function syncFindScopeLabel(){const label=$('#findScopeLabel');if(label&&!$('#findPopover').classList.contains('hidden')&&!state.find.navigatingMarkup)label.textContent=i18nSource(state.viewMode==='book'?'Book':state.viewMode==='scene'?'Scene':state.viewMode==='scene-gap'?'Tab':'Chapter')}
function refreshFindForContext(){if($('#findPopover').classList.contains('hidden')||state.find.navigatingMarkup)return;syncFindScopeLabel();if($('#findInput').value)queueMicrotask(()=>runFind({preserveIndex:false}).catch(error=>console.warn('Find context refresh failed',error)))}
async function selectChapter(id,save=true,refreshSearch=true){
  clearManuscriptSelection();if(save)await flushSave();const data=await loadChapterData(id);state.viewMode='chapter';state.bookData=null;state.scene=null;state.sceneData=null;state.sceneGap=null;state.chapter=data.chapter;state.chapterData=data;state.chapterRevisions.set(entityId(data.chapter.id),data.revision||data.chapter.revision||state.chapterRevisions.get(entityId(data.chapter.id))||'');state.activeDocumentId=data.chapter.tab_id;normalizeManuscriptNavMode();syncManuscriptViewControls();resetDirtyState();$('#chapterTitle').textContent=data.chapter.title;setSaveState('Saved');renderDocuments();renderChapterTree();renderEditor();$('#documentScroller').scrollTop=0;if(refreshSearch)refreshFindForContext();syncCheckpointPanelContext();syncCheckpointComparisonContext();syncHistoryComparisonContext();scheduleCommentsWorkspaceSync();schedulePersistManuscriptLocation();
}
async function loadChapterData(id){
  if(!runtimeSupports('hosted_persistence'))return api(`/api/chapters/${id}`);
  const project=currentProjectIdentity(),editGeneration=state.editGeneration,path=`/api/chapters/${encodeURIComponent(id)}`;
  const cache=state.chapterCache||(state.chapterCache=new Map()),key=`${project}:${id}`,cached=cache.get(key);
  const options=cached?.read_tag?{headers:{'If-None-Match':cached.read_tag},acceptNotModified:true}:undefined;
  const reply=await api(state.hostedChapterFirst?`${path}?block_offset=0&adaptive=true`:path,options);
  const data=reply===null?cached:reply;let offset=reply===null?null:data.next_block_offset;
  while(offset!==null&&offset!==undefined){
    const page=await api(`${path}?block_offset=${offset}&adaptive=true`);
    if(String(page.revision)!==String(data.revision))throw new Error('The manuscript changed while loading. Please reopen the chapter.');
    data.cache_refresh_needed=!!(data.cache_refresh_needed||page.cache_refresh_needed);
    data.blocks.push(...page.blocks);offset=page.next_block_offset;
  }
  if(String(currentProjectIdentity())!==String(project)||state.editGeneration!==editGeneration||state.dirty||state.saveInFlight)throw new Error('The manuscript changed while loading. Please reopen the chapter.');
  data.next_block_offset=null;
  cache.set(key,data);
  if(data.cache_refresh_needed)queueHostedReadCache(id,data.revision);
  while(cache.size>16)cache.delete(cache.keys().next().value);
  return data;
}
let fullTextLoading=false;
function fullTextPreview(){
  const dialog=document.createElement('dialog');dialog.className='full-text-preview';dialog.setAttribute('aria-label',i18nSource('Full Text'));
  const header=document.createElement('header'),title=document.createElement('h2'),status=document.createElement('p'),cancel=document.createElement('button'),body=document.createElement('div');
  title.textContent=i18nSource('Full Text');status.textContent=i18nSource('Loading Full Text…');status.setAttribute('role','status');
  cancel.textContent=i18nSource('Cancel');cancel.type='button';body.className='full-text-preview-body';header.append(title,status,cancel);dialog.append(header,body);
  const slots=[],preview={cancelled:false,
    initialize(chapters){for(const chapter of chapters){const section=document.createElement('section'),heading=document.createElement('h3'),text=document.createElement('div');heading.textContent=chapter.title;section.append(heading,text);body.append(section);slots.push(text)}},
    chapter(index,blocks,completed){const fragment=document.createDocumentFragment();for(const block of blocks){const p=document.createElement('p');p.textContent=block.block_type==='image'?i18nSource('Image'):block.text_content||'';fragment.append(p)}slots[index].replaceChildren(fragment);status.textContent=`${completed}/${slots.length} · ${i18nSource('Loading Full Text…')}`},
    close(){dialog.close();dialog.remove()}};
  const stop=()=>{preview.cancelled=true;preview.close()};cancel.onclick=stop;dialog.addEventListener('cancel',e=>{e.preventDefault();stop()});
  // Modal, text-only preview: no partial editor DOM/state or manuscript actions.
  dialog.addEventListener('keydown',e=>e.stopPropagation());document.body.append(dialog);dialog.showModal();cancel.focus();return preview;
}
async function openWholeBook(save=true,refreshSearch=true){
  if(fullTextLoading)return;if(save)await flushSave();if(!state.activeDocumentId)return;
  fullTextLoading=true;const preview=runtimeSupports('hosted_persistence')&&state.hostedChapterFirst?fullTextPreview():null;
  try{const data=await loadBookData(state.activeDocumentId,{preview});if(preview?.cancelled)return;normalizeManuscriptNavMode();syncManuscriptViewControls();state.viewMode='book';state.bookData=data;for(const ch of data.chapters||[])state.chapterRevisions.set(entityId(ch.id),ch.revision||state.chapterRevisions.get(entityId(ch.id))||'');state.scene=null;state.sceneData=null;state.sceneGap=null;state.chapter=null;state.chapterData=null;resetDirtyState();setSaveState('Saved');renderDocuments();renderChapterTree();renderBookEditor();$('#documentScroller').scrollTop=0;if(refreshSearch)refreshFindForContext();syncCheckpointPanelContext();syncCheckpointComparisonContext();syncHistoryComparisonContext();scheduleCommentsWorkspaceSync();schedulePersistManuscriptLocation();
  }catch(error){if(!error.cancelled)throw error}finally{preview?.close();fullTextLoading=false}
}
function captureManuscriptViewport(){
  const scroller=$('#documentScroller');if(!scroller)return null;
  const viewport=scroller.getBoundingClientRect(),candidates=[...scroller.querySelectorAll('#editor > .manuscript-block-wrap,#editor > .manuscript-image-row > .manuscript-block-wrap,#editor > .book-chapter-heading')];
  const anchor=candidates.find(el=>el.getBoundingClientRect().bottom>viewport.top+1)||null;let key=null;
  if(anchor?.classList.contains('manuscript-block-wrap'))key={kind:'block',id:String(anchor.dataset.blockId||'')};
  else if(anchor?.classList.contains('book-chapter-heading'))key={kind:'chapter',id:String(anchor.dataset.chapterId||'')};
  return {scrollTop:scroller.scrollTop,key,chapter_id:anchor?.dataset.chapterId||state.chapter?.id||null,offset:anchor?anchor.getBoundingClientRect().top-viewport.top:null};
}
function restoreManuscriptViewport(snapshot){
  const scroller=$('#documentScroller');if(!snapshot||!scroller)return;scroller.scrollTop=Math.max(0,snapshot.scrollTop||0);
  if(!snapshot.key||snapshot.offset===null)return;
  const selector=snapshot.key.kind==='block'?`.manuscript-block-wrap[data-block-id="${CSS.escape(snapshot.key.id)}"]`:`.book-chapter-heading[data-chapter-id="${CSS.escape(snapshot.key.id)}"]`,anchor=$(selector);
  if(!anchor)return;const viewport=scroller.getBoundingClientRect(),delta=(anchor.getBoundingClientRect().top-viewport.top)-snapshot.offset;if(Math.abs(delta)>.5)scroller.scrollTop=Math.max(0,scroller.scrollTop+delta);
}
function manuscriptLocationPayload(){
  if(!state.activeDocumentId)return null;const viewport=captureManuscriptViewport();const value={tab_id:entityId(state.activeDocumentId),view:state.viewMode,viewport};
  if(state.viewMode==='chapter'&&state.chapter)value.chapter_id=entityId(state.chapter.id);
  else if(state.viewMode==='scene'&&state.scene)value.scene_id=entityId(state.scene.id);
  else if(state.viewMode==='scene-gap'&&state.sceneGap)value.first_block_id=String(state.sceneGap.first_block_id||'');
  return value;
}
function schedulePersistManuscriptLocation(){
  if(state.restoringManuscriptLocation)return;clearTimeout(state.manuscriptLocationSaveTimer);state.manuscriptLocationSaveTimer=setTimeout(()=>{const value=manuscriptLocationPayload();if(!value)return;api('/api/ui-state/manuscript-last-location',{method:'PUT',body:JSON.stringify({value})}).catch(error=>console.warn('Could not persist manuscript location',error))},180);
}
async function restoreManuscriptLocation(value){
  if(!value||typeof value!=='object')return false;const tab=state.documents.find(d=>entityId(d.id)===entityId(value.tab_id));if(!tab)return false;state.restoringManuscriptLocation=true;
  try{
    state.activeDocumentId=entityId(tab.id);
    if(value.view==='chapter'&&value.chapter_id&&visibleChapters(tab.id).some(ch=>entityId(ch.id)===entityId(value.chapter_id)))await selectChapter(entityId(value.chapter_id),false,false);
    else if(value.view==='scene'&&value.scene_id&&state.scenes.some(sc=>entityId(sc.id)===entityId(value.scene_id)&&entityId(sc.tab_id)===entityId(tab.id)))await selectScene(entityId(value.scene_id),false,false);
    else if(value.view==='scene-gap'&&value.first_block_id){const gap=state.sceneGaps.find(g=>entityId(g.tab_id)===entityId(tab.id)&&String(g.first_block_id)===String(value.first_block_id));if(!gap)return false;await selectSceneGap(gap,false,false)}
    else if(value.view==='book')await openWholeBook(false,false);
    else return false;
    if(value.viewport)requestAnimationFrame(()=>restoreManuscriptViewport(value.viewport));return true;
  }finally{state.restoringManuscriptLocation=false}
}
async function restoreLastManuscriptLocation(preloaded=null){
  try{const value=preloaded&&Object.prototype.hasOwnProperty.call(preloaded,'manuscript-last-location')?preloaded['manuscript-last-location']:(await api('/api/ui-state/manuscript-last-location')).value;if(await restoreManuscriptLocation(value))return true}catch(error){console.warn('Could not restore last manuscript location',error)}return false;
}
function pulseBookmarkTarget(element){if(!element)return;element.classList.remove('bookmark-jump-pulse');requestAnimationFrame(()=>{element.classList.add('bookmark-jump-pulse');setTimeout(()=>element.classList.remove('bookmark-jump-pulse'),1100)})}
function bookmarkStartWrap(bookmark=state.bookmark){if(!bookmark?.start_block_id)return null;return $(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(bookmark.start_block_id))}"]`)}
function revealBookmarkWrap(bookmark=state.bookmark){const wrap=bookmarkStartWrap(bookmark);if(!wrap)return false;wrap.scrollIntoView({behavior:'auto',block:'center'});pulseBookmarkTarget(wrap);return true}
async function goToBookmark({activateView=true,silent=false}={}){
  const b=state.bookmark;if(!b){if(!silent)toast('No bookmark set');return false}if(activateView)setView('manuscript');const startingMode=state.viewMode;state.restoringManuscriptLocation=true;
  try{
    const scene=b.scene_id?sceneById(b.scene_id):(b.owner_scene_id?sceneById(b.owner_scene_id):null);
    const chapterId=entityId(b.target_type==='chapter'?b.chapter_id:(b.owner_chapter_id||scene?.first_chapter_id||0));
    const tabId=entityId(b.tab_id||scene?.first_tab_id||scene?.tab_id||0);
    // Full Text is a deliberate reading/editing context. A bookmark jump changes
    // location, not that context. The same rule applies to focused Chapters.
    if(startingMode==='book'&&tabId&&state.documents.some(d=>entityId(d.id)===tabId)){
      state.activeDocumentId=tabId;await openWholeBook(false,false);await new Promise(resolve=>requestAnimationFrame(resolve));
      if(b.target_type==='range')return revealBookmarkWrap(b);
      if(b.target_type==='chapter'){scrollBookChapter(entityId(b.chapter_id));pulseBookmarkTarget($(`.book-chapter-heading[data-chapter-id="${entityId(b.chapter_id)}"]`)||$(`.manuscript-block-wrap[data-chapter-id="${entityId(b.chapter_id)}"]`));return true}
      if(b.target_type==='scene'&&scene){const wrap=$(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(scene.first_block_id||''))}"]`);wrap?.scrollIntoView({behavior:'auto',block:'center'});pulseBookmarkTarget(wrap||$(`.ghost-scene-title[data-scene-id="${scene.id}"]`));return !!(wrap||$(`.ghost-scene-title[data-scene-id="${scene.id}"]`))}
    }
    if(startingMode==='chapter'&&chapterId&&!!visibleChapterById(chapterId)){
      await selectChapter(chapterId,false,false);await new Promise(resolve=>requestAnimationFrame(resolve));
      if(b.target_type==='range')return revealBookmarkWrap(b);
      if(b.target_type==='scene'&&scene?.first_block_id){const wrap=$(`.manuscript-block-wrap[data-block-id="${CSS.escape(String(scene.first_block_id))}"]`);if(wrap){wrap.scrollIntoView({behavior:'auto',block:'center'});pulseBookmarkTarget(wrap);return true}}
      pulseBookmarkTarget($('#chapterTitle'));return true
    }
    if(startingMode==='scene'&&scene){await selectScene(scene.id,false,false);await new Promise(resolve=>requestAnimationFrame(resolve));if(b.target_type==='range'&&revealBookmarkWrap(b))return true;pulseBookmarkTarget($('.scene-view-boundary'));return true}
    if(b.target_type==='chapter'&&b.chapter_id&&!!visibleChapterById(b.chapter_id)){await selectChapter(entityId(b.chapter_id),false,false);pulseBookmarkTarget($('#chapterTitle'));return true}
    if(b.target_type==='scene'&&scene){await selectScene(scene.id,false,false);pulseBookmarkTarget($('.scene-view-boundary'));return true}
    if(b.target_type==='range'&&b.start_block_id&&tabId&&state.documents.some(d=>entityId(d.id)===tabId)){
      state.activeDocumentId=tabId;await openWholeBook(false,false);await new Promise(resolve=>requestAnimationFrame(resolve));return revealBookmarkWrap(b)
    }
    return false;
  }finally{state.restoringManuscriptLocation=false;schedulePersistManuscriptLocation()}
}
async function preserveManuscriptViewport(action){const snapshot=captureManuscriptViewport();try{return await action()}finally{restoreManuscriptViewport(snapshot)}}
$('#documentScroller')?.addEventListener('scroll',()=>{if(currentView()==='manuscript')schedulePersistManuscriptLocation()},{passive:true});
async function reloadManuscriptView({fresh=true}={}){
  const viewport=captureManuscriptViewport();
  // Revalidate the exact cached snapshot, never attach a new revision to old text.
  // An unchanged book needs one authorized generation check, not all body reads.
  let revalidated=false;
  if(fresh&&runtimeSupports('hosted_persistence')&&state.viewMode==='book'&&state.bookData?.read_tag&&state.bookCache.get(entityId(state.activeDocumentId))===state.bookData){
    const updated=await api(`/api/book?tab_id=${encodeURIComponent(state.activeDocumentId)}${state.hostedChapterFirst?'&manifest=true':''}`,{headers:{'If-None-Match':state.bookData.read_tag},acceptNotModified:true});
    if(updated===null){restoreManuscriptViewport(viewport);return}
    if(state.hostedChapterFirst)state.bookCache.delete(entityId(state.activeDocumentId));
    else state.bookCache.set(entityId(state.activeDocumentId),updated);
    revalidated=true;
  }
  // Mutations which cleared the cache still force a complete fresh snapshot.
  if(fresh&&!revalidated&&state.activeDocumentId)state.bookCache.delete(entityId(state.activeDocumentId));
  if(state.viewMode==='book')await openWholeBook(false,false);else if(state.viewMode==='scene'&&state.scene)await selectScene(state.scene.id,false,false);else if(state.viewMode==='scene-gap'&&state.sceneGap)await selectSceneGap(state.sceneGap,false,false);else if(state.chapter)await selectChapter(state.chapter.id,false,false);
  restoreManuscriptViewport(viewport);
}
function scheduleCommentsWorkspaceSync(){if(!commentsPanelOpen())return;commentsWorkspace.dirty=true;queueMicrotask(()=>reloadCommentsWorkspace(commentsWorkspace.selectedId).catch(error=>console.warn('Notes refresh failed',error)))}

function manuscriptProseForStorage(prose){
  if(!prose)return{html:'',text:''};const clone=prose.cloneNode(true);clone.querySelectorAll('[data-manuscript-review-ghost]').forEach(node=>node.remove());clone.querySelectorAll('[data-manuscript-review-inline]').forEach(node=>{const parent=node.parentNode;if(!parent)return;while(node.firstChild)parent.insertBefore(node.firstChild,node);node.remove();parent.normalize()});return{html:clone.innerHTML,text:manuscriptReviewBaseText(clone)}
}
function serializeWrap(wrap){
  const image=wrap.querySelector('.manuscript-image'),prose=wrap.querySelector('.manuscript-block'),clean=prose?manuscriptProseForStorage(prose):{html:'',text:''};
  return {id:wrap.dataset.blockId.startsWith('tmp_')?null:wrap.dataset.blockId,block_type:image?'image':'paragraph',semantic_style:wrap.dataset.style||prose?.dataset.semanticStyle||'body',content_html:clean.html,text_content:clean.text,image_path:image?(wrap.dataset.imagePath||image.getAttribute('src')||''):'',image_width:image?Math.max(5,Math.min(100,Number(wrap.dataset.imageWidth)||100)):100,image_footer:image?(wrap.dataset.imageFooter||''):'',image_description:image?(wrap.dataset.imageDescription||''):'',image_row_id:image?(wrap.dataset.imageRowId||''):'',scene_id:wrap.dataset.sceneId?entityId(wrap.dataset.sceneId):null};
}
function serializeSceneWrap(wrap){return {...serializeWrap(wrap),chapter_id:entityId(wrap.dataset.chapterId)}}
function wrapsForChapter(chapterId){return $$('.manuscript-block-wrap').filter(w=>entityId(w.dataset.chapterId)===entityId(chapterId));}
function localChapterSnapshot(chapterId){const wraps=wrapsForChapter(chapterId);if(!wraps.length)return null;return{id:chapterId,title:visibleChapterTitleText(chapterId),blocks:wraps.map(serializeWrap)}}
function localSceneSnapshot(){if(state.viewMode!=='scene'||!state.scene)return null;return{id:state.scene.id,title:state.scene.title,blocks:$$('.manuscript-block-wrap').map(serializeSceneWrap)}}
