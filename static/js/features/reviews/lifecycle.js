// Review round/state history presentation and Reviews event wiring.

function reviewHistoryEventLabel(event){const key={REVIEW_ROUND_STARTED:'reviews.history_round_started',REVIEW_PAUSED:'reviews.history_paused',REVIEW_RESUMED:'reviews.history_resumed',REVIEW_ROUND_CLOSED:'reviews.history_closed',REVIEW_ROUND_REOPENED:'reviews.history_reopened',REVIEW_ARCHIVED:'reviews.history_archived',REVIEW_UNARCHIVED:'reviews.history_unarchived',REVIEW_CHECKPOINT_UPDATED:'reviews.history_checkpoint_updated'}[event?.event_type];return key?i18nText(key,{number:i18nNumber(event?.payload?.round_number||'')}):String(event?.event_type||'')}
function renderReviewStatePanel(){
  const body=$('#reviewStatePanelBody'),review=reviewById(state.currentReviewId);if(!body)return;if(!review){body.replaceChildren();return}
  const status=review.status||'draft',rounds=review.rounds||[],events=review.history||[],snapshot=review.snapshot||null;let actions='',checkpointNote='';
  if(status==='draft')actions+=`<button id="reviewStateStart" class="full-soft" type="button">${escapeHtml(i18nText('reviews.start_first_round'))}</button>`;
  if(status==='open')actions+=`<button id="reviewStatePause" class="full-soft" type="button">${escapeHtml(i18nText('reviews.pause_round'))}</button><button id="reviewStateClose" class="full-soft" type="button">${escapeHtml(i18nText('reviews.close_round'))}</button><button id="reviewStateStart" class="full-soft" type="button">${escapeHtml(i18nText('reviews.start_new_round'))}</button>`;
  if(status==='paused'){actions+=`<button id="reviewStateResume" class="full-soft" type="button">${escapeHtml(i18nText('reviews.resume_round'))}</button><button id="reviewStateUpdateCheckpoint" class="full-soft" type="button" ${snapshot?.checkpoint_update_allowed?'':'disabled'}>${escapeHtml(i18nText('reviews.update_checkpoint'))}</button><button id="reviewStateClose" class="full-soft" type="button">${escapeHtml(i18nText('reviews.close_round'))}</button><button id="reviewStateStart" class="full-soft" type="button">${escapeHtml(i18nText('reviews.start_new_round'))}</button>`;if(snapshot&&!snapshot.checkpoint_update_allowed)checkpointNote=`<div class="review-overview-muted">${escapeHtml(reviewCheckpointUpdateBlockText(snapshot))}</div>`}
  if(status==='closed')actions+=`<button id="reviewStateReopen" class="full-soft" type="button">${escapeHtml(i18nText('reviews.reopen_round'))}</button><button id="reviewStateStart" class="full-soft" type="button">${escapeHtml(i18nText('reviews.start_new_round'))}</button><button id="reviewStateArchive" class="full-soft" type="button">${escapeHtml(i18nText('reviews.archive_review'))}</button>`;
  if(status==='archived')actions+=`<button id="reviewStateUnarchive" class="full-soft" type="button">${escapeHtml(i18nText('reviews.unarchive_review'))}</button>`;
  const activeId=String(snapshot?.id||'');
  const roundRows=rounds.length?rounds.map(round=>{const roundStatus=String(round.status||'closed'),canActivate=roundStatus==='closed'||(roundStatus==='paused'&&String(round.id)!==activeId),actionLabel=roundStatus==='paused'?i18nText('reviews.resume_round'):i18nText('reviews.reopen_round');return `<div class="review-round-entry"><button type="button" class="review-history-row" data-review-round-open="${escapeHtml(round.id)}"><span><b>${escapeHtml(i18nText('reviews.round_number',{number:i18nNumber(round.round_number)}))}</b><small>${escapeHtml(reviewStatusLabel(roundStatus))}${round.created_at?` · ${escapeHtml(i18nDateTime(round.created_at))}`:''}</small></span><span aria-hidden="true">›</span></button>${canActivate?`<button type="button" class="review-round-reopen" data-review-round-reopen="${escapeHtml(round.id)}" data-review-round-number="${escapeHtml(round.round_number)}">${escapeHtml(actionLabel)}</button>`:''}</div>`}).join(''):`<div class="review-overview-muted">${escapeHtml(i18nText('reviews.no_rounds'))}</div>`;
  const historyRows=events.length?events.map(event=>`<div class="review-history-event"><span>${escapeHtml(reviewHistoryEventLabel(event))}</span><small>${event.created_at?escapeHtml(i18nDateTime(event.created_at)):''}</small></div>`).join(''):`<div class="review-overview-muted">${escapeHtml(i18nText('reviews.no_history'))}</div>`;
  body.innerHTML=`<div class="review-panel-fixed"><div class="review-state-current"><small>${escapeHtml(i18nText('reviews.current_state'))}</small><b>${escapeHtml(reviewStatusLabel(status))}</b></div><div class="review-state-actions">${actions}</div>${checkpointNote}</div><div class="review-panel-scroll"><div class="section-label">${escapeHtml(i18nText('reviews.rounds'))}</div><div class="review-round-list">${roundRows}</div><div class="section-label review-history-label">${escapeHtml(i18nText('reviews.history'))}</div><div class="review-history-list">${historyRows}</div></div>`;
  $('#reviewStateStart')?.addEventListener('click',()=>startReview(review.id));
  $('#reviewStatePause')?.addEventListener('click',()=>transitionReviewLifecycle(review.id,'pause'));
  $('#reviewStateResume')?.addEventListener('click',()=>transitionReviewLifecycle(review.id,'resume'));
  $('#reviewStateUpdateCheckpoint')?.addEventListener('click',()=>updateReviewCheckpoint(review.id));
  $('#reviewStateClose')?.addEventListener('click',()=>transitionReviewLifecycle(review.id,'close',{snapshotId:snapshot?.id,roundNumber:snapshot?.round_number}));
  $('#reviewStateReopen')?.addEventListener('click',()=>transitionReviewLifecycle(review.id,'reopen',{snapshotId:snapshot?.id,roundNumber:snapshot?.round_number}));
  $('#reviewStateArchive')?.addEventListener('click',()=>transitionReviewLifecycle(review.id,'archive'));
  $('#reviewStateUnarchive')?.addEventListener('click',()=>transitionReviewLifecycle(review.id,'unarchive'));
  $$('[data-review-round-open]',body).forEach(button=>button.onclick=()=>openReviewRoundSnapshot(review.id,button.dataset.reviewRoundOpen).catch(error=>toast(error.message,6000)));
  $$('[data-review-round-reopen]',body).forEach(button=>button.onclick=event=>{event.stopPropagation();transitionReviewLifecycle(review.id,'reopen',{snapshotId:button.dataset.reviewRoundReopen,roundNumber:button.dataset.reviewRoundNumber})});
}
$('#reviewCollectionMore')?.addEventListener('click',openReviewCollectionMenu);
$('#emptyCreateReview')?.addEventListener('click',createReviewDialog);
$('#editReviewDetailsBtn')?.addEventListener('click',()=>{if(state.currentReviewId)openReviewDetailsDialog(state.currentReviewId)});
$('#reviewParticipantsBtn')?.addEventListener('click',toggleReviewParticipantsPanel);
$('#reviewMarkersBtn')?.addEventListener('click',toggleReviewMarkersPanel);
$('#reviewQuestionsBtn')?.addEventListener('click',toggleReviewQuestionsPanel);
$('#reviewStateBtn')?.addEventListener('click',toggleReviewStatePanel);
$('#closeReviewParticipantsPanel')?.addEventListener('click',closeReviewParticipantsPanel);
$('#closeReviewMarkersPanel')?.addEventListener('click',closeReviewMarkersPanel);
$('#closeReviewQuestionsPanel')?.addEventListener('click',closeReviewQuestionsPanel);
$('#closeReviewStatePanel')?.addEventListener('click',closeReviewStatePanel);
$('#toggleReviewRail')?.addEventListener('click',()=>{state.reviewRailExpanded=!state.reviewRailExpanded;$('#reviewRail')?.classList.toggle('collapsed',!state.reviewRailExpanded);persistReviewRail()});
$('#reviewRail')?.addEventListener('contextmenu',event=>{if(event.target.closest('.review-item,#reviewCollectionMore'))return;openReviewCollectionMenu(event)});
$('#reviewerViewBtn')?.addEventListener('click',event=>{event.stopPropagation();toggleReviewerViewPopover()});
$('#reviewerTextSize')?.addEventListener('input',event=>applyReviewerTextSize(event.currentTarget.value));
$('#reviewerFont')?.addEventListener('change',event=>applyReviewerFont(event.currentTarget.value));
$('#exitReviewerMode')?.addEventListener('click',exitReviewerMode);

