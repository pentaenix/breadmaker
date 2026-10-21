// -------------------- manuscript editing / selection --------------------
function offsetIn(root,node,offset){try{const range=document.createRange();range.selectNodeContents(root);range.setEnd(node,offset);return range.toString().length}catch{return 0}}
function selectionElement(){const selection=getSelection();let node=selection?.anchorNode;if(!node)return null;return node.nodeType===Node.ELEMENT_NODE?node:node.parentElement}
function activeManuscriptProse(){return selectionElement()?.closest?.('.manuscript-block')||null}
function activeManuscriptWrap(){return activeManuscriptProse()?.closest('.manuscript-block-wrap')||null}
function focusedChapterSelectionCoversStructure(range){
  if(state.viewMode!=='chapter'||!state.chapter||!range)return false;const title=$('#chapterTitle'),prose=$$('.manuscript-block');if(!title||!prose.length)return false;
  try{return range.intersectsNode(title)&&prose.every(node=>range.intersectsNode(node))}catch{return false}
}
function focusedChapterSelectionIsTitleOnly(range){
  if(state.viewMode!=='chapter'||!state.chapter||!range)return false;const title=$('#chapterTitle');if(!title)return false;
  try{return range.intersectsNode(title)&&!$$('.manuscript-block').some(node=>range.intersectsNode(node))}catch{return false}
}
function selectedBookChapterHeadingIds(range){
  if(state.viewMode!=='book'||!range)return[];const out=[];for(const heading of $$('.book-chapter-heading')){try{if(range.intersectsNode(heading))out.push(entityId(heading.dataset.chapterId))}catch{}}return [...new Set(out.filter(Boolean))]
}
function bookChapterSelectionIsTitleOnly(range){
  if(state.viewMode!=='book'||!range)return false;const element=node=>node?.nodeType===Node.ELEMENT_NODE?node:node?.parentElement,start=element(range.startContainer)?.closest?.('.book-chapter-title'),end=element(range.endContainer)?.closest?.('.book-chapter-title');return !!start&&start===end;
}
function adjacentManuscriptWrap(wrap,direction){
  let node=direction<0?wrap.previousElementSibling:wrap.nextElementSibling;while(node&&!node.classList.contains('manuscript-block-wrap'))node=direction<0?node.previousElementSibling:node.nextElementSibling;return node||null
}
function rangeTouchesManuscriptSurface(range){if(!range)return false;const editor=$('#editor'),title=$('#chapterTitle'),paper=$('#manuscriptPaper');if(editor?.contains(range.commonAncestorContainer))return true;if(state.viewMode==='chapter'&&paper?.contains(range.commonAncestorContainer)){try{return !!title&&range.intersectsNode(title)}catch{return false}}return false}
function placeCaret(element,offset){element.focus();const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);let node,remain=offset;while((node=walker.nextNode())){if(remain<=node.nodeValue.length){const range=document.createRange();range.setStart(node,remain);range.collapse(true);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);return}remain-=node.nodeValue.length}const range=document.createRange();range.selectNodeContents(element);range.collapse(false);const selection=getSelection();selection.removeAllRanges();selection.addRange(range)}

