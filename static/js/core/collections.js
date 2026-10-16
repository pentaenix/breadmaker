// Shared compact collection-rail presentation helpers.

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
