// Shared browser UI infrastructure. Feature-specific presentation belongs under static/js/features/.

function runtimeSupports(capability){return !!state.runtime?.capabilities?.[String(capability||'')]}


function toast(message, duration=2600, action=null) {
  const el=$('#toast'),text=i18nSource(message);el.replaceChildren(document.createTextNode(text));
  if(action?.label&&typeof action.onClick==='function'){const button=document.createElement('button');button.type='button';button.className='toast-action';button.textContent=i18nSource(action.label);button.onclick=()=>{el.classList.add('hidden');clearTimeout(el._timer);action.onClick()};el.appendChild(button)}
  el.classList.remove('hidden');clearTimeout(el._timer);el._timer=setTimeout(()=>el.classList.add('hidden'),duration);
}
function undoToast(message){toast(message,4200,{label:i18nText('action.undo'),onClick:()=>appUndo()})}


window.addEventListener('unhandledrejection', event => {
  console.error('Unhandled Breadmaker promise rejection', event.reason);
  const err = event.reason;
  toast(err?.message ? String(err.message).slice(0,220) : 'Action could not be completed.');
});

const modalStack=[];
let currentModalDismiss=null;
function renderModal(title,bodyHtml,actionsHtml='',onDismiss=null){
  $('#modalTitle').textContent=i18nSource(title);$('#modalBody').innerHTML=resolveI18nMarkers(bodyHtml);$('#modalActions').innerHTML=resolveI18nMarkers(actionsHtml);currentModalDismiss=onDismiss;$('#modal').classList.remove('hidden');
}
function showModal(title, bodyHtml, actionsHtml='', onDismiss=null) {
  if(!$('#modal').classList.contains('hidden')&&currentModalDismiss){const dismiss=currentModalDismiss;currentModalDismiss=null;dismiss();}
  renderModal(title,bodyHtml,actionsHtml,onDismiss);
}
function showNestedModal(title,bodyHtml,actionsHtml='',onDismiss=null){
  if(!$('#modal').classList.contains('hidden')){
    const body=document.createDocumentFragment(),actions=document.createDocumentFragment();
    while($('#modalBody').firstChild)body.appendChild($('#modalBody').firstChild);
    while($('#modalActions').firstChild)actions.appendChild($('#modalActions').firstChild);
    modalStack.push({title:$('#modalTitle').textContent,body,actions,onDismiss:currentModalDismiss});
  }
  renderModal(title,bodyHtml,actionsHtml,onDismiss);
}