function selectionItems(rangeOverride=null){
  const selection=getSelection(),editor=$('#editor');const range=rangeOverride||((selection?.rangeCount&&!selection.isCollapsed)?selection.getRangeAt(0):null);if(!range||range.collapsed)return[];if(!rangeTouchesManuscriptSurface(range))return[];const out=[];
  for(const wrap of $$('.manuscript-block-wrap')){
    const prose=wrap.querySelector('.manuscript-block');if(!prose)continue;let intersects=false;try{intersects=range.intersectsNode(prose)}catch{}if(!intersects)continue;const text=prose.innerText;let start=0,end=text.length;if(prose.contains(range.startContainer))start=offsetIn(prose,range.startContainer,range.startOffset);if(prose.contains(range.endContainer))end=offsetIn(prose,range.endContainer,range.endOffset);start=Math.max(0,Math.min(start,text.length));end=Math.max(start,Math.min(end,text.length));if(end>start)out.push({block_id:wrap.dataset.blockId,chapter_id:entityId(wrap.dataset.chapterId),start_offset:start,end_offset:end,selected_text:text.slice(start,end),prefix_context:text.slice(Math.max(0,start-45),start),suffix_context:text.slice(end,end+45),scene_id:wrap.dataset.sceneId?entityId(wrap.dataset.sceneId):null,wrap});
  }
  return out;
}
function selectedChapterHeader(rangeOverride=null,itemsOverride=null){
  const selection=getSelection(),range=rangeOverride||((selection?.rangeCount&&!selection.isCollapsed)?selection.getRangeAt(0):null);if(!range||range.collapsed)return null;const items=itemsOverride||selectionItems(range);if(items.length)return null;const candidates=[];
  if(state.viewMode==='chapter'&&state.chapter){const title=$('#chapterTitle');if(title&&!title.classList.contains('hidden'))candidates.push({chapter_id:entityId(state.chapter.id),element:title})}
  else if(state.viewMode==='book')for(const heading of $$('.book-chapter-heading'))candidates.push({chapter_id:entityId(heading.dataset.chapterId),element:heading});
  const hits=candidates.filter(candidate=>{try{return range.intersectsNode(candidate.element)}catch{return false}});return hits.length===1?hits[0]:null;
}
function chapterHeaderSelectionItem(range,header){
  if(!range||!header?.chapter_id)return null;const chapter=visibleChapterById(header.chapter_id),root=header.element?.matches?.('.book-chapter-heading')?(header.element.querySelector('.book-chapter-title')||header.element):header.element;
  if(!chapter||!root)return null;const text=String(root.textContent||chapter.title||'');let start=0,end=text.length;
  try{if(root.contains(range.startContainer))start=offsetIn(root,range.startContainer,range.startOffset);if(root.contains(range.endContainer))end=offsetIn(root,range.endContainer,range.endOffset)}catch{}
  start=Math.max(0,Math.min(start,text.length));end=Math.max(start,Math.min(end,text.length));if(end<=start)return null;
  return {block_id:null,chapter_id:entityId(header.chapter_id),start_offset:start,end_offset:end,selected_text:text.slice(start,end),prefix_context:text.slice(Math.max(0,start-45),start),suffix_context:text.slice(end,end+45),scene_id:null,wrap:null,header:true};
}
function syncSelectionToolbarMode(headerMode=false){
  const toolbar=$('#selectionToolbar');if(!toolbar)return;$$('[data-selection-command]',toolbar).forEach(button=>button.classList.toggle('hidden',headerMode));$$('[data-selection-action]',toolbar).forEach(button=>{const allowed=!headerMode||['style','highlight','comment','bookmark','more'].includes(button.dataset.selectionAction);button.classList.toggle('hidden',!allowed)});$$('.selection-separator',toolbar).forEach(separator=>separator.classList.toggle('hidden',headerMode));
}

