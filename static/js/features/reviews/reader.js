// Reviewer Mode, portable .breadreview sessions, annotations, feedback, and drag/drop opening.

function breadReviewPassphraseDialog({title=null,message=null,confirm=false}={}){
  return new Promise(resolve=>{
    let settled=false;const settle=value=>{if(settled)return;settled=true;currentModalDismiss=null;resolve(value);closeModal(false)};
    const confirmField=confirm?`<div class="field"><label>${escapeHtml(i18nText('reviews.passphrase_confirm'))}</label><input id="breadReviewPassphraseConfirm" type="password" maxlength="1024" autocomplete="new-password"></div>`:'';
    showNestedModal(title||i18nText('reviews.passphrase_title'),`${message?`<p class="dialog-message">${escapeHtml(message)}</p>`:''}<div class="field"><label>${escapeHtml(i18nText('reviews.passphrase'))}</label><input id="breadReviewPassphrase" type="password" maxlength="1024" autocomplete="${confirm?'new-password':'current-password'}"></div>${confirmField}<p id="breadReviewPassphraseError" class="small-muted hidden"></p>`,`<button id="breadReviewPassphraseCancel">${escapeHtml(i18nText('ui.cancel'))}</button><button id="breadReviewPassphraseConfirmButton">${escapeHtml(i18nText('ui.continue'))}</button>`,()=>{if(!settled){settled=true;resolve(null)}});
    const input=$('#breadReviewPassphrase'),other=$('#breadReviewPassphraseConfirm'),error=$('#breadReviewPassphraseError');
    const submit=()=>{const value=input.value;if(confirm&&value.length<8){error.textContent=i18nText('reviews.passphrase_minimum');error.classList.remove('hidden');return}if(confirm&&value!==other.value){error.textContent=i18nText('reviews.passphrase_mismatch');error.classList.remove('hidden');return}settle(value)};
    $('#breadReviewPassphraseCancel').onclick=()=>settle(null);$('#breadReviewPassphraseConfirmButton').onclick=submit;input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();(other||input).focus();if(!confirm)submit()}});other?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();submit()}});queueMicrotask(()=>input.focus());
  });
}
async function unlockPortableBreadReview(opened){
  if(!opened?.locked)return opened;
  while(true){
    const passphrase=await breadReviewPassphraseDialog({message:i18nText('reviews.passphrase_open_message')});
    if(passphrase===null)return null;
    try{return await api('/api/portable-review/unlock',{method:'POST',body:JSON.stringify({passphrase})})}
    catch(error){if(!['BREADREVIEW_037','BREADREVIEW_038'].includes(error.code))throw error;toast(error.message,6000)}
  }
}
// -------------------- reviews --------------------
function reviewById(id){return(state.reviews||[]).find(review=>review.id===id)||null}
function reviewName(review){return String(review?.name||'').trim()}
function reviewRailLabel(review){return compactRailLabel(review?.name,['Review'])}
function reviewerSceneById(snapshot,id){return(snapshot?.scenes||[]).find(scene=>entityId(scene.source_scene_id)===entityId(id))||null}
function reviewerBlockHtml(block){
  if(block.block_type==='image'){
    const width=Math.max(5,Math.min(100,Number(block.image_width)||100));
    const description=escapeHtml(block.image_description||''),footer=String(block.image_footer||'').trim();
    const rowId=String(block.image_row_id||'').trim(),rowAttrs=rowId?` data-image-row-id="${escapeHtml(rowId)}" data-image-row-width="${width}"`:'';
    return `<figure class="reviewer-image-figure"${rowAttrs}><img class="reviewer-image" style="width:${rowId?100:width}%" src="${escapeHtml(projectAssetDisplayUrl(block.image_path||''))}" alt="${description}">${footer?`<figcaption>${escapeHtml(block.image_footer||'')}</figcaption>`:''}</figure>`;
  }
  return `<div class="reviewer-block" data-review-block-id="${escapeHtml(block.source_block_id||'')}" data-semantic-style="${escapeHtml(block.semantic_style||'body')}">${block.content_html||escapeHtml(block.text_content||'')}</div>`;
}
function renderReviewerSnapshot(payload){
  const paper=$('#reviewerPaper');if(!paper)return;
  const snapshot=payload?.snapshot||{},canRead=!!snapshot.permission_read,blocks=snapshot.blocks||[],chapters=snapshot.chapters||[];
  paper.classList.toggle('hidden',!canRead);
  if(!canRead){paper.replaceChildren();return}
  if(!blocks.length){paper.innerHTML=`<div class="reviewer-empty">${escapeHtml(i18nText('reviews.reviewer_empty'))}</div>`;return}
  const byChapter=new Map();for(const block of blocks){const cid=entityId(block.source_chapter_id);if(!byChapter.has(cid))byChapter.set(cid,[]);byChapter.get(cid).push(block)}
  paper.innerHTML=chapters.filter(chapter=>byChapter.has(entityId(chapter.source_chapter_id))).map(chapter=>{let lastScene=null;const content=byChapter.get(entityId(chapter.source_chapter_id)).map(block=>{let scene='';const sid=block.source_scene_id==null?null:entityId(block.source_scene_id);if(sid!==null&&sid!==lastScene){const found=reviewerSceneById(snapshot,sid);if(found)scene=`<h3 class="reviewer-scene-title">${escapeHtml(found.title||i18nText('reviews.untitled_scene'))}</h3>`;lastScene=sid}return scene+reviewerBlockHtml(block)}).join('');return `<section class="reviewer-chapter" data-reviewer-chapter="${chapter.source_chapter_id}"><h2 class="reviewer-chapter-title">${escapeHtml(chapter.title||i18nText('reviews.untitled_chapter'))}</h2>${content}</section>`}).join('');bindReadOnlyImageDescriptions(paper)
}
let reviewerFeedbackSaveTimer=null,reviewerFeedbackSaveRevision=0,reviewerFeedbackSavedRevision=0,reviewerFeedbackSaveQueue=Promise.resolve();
function autoSizeReviewerFeedbackArea(area){if(!area)return;area.style.height='auto';const style=getComputedStyle(area),line=parseFloat(style.lineHeight)||20,pad=(parseFloat(style.paddingTop)||0)+(parseFloat(style.paddingBottom)||0),border=(parseFloat(style.borderTopWidth)||0)+(parseFloat(style.borderBottomWidth)||0),max=Math.ceil(line*3+pad+border);const height=Math.min(Math.max(Math.ceil(area.scrollHeight),Math.ceil(line+pad+border)),max);area.style.height=`${height}px`;area.style.overflowY=area.scrollHeight>max?'auto':'hidden'}
function reviewerFeedbackPayload(){const form=$('#reviewerFeedbackForm');return{answers:$$('[data-review-feedback-question]',form).map(input=>({question_id:input.dataset.reviewFeedbackQuestion,answer_text:input.value})),general_feedback:$('#reviewGeneralFeedback',form)?.value||''}}
async function persistReviewerFeedback(){const session=state.reviewerSession;if(!session||session.writerSnapshot||!session.payload?.form)return;const revision=reviewerFeedbackSaveRevision,payload=reviewerFeedbackPayload();reviewerFeedbackSaveQueue=reviewerFeedbackSaveQueue.catch(()=>{}).then(()=>api(reviewerApiPath('feedback',session),{method:'PUT',body:JSON.stringify(payload)}).then(()=>{reviewerFeedbackSavedRevision=Math.max(reviewerFeedbackSavedRevision,revision)}));return reviewerFeedbackSaveQueue}
function scheduleReviewerFeedbackSave(){reviewerFeedbackSaveRevision+=1;clearTimeout(reviewerFeedbackSaveTimer);reviewerFeedbackSaveTimer=setTimeout(()=>{reviewerFeedbackSaveTimer=null;persistReviewerFeedback().catch(error=>toast(error.message,6000))},420)}
async function flushReviewerFeedbackSave(){clearTimeout(reviewerFeedbackSaveTimer);reviewerFeedbackSaveTimer=null;if(reviewerFeedbackSaveRevision>reviewerFeedbackSavedRevision)await persistReviewerFeedback()}
function wireReviewerFeedbackAutosave(target){$$('textarea',target).forEach(area=>{autoSizeReviewerFeedbackArea(area);area.addEventListener('input',()=>{autoSizeReviewerFeedbackArea(area);scheduleReviewerFeedbackSave()});area.addEventListener('blur',()=>{if(reviewerFeedbackSaveRevision>reviewerFeedbackSavedRevision)flushReviewerFeedbackSave().catch(error=>toast(error.message,6000))})})}
function renderReviewerFeedbackForm(payload){
  const target=$('#reviewerFeedbackForm');if(!target)return;
  if(state.reviewerSession?.writerSnapshot){
    const forms=payload?.writer_feedback_forms||[];if(!forms.length){target.classList.add('hidden');target.replaceChildren();return}
    const session=state.reviewerSession;session.feedbackFormIndex=Math.max(0,Math.min(Number(session.feedbackFormIndex||0),forms.length-1));const index=session.feedbackFormIndex,item=forms[index];
    target.classList.remove('hidden');target.classList.add('writer-feedback-summary');
    const answers=(item.answers||[]).filter(answer=>String(answer.answer_text||'').trim()).map(answer=>`<div class="writer-feedback-response-line"><div><small>${escapeHtml(answer.questionnaire_name||i18nText('reviews.questionnaires'))}</small><b>${escapeHtml(answer.prompt||'')}</b><p>${escapeHtml(answer.answer_text||'')}</p></div><button type="button" class="manuscript-item-more review-feedback-line-more" data-writer-round-task="${escapeHtml(answer.id||'')}" data-writer-round-task-kind="answer" aria-label="${escapeHtml(i18nText('reviews.more_actions'))}">⋮</button></div>`).join('');
    const general=String(item.general_feedback||'').trim()?`<div class="writer-feedback-response-line"><div><small>${escapeHtml(i18nText('reviews.general_feedback'))}</small><p>${escapeHtml(item.general_feedback)}</p></div><button type="button" class="manuscript-item-more review-feedback-line-more" data-writer-round-task="${escapeHtml(item.general_feedback_id||'')}" data-writer-round-task-kind="general" aria-label="${escapeHtml(i18nText('reviews.more_actions'))}">⋮</button></div>`:'';
    const nav=forms.length>1?`<div class="review-question-nav"><button type="button" id="writerFeedbackPrev" aria-label="${escapeHtml(i18nText('reviews.response_previous'))}">‹</button><span>${i18nNumber(index+1)} / ${i18nNumber(forms.length)}</span><button type="button" id="writerFeedbackNext" aria-label="${escapeHtml(i18nText('reviews.response_next'))}">›</button></div>`:'';
    target.innerHTML=`<div class="writer-feedback-response-head"><div><h2>${escapeHtml(i18nText('reviews.questions'))}</h2><b>${escapeHtml(item.participant_name||'')}</b></div>${nav}</div><div class="writer-feedback-responses"><section class="writer-feedback-response">${answers}${general}</section></div>`;
    $('#writerFeedbackPrev',target)?.addEventListener('click',()=>{session.feedbackFormIndex=(index-1+forms.length)%forms.length;renderReviewerFeedbackForm(payload)});$('#writerFeedbackNext',target)?.addEventListener('click',()=>{session.feedbackFormIndex=(index+1)%forms.length;renderReviewerFeedbackForm(payload)});
    $$('[data-writer-round-task]',target).forEach(button=>button.onclick=event=>{event.preventDefault();event.stopPropagation();const kind=button.dataset.writerRoundTaskKind,id=button.dataset.writerRoundTask,itemForTask=kind==='answer'?(item.answers||[]).find(row=>String(row.id)===String(id)):{id:item.general_feedback_id,general_feedback:item.general_feedback};if(!itemForTask)return;itemForTask.participant_name=item.participant_name;itemForTask.participant_id=item.participant_id;const rect=button.getBoundingClientRect();openContextMenu(rect.left,rect.bottom+4,[{label:i18nText('reviews.create_task'),action:()=>openReviewFeedbackTask(session.reviewId,kind,itemForTask)}],{anchorRect:rect})});
    return
  }
  target.classList.remove('writer-feedback-summary');const form=payload?.form;
  if(!form){target.classList.add('hidden');target.replaceChildren();return}
  const answers=new Map((form.answers||[]).map(row=>[String(row.question_id),String(row.answer_text||'')]));
  target.classList.remove('hidden');reviewerFeedbackSaveRevision=0;reviewerFeedbackSavedRevision=0;
  target.innerHTML=`<h2>${escapeHtml(i18nText('reviews.feedback_form'))}</h2>${(form.questions||[]).map((question,index)=>`<div class="reviewer-feedback-question"><label for="reviewFeedbackQuestion${index}">${escapeHtml(question.prompt||'')} ${question.required?`<span class="reviewer-feedback-required">${escapeHtml(i18nText('reviews.required'))}</span>`:`<span class="reviewer-feedback-required">${escapeHtml(i18nText('reviews.optional'))}</span>`}</label><textarea id="reviewFeedbackQuestion${index}" class="reviewer-feedback-input" data-review-feedback-question="${escapeHtml(question.id)}" rows="1" maxlength="120000">${escapeHtml(answers.get(String(question.id))||'')}</textarea></div>`).join('')}<div class="reviewer-feedback-question"><label for="reviewGeneralFeedback">${escapeHtml(i18nText('reviews.general_feedback'))}</label><textarea id="reviewGeneralFeedback" class="reviewer-general-feedback reviewer-feedback-input" rows="1" maxlength="240000">${escapeHtml(form.general_feedback||'')}</textarea></div><div class="reviewer-feedback-actions"><button id="reviewFeedbackFinish" type="button">${escapeHtml(i18nText('reviews.finish'))}</button></div>`;
  wireReviewerFeedbackAutosave(target);$('#reviewFeedbackFinish',target).onclick=()=>exitReviewerMode().catch(error=>toast(error.message,6000))
}
function reviewTextOffset(block,node,offset){const range=document.createRange();range.selectNodeContents(block);try{range.setEnd(node,offset)}catch{return null}return range.toString().length}
function reviewSelectionPayload(){const selection=window.getSelection();if(!selection||selection.rangeCount!==1||selection.isCollapsed)return null;const range=selection.getRangeAt(0),paper=$('#reviewerPaper');if(!paper||!paper.contains(range.commonAncestorContainer))return null;const elementFor=node=>node?.nodeType===Node.ELEMENT_NODE?node:node?.parentElement;const startBlock=elementFor(range.startContainer)?.closest?.('.reviewer-block[data-review-block-id]'),endBlock=elementFor(range.endContainer)?.closest?.('.reviewer-block[data-review-block-id]');if(!startBlock||!endBlock||!paper.contains(startBlock)||!paper.contains(endBlock))return null;const startOffset=reviewTextOffset(startBlock,range.startContainer,range.startOffset),endOffset=reviewTextOffset(endBlock,range.endContainer,range.endOffset);if(startOffset==null||endOffset==null)return null;return{start_block_id:startBlock.dataset.reviewBlockId,end_block_id:endBlock.dataset.reviewBlockId,start_offset:startOffset,end_offset:endOffset}}
let reviewerPointerSelecting=false;
function hideReviewMarkerPicker(){const picker=$('#reviewMarkerPicker');picker?.classList.add('hidden');if(picker)picker.style.removeProperty('inset')}
function hideReviewSelectionToolbar({preserveSelection=false}={}){const toolbar=$('#reviewSelectionToolbar');toolbar?.classList.add('hidden');hideReviewMarkerPicker();if(!preserveSelection)state.pendingReviewSelection=null}
function reviewSelectionRect(range){const rects=[...range.getClientRects()].filter(rect=>rect.width||rect.height);return rects[0]||range.getBoundingClientRect()}
function showReviewSelectionToolbar(){
  if(reviewerPointerSelecting)return;
  const session=state.reviewerSession;
  if(session?.writerSnapshot){hideReviewSelectionToolbar();return}
  const snap=session?.payload?.snapshot,status=session?.payload?.participant?.status||'not_started';
  if(status==='revoked'||!snap?.permission_read){hideReviewSelectionToolbar();return}
  const payload=reviewSelectionPayload();
  if(!payload){hideReviewSelectionToolbar();return}
  const selection=window.getSelection(),range=selection.getRangeAt(0),rect=reviewSelectionRect(range),toolbar=$('#reviewSelectionToolbar');
  if(!rect||(!rect.width&&!rect.height)){hideReviewSelectionToolbar();return}
  state.pendingReviewSelection=payload;
  const hasMarkers=!!snap.permission_comment&&Array.isArray(snap.markers)&&snap.markers.length>0;
  $('#reviewHighlightSelection')?.classList.toggle('hidden',!hasMarkers);
  $('#reviewCommentSelection')?.classList.toggle('hidden',!snap.permission_comment);
  $('#reviewSuggestSelection')?.classList.toggle('hidden',!snap.permission_suggest);
  $('#reviewBookmarkSelection')?.classList.remove('hidden');
  hideReviewMarkerPicker();
  toolbar.classList.remove('hidden');
  requestAnimationFrame(()=>{const box=toolbar.getBoundingClientRect(),margin=8;let left=rect.left+(rect.width-box.width)/2;left=Math.max(margin,Math.min(left,innerWidth-box.width-margin));let top=rect.top-box.height-8;if(top<margin)top=Math.min(innerHeight-box.height-margin,rect.bottom+8);toolbar.style.left=`${Math.round(left)}px`;toolbar.style.top=`${Math.round(top)}px`})
}
function openReviewMarkerPicker(){
  const session=state.reviewerSession,snapshot=session?.payload?.snapshot,selection=window.getSelection();
  if(!state.pendingReviewSelection||!snapshot?.permission_comment||!selection?.rangeCount)return;
  const markers=Array.isArray(snapshot.markers)?snapshot.markers:[];
  if(!markers.length){toast(i18nText('reviews.no_markers_available'));return}
  const picker=$('#reviewMarkerPicker'),list=$('#reviewMarkerPickerList');if(!picker||!list)return;
  const rect=reviewSelectionRect(selection.getRangeAt(0));
  list.replaceChildren();
  for(const marker of markers){
    const button=document.createElement('button');button.type='button';button.className='selection-highlight-marker';button.setAttribute('role','menuitem');button.title=marker.name||i18nText('reviews.marker');button.innerHTML=`<span class="color-chip" style="background:${escapeHtml(marker.color||'#f2c94c')}"></span><span>${escapeHtml(marker.name||i18nText('reviews.marker'))}</span>`;
    button.onpointerdown=event=>{event.preventDefault();event.stopPropagation()};
    button.onclick=()=>createReviewHighlight(marker.source_marker_id??marker.marker_id).catch(error=>toast(error.message,5000));
    list.appendChild(button)
  }
  $('#reviewSelectionToolbar')?.classList.add('hidden');
  picker.classList.remove('hidden');
  requestAnimationFrame(()=>{const box=picker.getBoundingClientRect(),margin=8;let left=rect.left+(rect.width-box.width)/2;left=Math.max(margin,Math.min(left,innerWidth-box.width-margin));let top=rect.top-box.height-8;if(top<margin)top=Math.min(innerHeight-box.height-margin,rect.bottom+8);picker.style.left=`${Math.round(left)}px`;picker.style.top=`${Math.round(top)}px`})
}
function reviewDomPoint(blockId,offset){const block=$(`#reviewerPaper .reviewer-block[data-review-block-id="${CSS.escape(String(blockId))}"]`);if(!block)return null;const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT,{acceptNode(node){return node.parentElement?.closest?.('.markup-gutter,.note-gutter-marker,.text-bookmark-marker')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT}});let remaining=Number(offset||0),node;while((node=walker.nextNode())){const length=node.nodeValue?.length||0;if(remaining<=length)return{node,offset:remaining};remaining-=length}return{node:block,offset:[...block.childNodes].findIndex(child=>child.matches?.('.markup-gutter,.note-gutter-marker,.text-bookmark-marker'))>=0?[...block.childNodes].findIndex(child=>child.matches?.('.markup-gutter,.note-gutter-marker,.text-bookmark-marker')):block.childNodes.length}}
function reviewRangeForAnchor(item){const start=reviewDomPoint(item.start_block_id,item.start_offset),end=reviewDomPoint(item.end_block_id,item.end_offset);if(!start||!end)return null;const range=document.createRange();try{range.setStart(start.node,start.offset);range.setEnd(end.node,end.offset);return range}catch{return null}}
function reviewerFeedbackBlock(blockId){return $(`#reviewerPaper .reviewer-block[data-review-block-id="${CSS.escape(String(blockId))}"]`)}
let reviewerHighlightHoverTimer=null,reviewerHighlightHoverKey='';
function hideReviewerHighlightHover(){clearTimeout(reviewerHighlightHoverTimer);reviewerHighlightHoverTimer=null;reviewerHighlightHoverKey='';$('#markupHoverCard')?.classList.add('hidden')}
function queueReviewerHighlightHover(item,target){hideReviewerHighlightHover();const key=`${item.id}@${item.start_block_id}`;reviewerHighlightHoverKey=key;reviewerHighlightHoverTimer=setTimeout(()=>{if(reviewerHighlightHoverKey!==key||!target.isConnected||!target.matches(':hover'))return;const card=$('#markupHoverCard');if(!card)return;const color=item.marker_color||'#f2c94c',name=item.marker_name||i18nText('reviews.marker');card.innerHTML=`<span class="markup-hover-dot reviewer-highlight-hover-dot" style="background:${escapeHtml(color)}"></span><span>${escapeHtml(item.participant_name||i18nText('reviews.participant'))} · ${escapeHtml(name)}</span>`;positionMarkupHoverCard(card,target)},300)}
let reviewerCommentHoverTimer=null,reviewerCommentHoverKey='';
function hideReviewerCommentHover(){clearTimeout(reviewerCommentHoverTimer);reviewerCommentHoverTimer=null;reviewerCommentHoverKey='';$('#commentHoverCard')?.classList.add('hidden')}
function reviewerCommentHoverHtml(items){const heading=items.length>1?`<div class="comment-hover-heading">${escapeHtml(i18nText('reviews.comment_count',{count:i18nNumber(items.length)}))}</div>`:'';return heading+items.map(item=>`<div class="comment-hover-thread reviewer-comment-preview"><small>${escapeHtml(item.participant_name||i18nText('reviews.participant'))}</small><span>${escapeHtml(item.body||i18nText('reviews.comment'))}</span></div>`).join('')}
function showReviewerCommentHover(items,target,key){if(reviewerCommentHoverKey!==key||!items.length)return;const card=$('#commentHoverCard');if(!card)return;card.innerHTML=reviewerCommentHoverHtml(items);card.classList.remove('comment-hover-picker');card.classList.add('comment-hover-preview');card.onmouseenter=()=>clearTimeout(reviewerCommentHoverTimer);card.onmouseleave=()=>{reviewerCommentHoverTimer=setTimeout(hideReviewerCommentHover,120)};positionCommentHoverCard(card,target.getBoundingClientRect(),target,false)}
function queueReviewerCommentHover(items,target){hideReviewerCommentHover();const key=`${items.map(item=>item.id).join(',')}@${target.dataset.blockId||''}`;reviewerCommentHoverKey=key;reviewerCommentHoverTimer=setTimeout(()=>showReviewerCommentHover(items,target,key),300)}
function reviewerSuggestionHoverHtml(items){const heading=items.length>1?`<div class="comment-hover-heading">${escapeHtml(i18nText('reviews.suggestion'))} · ${i18nNumber(items.length)}</div>`:'';const writer=!!state.reviewerSession?.writerSnapshot;return heading+items.map(item=>{const actions=writer&&item.status==='open'?`<div class="reviewer-suggestion-inline-actions"><button type="button" data-writer-suggestion-accept="${escapeHtml(item.id)}">${escapeHtml(i18nText('reviews.accept'))}</button><button type="button" data-writer-suggestion-reject="${escapeHtml(item.id)}">${escapeHtml(i18nText('reviews.reject'))}</button></div>`:`${writer&&item.status!=='open'?`<small>${escapeHtml(i18nText(`reviews.suggestion_status_${item.status}`))}</small>`:''}`;return `<div class="comment-hover-thread reviewer-comment-preview"><small>${escapeHtml(item.participant_name||i18nText('reviews.participant'))} · ${escapeHtml(i18nText(`reviews.suggestion_${item.operation||'replace'}`))}</small><span>${item.operation==='delete'?escapeHtml(item.selected_text||''):escapeHtml(item.proposed_text||'')}</span>${actions}</div>`}).join('')}
function showReviewerSuggestionHover(items,target,key){if(reviewerCommentHoverKey!==key||!items.length)return;const card=$('#commentHoverCard');if(!card)return;card.innerHTML=reviewerSuggestionHoverHtml(items);card.classList.remove('comment-hover-picker');card.classList.add('comment-hover-preview');card.onmouseenter=()=>clearTimeout(reviewerCommentHoverTimer);card.onmouseleave=()=>{reviewerCommentHoverTimer=setTimeout(hideReviewerCommentHover,120)};$$('[data-writer-suggestion-accept]',card).forEach(button=>button.onclick=async event=>{event.stopPropagation();try{await api(`/api/reviews/${encodeURIComponent(state.reviewerSession.reviewId)}/suggestions/${encodeURIComponent(button.dataset.writerSuggestionAccept)}/accept`,{method:'POST'});hideReviewerCommentHover();await refresh(false);await reloadManuscriptView();await refreshWriterReviewSnapshot();toast(i18nText('reviews.suggestion_accepted'))}catch(error){toast(error.message,7000)}});$$('[data-writer-suggestion-reject]',card).forEach(button=>button.onclick=async event=>{event.stopPropagation();try{await api(`/api/reviews/${encodeURIComponent(state.reviewerSession.reviewId)}/suggestions/${encodeURIComponent(button.dataset.writerSuggestionReject)}/reject`,{method:'POST'});hideReviewerCommentHover();await refreshWriterReviewSnapshot();toast(i18nText('reviews.suggestion_rejected'))}catch(error){toast(error.message,7000)}});positionCommentHoverCard(card,target.getBoundingClientRect(),target,false)}
function queueReviewerSuggestionHover(items,target){hideReviewerCommentHover();const key=`suggestion:${items.map(item=>item.id).join(',')}@${target.dataset.blockId||''}`;reviewerCommentHoverKey=key;reviewerCommentHoverTimer=setTimeout(()=>showReviewerSuggestionHover(items,target,key),300)}
function reviewerBookmarkMatchesSelection(selection){const b=state.reviewerSession?.payload?.bookmark;if(!b||!selection)return false;return String(b.start_block_id)===String(selection.start_block_id)&&String(b.end_block_id)===String(selection.end_block_id)&&+b.start_offset===+selection.start_offset&&+b.end_offset===+selection.end_offset}
function syncReviewerBookmarkUI(){const session=state.reviewerSession,button=$('#reviewerBookmarkBtn'),text=$('#reviewerBookmarkTooltipText'),status=$('#reviewerBookmarkTooltipStatus');if(!button)return;const allowed=!!session&&!session.writerSnapshot&&!!session.payload?.snapshot?.permission_read,has=!!session?.payload?.bookmark;button.classList.toggle('hidden',!allowed);button.classList.toggle('has-bookmark',has);button.setAttribute('aria-label',i18nText(has?'reviews.bookmark_go':'reviews.bookmark'));if(text)text.textContent=i18nText(has?'reviews.bookmark_go_hint':'reviews.bookmark_hint');if(status)status.textContent=has?i18nText('reviews.bookmark_set'):''}
async function setReviewerBookmark(selection=state.pendingReviewSelection){const session=state.reviewerSession;if(!session||!selection)return;if(reviewerBookmarkMatchesSelection(selection))return clearReviewerBookmark();const result=await api(reviewerApiPath('bookmark',session),{method:'PUT',body:JSON.stringify({selection})});session.payload.bookmark=result.bookmark||selection;hideReviewSelectionToolbar();window.getSelection()?.removeAllRanges();renderReviewerMarginAnnotations(session.payload);syncReviewerBookmarkUI();toast(i18nText('reviews.bookmark_set'))}
async function clearReviewerBookmark(){const session=state.reviewerSession;if(!session?.payload?.bookmark)return;await api(reviewerApiPath('bookmark',session),{method:'DELETE'});session.payload.bookmark=null;renderReviewerMarginAnnotations(session.payload);syncReviewerBookmarkUI();toast(i18nText('reviews.bookmark_removed'))}
function goToReviewerBookmark(){const bookmark=state.reviewerSession?.payload?.bookmark;if(!bookmark){toast(i18nText('reviews.bookmark_none'));return}const point=reviewDomPoint(bookmark.start_block_id,bookmark.start_offset);const block=point?.node?.nodeType===Node.ELEMENT_NODE?point.node:point?.node?.parentElement;block?.closest?.('.reviewer-block')?.scrollIntoView({behavior:'smooth',block:'center'})}
function reviewerAnchorTop(item,block){
  const range=reviewRangeForAnchor(item);if(!range||!block)return 0;
  const rect=reviewSelectionRect(range),blockRect=block.getBoundingClientRect();
  return Math.max(0,Math.round(rect.top-blockRect.top))
}
function reviewerLineGroups(block,items){
  const groups=[];
  for(const item of items){
    const top=reviewerAnchorTop(item,block);
    let group=groups.find(candidate=>Math.abs(candidate.top-top)<=4);
    if(!group){group={top,items:[]};groups.push(group)}
    group.items.push(item)
  }
  return groups.sort((a,b)=>a.top-b.top)
}
function clearReviewerMarginAnnotations(){const paper=$('#reviewerPaper');if(!paper)return;$$('.reviewer-block > .markup-gutter,.reviewer-block > .note-gutter-marker,.reviewer-block > .text-bookmark-marker',paper).forEach(node=>node.remove())}
function renderReviewerMarginAnnotations(payload){
  const paper=$('#reviewerPaper');if(!paper)return;
  clearReviewerMarginAnnotations();
  const feedback=payload?.feedback||{},highlights=feedback.highlights||[],comments=feedback.comments||[],suggestions=feedback.suggestions||[];
  const byBlock=items=>{const map=new Map();for(const item of items){const id=String(item.start_block_id||'');if(!id)continue;if(!map.has(id))map.set(id,[]);map.get(id).push(item)}return map};
  const highlightsByBlock=byBlock(highlights),commentsByBlock=byBlock(comments),suggestionsByBlock=byBlock(suggestions),commentLineTops=new Map();

  for(const [blockId,items] of highlightsByBlock){
    const block=reviewerFeedbackBlock(blockId);if(!block)continue;
    for(const group of reviewerLineGroups(block,items)){
      const gutter=document.createElement('div');gutter.className='markup-gutter reviewer-markup-gutter';gutter.contentEditable='false';gutter.style.top=`${group.top+2}px`;
      const seen=new Set();
      for(const item of group.items){
        const key=`${item.participant_id||item.participant_name||item.id}|${item.marker_name||''}|${item.marker_color||''}`;if(seen.has(key))continue;seen.add(key);
        const swatch=document.createElement('button');swatch.type='button';swatch.className='markup-gutter-swatch reviewer-highlight-swatch';swatch.style.background=item.marker_color||'#f2c94c';
        swatch.setAttribute('aria-label',`${item.participant_name||i18nText('reviews.participant')} · ${item.marker_name||i18nText('reviews.marker')}`);swatch.dataset.reviewItemIds=String(item.id||'');
        swatch.onpointerdown=e=>{e.preventDefault();e.stopPropagation()};swatch.onmouseenter=()=>queueReviewerHighlightHover(item,swatch);swatch.onmouseleave=hideReviewerHighlightHover;gutter.appendChild(swatch)
      }
      block.appendChild(gutter)
    }
  }

  for(const [blockId,items] of commentsByBlock){
    const block=reviewerFeedbackBlock(blockId);if(!block)continue;
    const groups=reviewerLineGroups(block,items);commentLineTops.set(blockId,groups.map(group=>group.top));
    for(const group of groups){
      const marker=document.createElement('button');marker.type='button';marker.className='note-gutter-marker reviewer-comment-gutter-marker';marker.contentEditable='false';marker.dataset.blockId=blockId;marker.dataset.reviewItemIds=group.items.map(item=>item.id).join(' ');marker.style.top=`${group.top}px`;
      marker.setAttribute('aria-label',i18nText('reviews.comment_count',{count:i18nNumber(group.items.length)}));marker.innerHTML=`<span class="note-gutter-pencil" aria-hidden="true">✎</span><span class="note-gutter-count" aria-hidden="true">${group.items.length}</span>`;
      marker.onpointerdown=e=>{e.preventDefault();e.stopPropagation()};marker.onmouseenter=()=>queueReviewerCommentHover(group.items,marker);marker.onmouseleave=()=>{clearTimeout(reviewerCommentHoverTimer);reviewerCommentHoverTimer=setTimeout(hideReviewerCommentHover,120)};
      marker.onclick=e=>{e.stopPropagation();const key=`${group.items.map(item=>item.id).join(',')}@${blockId}:${group.top}`;reviewerCommentHoverKey=key;showReviewerCommentHover(group.items,marker,key)};block.appendChild(marker)
    }
  }

  for(const [blockId,items] of suggestionsByBlock){
    const block=reviewerFeedbackBlock(blockId);if(!block)continue;
    const commentTops=commentLineTops.get(blockId)||[];
    for(const group of reviewerLineGroups(block,items)){
      const marker=document.createElement('button');marker.type='button';marker.className='note-gutter-marker reviewer-suggestion-gutter-marker';if(commentTops.some(top=>Math.abs(top-group.top)<=4))marker.classList.add('reviewer-gutter-collision');
      marker.contentEditable='false';marker.dataset.blockId=blockId;marker.dataset.reviewItemIds=group.items.map(item=>item.id).join(' ');marker.style.top=`${group.top}px`;marker.setAttribute('aria-label',`${i18nText('reviews.suggestion')} · ${i18nNumber(group.items.length)}`);marker.innerHTML=`<span class="note-gutter-pencil" aria-hidden="true">⇄</span><span class="note-gutter-count" aria-hidden="true">${group.items.length}</span>`;
      marker.onpointerdown=e=>{e.preventDefault();e.stopPropagation()};marker.onmouseenter=()=>queueReviewerSuggestionHover(group.items,marker);marker.onmouseleave=()=>{clearTimeout(reviewerCommentHoverTimer);reviewerCommentHoverTimer=setTimeout(hideReviewerCommentHover,120)};
      marker.onclick=e=>{e.stopPropagation();const key=`suggestion:${group.items.map(item=>item.id).join(',')}@${blockId}:${group.top}`;reviewerCommentHoverKey=key;showReviewerSuggestionHover(group.items,marker,key)};block.appendChild(marker)
    }
  }

  const bookmark=payload?.bookmark;if(bookmark){const block=reviewerFeedbackBlock(bookmark.start_block_id);if(block){const button=document.createElement('button');button.type='button';button.className='inline-bookmark-button text-bookmark-marker reviewer-bookmark-marker';button.setAttribute('aria-label',i18nText('reviews.bookmark_remove'));button.innerHTML=BOOKMARK_ICON_SVG;button.onpointerdown=e=>{e.preventDefault();e.stopPropagation()};button.onclick=e=>{e.preventDefault();e.stopPropagation();clearReviewerBookmark().catch(error=>toast(error.message,5000))};block.appendChild(button)}}
  syncReviewerBookmarkUI()
}
let reviewerMarkerHighlightNames=[];
function reviewerHighlightCssColor(value){return /^#[0-9a-f]{3,8}$/i.test(String(value||''))?String(value):'#f2c94c'}
function applyReviewerAnnotations(payload){
  // Annotation UI lives inside reviewer blocks for positioning, but it must never
  // participate in manuscript text ranges. Remove the previous gutter UI before
  // rebuilding CSS Highlight ranges, then render fresh gutter controls afterward.
  clearReviewerMarginAnnotations();
  if(globalThis.CSS?.highlights){
    CSS.highlights.delete('review-highlights');CSS.highlights.delete('review-comments');CSS.highlights.delete('review-suggestions');
    for(const name of reviewerMarkerHighlightNames)CSS.highlights.delete(name);reviewerMarkerHighlightNames=[];
    const styleId='reviewMarkerHighlightStyles';let style=$(`#${styleId}`);if(!style){style=document.createElement('style');style.id=styleId;document.head.appendChild(style)}
    const markerGroups=new Map();
    for(const item of payload?.feedback?.highlights||[]){const range=reviewRangeForAnchor(item);if(!range)continue;const key=`${item.marker_name||''}|${item.marker_color||''}`;if(!markerGroups.has(key))markerGroups.set(key,{color:reviewerHighlightCssColor(item.marker_color),ranges:[]});markerGroups.get(key).ranges.push(range)}
    const rules=[];let markerIndex=0;
    for(const group of markerGroups.values()){const name=`review-marker-${markerIndex++}`;reviewerMarkerHighlightNames.push(name);CSS.highlights.set(name,new Highlight(...group.ranges));rules.push(`::highlight(${name}){background:color-mix(in srgb,${group.color} 42%,transparent)}`)}
    style.textContent=rules.join('\n');
    const comments=(payload?.feedback?.comments||[]).map(reviewRangeForAnchor).filter(Boolean),suggestions=(payload?.feedback?.suggestions||[]).map(reviewRangeForAnchor).filter(Boolean);
    if(comments.length)CSS.highlights.set('review-comments',new Highlight(...comments));if(suggestions.length)CSS.highlights.set('review-suggestions',new Highlight(...suggestions))
  }
  renderReviewerMarginAnnotations(payload)
}
function reviewerApiPath(action,session=state.reviewerSession){if(session?.portable){return action==='reader'?'/api/portable-review/reader':`/api/portable-review/${action}`}const base=`/api/reviews/${encodeURIComponent(session.reviewId)}/participants/${encodeURIComponent(session.participantId)}`;return action==='reader'?`${base}/reader`:`${base}/${action}`}
function syncReviewerLifecycleControls(payload){const locked=(payload?.participant?.status||'')==='revoked';$$('#reviewerFeedbackForm textarea').forEach(input=>input.disabled=locked);const finish=$('#reviewFeedbackFinish');if(finish)finish.disabled=locked}
async function refreshReviewerSession(){const session=state.reviewerSession;if(!session)return;const payload=await api(reviewerApiPath('reader',session));session.payload=payload;renderReviewerSnapshot(payload);renderReviewerFeedbackForm(payload);applyReviewerAnnotations(payload);syncReviewerLifecycleControls(payload)}
async function createReviewHighlight(markerId){const session=state.reviewerSession,selection=state.pendingReviewSelection;if(!session||!selection||markerId==null)return;hideReviewSelectionToolbar({preserveSelection:true});await api(reviewerApiPath('highlights',session),{method:'POST',body:JSON.stringify({selection,marker_id:entityId(markerId)})});state.pendingReviewSelection=null;window.getSelection()?.removeAllRanges();await refreshReviewerSession();toast(i18nText('reviews.highlight_added'))}
function createReviewComment(){const session=state.reviewerSession,selection=state.pendingReviewSelection;if(!session||!selection)return;hideReviewSelectionToolbar();showModal(i18nText('reviews.comment'),`<div class="review-inline-dialog"><div class="field"><label for="reviewCommentBody">${escapeHtml(i18nText('reviews.comment_prompt'))}</label><textarea id="reviewCommentBody" rows="5" maxlength="12000"></textarea></div></div>`,`<button id="reviewCommentCancel">${escapeHtml(i18nText('ui.cancel'))}</button><button id="reviewCommentSave">${escapeHtml(i18nText('ui.save'))}</button>`);$('#reviewCommentCancel').onclick=()=>closeModal();$('#reviewCommentSave').onclick=async()=>{const body=$('#reviewCommentBody').value.trim();if(!body)return;await api(reviewerApiPath('comments',session),{method:'POST',body:JSON.stringify({selection,body})});closeModal();window.getSelection()?.removeAllRanges();await refreshReviewerSession();toast(i18nText('reviews.comment_added'))};$('#reviewCommentBody').focus()}
function createReviewSuggestion(){const session=state.reviewerSession,selection=state.pendingReviewSelection;if(!session||!selection)return;hideReviewSelectionToolbar();showModal(i18nText('reviews.suggestion'),`<div class="review-inline-dialog"><div class="field"><label for="reviewSuggestionOperation">${escapeHtml(i18nText('reviews.suggestion_operation'))}</label><select id="reviewSuggestionOperation"><option value="replace">${escapeHtml(i18nText('reviews.suggestion_replace'))}</option><option value="delete">${escapeHtml(i18nText('reviews.suggestion_delete'))}</option><option value="insert">${escapeHtml(i18nText('reviews.suggestion_insert'))}</option></select></div><div id="reviewSuggestionTextWrap" class="field"><label for="reviewSuggestionText">${escapeHtml(i18nText('reviews.suggestion_text'))}</label><textarea id="reviewSuggestionText" rows="5" maxlength="120000"></textarea></div></div>`,`<button id="reviewSuggestionCancel">${escapeHtml(i18nText('ui.cancel'))}</button><button id="reviewSuggestionSave">${escapeHtml(i18nText('ui.save'))}</button>`);const sync=()=>$('#reviewSuggestionTextWrap').classList.toggle('hidden',$('#reviewSuggestionOperation').value==='delete');$('#reviewSuggestionOperation').onchange=sync;sync();$('#reviewSuggestionCancel').onclick=()=>closeModal();$('#reviewSuggestionSave').onclick=async()=>{const operation=$('#reviewSuggestionOperation').value,proposed_text=$('#reviewSuggestionText').value;if(operation!=='delete'&&!proposed_text.trim())return;await api(reviewerApiPath('suggestions',session),{method:'POST',body:JSON.stringify({selection,operation,proposed_text})});closeModal();window.getSelection()?.removeAllRanges();await refreshReviewerSession();toast(i18nText('reviews.suggestion_added'))};$('#reviewSuggestionText').focus()}
async function openReviewerMode(reviewId,participantId){hideProjectMenu();$('#contextMenu')?.classList.add('hidden');closeModal();await api(`/api/reviews/${encodeURIComponent(reviewId)}/participants/${encodeURIComponent(participantId)}/begin`,{method:'POST'});const payload=await api(`/api/reviews/${encodeURIComponent(reviewId)}/participants/${encodeURIComponent(participantId)}/reader`);state.reviewerSession={reviewId,participantId,payload,portable:false};$('#reviewerModeReviewName').textContent=payload.review.name;$('#reviewerModeParticipant').textContent=i18nText('reviews.reviewer_as',{name:payload.participant.display_name});renderReviewerSnapshot(payload);renderReviewerFeedbackForm(payload);applyReviewerAnnotations(payload);syncReviewerLifecycleControls(payload);restoreReviewerTextSize();restoreReviewerFont();$('#reviewerMode').classList.remove('hidden');document.body.classList.add('reviewer-mode-active');$('#reviewerMode .reviewer-mode-scroller').scrollTop=0}
async function openReviewRoundSnapshot(reviewId,snapshotId,{focusKind=null,focusId=null}={}){hideProjectMenu();$('#contextMenu')?.classList.add('hidden');closeModal();const review=reviewById(reviewId);if(!review)return;const snapshot=await api(`/api/reviews/${encodeURIComponent(reviewId)}/snapshot?snapshot_id=${encodeURIComponent(snapshotId)}`);const feedback=await api(`/api/reviews/${encodeURIComponent(reviewId)}/feedback`);const onlyRound=rows=>(rows||[]).filter(item=>String(item.snapshot_id||'')===String(snapshot.id));const payload={review:{id:review.id,name:review.name},participant:{status:'completed',display_name:''},snapshot,feedback:{highlights:onlyRound(feedback.highlights),comments:onlyRound(feedback.comments),suggestions:onlyRound(feedback.suggestions)},writer_feedback_forms:onlyRound(feedback.feedback_forms),form:null};state.reviewerSession={reviewId,participantId:null,payload,portable:false,writerSnapshot:true,feedbackFormIndex:0};$('#reviewerModeReviewName').textContent=review.name;$('#reviewerModeParticipant').textContent=i18nText('reviews.writer_feedback_round',{number:i18nNumber(snapshot.round_number)});renderReviewerSnapshot(payload);renderReviewerFeedbackForm(payload);applyReviewerAnnotations(payload);restoreReviewerTextSize();restoreReviewerFont();$('#reviewerMode').classList.remove('hidden');document.body.classList.add('reviewer-mode-active');$('#reviewerMode .reviewer-mode-scroller').scrollTop=0;if(focusId)setTimeout(()=>focusWriterReviewItem(focusKind,focusId),60)}
function focusWriterReviewItem(kind,itemId){const payload=state.reviewerSession?.payload;if(!state.reviewerSession?.writerSnapshot||!itemId)return;const rows=payload?.feedback?.[kind==='highlight'?'highlights':kind==='comment'?'comments':'suggestions']||[],item=rows.find(row=>String(row.id)===String(itemId));if(!item)return;const block=reviewerFeedbackBlock(item.start_block_id);if(!block)return;block.scrollIntoView({behavior:'smooth',block:'center'});block.classList.add('review-feedback-anchor-focus');setTimeout(()=>block.classList.remove('review-feedback-anchor-focus'),1800);const control=[...block.querySelectorAll('[data-review-item-ids]')].find(node=>String(node.dataset.reviewItemIds||'').split(/\s+/).includes(String(itemId)));if(control&&kind!=='highlight')setTimeout(()=>control.click(),220)}
async function refreshWriterReviewSnapshot(){const session=state.reviewerSession;if(!session?.writerSnapshot)return;const feedback=await api(`/api/reviews/${encodeURIComponent(session.reviewId)}/feedback`),sid=session.payload?.snapshot?.id,onlyRound=rows=>(rows||[]).filter(item=>String(item.snapshot_id||'')===String(sid));session.payload.feedback={highlights:onlyRound(feedback.highlights),comments:onlyRound(feedback.comments),suggestions:onlyRound(feedback.suggestions)};session.payload.writer_feedback_forms=onlyRound(feedback.feedback_forms);renderReviewerFeedbackForm(session.payload);applyReviewerAnnotations(session.payload)}

async function openPortableReviewerMode(initialPayload=null,{temporary=false,filename='',fileHandle=null}={}){hideProjectMenu();$('#contextMenu')?.classList.add('hidden');closeModal();let payload=initialPayload||await api('/api/portable-review/reader');await api('/api/portable-review/begin',{method:'POST'});payload=await api('/api/portable-review/reader');state.temporaryReviewFileHandle=temporary?fileHandle:null;state.reviewerSession={reviewId:payload.review.id,participantId:payload.participant.id,payload,portable:true,temporary,filename};$('#reviewerModeReviewName').textContent=payload.review.name;$('#reviewerModeParticipant').textContent=i18nText('reviews.reviewer_as',{name:payload.participant.display_name});renderReviewerSnapshot(payload);renderReviewerFeedbackForm(payload);applyReviewerAnnotations(payload);syncReviewerLifecycleControls(payload);restoreReviewerTextSize();restoreReviewerFont();$('#reviewerMode').classList.remove('hidden');document.body.classList.add('reviewer-mode-active','portable-review-active');$('#reviewerMode .reviewer-mode-scroller').scrollTop=0}
function clearReviewerModeSurface(){closeReviewerViewPopover();hideReviewSelectionToolbar();hideReviewerHighlightHover();hideReviewerCommentHover();reviewerPointerSelecting=false;clearTimeout(reviewerFeedbackSaveTimer);reviewerFeedbackSaveTimer=null;if(globalThis.CSS?.highlights){CSS.highlights.delete('review-highlights');CSS.highlights.delete('review-comments');CSS.highlights.delete('review-suggestions');for(const name of reviewerMarkerHighlightNames)CSS.highlights.delete(name);reviewerMarkerHighlightNames=[]}$('#reviewMarkerHighlightStyles')?.remove();document.body.classList.remove('reviewer-mode-active','portable-review-active');$('#reviewerMode')?.classList.add('hidden');$('#reviewerPaper')?.replaceChildren();$('#reviewerFeedbackForm')?.replaceChildren();$('#reviewerFeedbackForm')?.classList.add('hidden');syncReviewerBookmarkUI()}
async function saveTemporaryBreadReview(session){await api('/api/portable-review/status');const response=await fetch('/api/portable-review/package',{headers:{'X-Breadmaker-Client':CLIENT_ID,'X-Breadmaker-Locale':i18nLocale()}});if(!response.ok)throw new Error(i18nText('reviews.temporary_save_failed'));const blob=await response.blob(),handle=state.temporaryReviewFileHandle;let saved=false;if(handle?.createWritable){try{let permission=handle.queryPermission?await handle.queryPermission({mode:'readwrite'}):'granted';if(permission!=='granted'&&handle.requestPermission)permission=await handle.requestPermission({mode:'readwrite'});if(permission==='granted'){const writable=await handle.createWritable();await writable.write(blob);await writable.close();saved=true}}catch(error){console.warn('Could not write BreadReview back to dropped file',error)}}if(!saved){const href=URL.createObjectURL(blob),a=document.createElement('a');a.href=href;a.download=session.filename||'Review.breadreview';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),1500)}toast(i18nText(saved?'reviews.temporary_saved':'reviews.temporary_downloaded'),5000)}
async function exitReviewerMode(){const session=state.reviewerSession;if(session&&!session.writerSnapshot){await flushReviewerFeedbackSave()}if(session?.portable){if(!session.temporary){toast(i18nText('reviews.portable_close_hint'),7000);return}try{await saveTemporaryBreadReview(session);await api('/api/portable-review/close-temporary',{method:'POST'})}catch(error){toast(error.message||i18nText('reviews.temporary_save_failed'),7000);return}clearReviewerModeSurface();state.reviewerSession=null;state.temporaryReviewFileHandle=null;return}clearReviewerModeSurface();state.reviewerSession=null;setView('reviews',{historyMode:'replace'});renderReviews()}