// Shared context-menu renderer used by Manuscript, Canvas, Maps, Reviews, and other features.
let contextMenuTipTimer=null;
function hideContextMenuTip(){clearTimeout(contextMenuTipTimer);contextMenuTipTimer=null;$('#contextMenuHoverTip')?.remove()}
function queueContextMenuTip(button,text){
  hideContextMenuTip();if(!text)return;
  contextMenuTipTimer=setTimeout(()=>{if(!button.isConnected||!button.matches(':hover'))return;const tip=document.createElement('div');tip.id='contextMenuHoverTip';tip.className='context-menu-hover-tip';tip.textContent=text;document.body.appendChild(tip);const r=button.getBoundingClientRect(),b=tip.getBoundingClientRect(),gap=7,edge=7;let left=r.right+gap;if(left+b.width>innerWidth-edge)left=Math.max(edge,r.left-b.width-gap);let top=Math.max(edge,Math.min(innerHeight-b.height-edge,r.top+(r.height-b.height)/2));tip.style.left=`${Math.round(left)}px`;tip.style.top=`${Math.round(top)}px`},300)
}
function contextMenuItems(container,items,root){
  for(const item of items){
    if(item.separator){const sep=document.createElement('div');sep.className='context-sep';container.appendChild(sep);continue}
    if(item.heading){const heading=document.createElement('div');heading.className='context-heading';heading.textContent=i18nSource(item.heading);container.appendChild(heading);continue}
    const row=document.createElement('div');row.className='context-menu-row';const button=document.createElement('button');button.textContent=i18nSource(item.label||'');if(item.testId)button.dataset.testid=item.testId;
    if(item.danger)button.classList.add('danger');if(item.disabled){button.disabled=true;button.title=i18nSource(item.disabledReason||'')}
    if(item.tooltip){button.onmouseenter=()=>queueContextMenuTip(button,i18nSource(item.tooltip));button.onmouseleave=hideContextMenuTip}
    if(item.children?.length){
      button.classList.add('context-parent');button.setAttribute('aria-haspopup','menu');const submenu=document.createElement('div');submenu.className='context-submenu';contextMenuItems(submenu,item.children,root);row.append(button,submenu);
      let closeTimer=null;const cancelClose=()=>{if(closeTimer){clearTimeout(closeTimer);closeTimer=null}},closeSiblingMenus=()=>{for(const sibling of [...(row.parentElement?.children||[])]){if(sibling===row||!sibling.classList?.contains('context-menu-row'))continue;sibling.classList.remove('submenu-open');const child=sibling.querySelector(':scope > .context-submenu');if(child)child.style.display=''}},open=()=>{cancelClose();closeSiblingMenus();row.classList.add('submenu-open');positionContextSubmenu(row,submenu)},closeSoon=()=>{cancelClose();closeTimer=setTimeout(()=>{row.classList.remove('submenu-open');submenu.style.display=''},240)};
      row.addEventListener('mouseenter',open);row.addEventListener('mouseleave',closeSoon);submenu.addEventListener('mouseenter',open);submenu.addEventListener('mouseleave',closeSoon);button.addEventListener('focus',open);button.addEventListener('blur',closeSoon);
    }
    else{button.onclick=()=>{if(item.disabled)return;hideContextMenuTip();root.classList.add('hidden');item.action?.()};row.appendChild(button)}
    container.appendChild(row)
  }
}
function positionContextSubmenu(row,submenu){submenu.style.display='block';submenu.style.visibility='hidden';submenu.style.position='fixed';const rowRect=row.getBoundingClientRect(),w=submenu.offsetWidth||230,h=submenu.offsetHeight||300;let left=rowRect.right+5;if(left+w>innerWidth-4)left=Math.max(4,rowRect.left-w-5);const top=Math.max(4,Math.min(rowRect.top-4,innerHeight-h-4));submenu.style.left=`${left}px`;submenu.style.top=`${top}px`;submenu.style.visibility='';submenu.style.display=''}
function openContextMenu(x,y,items,{anchorRect=null}={}){hideContextMenuTip();const menu=$('#contextMenu'),margin=6;menu.classList.remove('review-hover-context');menu.innerHTML='';contextMenuItems(menu,items,menu);menu.style.visibility='hidden';menu.style.left='0px';menu.style.top='0px';menu.classList.remove('hidden');const box=menu.getBoundingClientRect(),w=box.width||190,h=box.height||40;let left=Math.max(margin,Math.min(Number(x)||margin,innerWidth-w-margin)),top=Number(y)||margin;if(anchorRect&&top+h>innerHeight-margin)top=anchorRect.top-h-6;top=Math.max(margin,Math.min(top,innerHeight-h-margin));menu.style.left=`${Math.round(left)}px`;menu.style.top=`${Math.round(top)}px`;menu.style.visibility='';}