function hideSelectionHighlightPicker(){$('#selectionHighlightPicker')?.classList.add('hidden')}
function hideSelectionToolbar(){$('#selectionToolbar')?.classList.add('hidden')}
function restoreLastManuscriptRange(){const range=state.lastManuscriptRange;if(!range)return false;try{const selection=getSelection();selection.removeAllRanges();selection.addRange(range.cloneRange());return true}catch{return false}}
function restoreSelectionToolbarRange(){const range=state.selectionToolbarRange||state.lastManuscriptRange;if(!range)return false;try{const selection=getSelection();selection.removeAllRanges();selection.addRange(range.cloneRange());state.lastManuscriptRange=range.cloneRange();return true}catch{return false}}
function positionSelectionToolbar(range){const toolbar=$('#selectionToolbar');if(!toolbar||range.collapsed||state.manuscriptPointerSelecting){hideSelectionToolbar();return}let header=null;try{state.selectionToolbarRange=range.cloneRange();state.selectionToolbarItems=selectionItems(range);header=selectedChapterHeader(range,state.selectionToolbarItems);state.selectionToolbarHeaderChapterId=header?.chapter_id||null;if(header){const item=chapterHeaderSelectionItem(range,header);state.selectionToolbarItems=item?[item]:[]}}catch{state.selectionToolbarItems=[];state.selectionToolbarHeaderChapterId=null}if(!state.selectionToolbarItems.length&&!header){state.selectionToolbarRange=null;hideSelectionToolbar();return}syncSelectionToolbarMode(!!header);if(!header)syncSelectionSceneAction(state.selectionToolbarItems);syncSelectionBookmarkAction(state.selectionToolbarItems,header?.chapter_id||null);const rect=range.getBoundingClientRect();if(!rect||(!rect.width&&!rect.height)){hideSelectionToolbar();return}
  // Measure and position while non-interactive, then reveal at the final coordinates.
  // Revealing before a requestAnimationFrame position pass lets a fast pointer enter the
  // old coordinates and immediately leave when the toolbar jumps, cancelling delayed UI.
  toolbar.style.visibility='hidden';toolbar.classList.remove('hidden');const box=toolbar.getBoundingClientRect(),margin=8;let left=rect.left+(rect.width-box.width)/2;left=Math.max(margin,Math.min(left,innerWidth-box.width-margin));let top=rect.top-box.height-8;if(top<margin)top=Math.min(innerHeight-box.height-margin,rect.bottom+8);toolbar.style.left=`${left}px`;toolbar.style.top=`${top}px`;toolbar.style.visibility='';}
function showSelectionToolbarAfterPointer(){
  state.manuscriptPointerSelecting=false;
  const selection=getSelection();if(!selection?.rangeCount||selection.isCollapsed)return hideSelectionToolbar();
  const range=selection.getRangeAt(0);if(rangeTouchesManuscriptSurface(range)){try{state.lastManuscriptRange=range.cloneRange()}catch{}if(state.activeMarkupMarkerId||state.activeMarkupClear){hideSelectionToolbar();applyActiveMarkupSelection().catch(error=>toast(error.message||String(error)))}else positionSelectionToolbar(range)}
}
function currentStyleWraps(){
  const liveItems=selectionItems(),savedItems=liveItems.length?[]:selectionItems(state.lastManuscriptRange);const items=liveItems.length?liveItems:savedItems;return items.length?[...new Set(items.map(i=>i.wrap))]:(activeManuscriptWrap()?[activeManuscriptWrap()]:[]);
}
async function createChapterFromStyle(wrap){
  if(!wrap?.querySelector('.manuscript-block'))return;const title=wrap.querySelector('.manuscript-block').innerText.trim()||i18nSource('Untitled Chapter'),sourceChapter=entityId(wrap.dataset.chapterId),wasBook=state.viewMode==='book';let chapter;
  await flushSave();
  await withStructuralUndo(`Create chapter: ${title}`,async()=>{chapter=await api(`/api/chapters/${sourceChapter}/split`,{method:'POST',body:JSON.stringify({block_id:wrap.dataset.blockId,title})})},{view:'manuscript',chapterId:sourceChapter});
  state.manuscriptCollapsedHeadings.delete(String(wrap.dataset.blockId));await refresh(false);
  if(wasBook){await openWholeBook(false);requestAnimationFrame(()=>scrollBookChapter(chapter.id))}else await selectChapter(chapter.id,false);
  hideSelectionToolbar();toast(i18nText('entity.created',{title:chapter.title}));
}
async function applyWritingStyle(style){
  if(!['body','chapter','heading2','heading3','heading4'].includes(style))return;const wraps=currentStyleWraps();if(!wraps.length)return;restoreLastManuscriptRange();const chapters=[...new Set(wraps.map(w=>entityId(w.dataset.chapterId)))];if(chapters.length>1){toast('Text styles can be changed within one chapter at a time.');return}
  if(style==='chapter'){if(wraps.length!==1){toast('Select one paragraph to create a Chapter.');return}await createChapterFromStyle(wraps[0]);return}
  for(const wrap of wraps)await setBlockStyle(wrap,style);hideSelectionToolbar();
}
async function convertChapterHeaderToStyle(chapterId,style){
  if(!['body','heading2','heading3','heading4'].includes(style))return;const chapter=visibleChapterById(chapterId);if(!chapter)return;await dissolveChapterBoundary(chapterId,{stayInBook:state.viewMode==='book',titleStyle:style});
}