const reviewerPaper=$('#reviewerPaper');
reviewerPaper?.addEventListener('pointerdown',event=>{if(event.button!==0)return;reviewerPointerSelecting=true;hideReviewSelectionToolbar()});
window.addEventListener('pointerup',()=>{if(!reviewerPointerSelecting)return;reviewerPointerSelecting=false;if(document.body.classList.contains('reviewer-mode-active'))setTimeout(showReviewSelectionToolbar,0)});
window.addEventListener('pointercancel',()=>{if(!reviewerPointerSelecting)return;reviewerPointerSelecting=false;hideReviewSelectionToolbar()});
reviewerPaper?.addEventListener('keyup',()=>{if(!reviewerPointerSelecting)setTimeout(showReviewSelectionToolbar,0)});
$('#reviewerMode .reviewer-mode-scroller')?.addEventListener('scroll',()=>hideReviewSelectionToolbar(),{passive:true});
$('#reviewHighlightSelection')?.addEventListener('pointerdown',event=>event.preventDefault());
$('#reviewCommentSelection')?.addEventListener('pointerdown',event=>event.preventDefault());
$('#reviewSuggestSelection')?.addEventListener('pointerdown',event=>event.preventDefault());
$('#reviewBookmarkSelection')?.addEventListener('pointerdown',event=>event.preventDefault());
$('#reviewHighlightSelection')?.addEventListener('click',()=>openReviewMarkerPicker());
$('#reviewCommentSelection')?.addEventListener('click',()=>createReviewComment());
$('#reviewSuggestSelection')?.addEventListener('click',()=>createReviewSuggestion());
$('#reviewBookmarkSelection')?.addEventListener('click',()=>setReviewerBookmark().catch(error=>toast(error.message,5000)));
$('#reviewerBookmarkBtn')?.addEventListener('click',()=>goToReviewerBookmark());
document.addEventListener('pointerdown',event=>{const picker=$('#reviewMarkerPicker');if(!picker?.classList.contains('hidden')&&!picker.contains(event.target)&&event.target!==$('#reviewHighlightSelection'))hideReviewSelectionToolbar()});
document.addEventListener('selectionchange',()=>{
  if(!document.body.classList.contains('reviewer-mode-active'))return;
  const selection=getSelection();
  if(!selection?.rangeCount||selection.isCollapsed){if(!$('#reviewSelectionToolbar')?.matches(':hover')&&!$('#reviewMarkerPicker')?.matches(':hover'))hideReviewSelectionToolbar();return}
  const range=selection.getRangeAt(0),paper=$('#reviewerPaper');
  if(!paper||!paper.contains(range.commonAncestorContainer)){hideReviewSelectionToolbar();return}
  if(reviewerPointerSelecting){$('#reviewSelectionToolbar')?.classList.add('hidden');hideReviewMarkerPicker();return}
  // Keyboard selections do not have a pointer-up event. Delay until the native selection has settled.
  requestAnimationFrame(showReviewSelectionToolbar)
});
let breadReviewDragDepth=0;
function transferHasFiles(dataTransfer){return Array.from(dataTransfer?.types||[]).includes('Files')}
function breadReviewFileFromTransfer(dataTransfer){const files=[...(dataTransfer?.files||[])].filter(file=>String(file?.name||'').toLowerCase().endsWith('.breadreview'));return files.length===1?files[0]:null}
function showBreadReviewDropOverlay(show){$('#breadReviewDropOverlay')?.classList.toggle('hidden',!show)}
async function importOpenTemporaryBreadReview(){const result=await api('/api/portable-review/import-current',{method:'POST'});await refresh(false);selectReview(result.review_id);toast(result.duplicate?i18nText('reviews.portable_already_imported'):i18nText('reviews.portable_imported',{count:i18nNumber(result.imported||0)}),6000)}
async function chooseDroppedBreadReviewAction(opened,{filename,fileHandle}){
  if(!opened?.can_import_to_current_project){await openPortableReviewerMode(opened.payload,{temporary:true,filename,fileHandle});return}
  return new Promise(resolve=>{let settled=false;const finish=async action=>{if(settled)return;settled=true;currentModalDismiss=null;closeModal(false);try{if(action==='review')await openPortableReviewerMode(opened.payload,{temporary:true,filename,fileHandle});else if(action==='import')await importOpenTemporaryBreadReview();else await api('/api/portable-review/close-temporary',{method:'POST'})}finally{resolve()}};showNestedModal(i18nText('reviews.bread_review'),`<p class="dialog-message">${escapeHtml(i18nText('reviews.drop_current_project_choice'))}</p>`,`<button id="breadReviewDropCancel" class="bare-button">${escapeHtml(i18nText('ui.cancel'))}</button><button id="breadReviewDropOpen">${escapeHtml(i18nText('reviews.open_as_reviewer_file'))}</button><button id="breadReviewDropImport">${escapeHtml(i18nText('reviews.import_returned_feedback'))}</button>`,()=>finish(null));$('#breadReviewDropCancel').onclick=()=>finish(null);$('#breadReviewDropOpen').onclick=()=>finish('review');$('#breadReviewDropImport').onclick=()=>finish('import')})
}
async function openDroppedBreadReview(event){if(state.reviewerSession){toast(i18nText('reviews.drop_while_reviewing'),5000);return}const file=breadReviewFileFromTransfer(event.dataTransfer);if(!file){toast(i18nText('reviews.drop_one_file'),5000);return}let handle=null;try{const item=[...(event.dataTransfer?.items||[])].find(item=>item.kind==='file'&&String(item.getAsFile?.()?.name||'').toLowerCase().endsWith('.breadreview'));if(item?.getAsFileSystemHandle){const candidate=await item.getAsFileSystemHandle();if(candidate?.kind==='file')handle=candidate}}catch(error){console.warn('Bread Review file handle unavailable',error)}const fd=new FormData();fd.append('file',file,file.name);let opened=await api('/api/portable-review/open-temporary',{method:'POST',body:fd});if(opened.locked){opened=await unlockPortableBreadReview(opened);if(!opened){await api('/api/portable-review/close-temporary',{method:'POST'});return}}await chooseDroppedBreadReviewAction(opened,{filename:opened.filename||file.name,fileHandle:handle})}
function installBreadReviewDropTarget(){window.addEventListener('dragenter',event=>{if(!transferHasFiles(event.dataTransfer))return;breadReviewDragDepth++;event.preventDefault();showBreadReviewDropOverlay(true)});window.addEventListener('dragover',event=>{if(!transferHasFiles(event.dataTransfer))return;event.preventDefault();event.dataTransfer.dropEffect='copy'});window.addEventListener('dragleave',event=>{if(!transferHasFiles(event.dataTransfer))return;breadReviewDragDepth=Math.max(0,breadReviewDragDepth-1);if(!breadReviewDragDepth)showBreadReviewDropOverlay(false)});window.addEventListener('drop',event=>{if(!transferHasFiles(event.dataTransfer))return;event.preventDefault();breadReviewDragDepth=0;showBreadReviewDropOverlay(false);openDroppedBreadReview(event).catch(error=>toast(error.message||i18nText('reviews.drop_failed'),7000))})}
