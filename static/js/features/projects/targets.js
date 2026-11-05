// Optional personal project Targets. Reuses collaborator UI-state durability and
// Manuscript's modal, switches and delayed hover; never changes manuscript data.
const targetsModel=window.BreadmakerTargetsModel;
const targets={context:'',pid:'',user:'',doc:targetsModel.defaults(),pending:[],ready:false,loading:null,saving:null,lastTick:Date.now(),lastActivity:0,active:false,signature:'',audio:null,revision:0};
const targetsJournal=window.BreadmakerTargetsJournal;
const targetText=(key,vars={})=>i18nText(`targets.${key}`,vars);
const targetHtml=(key,vars={})=>escapeHtml(targetText(key,vars));
function targetsContext(){return currentProjectIdentity()&&!state.reviewerSession?`${currentProjectIdentity()}:${currentUserIdentity()}`:''}
function targetsAcknowledged(doc,operation){return operation.stream&&(doc.receipts[operation.stream]||0)>=operation.sequence}
function targetsMergePending(records,doc=targets.doc){
  const byId=new Map(targets.pending.map(op=>[op.id,op]));for(const op of records)byId.set(op.id,op);
  targets.pending=[...byId.values()].filter(op=>!targetsAcknowledged(doc,op));
}
function targetsPreviewPending(){for(const op of targets.pending){const {stream,sequence,...preview}=op;targetsModel.apply(targets.doc,preview)}}
async function targetsRequest(options={}){
  const hosted=runtimeSupports('hosted_persistence');
  try{return await api(`/api/ui-state/${hosted?'targets-v1':`targets-v1-${targets.user}`}`,{...options,headers:{'X-Breadmaker-Project':targets.pid,...(hosted?{'X-Breadmaker-Targets-Account':targets.user}:{'X-Breadmaker-User':targets.user})}})}
  catch(error){if([401,403].includes(error?.status)||String(error?.message||'').includes('TARGETS_ACCOUNT_CONTEXT_CHANGED')){targets.ready=false;targetsStopSound();renderTargets()}throw error}
}
function targetsHideTips(){for(const id of ['targetsTopTip','targetsFooterTip','targetsFocusTip'])$(`#${id}`)?.classList.add('hidden')}
async function loadTargets(){
  const context=targetsContext();if(!context){targets.context='';targets.ready=false;targets.revision++;targetsStopSound();renderTargets();return false}
  if(targets.context===context&&targets.ready)return true;
  if(targets.context===context&&targets.loading)return targets.loading;
  targetsStopSound();targetsHideTips();targets.context=context;targets.pid=currentProjectIdentity();targets.user=currentUserIdentity();targets.ready=false;targets.pending=[];targets.doc=targetsModel.defaults();targets.lastTick=Date.now();targets.lastActivity=0;renderTargets();
  const revision=++targets.revision;
  const loading=(async()=>{
    try{
      const response=await targetsRequest();if(revision!==targets.revision||context!==targetsContext())return false;
      if(response.value&&response.value.version!==1)throw new Error('TARGETS_VERSION_UNSUPPORTED');
      targets.doc=targetsModel.normalize(response.value);
      const recovered=await targetsJournal.read(context);if(revision!==targets.revision||context!==targetsContext())return false;
      targetsMergePending(recovered);targetsPreviewPending();
      targets.ready=true;renderTargets();if(targets.pending.length)flushTargets().catch(()=>{});return true;
    }catch(error){if(revision===targets.revision){targets.ready=false;targets.error=error}return false}
    finally{if(revision===targets.revision)targets.loading=null}
  })();targets.loading=loading;return loading;
}
function targetsEnqueue(delta={},at=Date.now(),settings=null,intervals=null){
  if(!targets.ready||targets.context!==targetsContext())return;
  // Timed achievements no longer consume clock intervals. Do not fill the
  // durable outbox or rewrite receipts for those no-op ticks; a new calendar
  // period resumes automatically, and writing goals keep their net totals.
  if(!settings&&!targets.doc.goals.some(goal=>Number(delta[goal.metric])&&(!['time','active'].includes(goal.metric)||!targetsModel.entry(targets.doc,goal,at).achieved)))return;
  const operation={id:uid(),at,delta:{...delta},sealed:true,...(intervals?{intervals}:{}),...(settings?{settings}:{goals:structuredClone(targets.doc.goals),calendar:Object.fromEntries(targets.doc.goals.map(goal=>[goal.id,targetsModel.period(goal.period,at)]))})};
  const context=targets.context;targets.pending.push(operation);
  // enqueue writes the synchronous pre-journal before returning its IDB promise.
  targetsJournal.enqueue(context,operation).then(stored=>{if(context===targets.context){const pending=targets.pending.find(op=>op.id===stored.id);if(pending)Object.assign(pending,stored)}}).catch(error=>{targets.journalError=error});
  const achieved=targetsModel.apply(targets.doc,operation);renderTargets();
  if(achieved.length)targetsCelebrate(achieved);
}
async function flushTargets(){
  if(!targets.ready||targets.context!==targetsContext())return;
  if(targets.saving){await targets.saving;return flushTargets()}
  const revision=targets.revision,context=targets.context,current=()=>revision===targets.revision&&context===targetsContext();
  const save=async()=>{
    const records=await targetsJournal.read(context);if(!current())return;
    targetsMergePending(records);
    // A fixed snapshot avoids chasing continuously arriving timer operations.
    const batches=records.length?Array.from({length:Math.ceil(records.length/40)},(_,i)=>records.slice(i*40,i*40+40)):[[]];
    for(const batch of batches){
      let committed=null;
      for(let attempt=0;attempt<8;attempt++){
        const response=await targetsRequest();if(!current())return;
        if(response.value&&response.value.version!==1)throw new Error('TARGETS_VERSION_UNSUPPORTED');
        if(typeof response.revision!=='string')throw new Error('TARGETS_REVISION_REQUIRED');
        const doc=targetsModel.normalize(response.value),unapplied=batch.filter(op=>!targetsAcknowledged(doc,op));
        for(const operation of unapplied)targetsModel.apply(doc,operation);
        if(unapplied.length){
          try{await targetsRequest({method:'PUT',body:JSON.stringify({value:doc,expected_revision:response.revision}),keepalive:true})}
          catch(error){if(error?.status===409&&String(error.message).includes('TARGETS_REVISION_CONFLICT'))continue;throw error}
        }
        committed=doc;break;
      }
      if(!committed)throw new Error('TARGETS_REVISION_CONFLICT');
      if(!current())return;
      await targetsJournal.acknowledge(context,committed.receipts);if(!current())return;
      targetsMergePending([],committed);targets.doc=committed;targetsPreviewPending();renderTargets();
    }
  };
  const promise=save();targets.saving=promise;
  try{await promise}finally{if(targets.saving===promise)targets.saving=null}
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
    if(delta.time||delta.active)targetsEnqueue(delta,to===boundary?to-1:to,null,{...(delta.time?{time:[from,to]}:{}),...(delta.active?{active:[from,Math.min(to,targets.lastActivity+45000)]}:{})});
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
function targetsRing(ratio,complete=false){return `<svg class="targets-ring${complete?' targets-check':''}" viewBox="0 0 20 20" aria-hidden="true"><circle class="targets-ring-track" cx="10" cy="10" r="7.5"/><circle class="targets-ring-value" cx="10" cy="10" r="7.5" pathLength="100" stroke-dasharray="${complete?100:Math.max(0,Math.min(100,ratio*100))} 100"/>${complete?'<circle class="targets-ring-seed" cx="10" cy="10" r="2"/>':''}</svg>`}
function renderTargets(){
  const enabled=targets.ready&&targets.doc.enabled&&targets.context===targetsContext(),progress=enabled?targetsModel.progress(targets.doc):[];
  const shown=progress.length>0,complete=shown&&progress.every(p=>p.achieved),ratio=shown?progress.reduce((sum,p)=>sum+p.ratio,0)/progress.length:0;
  const description=progress.map(p=>`${targetText(p.period)} · ${targetsProgressLabel(p)}${p.achieved?' · '+targetText('achieved_'+p.period):''}`).join('\n');
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
function targetsHistoryRows(history){
  return history.map(e=>`<div class="targets-history-row"><span><b>${targetHtml(e.period)} · ${targetHtml(e.metric)}</b><small>${escapeHtml(i18nDate(e.start,{month:'short',day:'numeric',year:'numeric'}))} · ${escapeHtml(targetsProgressLabel(e))}</small></span>${e.achieved?`<span role="img" aria-label="${targetHtml('achieved_'+e.period)}">${targetsRing(1,true)}</span>`:`<span class="small-muted">${targetHtml(e.end<=Date.now()?'not_achieved':'in_progress')}</span>`}</div>`).join('')||`<p class="small-muted">${targetHtml('history_empty')}</p>`;
}
function targetsHistoryChart(history,period){
  // Only observed periods are shown. Missing days are not failed goals, and
  // unlike measures are never summed into one invented word/time total.
  const groups=new Map();
  for(const entry of history.filter(e=>e.period===period)){const rows=groups.get(entry.key)||[];rows.push(entry);groups.set(entry.key,rows)}
  return [...groups].sort(([a],[b])=>a.localeCompare(b)).slice(period==='daily'?-28:-12).map(([key,rows])=>{
    const achieved=rows.filter(e=>e.achieved).length,description=rows.map(e=>targetsProgressLabel(e)+(e.achieved?' · '+targetText('achieved_'+period):'')).join('\n');
    const date=new Date(key+'T12:00:00'),label=i18nDate(date.getTime(),{month:'short',...(period==='monthly'?{year:'numeric'}:{day:'numeric'})});
    const summary=`${targetText(period)} · ${label} · ${targetText('history_group',{count:i18nNumber(achieved),total:i18nNumber(rows.length)})}\n${description}`;
    return `<button type="button" class="targets-history-cell${achieved===rows.length?' achieved':achieved?' partial':''}" aria-label="${escapeHtml(summary)}" data-target-history-tip="${escapeHtml(summary)}"><span>${escapeHtml(label)}</span>${achieved===rows.length?targetsRing(1,true):''}</button>`;
  }).join('')||`<p class="small-muted">${targetHtml('history_empty')}</p>`;
}
async function openTargets(){
  if(!await loadTargets()){toast(targetText('load_failed'),4000);return}
  await flushTargets().catch(()=>{});
  const context=targets.context,draft=structuredClone(targets.doc),original=new Map(draft.goals.map(g=>[g.id,{...g}]));
  let closing=false,activeGoal=draft.goals[0]?.id||'';
  const history=Object.values(draft.entries).sort((a,b)=>b.start-a.start),achieved=Object.values(draft.entries).filter(e=>e.achieved).length;
  showModal(targetText('title'),`<form class="targets-form" id="targetsForm">
    ${targetsSwitch('enabled','enable',draft.enabled)}
    <div id="targetsOptions"${draft.enabled?'':' class="hidden"'}>
      <section class="project-settings-section targets-goals-section">
        <h3 class="project-settings-section-title">${targetHtml('goals')}</h3>
        <div class="targets-goal-actions"><div class="field" id="targetsGoalPicker"><label for="targetsGoalSelect">${targetHtml('choose_goal')}</label><select id="targetsGoalSelect"></select></div><button id="targetsAdd" class="quiet-button" type="button">+ ${targetHtml('add')}</button></div>
        <div id="targetsGoalList">${draft.goals.map(targetsGoalRow).join('')}</div>
      </section>
      <section class="project-settings-section targets-celebration-section">
        <h3 class="project-settings-section-title">${targetHtml('celebration')}</h3>
        ${targetsSwitch('banner','banner',draft.banner)}${targetsSwitch('chime','chime',draft.chime)}
        <div id="targetsSounds" class="targets-sounds${draft.chime?'':' hidden'}"><div class="field"><label for="targetsSound">${targetHtml('sound')}</label><span class="narration-select"><select id="targetsSound">${targetsModel.sounds.map(name=>`<option${name===draft.sound?' selected':''}>${name}</option>`).join('')}</select><svg class="view-menu-chevron" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></span></div></div>
      </section>
      <section class="project-settings-section targets-history-section" id="targetsHistoryDetails">
        <button id="targetsHistoryToggle" type="button" class="project-settings-row" aria-expanded="false" aria-controls="targetsHistoryContent"><span><b>${targetHtml('history')}</b><small>${targetHtml('history_summary',{count:i18nNumber(achieved),total:i18nNumber(Object.keys(draft.entries).length)})}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>
        <div id="targetsHistoryContent" class="targets-history-content hidden"><div class="targets-history-controls"><label>${targetHtml('history_view')}<select id="targetsHistoryView"><option value="list">${targetHtml('history_list')}</option><option value="chart">${targetHtml('history_chart')}</option></select></label><label id="targetsHistoryPeriodField" class="hidden">${targetHtml('history_period')}<select id="targetsHistoryPeriod">${targetsModel.periods.map(period=>`<option value="${period}">${targetHtml(period)}</option>`).join('')}</select></label></div>
        <div class="targets-history" tabindex="0" role="region" aria-label="${targetHtml('history')}"><div id="targetsHistoryList">${targetsHistoryRows(history.slice(0,30))}</div><div id="targetsHistoryChart" class="targets-history-chart hidden"></div></div></div>
      </section>
    </div><p id="targetsError" class="small-muted" role="status"></p></form>`,
    `<button id="targetsCancel" class="bare-button">${escapeHtml(i18nSource('Cancel'))}</button><button id="targetsSave" class="soft-button">${escapeHtml(i18nSource('Save'))}</button>`,()=>{closing=true;targetsStopSound();targetsHideTips();hideContextMenuTip()});
  const readGoals=()=>$$('[data-target-goal]',$('#targetsGoalList')).map(row=>{const id=row.dataset.targetGoal,metric=$('[data-target-field="metric"]',row).value,period=$('[data-target-field="period"]',row).value,amount=Number($('[data-target-field="amount"]',row).value)*(['time','active'].includes(metric)?60:1);return {id,metric,period,amount,created:original.get(id)?.created||Date.now()}});
  const syncGoalPicker=()=>{
    const rows=$$('[data-target-goal]');if(!rows.some(row=>row.dataset.targetGoal===activeGoal))activeGoal=rows[0]?.dataset.targetGoal||'';
    $('#targetsGoalPicker').classList.toggle('hidden',rows.length<2);
    $('#targetsGoalSelect').innerHTML=readGoals().map(goal=>`<option value="${escapeHtml(goal.id)}">${targetHtml(goal.period)} · ${targetHtml(goal.metric)} · ${escapeHtml(targetsAmount(goal.amount,goal.metric))}</option>`).join('');
    $('#targetsGoalSelect').value=activeGoal;for(const row of rows)row.hidden=row.dataset.targetGoal!==activeGoal;
  };
  const wireRows=()=>{
    for(const row of $$('[data-target-goal]')){
      $('[data-target-remove]',row).onclick=()=>{hideContextMenuTip();row.remove();wireRows()};
      for(const select of row.querySelectorAll('select')){select.setAttribute('aria-description',targetText('count_help')+' '+targetText('time_help'));select.onmouseenter=()=>queueContextMenuTip(select,targetText(['time','active'].includes($('[data-target-field="metric"]',row).value)?'time_help':'count_help'));select.onmouseleave=hideContextMenuTip;select.onblur=hideContextMenuTip}
      $('[data-target-field="metric"]',row).onchange=()=>{const input=$('[data-target-field="amount"]',row),metric=$('[data-target-field="metric"]',row).value;input.parentElement.firstChild.textContent=targetText(['time','active'].includes(metric)?'minutes':'amount');syncGoalPicker()};
      for(const input of row.querySelectorAll('input,select'))input.oninput=syncGoalPicker;
    }
    $('#targetsAdd').disabled=$$('[data-target-goal]').length>=8;syncGoalPicker();
  };
  for(const toggle of $$('[data-target-toggle]'))toggle.onclick=()=>{const value=toggle.getAttribute('aria-checked')!=='true';toggle.setAttribute('aria-checked',String(value));toggle.classList.toggle('active',value);draft[toggle.dataset.targetToggle]=value;if(toggle.dataset.targetToggle==='enabled')$('#targetsOptions').classList.toggle('hidden',!value);if(toggle.dataset.targetToggle==='chime')$('#targetsSounds').classList.toggle('hidden',!value)};
  $('#targetsAdd').onclick=()=>{if($$('[data-target-goal]').length>=8)return;$('#targetsGoalList').insertAdjacentHTML('beforeend',targetsGoalRow({id:uid(),metric:'words',period:'daily',amount:500}));activeGoal=$$('[data-target-goal]').at(-1).dataset.targetGoal;wireRows();$$('[data-target-goal]').at(-1).querySelector('select').focus()};
  $('#targetsGoalSelect').onchange=event=>{activeGoal=event.target.value;syncGoalPicker()};
  $('#targetsSound').onchange=()=>targetsPlaySound($('#targetsSound').value,true);
  const renderHistory=()=>{
    const chart=$('#targetsHistoryView').value==='chart';$('#targetsHistoryPeriodField').classList.toggle('hidden',!chart);$('#targetsHistoryList').classList.toggle('hidden',chart);$('#targetsHistoryChart').classList.toggle('hidden',!chart);hideContextMenuTip();
    if(chart){$('#targetsHistoryChart').innerHTML=targetsHistoryChart(history,$('#targetsHistoryPeriod').value);for(const cell of $$('[data-target-history-tip]')){const show=()=>queueContextMenuTip(cell,cell.dataset.targetHistoryTip);cell.onmouseenter=show;cell.onfocus=()=>queueContextMenuTip(cell,cell.dataset.targetHistoryTip,{focus:true});cell.onmouseleave=cell.onblur=hideContextMenuTip}}
  };
  $('#targetsHistoryView').onchange=$('#targetsHistoryPeriod').onchange=renderHistory;$('#targetsHistoryToggle').onclick=()=>{const open=$('#targetsHistoryToggle').getAttribute('aria-expanded')!=='true';$('#targetsHistoryToggle').setAttribute('aria-expanded',String(open));$('#targetsHistoryDetails').classList.toggle('expanded',open);$('#targetsHistoryContent').classList.toggle('hidden',!open);hideContextMenuTip()};
  $('#targetsCancel').onclick=()=>closeModal();
  const save=async event=>{
    event?.preventDefault();if(context!==targetsContext()){closeModal();return}
    const invalid=$$('[data-target-goal]').find(row=>!row.querySelector('input').checkValidity());
    if(draft.enabled&&invalid){activeGoal=invalid.dataset.targetGoal;syncGoalPicker();invalid.querySelector('input').reportValidity();return}
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
window.addEventListener('focus',()=>{targets.lastTick=Date.now();renderTargets();if(targets.ready)flushTargets().catch(()=>{})});
document.addEventListener('visibilitychange',()=>{targetsClock();targets.lastActivity=0;targets.lastTick=Date.now();if(document.hidden){targetsStopSound();targetsHideTips();flushTargets().catch(()=>{})}});
window.addEventListener('pagehide',()=>{targetsClock();flushTargets().catch(()=>{})});
window.addEventListener('online',()=>{if(!targets.ready)loadTargets();else flushTargets().catch(()=>{})});
let targetsHeartbeat=0;
setInterval(()=>{
  if(document.documentElement.dataset.breadmakerBootReady!=='true')return;
  if(targets.context!==targetsContext()){targetsHideTips();loadTargets();return}
  if(!targets.ready)return;
  targetsClock();renderTargets();
  if(++targetsHeartbeat%15===0&&(targets.doc.enabled||targets.pending.length))flushTargets().catch(()=>{});
},1000);