$('#manuscriptPaper').addEventListener('pointerdown',event=>{if(event.isPrimary!==false&&event.button===0){state.manuscriptPointerSelecting=true;state.activeMarkupSelectionKey=null;state.selectionToolbarRange=null;state.selectionToolbarItems=[];state.selectionToolbarHeaderChapterId=null;hideSelectionToolbar();hideSelectionHighlightPicker()}});
// Never consume the browser's native double-click gesture. Chromium/Safari should
// own word selection; Breadmaker only reacts after that native range exists.
$('#manuscriptPaper').addEventListener('dblclick',event=>{if(!event.target.closest?.('.manuscript-block'))return;state.manuscriptPointerSelecting=false;requestAnimationFrame(()=>{const selection=getSelection();if(!selection?.rangeCount||selection.isCollapsed)return;const range=selection.getRangeAt(0);if(!rangeTouchesManuscriptSurface(range))return;try{state.lastManuscriptRange=range.cloneRange()}catch{}positionSelectionToolbar(range)})});
window.addEventListener('pointerup',showSelectionToolbarAfterPointer,{passive:true});
window.addEventListener('pointercancel',showSelectionToolbarAfterPointer,{passive:true});
document.addEventListener('selectionchange',()=>{const selection=getSelection();if(!selection.rangeCount){hideSelectionToolbar();syncCutsPanelContext();syncCommentsPanelContext();return}const range=selection.getRangeAt(0);if(rangeTouchesManuscriptSurface(range)){if(!(state.selectionToolbarInteracting&&selection.isCollapsed)){try{state.lastManuscriptRange=range.cloneRange()}catch{}}if(selection.isCollapsed||state.manuscriptPointerSelecting||!$('#selectionHighlightPicker')?.classList.contains('hidden'))hideSelectionToolbar();else positionSelectionToolbar(range)}else if(!selectionElement()?.closest?.('#selectionToolbar,#selectionHighlightPicker'))hideSelectionToolbar();syncCutsPanelContext();syncCommentsPanelContext();});
$('#documentScroller').addEventListener('scroll',()=>{hideSelectionToolbar();hideSelectionHighlightPicker();hideMarkupHover()},{passive:true});window.addEventListener('resize',()=>{hideSelectionToolbar();hideSelectionHighlightPicker();hideMarkupHover()},{passive:true});