function closeModal(notify=true) {
  const dismiss=currentModalDismiss;currentModalDismiss=null;if(notify&&dismiss)dismiss();
  if(modalStack.length){const previous=modalStack.pop();$('#modalTitle').textContent=previous.title;$('#modalBody').replaceChildren(previous.body);$('#modalActions').replaceChildren(previous.actions);currentModalDismiss=previous.onDismiss||null;$('#modal').classList.remove('hidden');return;}
  $('#modal').classList.add('hidden');
}
function dismissModalTree(){
  if(currentModalDismiss){const dismiss=currentModalDismiss;currentModalDismiss=null;dismiss();}
  while(modalStack.length){const frame=modalStack.pop();frame.onDismiss?.();}
  $('#modal').classList.add('hidden');
}
function confirmDialog(title,message,{confirmLabel='Continue',danger=false}={}){
  return new Promise(resolve=>{
    let settled=false;const settle=value=>{if(settled)return;settled=true;currentModalDismiss=null;resolve(value);closeModal(false)};
    showNestedModal(title,`<p class="dialog-message">${escapeHtml(i18nSource(message))}</p>`,`<button id="dialogCancel">{{i18n:dynamic.html.text.button.92113f72}}</button><button id="dialogConfirm" class="${danger?'danger':''}">${escapeHtml(i18nSource(confirmLabel))}</button>`,()=>{if(!settled){settled=true;resolve(false)}});
    $('#dialogCancel').onclick=()=>settle(false);$('#dialogConfirm').onclick=()=>settle(true);
  });
}
function textDialog(title,{label='Name',value='',placeholder='',multiline=false,description='',confirmLabel='Save'}={}){
  return new Promise(resolve=>{
    let settled=false;const settle=result=>{if(settled)return;settled=true;currentModalDismiss=null;resolve(result);closeModal(false)};
    const control=multiline?`<textarea id="dialogText" rows="5" placeholder="${escapeHtml(i18nSource(placeholder))}">${escapeHtml(value)}</textarea>`:`<input id="dialogText" value="${escapeHtml(value)}" placeholder="${escapeHtml(i18nSource(placeholder))}" autofocus>`;
    showNestedModal(title,`${description?`<p class="small-muted">${escapeHtml(i18nSource(description))}</p>`:''}<div class="field"><label>${escapeHtml(i18nSource(label))}</label>${control}</div>`,`<button id="dialogCancel">{{i18n:dynamic.html.text.button.92113f72}}</button><button id="dialogConfirm">${escapeHtml(i18nSource(confirmLabel))}</button>`,()=>{if(!settled){settled=true;resolve(null)}});
    const input=$('#dialogText');$('#dialogCancel').onclick=()=>settle(null);$('#dialogConfirm').onclick=()=>settle(input.value);if(!multiline)input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();settle(input.value)}});queueMicrotask(()=>input.focus());
  });
}
$('#modalClose').addEventListener('click', () => closeModal());
$('#modal').addEventListener('click', e => { if (e.target === $('#modal') && !$('#modalClose').disabled) closeModal(); });

function closeTransientUI() {
  hideContextMenuTip();
  closeUserProfilePopover();
  $('#contextMenu').classList.add('hidden');
  hideProjectMenu();
  $('#quickOpen').classList.add('hidden');
  closeViewPopover();
  closeCanvasViewPopover();
  closeMapViewPopover();
}

document.addEventListener('pointerdown', e => {
  if (!e.target.closest('#projectMenu') && !e.target.closest('#projectSubmenu') && !e.target.closest('#brandButton')) hideProjectMenu();
  if (!e.target.closest('.context-menu')) { hideContextMenuTip(); $('#contextMenu').classList.add('hidden'); }
  if (!e.target.closest('.quick-open') && !e.target.closest('#quickOpenBtn')) $('#quickOpen').classList.add('hidden');
  if (!e.target.closest('.manuscript-view-control')) closeViewPopover();
  if (!e.target.closest('.canvas-view-control')) closeCanvasViewPopover();
  if (!e.target.closest('.map-view-control')) closeMapViewPopover();
  if (!e.target.closest('.reviewer-view-control')) closeReviewerViewPopover();
});

