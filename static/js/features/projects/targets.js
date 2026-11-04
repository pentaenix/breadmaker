// Optional personal project Targets. Reuses collaborator UI-state durability and
// Manuscript's modal, switches and delayed hover; never changes manuscript data.
const targetsModel=window.BreadmakerTargetsModel;
const targets={context:'',pid:'',user:'',doc:targetsModel.defaults(),pending:[],ready:false,loading:null,saving:null,lastTick:Date.now(),lastActivity:0,active:false,signature:'',audio:null,revision:0};
let targetsSession;
try{targetsSession=sessionStorage.getItem('breadmaker-targets-session')||uid();sessionStorage.setItem('breadmaker-targets-session',targetsSession)}catch{targetsSession=uid()}
const targetText=(key,vars={})=>i18nText(`targets.${key}`,vars);
const targetHtml=(key,vars={})=>escapeHtml(targetText(key,vars));
function targetsContext(){return currentProjectIdentity()&&!state.reviewerSession?`${currentProjectIdentity()}:${currentUserIdentity()}`:''}
function targetsCacheKey(){return `breadmaker-targets-outbox:${targets.context}:${targetsSession}`}
function targetsCache(){try{if(targets.pending.length)localStorage.setItem(targetsCacheKey(),JSON.stringify(targets.pending));else localStorage.removeItem(targetsCacheKey())}catch{/* Quota/private-mode failure never prevents writing. */}}
async function targetsRequest(options={}){
  const hosted=runtimeSupports('hosted_persistence');
  try{return await api(`/api/ui-state/${hosted?'targets-v1':`targets-v1-${targets.user}`}`,{...options,headers:{'X-Breadmaker-Project':targets.pid,...(hosted?{'X-Breadmaker-Targets-Account':targets.user}:{'X-Breadmaker-User':targets.user})}})}
  catch(error){if([401,403].includes(error?.status)||String(error?.message||'').includes('TARGETS_ACCOUNT_CONTEXT_CHANGED')){targets.ready=false;targetsStopSound();renderTargets()}throw error}
}
function targetsHideTips(){for(const id of ['targetsTopTip','targetsFooterTip','targetsFocusTip'])$(`#${id}`)?.classList.add('hidden')}
async function loadTargets(){
  const context=targetsContext();if(!context){targetsCache();targets.context='';targets.ready=false;targets.revision++;targetsStopSound();renderTargets();return false}
  if(targets.context===context&&targets.ready)return true;
  if(targets.context===context&&targets.loading)return targets.loading;
  targetsStopSound();targetsHideTips();targets.context=context;targets.pid=currentProjectIdentity();targets.user=currentUserIdentity();targets.ready=false;targets.pending=[];targets.doc=targetsModel.defaults();targets.lastTick=Date.now();targets.lastActivity=0;renderTargets();
  const revision=++targets.revision;
  const loading=(async()=>{
    try{
      const response=await targetsRequest();if(revision!==targets.revision||context!==targetsContext())return false;
      if(response.value&&response.value.version!==1)throw new Error('TARGETS_VERSION_UNSUPPORTED');
      targets.doc=targetsModel.normalize(response.value);
      // Recover only unacknowledged operations from this tab, including refresh.
      try{const cached=JSON.parse(localStorage.getItem(targetsCacheKey())||'[]');if(Array.isArray(cached))targets.pending=cached.filter(op=>op&&typeof op.id==='string'&&Number.isFinite(op.at)&&op.at<=Date.now()+60000)}catch{}
      for(const operation of targets.pending)targetsModel.apply(targets.doc,operation);
      targets.ready=true;renderTargets();if(targets.pending.length)flushTargets().catch(()=>{});return true;
    }catch(error){if(revision===targets.revision){targets.ready=false;targets.error=error}return false}
    finally{if(revision===targets.revision)targets.loading=null}
  })();targets.loading=loading;return loading;
}
function targetsEnqueue(delta={},at=Date.now(),settings=null){
  if(!targets.ready||targets.context!==targetsContext())return;
  if(!settings&&!targets.doc.goals.some(goal=>Number(delta[goal.metric])))return;
  const tail=targets.pending.at(-1),day=targetsModel.period('daily',at).key;
  // Coalesce quiet timer ticks; in-flight operation IDs remain immutable.
  const combine=!settings&&tail&&!tail.settings&&!tail.sealed&&!targets.savingIds?.has(tail.id)&&targetsModel.period('daily',tail.at).key===day;
  const operation=combine?tail:{id:uid(),at,delta:{},...(settings?{settings}:{} )};
  if(combine){for(const [key,value] of Object.entries(delta))tail.delta[key]=(tail.delta[key]||0)+value}
  else{operation.delta={...delta};targets.pending.push(operation)}
  // Apply just this increment, independent of the coalesced transport operation.
  const local={id:uid(),at,delta,...(settings?{settings}:{} )};
  const achieved=targetsModel.apply(targets.doc,local);targetsCache();renderTargets();
  if(achieved.length)targetsCelebrate(achieved);
}
async function flushTargets(throughId=null){
  if(!targets.ready||targets.context!==targetsContext()||!targets.pending.length)return;
  throughId=throughId||targets.pending.at(-1).id;
  if(targets.saving){await targets.saving;if(targets.pending.some(op=>op.id===throughId))return flushTargets(throughId);return}
  const revision=targets.revision,context=targets.context,batch=structuredClone(targets.pending.slice(0,40)),ids=new Set(batch.map(op=>op.id));targets.savingIds=ids;
  const save=async()=>{
    if(revision!==targets.revision||context!==targetsContext())return;
    // Merge into the latest personal record. Web Locks serialize tabs in this
    // browser; operation IDs make a successful-response-loss retry idempotent.
    const response=await targetsRequest();if(revision!==targets.revision||context!==targetsContext())return;
    if(response.value&&response.value.version!==1)throw new Error('TARGETS_VERSION_UNSUPPORTED');
    const doc=targetsModel.normalize(response.value);for(const operation of batch)targetsModel.apply(doc,operation);
    // A response can be lost after commit: never grow an operation once it may
    // have reached the store, or its stable ID would mask subsequent writing.
    for(const operation of targets.pending)if(ids.has(operation.id))operation.sealed=true;
    targetsCache();
    await targetsRequest({method:'PUT',body:JSON.stringify({value:doc}),keepalive:true});
    if(revision!==targets.revision||context!==targetsContext())return;
    targets.pending=targets.pending.filter(op=>!ids.has(op.id));targets.doc=doc;
    for(const operation of targets.pending)targetsModel.apply(targets.doc,operation);
    targetsCache();renderTargets();
  };
  const promise=(navigator.locks?navigator.locks.request(`breadmaker-targets:${context}`,save):save());targets.saving=promise;
  try{await promise}finally{if(targets.saving===promise){targets.saving=null;targets.savingIds=null}}
  if(targets.pending.some(op=>op.id===throughId)&&revision===targets.revision)return flushTargets(throughId);
}
function targetsClock(now=Date.now()){
  const previous=targets.lastTick;targets.lastTick=now;
  if(!targets.ready||!targets.doc.enabled||targets.context!==targetsContext())return;
  const visible=document.visibilityState==='visible',focused=document.hasFocus();
  // Split midnight so a delayed heartbeat cannot credit yesterday to today.
  const boundary=targetsModel.period('daily',previous).end;
  const chunks=boundary<now?[[previous,boundary],[boundary,now]]:[[previous,now]];
  for(const [from,to] of chunks){
    if(now-previous>10000)continue;
    const delta=targetsModel.elapsed(from,to,{visible,focused,lastActivity:targets.lastActivity});
    if(delta.time||delta.active)targetsEnqueue(delta,to===boundary?to-1:to);
  }
  const active=visible&&focused&&now-targets.lastActivity<45000;
  if(active!==targets.active){targets.active=active;renderTargets()}
}
function targetsActivity(event){
  if(!event.isTrusted||!targets.ready||!targets.doc.enabled||!document.hasFocus()||document.visibilityState!=='visible')return;
  if(event.type==='pointermove'&&!event.buttons)return;
  if(event.type==='keydown'&&(['Shift','Control','Alt','Meta','Tab','Escape'].includes(event.key)||event.metaKey||event.ctrlKey))return;
  const node=event.target instanceof Element?event.target:null;
  if(!node||!node.closest('#app,#modal')||node.closest('.targets-form,[data-target-status],#projectMenu,#projectSubmenu'))return;
  targetsClock();targets.lastActivity=Date.now();targets.active=true;
}
let targetsBeforeInput=null;
function targetsManuscriptText(chapter=null){return $$('.manuscript-block', $('#manuscriptPaper')).filter(node=>{const wrap=node.closest('.manuscript-block-wrap');return wrap?.dataset.blockId&&(chapter===null||wrap.dataset.chapterId===chapter)}).map(node=>node.textContent||'').join('\n')}
function targetsCaptureWriting(event){
  if(!event.isTrusted||!targets.ready||!targets.doc.enabled||!event.target.closest?.('#manuscriptPaper'))return;
  const prose=activeManuscriptProse(),chapter=prose?.closest('.manuscript-block-wrap')?.dataset.chapterId;if(!chapter)return;
  targetsBeforeInput={context:targets.context,chapter,count:targetsModel.count(targetsManuscriptText(chapter),i18nLocale())};
}
function targetsFinishWriting(){
  if(!targetsBeforeInput)return;
  const before=targetsBeforeInput;targetsBeforeInput=null;if(before.context!==targetsContext())return;
  const after=targetsModel.count(targetsManuscriptText(before.chapter),i18nLocale());
  const delta={words:after.words-before.count.words,characters:after.characters-before.count.characters};
  if(delta.words||delta.characters)targetsEnqueue(delta);
}
document.addEventListener('beforeinput',event=>{
  // The article is the editing host; native beforeinput targets the article,
  // while the live selection identifies the paragraph being written.
  if(['historyUndo','historyRedo'].includes(event.inputType))return;
  targetsCaptureWriting(event);
},true);
document.addEventListener('input',event=>{
  if(!event.isTrusted||!targetsBeforeInput||!event.target.closest?.('#manuscriptPaper'))return;
  // Read after the editor has reconciled split/merged paragraphs. Navigation,
  // rendering, imports and other members' edits never count as new writing.
  queueMicrotask(targetsFinishWriting);
},true);
// Manuscript owns Enter, paragraph joins and paste; some of those edits do not
// emit native input. Capture them before the owning handler and settle afterward.
document.addEventListener('keydown',event=>{if(!['Enter','Backspace','Delete','Tab'].includes(event.key))return;targetsCaptureWriting(event);queueMicrotask(targetsFinishWriting)},true);
for(const type of ['paste','cut'])document.addEventListener(type,event=>{targetsCaptureWriting(event);queueMicrotask(targetsFinishWriting)},true);
document.addEventListener('breadmaker-manuscript-history',event=>{
  if(!targets.ready||!targets.doc.enabled||targets.context!==targetsContext())return;
  const text=snapshot=>(snapshot.kind==='scene_edit'?snapshot.scene?.blocks||[]:(snapshot.chapters||[]).flatMap(chapter=>chapter.blocks||[])).filter(block=>block.block_type!=='image').map(block=>block.text_content||'').join('\n');
  const before=targetsModel.count(text(event.detail.before),i18nLocale()),after=targetsModel.count(text(event.detail.after),i18nLocale());
  targetsEnqueue({words:after.words-before.words,characters:after.characters-before.characters});
});
for(const type of ['pointerdown','pointermove','keydown','input','wheel','touchmove'])document.addEventListener(type,targetsActivity,{capture:true,passive:true});
function targetsStopSound(){if(targets.audio){targets.audio.pause();targets.audio.currentTime=0;targets.audio=null}}
function targetsPlaySound(sound,preview=false){
  targetsStopSound();if(!targetsModel.sounds.includes(sound)||document.visibilityState!=='visible')return;
  const audio=new Audio(`/static/audio/targets/${sound}.mp3`);audio.volume=.45;targets.audio=audio;
  audio.play().catch(()=>{if(preview)toast(targetText('sound_unavailable'))});
}
function targetsCelebrate(entries){
  if(document.visibilityState!=='visible'||!document.hasFocus())return;
  if(targets.doc.banner)toast(entries.length===1?targetText('achieved_'+entries[0].period):targetText('achieved_many',{count:i18nNumber(entries.length)}),4000);
  if(targets.doc.chime)targetsPlaySound(targets.doc.sound);
}
function targetsAmount(value,metric){
  value=Math.max(0,value);
  if(metric==='words'||metric==='characters')return i18nNumber(Math.floor(value));
  const seconds=Math.floor(value),minutes=Math.floor(seconds/60),rest=String(seconds%60).padStart(2,'0');
  return minutes>=60?`${Math.floor(minutes/60)}:${String(minutes%60).padStart(2,'0')}:${rest}`:`${minutes}:${rest}`;
}
function targetsProgressLabel(p){return `${targetsAmount(p.value,p.metric)}/${targetsAmount(p.amount,p.metric)} ${targetText(p.metric)}`}
function targetsRing(ratio,complete=false){return complete?'<span class="targets-check" aria-hidden="true">✓</span>':`<svg class="targets-ring" viewBox="0 0 20 20" aria-hidden="true"><circle class="targets-ring-track" cx="10" cy="10" r="7.5"/><circle class="targets-ring-value" cx="10" cy="10" r="7.5" pathLength="100" stroke-dasharray="${Math.max(0,Math.min(100,ratio*100))} 100"/></svg>`}
function renderTargets(){
  const enabled=targets.ready&&targets.doc.enabled&&targets.context===targetsContext(),progress=enabled?targetsModel.progress(targets.doc):[];
  const shown=progress.length>0,complete=shown&&progress.every(p=>p.achieved),ratio=shown?progress.reduce((sum,p)=>sum+p.ratio,0)/progress.length:0;
  const description=progress.map(p=>`${targetText(p.period)} · ${p.achieved?targetText('achieved_'+p.period):targetsProgressLabel(p)}`).join('\n');
  for(const id of ['targetsTop','targetsFooter','targetsFocus']){
    const button=$(`#${id}`);if(!button)continue;button.classList.toggle('hidden',!shown);button.setAttribute('aria-label',description||targetText('title'));
    if(!shown)continue;
    if(id==='targetsTop'){
      if(button.dataset.complete!==String(complete)||!button.firstChild){button.innerHTML=targetsRing(ratio,complete);button.dataset.complete=String(complete)}
      button.querySelector('.targets-ring-value')?.setAttribute('stroke-dasharray',`${ratio*100} 100`);
    }else{
      const ids=new Set(progress.map(p=>p.id));for(const node of [...button.children])if(!ids.has(node.dataset.targetId))node.remove();
      for(const p of progress){
        let node=button.querySelector(`[data-target-id="${CSS.escape(p.id)}"]`);
        if(!node){node=document.createElement('span');node.className='targets-progress';node.dataset.targetId=p.id;node.innerHTML='<span class="targets-progress-copy"></span><span class="targets-progress-mark"></span>';button.appendChild(node)}
        node.querySelector('.targets-progress-copy').textContent=targetsProgressLabel(p);
        const mark=node.querySelector('.targets-progress-mark'),checked=!!p.achieved;
        if(mark.dataset.complete!==String(checked)){mark.innerHTML=id==='targetsFocus'&&!checked?'':targetsRing(p.ratio,checked);mark.dataset.complete=String(checked)}
        mark.querySelector('.targets-ring-value')?.setAttribute('stroke-dasharray',`${p.ratio*100} 100`);
        node.classList.toggle('achieved',checked);node.classList.toggle('settled',checked&&Date.now()-p.achieved>4500);
      }
    }
    const tip=$(`#${id==='targetsTop'?'targetsTopTip':id==='targetsFooter'?'targetsFooterTip':'targetsFocusTip'}`);
    if(tip)tip.textContent=description+(progress.some(p=>p.metric==='active'&&!p.achieved)?`\n${targetText(targets.active?'active_running':'active_paused')}`:'');
  }
  if(!shown)targetsHideTips();
}
function targetsSwitch(key,label,value){return `<div class="project-settings-switch-row"><b>${targetHtml(label)}</b><button class="structure-switch${value?' active':''}" data-target-toggle="${key}" type="button" role="switch" aria-checked="${value}" aria-label="${targetHtml(label)}"><span aria-hidden="true"></span></button></div>`}
function targetsGoalRow(goal){
  const field=(label,input)=>`<div class="field"><label>${targetHtml(label)}${input}</label></div>`;
  return `<div class="targets-goal" data-target-goal="${escapeHtml(goal.id)}">${field('metric',`<select data-target-field="metric" aria-label="${targetHtml('metric')}">${targetsModel.metrics.map(metric=>`<option value="${metric}"${goal.metric===metric?' selected':''}>${targetHtml(metric)}</option>`).join('')}</select>`)}${field('period',`<select data-target-field="period" aria-label="${targetHtml('period')}">${targetsModel.periods.map(period=>`<option value="${period}"${goal.period===period?' selected':''}>${targetHtml(period)}</option>`).join('')}</select>`)}${field(['time','active'].includes(goal.metric)?'minutes':'amount',`<input data-target-field="amount" type="number" inputmode="numeric" min="1" max="100000000" step="1" required value="${['time','active'].includes(goal.metric)?goal.amount/60:goal.amount}" aria-label="${targetHtml('amount')}">`)}<button class="bare-button ui-close-button" data-target-remove type="button" aria-label="${targetHtml('remove')}">×</button></div>`;
}
async function openTargets(){
  if(!await loadTargets()){toast(targetText('load_failed'),4000);return}
  await flushTargets().catch(()=>{});
  const context=targets.context,draft=structuredClone(targets.doc),original=new Map(draft.goals.map(g=>[g.id,{...g}]));
  let closing=false;
  const history=Object.values(draft.entries).sort((a,b)=>b.start-a.start).slice(0,30),achieved=Object.values(draft.entries).filter(e=>e.achieved).length;
  showModal(targetText('title'),`<form class="targets-form" id="targetsForm">${targetsSwitch('enabled','enable',draft.enabled)}<div id="targetsOptions"${draft.enabled?'':' class="hidden"'}><section class="project-settings-section"><h3 class="project-settings-section-title">${targetHtml('goals')}</h3><div id="targetsGoalList">${draft.goals.map(targetsGoalRow).join('')}</div><button id="targetsAdd" class="quiet-button" type="button">+ ${targetHtml('add')}</button></section><section class="project-settings-section"><h3 class="project-settings-section-title">${targetHtml('celebration')}</h3>${targetsSwitch('banner','banner',draft.banner)}${targetsSwitch('chime','chime',draft.chime)}<div id="targetsSounds" class="targets-sounds${draft.chime?'':' hidden'}"><div class="field"><label for="targetsSound">${targetHtml('sound')}</label><select id="targetsSound">${targetsModel.sounds.map(name=>`<option${name===draft.sound?' selected':''}>${name}</option>`).join('')}</select></div><button id="targetsPreview" class="quiet-button" type="button">${targetHtml('preview')}</button></div></section><section class="project-settings-section"><h3 class="project-settings-section-title">${targetHtml('history')}</h3><p class="small-muted">${targetHtml('history_summary',{count:i18nNumber(achieved),total:i18nNumber(Object.keys(draft.entries).length)})}</p><div class="targets-history">${history.length?history.map(e=>`<div class="targets-history-row"><span><b>${targetHtml(e.period)} · ${targetHtml(e.metric)}</b><small>${escapeHtml(i18nDate(e.start,{month:'short',day:'numeric',year:'numeric'}))} · ${escapeHtml(targetsProgressLabel(e))}</small></span><span class="${e.achieved?'targets-check':'small-muted'}">${e.achieved?'✓':targetHtml(e.end<=Date.now()?'not_achieved':'in_progress')}</span></div>`).join(''):`<p class="small-muted">${targetHtml('history_empty')}</p>`}</div></section></div><p id="targetsError" class="small-muted" role="status"></p></form>`,`<button id="targetsCancel" class="bare-button">${escapeHtml(i18nSource('Cancel'))}</button><button id="targetsSave" class="soft-button">${escapeHtml(i18nSource('Save'))}</button>`,()=>{closing=true;targetsStopSound();targetsHideTips();hideContextMenuTip()});
  const readGoals=()=>$$('[data-target-goal]',$('#targetsGoalList')).map(row=>{const id=row.dataset.targetGoal,metric=$('[data-target-field="metric"]',row).value,period=$('[data-target-field="period"]',row).value,amount=Number($('[data-target-field="amount"]',row).value)*(['time','active'].includes(metric)?60:1);return {id,metric,period,amount,created:original.get(id)?.created||Date.now()}});
  const wireRows=()=>{
    for(const row of $$('[data-target-goal]')){
      $('[data-target-remove]',row).onclick=()=>{hideContextMenuTip();row.remove();$('#targetsAdd').disabled=false};
      for(const select of row.querySelectorAll('select')){select.setAttribute('aria-description',targetText('count_help')+' '+targetText('time_help'));select.onmouseenter=()=>queueContextMenuTip(select,targetText(['time','active'].includes($('[data-target-field="metric"]',row).value)?'time_help':'count_help'));select.onmouseleave=hideContextMenuTip;select.onblur=hideContextMenuTip}
      $('[data-target-field="metric"]',row).onchange=()=>{const input=$('[data-target-field="amount"]',row),metric=$('[data-target-field="metric"]',row).value;input.parentElement.firstChild.textContent=targetText(['time','active'].includes(metric)?'minutes':'amount')};
    }
    $('#targetsAdd').disabled=$$('[data-target-goal]').length>=8;
  };
  for(const toggle of $$('[data-target-toggle]'))toggle.onclick=()=>{const value=toggle.getAttribute('aria-checked')!=='true';toggle.setAttribute('aria-checked',String(value));toggle.classList.toggle('active',value);draft[toggle.dataset.targetToggle]=value;if(toggle.dataset.targetToggle==='enabled')$('#targetsOptions').classList.toggle('hidden',!value);if(toggle.dataset.targetToggle==='chime')$('#targetsSounds').classList.toggle('hidden',!value)};
  $('#targetsAdd').onclick=()=>{if($$('[data-target-goal]').length>=8)return;$('#targetsGoalList').insertAdjacentHTML('beforeend',targetsGoalRow({id:uid(),metric:'words',period:'daily',amount:500}));wireRows();$$('[data-target-goal]').at(-1).querySelector('select').focus()};
  $('#targetsPreview').onclick=()=>targetsPlaySound($('#targetsSound').value,true);
  $('#targetsCancel').onclick=()=>closeModal();
  const save=async event=>{
    event?.preventDefault();if(context!==targetsContext()){closeModal();return}
    if(draft.enabled&&!$('#targetsForm').reportValidity())return;
    const goals=readGoals();
    if(draft.enabled&&goals.some(g=>!Number.isInteger(g.amount)||g.amount<=0||g.amount>100000000)){$('#targetsError').textContent=targetText('invalid');return}
    // A changed goal starts fresh; immutable old snapshots remain in History.
    draft.goals=goals.map(g=>{const old=original.get(g.id);return old&&(old.metric!==g.metric||old.period!==g.period||old.amount!==g.amount)?{...g,id:uid(),created:Date.now()}:g});draft.sound=$('#targetsSound').value;
    $('#targetsSave').disabled=true;$('#targetsCancel').disabled=true;$('#modalClose').disabled=true;
    try{
      targetsClock();await flushTargets();targetsEnqueue({},Date.now(),draft);await flushTargets();
      if(!closing&&context===targetsContext())closeModal();
    }catch(error){if(!closing){$('#targetsError').textContent=targetText('save_failed');$('#targetsSave').disabled=false;$('#targetsCancel').disabled=false;$('#modalClose').disabled=false}}
  };
  $('#targetsSave').onclick=save;$('#targetsForm').onsubmit=save;wireRows();
}
for(const [button,tip] of [['targetsTop','targetsTopTip'],['targetsFooter','targetsFooterTip'],['targetsFocus','targetsFocusTip']]){
  $(`#${button}`).onclick=()=>{targetsHideTips();openTargets()};attachManuscriptToolTooltip(button,tip,{delay:300});
}
window.addEventListener('blur',()=>{targetsClock();targets.lastActivity=0;targetsStopSound();targetsHideTips();flushTargets().catch(()=>{})});
window.addEventListener('focus',()=>{targets.lastTick=Date.now();renderTargets()});
document.addEventListener('visibilitychange',()=>{targetsClock();targets.lastActivity=0;targets.lastTick=Date.now();if(document.hidden){targetsStopSound();targetsHideTips();flushTargets().catch(()=>{})}});
window.addEventListener('pagehide',()=>{targetsClock();targetsCache();flushTargets().catch(()=>{})});
window.addEventListener('online',()=>{if(!targets.ready)loadTargets();else flushTargets().catch(()=>{})});
let targetsHeartbeat=0;
setInterval(()=>{
  if(document.documentElement.dataset.breadmakerBootReady!=='true')return;
  if(targets.context!==targetsContext()){targetsHideTips();loadTargets();return}
  if(!targets.ready)return;
  targetsClock();renderTargets();
  if(++targetsHeartbeat%15===0)flushTargets().catch(()=>{});
},1000);