document.addEventListener('keydown',event=>{if(event.key!=='Escape'||(!state.activeMarkupMarkerId&&!state.activeMarkupClear))return;const target=event.target;if(target?.matches?.('input,textarea,select'))return;state.activeMarkupMarkerId=null;state.activeMarkupClear=false;state.activeMarkupSelectionKey=null;renderMarkers();toast(i18nSource('Marker tool off'))});
$('#manuscriptPaper').addEventListener('beforeinput',event=>{
  if(event.inputType==='historyUndo'||event.inputType==='historyRedo'){if(event.cancelable)event.preventDefault();requestHistory(event.inputType==='historyUndo'?'undo':'redo','beforeinput');return}
  const selection=getSelection(),range=selection?.rangeCount&&!selection.isCollapsed?selection.getRangeAt(0):null,titleOnlySelection=range&&focusedChapterSelectionIsTitleOnly(range);
  if(range&&!titleOnlySelection&&focusedChapterSelectionCoversStructure(range)&&(event.inputType.startsWith('insert')||event.inputType.startsWith('delete'))){
    if(event.cancelable)event.preventDefault();
    if(event.inputType.startsWith('delete'))deleteFocusedChapterSelection(state.chapter.id).catch(error=>toast(error.message||String(error)));
    else toast('The whole Chapter is selected. Delete removes the Chapter and its contents; select prose only to replace its text.',4200);
    return;
  }
  if(range&&state.viewMode==='book'&&event.inputType.startsWith('delete')&&!bookChapterSelectionIsTitleOnly(range)){const headings=selectedBookChapterHeadingIds(range),items=selectionItems(range);if(headings.length===1&&!items.length){if(event.cancelable)event.preventDefault();dissolveChapterBoundary(headings[0],{stayInBook:true}).catch(error=>toast(error.message||String(error)));return}}
  const selected=selectionItems();let chapterIds=selected.map(x=>x.chapter_id);if(!chapterIds.length){const heading=event.target.closest?.('.book-chapter-heading'),wrap=event.target.closest?.('.manuscript-block-wrap')||activeManuscriptWrap();if(heading?.dataset.chapterId)chapterIds=[entityId(heading.dataset.chapterId)];else if(wrap)chapterIds=[entityId(wrap.dataset.chapterId)]}
  const unique=[...new Set(chapterIds)];if(unique.length>1&&(event.inputType.startsWith('insert')||event.inputType.startsWith('delete'))){if(event.cancelable)event.preventDefault();toast('Cross-Chapter prose stays protected. Delete a Chapter header itself, or Backspace at the start of its first paragraph, to remove that Chapter boundary.');return}
  recordUndo(chapterIds,false);
});