function userAvatarHash(seed,salt=0){let hash=(0x811c9dc5^salt)>>>0;for(const char of String(seed)){hash^=char.charCodeAt(0);hash=Math.imul(hash,0x01000193)>>>0}return hash>>>0}
function userAvatarSeed(name){return String(name||'Writer').trim().normalize('NFKC').toLocaleLowerCase('en-US')||'writer'}
function userAvatarPalette(name){
  const seed=userAvatarSeed(name),a=userAvatarHash(seed,0x51f15e),b=userAvatarHash(seed,0xa7c31d),dark=!!(a&1);
  const hue=a%360,offset=18+(b%38),lineHue=(hue+(b&1?offset:-offset)+360)%360;
  return dark
    ?{background:`hsl(${hue} ${38+(b%18)}% ${20+(a%6)}%)`,line:`hsl(${lineHue} ${58+(a%15)}% ${84+(b%7)}%)`}
    :{background:`hsl(${hue} ${40+(b%18)}% ${86+(a%5)}%)`,line:`hsl(${lineHue} ${60+(a%14)}% ${24+(b%8)}%)`};
}
function userAvatarLineSvg(name){
  const seed=userAvatarSeed(name),colors=userAvatarPalette(name),segments=[],size=5.15,pad=3.1;
  const addSegment=(row,col,orientation,mirror=false)=>{
    const x=pad+col*size,y=pad+row*size,cx=x+size/2,cy=y+size/2,d=size*.29;
    let x1=cx-d,x2=cx+d,y1=cy,y2=cy;
    if(orientation===1){x1=x2=cx;y1=cy-d;y2=cy+d}
    else if(orientation===2){x1=cx-d;x2=cx+d;y1=cy-d;y2=cy+d}
    else if(orientation===3){x1=cx-d;x2=cx+d;y1=cy+d;y2=cy-d}
    if(mirror&&orientation===2){y1=cy+d;y2=cy-d}
    else if(mirror&&orientation===3){y1=cy-d;y2=cy+d}
    segments.push(`<line x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}"/>`);
  };
  let count=0;
  for(let row=0;row<5;row++)for(let sourceCol=0;sourceCol<3;sourceCol++){
    const h=userAvatarHash(`${seed}:${row}:${sourceCol}`,0x3197af),active=(h%100)<58||sourceCol===2&&row===2;
    if(!active)continue;const orientation=(h>>>9)&3,mirrorCol=4-sourceCol;addSegment(row,sourceCol,orientation,false);count++;if(mirrorCol!==sourceCol){addSegment(row,mirrorCol,orientation,true);count++}
  }
  if(count<6){for(const [row,sourceCol,orientation] of [[0,2,1],[1,1,2],[2,2,0],[3,1,3],[4,2,1]]){addSegment(row,sourceCol,orientation,false);const mirrorCol=4-sourceCol;if(mirrorCol!==sourceCol)addSegment(row,mirrorCol,orientation,true)}}
  return `<svg viewBox="0 0 32 32" aria-hidden="true" focusable="false"><circle cx="16" cy="16" r="16" fill="${colors.background}"/><g fill="none" stroke="${colors.line}" stroke-width="1.85" stroke-linecap="round">${segments.join('')}</g></svg>`;
}
function userAvatarMarkup(profile=state.profile){
  const current=profile||{display_name:'Writer',has_avatar:false,avatar_url:''};
  return current.has_avatar&&current.avatar_url?`<img src="${escapeHtml(current.avatar_url)}" alt="">`:userAvatarLineSvg(current.display_name||'Writer');
}
function renderUserProfile(){
  const host=$('#userAvatar');if(host)host.innerHTML=userAvatarMarkup();
  const button=$('#userAvatarButton');if(button)button.setAttribute('aria-label',i18nText('profile.open_menu',{name:state.profile?.display_name||'Writer'}));
  if(!$('#userProfilePopover')?.classList.contains('hidden'))renderUserProfilePopover();
}
async function loadUserProfile(){
  try{
    const result=await api('/api/profiles');state.profile=result?.active||null;state.profiles=Array.isArray(result?.profiles)?result.profiles:[];
    if(!state.profile&&state.profiles.length)state.profile=state.profiles[0];
  }catch(error){console.warn('Local profiles could not be loaded',error);state.profile={id:'',display_name:'Writer',kind:'local',has_avatar:false,avatar_url:''};state.profiles=[state.profile]}
  renderUserProfile();return state.profile;
}
function renderUserProfilePopover(){
  const pop=$('#userProfilePopover');if(!pop)return;const profile=state.profile||{display_name:'Writer'},localProfiles=runtimeSupports('local_profiles'),others=localProfiles?(state.profiles||[]).filter(item=>item.id&&item.id!==profile.id):[];
  const switchRows=others.length?`<div class="user-profile-section-label">${escapeHtml(i18nText('profile.switch_profile'))}</div><div class="user-profile-switch-list">${others.map(item=>`<button class="user-profile-switch-row" type="button" role="menuitem" data-profile-switch="${escapeHtml(item.id)}"><span class="user-profile-switch-avatar">${userAvatarMarkup(item)}</span><span>${escapeHtml(item.display_name||'Writer')}</span></button>`).join('')}</div>`:'';
  const profileKind=runtimeSupports('account_sessions')?i18nText('profile.account_profile'):(state.runtime?.mode==='hosted_dev'?i18nText('profile.hosted_dev_profile'):i18nText('profile.local_profile'));
  const addProfile=localProfiles?`<button id="addLocalProfile" class="user-profile-menu-item" type="button" role="menuitem">${escapeHtml(i18nText('profile.add'))}</button><div class="context-sep"></div>`:'';
  const accountSecurity=accountSecurityAvailable?.()?`<button id="accountSecurityMenu" class="user-profile-menu-item" type="button" role="menuitem">${escapeHtml(i18nText('account_security.menu'))}</button>`:'';
  pop.innerHTML=`<div class="user-profile-popover-head"><div class="user-profile-popover-avatar">${userAvatarMarkup(profile)}</div><div class="user-profile-popover-copy"><b>${escapeHtml(profile.display_name||'Writer')}</b><small>${escapeHtml(profileKind)}</small></div></div><div class="context-sep"></div>${switchRows}${addProfile}<button id="editLocalProfile" class="user-profile-menu-item" type="button" role="menuitem">${escapeHtml(i18nText('profile.edit'))}</button>${accountSecurity}<div class="context-sep"></div><button id="localProfileLogout" class="user-profile-menu-item logout" type="button" role="menuitem">${escapeHtml(i18nText('profile.logout'))}</button>`;
  $$('[data-profile-switch]',pop).forEach(button=>button.onclick=async()=>{const userId=button.dataset.profileSwitch;if(!userId)return;button.disabled=true;try{closeUserProfilePopover();await flushSave();await api(`/api/profiles/${encodeURIComponent(userId)}/activate`,{method:'POST'});location.reload()}catch(error){button.disabled=false;toast(error.message||i18nText('profile.switch_failed'),5000)}});
  const add=$('#addLocalProfile');if(add)add.onclick=()=>{closeUserProfilePopover();openCreateLocalProfile()};
  $('#editLocalProfile').onclick=()=>{closeUserProfilePopover();openLocalProfileEditor()};
  const security=$('#accountSecurityMenu');if(security)security.onclick=()=>openAccountSecurity();
  const logout=$('#localProfileLogout');if(logout&&runtimeSupports('account_sessions'))logout.onclick=async()=>{logout.disabled=true;try{try{await flushSave()}catch(error){if(error?.status!==401)throw error}await api('/api/auth/logout',{method:'POST'});location.reload()}catch(error){logout.disabled=false;toast(error.message||'Log out failed',5000)}};
}
function closeUserProfilePopover(){const pop=$('#userProfilePopover'),button=$('#userAvatarButton');pop?.classList.add('hidden');button?.setAttribute('aria-expanded','false')}
function toggleUserProfilePopover(){const pop=$('#userProfilePopover'),button=$('#userAvatarButton');if(!pop||!button)return;const opening=pop.classList.contains('hidden');if(!opening)return closeUserProfilePopover();renderUserProfilePopover();pop.classList.remove('hidden');button.setAttribute('aria-expanded','true')}
function openCreateLocalProfile(){
  const body=`<div class="field"><label for="newProfileDisplayName">${escapeHtml(i18nText('profile.name_label'))}</label><input id="newProfileDisplayName" maxlength="80" autocomplete="name" placeholder="${escapeHtml(i18nText('profile.name_placeholder'))}"></div><p class="small-muted">${escapeHtml(i18nText('profile.new_help'))}</p>`;
  showModal(i18nText('profile.new_title'),body,`<button id="newProfileCancel" class="bare-button">${escapeHtml(i18nText('profile.cancel'))}</button><button id="newProfileCreate">${escapeHtml(i18nText('profile.create'))}</button>`);
  const input=$('#newProfileDisplayName');$('#newProfileCancel').onclick=()=>closeModal();
  const create=async()=>{const name=input.value.trim();if(!name){input.focus();return}const button=$('#newProfileCreate');button.disabled=true;try{const created=await api('/api/profiles',{method:'POST',body:JSON.stringify({display_name:name})});const profile=created?.profile;if(!profile?.id)throw new Error(i18nText('profile.create_failed'));await flushSave();await api(`/api/profiles/${encodeURIComponent(profile.id)}/activate`,{method:'POST'});location.reload()}catch(error){button.disabled=false;toast(error.message||i18nText('profile.create_failed'),5000)}};
  $('#newProfileCreate').onclick=create;input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();create()}};queueMicrotask(()=>input.focus());
}

