// Manuscript focus mode, tooltips, Checkpoint controls, and Editor's Eye presentation.

function syncFocusModeControl(){
  const on=document.body.classList.contains('focus'),button=$('#viewFocusBtn');
  if(button){button.setAttribute('aria-checked',String(on));button.setAttribute('aria-label',i18nSource(on?'Focus Mode on':'Focus Mode'))}
}
function setFocusMode(on){
  document.body.classList.toggle('focus',on);syncFocusModeControl();
  if(on)toast('Press Esc to exit Focus',1800);
}

function positionCheckpointTooltip(){
  const button=$('#checkpointBtn'),tip=$('#checkpointTooltip');if(!button||!tip||tip.classList.contains('hidden'))return;
  const rect=button.getBoundingClientRect(),margin=8;
  tip.style.left=`${Math.max(margin,Math.min(innerWidth-tip.offsetWidth-margin,rect.left+rect.width/2-tip.offsetWidth/2))}px`;
  tip.style.top=`${Math.min(innerHeight-tip.offsetHeight-margin,rect.bottom+8)}px`;
}
function showCheckpointTooltip(){const tip=$('#checkpointTooltip');if(!tip)return;tip.classList.remove('hidden');requestAnimationFrame(positionCheckpointTooltip)}
function hideCheckpointTooltip(){const tip=$('#checkpointTooltip');if(tip)tip.classList.add('hidden')}
function syncCheckpointTooltipStatus(){
  const button=$('#checkpointBtn'),status=$('#checkpointTooltipStatus');if(!button)return;
  const open=!$('#checkpointPanel')?.classList.contains('hidden');
  button.setAttribute('aria-pressed',String(open));
  button.setAttribute('aria-label',i18nSource(open?'Checkpoints open — click to close':'Checkpoints'));
  if(status)status.textContent=open?i18nSource('Open — click to close the Checkpoints panel.'):'';
}
const checkpointToolbarButton=$('#checkpointBtn');
checkpointToolbarButton?.addEventListener('mouseenter',showCheckpointTooltip);
checkpointToolbarButton?.addEventListener('mouseleave',hideCheckpointTooltip);
checkpointToolbarButton?.addEventListener('focus',showCheckpointTooltip);
checkpointToolbarButton?.addEventListener('blur',hideCheckpointTooltip);
window.addEventListener('resize',positionCheckpointTooltip);
window.addEventListener('scroll',positionCheckpointTooltip,true);