$('#manuscriptPaper').addEventListener('keydown',event=>{
  const prose=event.target.closest?.('.manuscript-block')||activeManuscriptProse();if(!prose)return;
  const selected=selectionItems(),crossChapter=new Set(selected.map(x=>x.chapter_id)).size>1;
  if(crossChapter&&['Enter','Backspace','Delete','Tab'].includes(event.key)){event.preventDefault();toast('The chapter boundary is structural. Use selection actions across chapters, but edit prose within one chapter.');return}
  if(event.key==='Tab'){event.preventDefault();document.execCommand('insertText',false,'\t');markDirty(event);return}
  if((event.key==='Backspace'||event.key==='Delete')&&!event.metaKey&&!event.ctrlKey&&!event.altKey){
    const selection=getSelection();if(selection?.rangeCount&&selection.isCollapsed){const range=selection.getRangeAt(0);if(prose.contains(range.startContainer)){const offset=offsetIn(prose,range.startContainer,range.startOffset),wrap=prose.closest('.manuscript-block-wrap'),textLength=prose.innerText.length,direction=event.key==='Backspace'?-1:1;let adjacent=wrap;do{adjacent=direction<0?adjacent?.previousElementSibling:adjacent?.nextElementSibling}while(adjacent&&(adjacent.classList.contains('ghost-scene-title')||adjacent.classList.contains('ghost-scene-end')||adjacent.classList.contains('book-chapter-heading')));const atEdge=event.key==='Backspace'?offset===0:offset>=textLength;if(atEdge&&adjacent?.classList.contains('manuscript-image-wrap')&&entityId(adjacent.dataset.chapterId)===entityId(wrap.dataset.chapterId)){event.preventDefault();event.stopPropagation();removeManuscriptImageWrap(adjacent,{caretProse:prose,caretOffset:offset});return}}}
  }
  if(state.viewMode==='book'&&chapterMarkersVisible()&&(event.key==='Backspace'||event.key==='Delete')){
    const selection=getSelection();if(selection?.rangeCount&&selection.isCollapsed){const range=selection.getRangeAt(0),offset=offsetIn(prose,range.startContainer,range.startOffset),wrap=prose.closest('.manuscript-block-wrap'),textLength=prose.innerText.length;
      if(event.key==='Backspace'&&offset===0){const previous=adjacentManuscriptWrap(wrap,-1);if(!previous||entityId(previous.dataset.chapterId)!==entityId(wrap.dataset.chapterId)){const chapter=visibleChapterById(entityId(wrap.dataset.chapterId));if(chapter){event.preventDefault();dissolveChapterBoundary(chapter.id,{stayInBook:true}).catch(error=>toast(error.message||String(error)));return}}}
      if(event.key==='Delete'&&offset>=textLength){const next=adjacentManuscriptWrap(wrap,1);if(next&&entityId(next.dataset.chapterId)!==entityId(wrap.dataset.chapterId)){const chapter=visibleChapterById(entityId(next.dataset.chapterId));if(chapter){event.preventDefault();dissolveChapterBoundary(chapter.id,{stayInBook:true}).catch(error=>toast(error.message||String(error)));return}}}
    }
  }
  if(event.key==='Enter'&&!event.shiftKey){
    event.preventDefault();const wrap=prose.closest('.manuscript-block-wrap');recordUndo([entityId(wrap.dataset.chapterId)],true);const selection=getSelection();if(!selection.rangeCount)return;let range=selection.getRangeAt(0);if(!range.collapsed){document.execCommand('delete');range=selection.rangeCount?selection.getRangeAt(0):range}if(!prose.contains(range.startContainer))return;
    const tail=range.cloneRange();tail.setEnd(prose,prose.childNodes.length);const fragment=tail.extractContents(),holder=document.createElement('div');holder.appendChild(fragment);const nextStyle=outlineStyleLevel(wrap.dataset.style)?'body':(wrap.dataset.style||'body');const newWrap=makeBlockWrap({id:newTempId(),block_type:'paragraph',semantic_style:nextStyle,content_html:holder.innerHTML,text_content:holder.innerText||holder.textContent||'',scene_id:wrap.dataset.sceneId?entityId(wrap.dataset.sceneId):null},entityId(wrap.dataset.chapterId));wrap.after(newWrap);applyOutlineVisibility();placeCaret(newWrap.querySelector('.manuscript-block'),0);markDirty(entityId(wrap.dataset.chapterId));return;
  }
  if(event.key==='Backspace'){
    const selection=getSelection();if(selection.rangeCount&&selection.isCollapsed){const range=selection.getRangeAt(0),offset=offsetIn(prose,range.startContainer,range.startOffset);if(offset===0){const wrap=prose.closest('.manuscript-block-wrap');let previous=wrap.previousElementSibling;while(previous&&(previous.classList.contains('ghost-scene-title')||previous.classList.contains('ghost-scene-end')||previous.classList.contains('book-chapter-heading')))previous=previous.previousElementSibling;if(state.viewMode==='scene-gap'&&previous?.classList.contains('scene-gap-hidden')){event.preventDefault();return}if(previous?.classList.contains('manuscript-block-wrap')&&entityId(previous.dataset.chapterId)===entityId(wrap.dataset.chapterId)&&previous.querySelector('.manuscript-block')){event.preventDefault();recordUndo([entityId(wrap.dataset.chapterId)],true);const target=previous.querySelector('.manuscript-block'),length=target.innerText.length;while(prose.firstChild)target.appendChild(prose.firstChild);wrap.remove();placeCaret(target,length);markDirty(entityId(previous.dataset.chapterId))}}}
  }
});

