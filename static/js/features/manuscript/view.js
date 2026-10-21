// Manuscript view preferences and View popover presentation.

const MANUSCRIPT_TEXT_SIZE_KEY='breadmaker-manuscript-text-size';
function applyManuscriptTextSize(value,{persist=true}={}){
  const size=normalizeManuscriptTextSize(value);state.manuscriptFontSize=size;
  document.documentElement.style.setProperty('--manuscript-font-size',`${size}px`);
  const slider=$('#manuscriptTextSize'),output=$('#viewTextSizeValue');if(slider)slider.value=String(size);if(output)output.textContent=formatManuscriptTextSize(size);
  if(persist){try{localStorage.setItem(MANUSCRIPT_TEXT_SIZE_KEY,String(size))}catch{}}
  if(state.editorsEyeActive)applyEditorsEyePreset(state.editorsEyePresetIndex);
  return size;
}
function restoreManuscriptTextSize(){let stored=MANUSCRIPT_TEXT_SIZE_DEFAULT;try{stored=localStorage.getItem(MANUSCRIPT_TEXT_SIZE_KEY)??stored}catch{}applyManuscriptTextSize(stored,{persist:false})}
const MANUSCRIPT_FONT_KEY='breadmaker-manuscript-font';
function applyManuscriptFont(value,{persist=true}={}){
  const key=Object.hasOwn(MANUSCRIPT_FONTS,String(value||''))?String(value):'arial';state.manuscriptFont=key;
  document.documentElement.style.setProperty('--manuscript-font-family',MANUSCRIPT_FONTS[key].css);
  const select=$('#manuscriptFont');if(select)select.value=key;
  if(persist){try{localStorage.setItem(MANUSCRIPT_FONT_KEY,key)}catch{}}
  return key;
}
function restoreManuscriptFont(){let stored='arial';try{stored=localStorage.getItem(MANUSCRIPT_FONT_KEY)||stored}catch{}applyManuscriptFont(stored,{persist:false})}
function positionViewPopover(){
  const button=$('#viewBtn'),pop=$('#viewPopover');if(!button||!pop||pop.classList.contains('hidden'))return;
  const rect=button.getBoundingClientRect(),margin=8,width=pop.offsetWidth||254;
  let left=rect.right-width;left=Math.max(margin,Math.min(innerWidth-width-margin,left));
  let top=rect.bottom+6;if(top+(pop.offsetHeight||120)>innerHeight-margin)top=Math.max(margin,rect.top-(pop.offsetHeight||120)-6);
  pop.style.left=`${left}px`;pop.style.top=`${top}px`;
}
function closeViewPopover(){const pop=$('#viewPopover'),button=$('#viewBtn');if(!pop||pop.classList.contains('hidden'))return;pop.classList.add('hidden');button?.setAttribute('aria-expanded','false')}
function toggleViewPopover(){
  const pop=$('#viewPopover'),button=$('#viewBtn');if(!pop||!button)return;
  const opening=pop.classList.contains('hidden');
  if(!opening)return closeViewPopover();$('#viewTooltip')?.classList.add('hidden');pop.classList.remove('hidden');button.setAttribute('aria-expanded','true');positionViewPopover();
}

async function restoreManuscriptUIState(preloaded=null,{render=true}={}){
  try{let tabsValue,bookValue,outlineValue,presentationValue;if(preloaded){tabsValue=preloaded['tabs-expanded'];bookValue=preloaded['book-nav-collapsed'];outlineValue=preloaded['manuscript-outline-collapse'];presentationValue=preloaded['manuscript-tab-presentation']}else{const[tabs,book,outline,presentation]=await Promise.all([api('/api/ui-state/tabs-expanded'),api('/api/ui-state/book-nav-collapsed'),api('/api/ui-state/manuscript-outline-collapse'),api('/api/ui-state/manuscript-tab-presentation')]);tabsValue=tabs.value;bookValue=book.value;outlineValue=outline.value;presentationValue=presentation.value}state.tabsExpanded=tabsValue!==false;state.bookNavCollapsed=bookValue===true;const saved=outlineValue&&typeof outlineValue==='object'?outlineValue:{};state.manuscriptCollapsedHeadings=new Set(Array.isArray(saved.headings)?saved.headings.map(String):[]);state.manuscriptCollapsedChapters=new Set(Array.isArray(saved.chapters)?saved.chapters.map(entityId).filter(isEntityId):[]);state.manuscriptTabPresentation=presentationValue&&typeof presentationValue==='object'?presentationValue:{};normalizeManuscriptNavMode();syncManuscriptViewControls();if(render){renderDocuments();renderChapterTree();applyOutlineVisibility()}}catch(error){console.warn('Could not restore manuscript UI state',error)}
}