function positionManuscriptToolTooltip(buttonId,tooltipId){
  const button=$(`#${buttonId}`),tip=$(`#${tooltipId}`);if(!button||!tip||tip.classList.contains('hidden'))return;
  const rect=button.getBoundingClientRect(),margin=8,gap=8;let left=rect.left+rect.width/2-tip.offsetWidth/2;left=Math.max(margin,Math.min(innerWidth-tip.offsetWidth-margin,left));let top=rect.bottom+gap;if(top+tip.offsetHeight>innerHeight-margin)top=Math.max(margin,rect.top-tip.offsetHeight-gap);tip.style.left=`${Math.round(left)}px`;tip.style.top=`${Math.round(top)}px`;
}
function attachManuscriptToolTooltip(buttonId,tooltipId,{hoverOnly=false,delay=0}={}){
  const button=$(`#${buttonId}`),tip=$(`#${tooltipId}`);if(!button||!tip)return;let timer=null,hovered=false,focused=false;const active=()=>hovered||(!hoverOnly&&focused),hide=()=>{clearTimeout(timer);timer=null;tip.classList.add('hidden')},showNow=()=>{clearTimeout(timer);timer=null;if(!active())return;tip.classList.remove('hidden');requestAnimationFrame(()=>positionManuscriptToolTooltip(buttonId,tooltipId))},schedule=()=>{clearTimeout(timer);timer=null;if(!active())return;if(delay>0)timer=setTimeout(showNow,delay);else showNow()};
  button.addEventListener('mouseenter',()=>{hovered=true;schedule()});button.addEventListener('mouseleave',()=>{hovered=false;if(!active())hide()});if(!hoverOnly){button.addEventListener('focus',()=>{focused=true;schedule()});button.addEventListener('blur',()=>{focused=false;if(!active())hide()})}button.addEventListener('click',hide);
  window.addEventListener('resize',()=>positionManuscriptToolTooltip(buttonId,tooltipId));window.addEventListener('scroll',()=>positionManuscriptToolTooltip(buttonId,tooltipId),true);
}
attachManuscriptToolTooltip('undoBtn','undoTooltip');attachManuscriptToolTooltip('redoBtn','redoTooltip');attachManuscriptToolTooltip('quickOpenBtn','quickOpenTooltip');attachManuscriptToolTooltip('themeBtn','themeTooltip');attachManuscriptToolTooltip('reviewerBookmarkBtn','reviewerBookmarkTooltip');
attachManuscriptToolTooltip('bookmarkBtn','bookmarkTooltip',{hoverOnly:true,delay:300});attachManuscriptToolTooltip('selectionBookmarkBtn','selectionBookmarkTooltip',{hoverOnly:true,delay:300});attachManuscriptToolTooltip('markupBtn','markupTooltip');attachManuscriptToolTooltip('manuscriptImageBtn','manuscriptImageTooltip');attachManuscriptToolTooltip('manuscriptReviewsBtn','manuscriptReviewsTooltip');attachManuscriptToolTooltip('canvasStructureBtn','canvasStructureTooltip');attachManuscriptToolTooltip('addNodePanelBtn','canvasAddCardTooltip');attachManuscriptToolTooltip('addFigureBtn','canvasAddFigureTooltip');attachManuscriptToolTooltip('addImage','canvasAddImageTooltip');attachManuscriptToolTooltip('mapMetroViewBtn','mapRendererTooltip');
attachManuscriptToolTooltip('reviewMarkersBtn','reviewMarkersTooltip');attachManuscriptToolTooltip('reviewParticipantsBtn','reviewParticipantsTooltip');attachManuscriptToolTooltip('reviewQuestionsBtn','reviewQuestionsTooltip');attachManuscriptToolTooltip('reviewStateBtn','reviewStateTooltip');
(function attachCanvasViewTooltip(){const button=$('#canvasViewBtn'),tip=$('#canvasViewTooltip'),popover=$('#canvasViewPopover');if(!button||!tip||!popover)return;const show=()=>{if(!popover.classList.contains('hidden'))return;tip.classList.remove('hidden');requestAnimationFrame(()=>positionManuscriptToolTooltip('canvasViewBtn','canvasViewTooltip'))},hide=()=>tip.classList.add('hidden');button.addEventListener('mouseenter',show);button.addEventListener('mouseleave',hide);button.addEventListener('focus',show);button.addEventListener('blur',hide);button.addEventListener('click',hide);window.addEventListener('resize',()=>positionManuscriptToolTooltip('canvasViewBtn','canvasViewTooltip'));window.addEventListener('scroll',()=>positionManuscriptToolTooltip('canvasViewBtn','canvasViewTooltip'),true)})();
(function attachMapViewTooltip(){const button=$('#mapViewBtn'),tip=$('#mapViewTooltip'),popover=$('#mapViewPopover');if(!button||!tip||!popover)return;const show=()=>{if(!popover.classList.contains('hidden'))return;tip.classList.remove('hidden');requestAnimationFrame(()=>positionManuscriptToolTooltip('mapViewBtn','mapViewTooltip'))},hide=()=>tip.classList.add('hidden');button.addEventListener('mouseenter',show);button.addEventListener('mouseleave',hide);button.addEventListener('focus',show);button.addEventListener('blur',hide);button.addEventListener('click',hide);window.addEventListener('resize',()=>positionManuscriptToolTooltip('mapViewBtn','mapViewTooltip'));window.addEventListener('scroll',()=>positionManuscriptToolTooltip('mapViewBtn','mapViewTooltip'),true)})();
(function attachViewTooltip(){
  const button=$('#viewBtn'),tip=$('#viewTooltip'),popover=$('#viewPopover');if(!button||!tip||!popover)return;
  const show=()=>{if(!popover.classList.contains('hidden'))return;tip.classList.remove('hidden');requestAnimationFrame(()=>positionManuscriptToolTooltip('viewBtn','viewTooltip'))};
  const hide=()=>tip.classList.add('hidden');
  button.addEventListener('mouseenter',show);button.addEventListener('mouseleave',hide);button.addEventListener('focus',show);button.addEventListener('blur',hide);button.addEventListener('click',hide);
  window.addEventListener('resize',()=>positionManuscriptToolTooltip('viewBtn','viewTooltip'));window.addEventListener('scroll',()=>positionManuscriptToolTooltip('viewBtn','viewTooltip'),true);
})();

