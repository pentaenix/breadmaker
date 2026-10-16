// Reviewer Mode typography and view-popover presentation.

const REVIEWER_TEXT_SIZE_KEY='breadmaker-reviewer-text-size';
const REVIEWER_FONT_KEY='breadmaker-reviewer-font';
function applyReviewerTextSize(value,{persist=true}={}){const size=normalizeManuscriptTextSize(value);document.documentElement.style.setProperty('--reviewer-font-size',`${size}px`);const slider=$('#reviewerTextSize'),output=$('#reviewerTextSizeValue');if(slider)slider.value=String(size);if(output)output.textContent=formatManuscriptTextSize(size);if(persist){try{localStorage.setItem(REVIEWER_TEXT_SIZE_KEY,String(size))}catch{}}return size}
function restoreReviewerTextSize(){let stored=MANUSCRIPT_TEXT_SIZE_DEFAULT;try{stored=localStorage.getItem(REVIEWER_TEXT_SIZE_KEY)??stored}catch{}applyReviewerTextSize(stored,{persist:false})}
function applyReviewerFont(value,{persist=true}={}){const key=Object.hasOwn(MANUSCRIPT_FONTS,String(value||''))?String(value):'arial';document.documentElement.style.setProperty('--reviewer-font-family',MANUSCRIPT_FONTS[key].css);const select=$('#reviewerFont');if(select)select.value=key;if(persist){try{localStorage.setItem(REVIEWER_FONT_KEY,key)}catch{}}return key}
function restoreReviewerFont(){let stored='arial';try{stored=localStorage.getItem(REVIEWER_FONT_KEY)||stored}catch{}applyReviewerFont(stored,{persist:false})}
function closeReviewerViewPopover(){const pop=$('#reviewerViewPopover'),btn=$('#reviewerViewBtn');pop?.classList.add('hidden');btn?.setAttribute('aria-expanded','false')}
function toggleReviewerViewPopover(){const pop=$('#reviewerViewPopover'),btn=$('#reviewerViewBtn');if(!pop||!btn)return;const open=pop.classList.contains('hidden');pop.classList.toggle('hidden',!open);btn.setAttribute('aria-expanded',String(open))}