$('#manuscriptPaper').addEventListener('paste',event=>{
  const imageBlock=manuscriptImageBlockFromClipboard(event.clipboardData);if(imageBlock){event.preventDefault();const anchor=manuscriptImageInsertAnchor();insertManuscriptImageReference(imageBlock,anchor).catch(error=>toast(error.message||i18nText('manuscript.image_upload_failed'),5000));return}
  const imageFile=manuscriptImageFileFromTransfer(event.clipboardData);if(imageFile){event.preventDefault();const anchor=manuscriptImageInsertAnchor();addManuscriptImageFile(imageFile,anchor).catch(error=>toast(error.message||i18nText('manuscript.image_upload_failed'),5000));return}
  const prose=event.target.closest?.('.manuscript-block')||activeManuscriptProse();if(!prose)return;const selected=selectionItems();if(new Set(selected.map(x=>x.chapter_id)).size>1){event.preventDefault();toast('Paste into one chapter at a time.');return}const text=event.clipboardData?.getData('text/plain');if(text==null)return;event.preventDefault();const wrap=prose.closest('.manuscript-block-wrap');recordUndo([entityId(wrap.dataset.chapterId)],true);const lines=text.replace(/\r\n/g,'\n').split('\n');document.execCommand('insertText',false,lines[0]);let current=wrap;let nextStyle=outlineStyleLevel(wrap.dataset.style)?'body':(wrap.dataset.style||'body');for(const line of lines.slice(1)){const next=makeBlockWrap({id:newTempId(),block_type:'paragraph',semantic_style:nextStyle,content_html:escapeHtml(line),text_content:line,scene_id:current.dataset.sceneId?entityId(current.dataset.sceneId):null},entityId(current.dataset.chapterId));current.after(next);current=next;nextStyle='body'}if(lines.length>1){applyOutlineVisibility();placeCaret(current.querySelector('.manuscript-block'),current.querySelector('.manuscript-block').innerText.length)}markDirty(entityId(wrap.dataset.chapterId));
});

// Prevent browser navigation shortcuts from bypassing the editor history.
document.addEventListener('keydown',event=>{
  if(document.body.classList.contains('reviewer-mode-active')){if(event.key==='Escape'){event.preventDefault();closeReviewerViewPopover()}return}
  const mod=event.metaKey||event.ctrlKey;
  const active=document.activeElement;
  const canvasEditor=active?.isContentEditable&&active.closest?.('.canvas-node');
  // A newly created canvas node focuses its title for immediate naming. Until the
  // user actually edits that field, Cmd/Ctrl+Z must still undo the structural
  // creation. Once the field has native edit history, let the browser undo text.
  const nativeCanvasEdit=canvasEditor&&active.dataset.canvasNativeUndo==='1';
  const nativeUndoField=active && (active.matches('input,textarea') || (active.isContentEditable && !active.closest('#manuscriptPaper') && (!canvasEditor||nativeCanvasEdit)));
  if(mod&&event.altKey&&/^[0-4]$/.test(event.key)&&$('#manuscriptView').classList.contains('active')&&active?.closest?.('#editor')){event.preventDefault();applyWritingStyle(event.key==='0'?'body':event.key==='1'?'chapter':`heading${event.key}`);return}
  if(mod&&event.key.toLowerCase()==='z'){if(nativeUndoField)return;event.preventDefault();requestHistory(event.shiftKey?'redo':'undo','shortcut');return}
  if(event.ctrlKey&&event.key.toLowerCase()==='y'){if(nativeUndoField)return;event.preventDefault();requestHistory('redo','shortcut');return}
  if(mod&&event.key.toLowerCase()==='f'){event.preventDefault();openFind();return}
  if(mod&&event.key.toLowerCase()==='k'){event.preventDefault();openQuickOpen();return}
  if(mod&&event.key.toLowerCase()==='s'){event.preventDefault();saveNow();return}
  if(event.key==='Escape'){if(!$('#commentComposer')?.classList.contains('hidden')){event.preventDefault();cancelPendingComment();return}if(state.mapPendingConnection){event.preventDefault();cancelMapLineConnection();return}if(document.body.classList.contains('focus'))setFocusMode(false);closeTransientUI();closeFind();}
});

async function saveNow(){await flushPendingCanvasNodeSaves();await flushSave();toast('Saved ✓')}