function editorsEyeTheme(){const theme=document.documentElement.dataset.theme||'light';return THEME_CYCLE.includes(theme)?theme:'light'}
function chooseEditorsEyePreset(){
  const count=EDITORS_EYE_PRESETS.length;if(!count)return-1;if(count===1)return 0;
  let index=Math.floor(Math.random()*count);
  if(index===state.editorsEyeLastPresetIndex){const step=1+Math.floor(Math.random()*(count-1));index=(index+step)%count}
  state.editorsEyeLastPresetIndex=index;return index;
}
function applyEditorsEyePreset(index=state.editorsEyePresetIndex){
  const scroller=$('#documentScroller'),preset=EDITORS_EYE_PRESETS[index];if(!scroller||!preset)return;
  const palette=preset[editorsEyeTheme()];
  scroller.style.setProperty('--ee-bg',palette.bg);scroller.style.setProperty('--ee-text',palette.text);
  const accessibilityScale=(state.manuscriptFontSize||MANUSCRIPT_TEXT_SIZE_DEFAULT)/MANUSCRIPT_TEXT_SIZE_DEFAULT,eyeSize=preset.size*accessibilityScale;
  scroller.style.setProperty('--ee-font',preset.font);scroller.style.setProperty('--ee-size',`${eyeSize.toFixed(2)}px`);scroller.style.setProperty('--ee-title-size',`${(eyeSize*1.42).toFixed(2)}px`);
  scroller.style.setProperty('--ee-line-height',String(preset.lineHeight));scroller.style.setProperty('--ee-paper-width',`${preset.width}px`);
  scroller.style.setProperty('--ee-paper-pad',`${preset.pad}px`);scroller.style.setProperty('--ee-block-gap',`${preset.gap}em`);
  scroller.style.setProperty('--ee-tracking',`${preset.tracking}em`);
}
function clearEditorsEyeStyles(){
  const scroller=$('#documentScroller');if(!scroller)return;
  for(const name of ['--ee-bg','--ee-text','--ee-font','--ee-size','--ee-title-size','--ee-line-height','--ee-paper-width','--ee-paper-pad','--ee-block-gap','--ee-tracking'])scroller.style.removeProperty(name);
}
const EDITORS_EYE_WAVE_MS=430;
const EDITORS_EYE_SWAP_MS=135;
let editorsEyeTransitionToken=0;
function playEditorsEyeWave(direction='on',swap=()=>{}){
  const token=++editorsEyeTransitionToken;
  if(globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches){swap();return Promise.resolve();}
  const scroller=$('#documentScroller');if(!scroller||!scroller.getClientRects().length){swap();return Promise.resolve();}
  $$('.editors-eye-wave').forEach(node=>node.remove());
  const rect=scroller.getBoundingClientRect(),wave=document.createElement('div');
  wave.className=`editors-eye-wave ${direction==='off'?'wave-off':'wave-on'}`;wave.setAttribute('aria-hidden','true');
  Object.assign(wave.style,{left:`${rect.left}px`,top:`${rect.top}px`,width:`${rect.width}px`,height:`${rect.height}px`});
  document.body.appendChild(wave);
  let swapped=false;
  const doSwap=()=>{if(swapped||token!==editorsEyeTransitionToken)return;swapped=true;swap()};
  const swapTimer=setTimeout(doSwap,EDITORS_EYE_SWAP_MS);
  return new Promise(resolve=>{
    const finish=()=>{clearTimeout(swapTimer);doSwap();wave.remove();resolve()};
    wave.addEventListener('animationend',finish,{once:true});setTimeout(finish,EDITORS_EYE_WAVE_MS+100);
  });
}
function positionEditorsEyeTooltip(){
  const button=$('#editorsEyeBtn'),tip=$('#editorsEyeTooltip');if(!button||!tip||tip.classList.contains('hidden'))return;
  const rect=button.getBoundingClientRect(),margin=8;
  tip.style.left=`${Math.max(margin,Math.min(innerWidth-tip.offsetWidth-margin,rect.left+rect.width/2-tip.offsetWidth/2))}px`;
  tip.style.top=`${Math.min(innerHeight-tip.offsetHeight-margin,rect.bottom+8)}px`;
}
function showEditorsEyeTooltip(){
  const tip=$('#editorsEyeTooltip');if(!tip)return;tip.classList.remove('hidden');requestAnimationFrame(positionEditorsEyeTooltip);
}
function hideEditorsEyeTooltip(){const tip=$('#editorsEyeTooltip');if(tip)tip.classList.add('hidden')}
function syncEditorsEyeButton(){
  const button=$('#editorsEyeBtn'),status=$('#editorsEyeTooltipStatus');if(!button)return;
  button.classList.toggle('active',state.editorsEyeActive);button.setAttribute('aria-pressed',String(state.editorsEyeActive));
  button.setAttribute('aria-label',i18nSource(state.editorsEyeActive?'Editor’s Eye on — click to restore normal manuscript view':'Editor’s Eye'));
  if(status)status.textContent=state.editorsEyeActive?i18nSource('On — click to restore the normal manuscript view.'):'';
}
async function setEditorsEye(on){
  const scroller=$('#documentScroller');if(!scroller)return;
  const token=editorsEyeTransitionToken+1;
  if(on){
    const index=chooseEditorsEyePreset();if(index<0)return;
    state.editorsEyeActive=true;state.editorsEyePresetIndex=index;syncEditorsEyeButton();
    await playEditorsEyeWave('on',()=>{applyEditorsEyePreset(index);scroller.classList.add('editors-eye-active')});
  }else{
    state.editorsEyeActive=false;syncEditorsEyeButton();
    await playEditorsEyeWave('off',()=>{scroller.classList.remove('editors-eye-active');clearEditorsEyeStyles();state.editorsEyePresetIndex=-1});
  }
  if(token===editorsEyeTransitionToken)syncEditorsEyeButton();
}
const editorsEyeButton=$('#editorsEyeBtn');
editorsEyeButton?.addEventListener('click',()=>setEditorsEye(!state.editorsEyeActive));
editorsEyeButton?.addEventListener('mouseenter',showEditorsEyeTooltip);
editorsEyeButton?.addEventListener('mouseleave',hideEditorsEyeTooltip);
editorsEyeButton?.addEventListener('focus',showEditorsEyeTooltip);
editorsEyeButton?.addEventListener('blur',hideEditorsEyeTooltip);
window.addEventListener('resize',positionEditorsEyeTooltip);
window.addEventListener('scroll',positionEditorsEyeTooltip,true);
syncEditorsEyeButton();