async function normalizeLocalProfileAvatar(file){
  if(!file||!String(file.type||'').startsWith('image/'))throw new Error(i18nText('profile.image_invalid'));
  if(file.size>25*1024*1024)throw new Error(i18nText('profile.image_too_large'));
  let bitmap=null,img=null,url='';
  try{
    if('createImageBitmap'in window)bitmap=await createImageBitmap(file);
    else{url=URL.createObjectURL(file);img=await new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=reject;image.src=url})}
    const source=bitmap||img,width=Number(source.width||source.naturalWidth||0),height=Number(source.height||source.naturalHeight||0);if(!width||!height)throw new Error(i18nText('profile.image_invalid'));
    const scale=Math.min(1,512/Math.max(width,height)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));
    const context=canvas.getContext('2d');context.imageSmoothingEnabled=true;context.imageSmoothingQuality='high';context.drawImage(source,0,0,canvas.width,canvas.height);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/webp',.9));if(!blob)throw new Error(i18nText('profile.image_invalid'));return blob;
  }finally{try{bitmap?.close?.()}catch{}if(url)URL.revokeObjectURL(url)}
}
function openLocalProfileEditor(){
  const profile=state.profile||{display_name:'Writer',has_avatar:false,avatar_url:''};let pendingAvatar=null,pendingAvatarUrl='',removeAvatar=false;
  const body=`<div class="profile-editor"><div class="profile-avatar-editor"><div id="profileAvatarPreview" class="profile-avatar-preview">${userAvatarMarkup(profile)}</div><div class="profile-avatar-actions"><div class="profile-avatar-button-row"><button id="profileChooseImage" class="quiet-button" type="button">${escapeHtml(i18nText('profile.choose_picture'))}</button><button id="profileRemoveImage" class="quiet-button${profile.has_avatar?'':' hidden'}" type="button">${escapeHtml(i18nText('profile.remove_picture'))}</button></div><small>${escapeHtml(i18nText('profile.picture_help'))}</small><input id="profileImageInput" class="profile-image-input" type="file" accept="image/*"></div></div><div class="field"><label for="profileDisplayName">${escapeHtml(i18nText('profile.name_label'))}</label><input id="profileDisplayName" maxlength="80" autocomplete="name" value="${escapeHtml(profile.display_name||'Writer')}" placeholder="${escapeHtml(i18nText('profile.name_placeholder'))}"></div></div>`;
  showModal(i18nText('profile.modal_title'),body,`<button id="profileCancel" class="bare-button">${escapeHtml(i18nText('profile.cancel'))}</button><button id="profileSave">${escapeHtml(i18nText('profile.save'))}</button>`,()=>{if(pendingAvatarUrl)URL.revokeObjectURL(pendingAvatarUrl)});
  const preview=$('#profileAvatarPreview'),input=$('#profileDisplayName'),picker=$('#profileImageInput'),remove=$('#profileRemoveImage');
  const renderPreview=()=>{if(pendingAvatarUrl)preview.innerHTML=`<img src="${escapeHtml(pendingAvatarUrl)}" alt="">`;else if(profile.has_avatar&&!removeAvatar)preview.innerHTML=userAvatarMarkup(profile);else preview.innerHTML=userAvatarLineSvg(input.value.trim()||'Writer');remove.classList.toggle('hidden',!(pendingAvatar||profile.has_avatar&&!removeAvatar))};
  input.addEventListener('input',()=>{if(!pendingAvatar&&(!profile.has_avatar||removeAvatar))renderPreview()});
  $('#profileChooseImage').onclick=()=>picker.click();
  picker.onchange=async()=>{const file=picker.files?.[0];if(!file)return;try{const blob=await normalizeLocalProfileAvatar(file);if(pendingAvatarUrl)URL.revokeObjectURL(pendingAvatarUrl);pendingAvatar=blob;pendingAvatarUrl=URL.createObjectURL(blob);removeAvatar=false;renderPreview()}catch(error){toast(error.message||i18nText('profile.image_invalid'),5000)}finally{picker.value=''}};
  remove.onclick=()=>{if(pendingAvatarUrl){URL.revokeObjectURL(pendingAvatarUrl);pendingAvatarUrl=''}pendingAvatar=null;removeAvatar=true;renderPreview()};
  $('#profileCancel').onclick=()=>closeModal();
  $('#profileSave').onclick=async()=>{const name=input.value.trim();if(!name){input.focus();return}const save=$('#profileSave');save.disabled=true;try{let updated=await api('/api/profile',{method:'PUT',body:JSON.stringify({display_name:name})});if(removeAvatar&&profile.has_avatar)updated=await api('/api/profile/avatar',{method:'DELETE'});else if(pendingAvatar){const fd=new FormData();fd.append('file',pendingAvatar,'avatar.webp');updated=await api('/api/profile/avatar',{method:'POST',body:fd})}state.profile=updated;state.profiles=(state.profiles||[]).map(item=>item.id===updated.id?updated:item);renderUserProfile();closeModal();toast(i18nText('profile.saved'))}catch(error){save.disabled=false;toast(error.message||i18nText('profile.save_failed'),5000)}};
}
$('#userAvatarButton')?.addEventListener('click',event=>{event.stopPropagation();toggleUserProfilePopover()});
document.addEventListener('pointerdown',event=>{const control=event.target?.closest?.('.user-profile-control');if(!control)closeUserProfilePopover()});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeUserProfilePopover()});
renderUserProfile();
const THEME_CYCLE=['light','dusk','dark'];
function setTheme(theme) {
  theme=THEME_CYCLE.includes(theme)?theme:'light';
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('breadmaker-theme', theme);
  if (state.editorsEyeActive) applyEditorsEyePreset(state.editorsEyePresetIndex);
  applyMapTheme?.($('#mapStage'),state.mapData);
  if (state.mapData) requestAnimationFrame(() => renderMapVisual());
  if (state.split?.type === 'map') requestAnimationFrame(() => openMapSplit(state.split.id).catch(console.warn));
  if(state.currentCanvas)requestAnimationFrame(()=>{renderNodes();drawEdges();if(state.selectedNodes?.size||canvasSelectedEdgeIds().size)updateSelectionInspector()});
  if(state.split?.type==='canvas')requestAnimationFrame(()=>openCanvasSplit(state.split.id).catch(console.warn));
  const themeStatus=$('#themeTooltipStatus');if(themeStatus)themeStatus.textContent=i18nText(`global.theme_${theme}`);
}
$('#themeBtn').addEventListener('click', () => {const current=document.documentElement.dataset.theme||'light',index=THEME_CYCLE.indexOf(current);setTheme(THEME_CYCLE[(index+1+THEME_CYCLE.length)%THEME_CYCLE.length])});

