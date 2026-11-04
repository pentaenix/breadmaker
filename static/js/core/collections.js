// Shared compact collection-rail presentation helpers.

// Transient phone drawers share Manuscript's rail and toolbar grammar. Desktop
// collapse preferences remain owned/persisted by their individual workspaces.
const phoneRailMedia=matchMedia('(max-width:640px)'),phoneRails=new Map();
let phoneRailMenuOwner=null;
function phoneRailOwnsMenu(view){const item=phoneRails.get(view);return phoneRailMenuOwner?.view===view&&item?.open&&item.rail.contains(phoneRailMenuOwner.node)&&!$('#contextMenu').classList.contains('hidden')}
function setPhoneRailMenuOwner(node){
  const entry=[...phoneRails].find(([,item])=>item.open&&item.rail.contains(node));
  const owner=entry?{view:entry[0],node}:null;phoneRailMenuOwner=owner;
  if(owner)queueMicrotask(()=>{if(phoneRailMenuOwner===owner&&phoneRailOwnsMenu(owner.view))$('#contextMenu button:not(:disabled)')?.focus()});
}
function setPhoneRail(view,open,{restoreFocus=false}={}){
  const item=phoneRails.get(view);if(!item)return;
  open=!!open&&phoneRailMedia.matches;
  if(item.open===open)return;
  if(open){
    closePhoneRails();
    item.primaryInert=item.primary.inert;item.rail.classList.remove('collapsed');item.primary.inert=true;
  }else{
    if(phoneRailOwnsMenu(view)){$('#contextMenu').classList.add('hidden');hideContextMenuTip()}
    if(phoneRailMenuOwner?.view===view)phoneRailMenuOwner=null;
    item.rail.classList.toggle('collapsed',item.desktopCollapsed());
    item.primary.inert=item.primaryInert||item.primary.getAttribute('aria-busy')==='true';
  }
  item.open=open;item.shell.classList.toggle('phone-nav-open',open);item.opener.setAttribute('aria-expanded',String(open));
  if(open)item.toggle.focus({preventScroll:true});
  else if(restoreFocus&&phoneRailMedia.matches&&!$('#app').inert)item.opener.focus({preventScroll:true});
}
function closePhoneRails(){for(const [view,item]of phoneRails)if(item.open)setPhoneRail(view,false)}
function installPhoneRail(view,railId,openerId,toggleId,primarySelector,backdropId,desktopCollapsed){
  const shell=$(`#${view}View`),rail=$(`#${railId}`),opener=$(`#${openerId}`),toggle=$(`#${toggleId}`),primary=shell?.querySelector(primarySelector);
  if(!shell||!rail||!opener||!toggle||!primary)return;
  shell.classList.add('phone-rail-workspace');rail.classList.add('phone-rail');opener.classList.add('phone-rail-button');$(`#${backdropId}`).classList.add('phone-rail-backdrop');
  const item={shell,rail,opener,toggle,primary,desktopCollapsed,open:false};phoneRails.set(view,item);
  opener.onclick=()=>setPhoneRail(view,true);
  $(`#${backdropId}`).onclick=()=>setPhoneRail(view,false,{restoreFocus:true});
  toggle.addEventListener('click',event=>{if(!phoneRailMedia.matches)return;event.preventDefault();event.stopImmediatePropagation();setPhoneRail(view,false,{restoreFocus:true})},true);
  // An async UI-state read may restore the desktop class while a drawer is
  // open. Keep the live drawer expanded without writing that preference back.
  new MutationObserver(()=>{if(item.open&&rail.classList.contains('collapsed'))rail.classList.remove('collapsed')}).observe(rail,{attributes:true,attributeFilter:['class']});
}
installPhoneRail('manuscript','bookNavigation','phoneBookNavBtn','toggleBookNavBtn','#manuscriptPrimaryPane','phoneBookNavBackdrop',()=>state.bookNavCollapsed);
installPhoneRail('canvas','canvasRail','phoneCanvasNavBtn','toggleRail','.canvas-shell','phoneCanvasNavBackdrop',()=>!state.canvasRailExpanded);
installPhoneRail('maps','mapRail','phoneMapNavBtn','toggleMapRail','.map-workspace','phoneMapNavBackdrop',()=>!state.mapRailExpanded);
installPhoneRail('reviews','reviewRail','phoneReviewNavBtn','toggleReviewRail','.review-workspace-shell','phoneReviewNavBackdrop',()=>!state.reviewRailExpanded);
phoneRailMedia.addEventListener('change',()=>closePhoneRails());
document.addEventListener('keydown',event=>{
  const entry=[...phoneRails].find(([,item])=>item.open);if(!entry||!$('#modal').classList.contains('hidden'))return;
  const[view,item]=entry;
  if(event.key==='Escape'){
    if(phoneRailOwnsMenu(view)){
      const opener=phoneRailMenuOwner.node;closeTransientUI();phoneRailMenuOwner=null;
      event.preventDefault();event.stopImmediatePropagation();opener.focus();return;
    }
    if(!$('#contextMenu').classList.contains('hidden'))return;
    event.preventDefault();setPhoneRail(view,false,{restoreFocus:true});
  }else if(event.key==='Tab'){
    const ownsMenu=phoneRailOwnsMenu(view),selector='button:not(:disabled),[tabindex="0"]';
    const controls=[...item.rail.querySelectorAll(selector),...(ownsMenu?$('#contextMenu').querySelectorAll(selector):[])].filter(node=>node.getClientRects().length);
    const first=controls[0],last=controls.at(-1);
    if(ownsMenu){const index=controls.indexOf(document.activeElement);event.preventDefault();controls[index<0?0:(index+(event.shiftKey?-1:1)+controls.length)%controls.length]?.focus()}
    else if(!item.rail.contains(document.activeElement)||(event.shiftKey&&document.activeElement===first)||(!event.shiftKey&&document.activeElement===last)){event.preventDefault();(event.shiftKey?last:first)?.focus()}
  }
});

function compactRailLabel(value,prefixes=[]){
  const name=String(value||'').trim();
  if(prefixes.length){
    const escaped=prefixes.map(item=>String(item).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|');
    const prefixed=name.match(new RegExp(`^(?:${escaped})\\s+(\\d+|[A-Za-z])(?=$|[\\s:._-])`,'i'));
    if(prefixed)return prefixed[1];
  }
  const bare=name.match(/^(\d+|[A-Za-z])(?=$|[\s:._-])/);if(bare)return bare[1];
  const first=Array.from(name).find(ch=>/[\p{L}\p{N}]/u.test(ch));return first||'?';
}

function appendCollectionQuickAdd(container,label,onClick){
  if(!container)return;const wrap=document.createElement('div');wrap.className='collection-quick-add-row';const button=document.createElement('button');button.type='button';button.className='collection-quick-add';button.title=label;button.setAttribute('aria-label',label);button.innerHTML='<span aria-hidden="true">+</span>';button.onclick=event=>{event.preventDefault();event.stopPropagation();onClick()};wrap.appendChild(button);container.appendChild(wrap);
}