let currentSaveStateSource='Saved';
function setSaveState(text) {
  currentSaveStateSource=text||'Saved';
  const localized=i18nSource(currentSaveStateSource);
  $('#saveState').textContent = localized;
  $('#globalSaveState').textContent = localized;
}

function i18nLocale(){return i18nCatalog?.code||'en'}
function i18nNumber(value,options=undefined){const number=Number(value||0);try{return number.toLocaleString(i18nLocale(),options)}catch{return number.toLocaleString(undefined,options)}}
function i18nDate(value,options={year:'numeric',month:'long',day:'numeric'}){try{return new Intl.DateTimeFormat(i18nLocale(),options).format(new Date(value))}catch{return String(value||'')}}
function i18nDateTime(value,options=null){try{const format=options&&Object.keys(options).length?options:{dateStyle:'medium',timeStyle:'short'};return new Intl.DateTimeFormat(i18nLocale(),format).format(new Date(value))}catch{return String(value||'')}}
function i18nTime(value,options={hour:'2-digit',minute:'2-digit'}){try{return new Intl.DateTimeFormat(i18nLocale(),options).format(new Date(value))}catch{return String(value||'')}}
function i18nCount(value,oneKey,manyKey){const count=Number(value||0);return i18nText(count===1?oneKey:manyKey,{count:i18nNumber(count)})}
function refreshRuntimeLocalizedUi(){
  setSaveState(currentSaveStateSource);
  renderUserProfile?.();
  syncBookmarkToolbar?.();
  syncTabsToggle?.();
  syncFindScopeLabel?.();
  syncEditorsEyeButton?.();
  syncCheckpointTooltipStatus?.();
  countWords?.();
  renderDocuments?.();
  renderChapterTree?.();
  renderMarkers?.();
  if(projectMenuIsOpen?.())renderProjectMenu?.();
  if(currentView?.()==='canvas'){
    renderCanvasRail?.();renderNodes?.();drawEdges?.();renderDevelopmentalLane?.();syncApplyStructureButton?.();
    if(state.canvasLegendOpen)renderLegendDrawer?.();
    if(!$('#canvasToolPanel')?.classList.contains('hidden')){
      if($('#canvasStructureBtn')?.classList.contains('active'))openStructurePanel?.(false);else updateSelectionInspector?.();
    }
  }
  if(currentView?.()==='maps'){
    renderMapTree?.();renderMapVisual?.();
    if(state.selectedMapItemId&&!$('#mapToolPanel')?.classList.contains('hidden'))openMapItemInspector?.(state.selectedMapItemId).catch?.(console.warn);
    if(!$('#metroViewModal')?.classList.contains('hidden'))renderMetroPosterPreview?.();
  }
  if(currentView?.()==='tasks')loadTasks?.().catch?.(console.warn);
  if(state.split?.type==='tasks')renderTasksSplitBody?.().catch?.(console.warn);
  if(state.split?.type==='checkpoint')renderCheckpointSplit?.().catch?.(console.warn);
  if(state.split?.type==='history')renderHistorySplit?.().catch?.(console.warn);
  if(state.split?.type==='canvas')openCanvasSplit?.(state.split.id).catch?.(console.warn);
  if(state.split?.type==='map')openMapSplit?.(state.split.id).catch?.(console.warn);
  if(!$('#checkpointPanel')?.classList.contains('hidden'))renderCheckpointList?.();
  if(!$('#cutsPanel')?.classList.contains('hidden')){renderCutsList?.();syncCutsPanelContext?.()}
  syncNarrationVoiceChoices?.();
  if(!$('#narrationPanel')?.classList.contains('hidden'))renderNarrations?.();
  if(!$('#commentsPanel')?.classList.contains('hidden')){renderCommentsList?.();renderCommentsDetail?.(commentsWorkspace?.items?.find?.(x=>entityId(x.id)===entityId(commentsWorkspace.selectedId))||null)}
}

