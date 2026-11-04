// Calendar/progress rules. No browser, transport, notifications or manuscript writes.
(() => {
  const metrics=['words','characters','time','active'],periods=['daily','weekly','monthly'];
  const sounds=['Moss','Fern','Oak','Palm','Monstera','Pothos'];
  const dayKey=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  function mergeIntervals(intervals){
    const result=[];
    for(const span of intervals.filter(v=>Array.isArray(v)&&v.length===2&&v.every(Number.isFinite)&&v[1]>v[0]).sort((a,b)=>a[0]-b[0])){
      const last=result.at(-1);if(last&&span[0]<=last[1])last[1]=Math.max(last[1],span[1]);else result.push([...span]);
    }return result;
  }
  function period(kind,now=Date.now()){
    const start=new Date(now);start.setHours(0,0,0,0);
    if(kind==='weekly')start.setDate(start.getDate()-((start.getDay()+6)%7));
    if(kind==='monthly')start.setDate(1);
    const end=new Date(start);
    if(kind==='monthly')end.setMonth(end.getMonth()+1);else end.setDate(end.getDate()+(kind==='weekly'?7:1));
    return {key:dayKey(start),start:start.getTime(),end:end.getTime()};
  }
  const defaults=()=>({version:1,enabled:false,banner:false,chime:false,sound:'Moss',goals:[],entries:{},applied:[],receipts:{}});
  function normalize(raw){
    const out=defaults();if(!raw||typeof raw!=='object')return out;
    for(const key of ['enabled','banner','chime'])out[key]=raw[key]===true;
    if(sounds.includes(raw.sound))out.sound=raw.sound;
    out.goals=(Array.isArray(raw.goals)?raw.goals:[]).filter(g=>g&&/^[a-zA-Z0-9_-]{1,64}$/.test(g.id)&&metrics.includes(g.metric)&&periods.includes(g.period)&&Number.isFinite(g.amount)&&g.amount>0&&g.amount<=100000000).slice(0,8).map(g=>({id:g.id,metric:g.metric,period:g.period,amount:g.amount,created:Number(g.created)||Date.now()}));
    for(const [key,e] of Object.entries(raw.entries||{}).slice(-240)){
      if(!e||!metrics.includes(e.metric)||!periods.includes(e.period)||!Number.isFinite(e.value)||!Number.isFinite(e.amount)||e.amount<=0||!Number.isFinite(e.start)||!Number.isFinite(e.end)||!/^\d{4}-\d{2}-\d{2}$/.test(e.key)||!/^[a-zA-Z0-9_-]{1,64}$/.test(e.goal))continue;
      if(key!==`${e.goal}:${e.key}`)continue;
      out.entries[key]={goal:e.goal,metric:e.metric,period:e.period,amount:e.amount,key:e.key,start:e.start,end:e.end,value:Math.max(-1e10,Math.min(e.value,1e10)),achieved:Number(e.achieved)||0};
      if(['time','active'].includes(e.metric)&&Array.isArray(e.intervals)){
        out.entries[key].intervals=mergeIntervals(e.intervals.map(v=>Array.isArray(v)?[Math.max(e.start,v[0]),Math.min(e.end,v[1])]:[]));
        out.entries[key].timeBase=Number.isFinite(e.timeBase)?e.timeBase:0;
      }
    }
    out.applied=(Array.isArray(raw.applied)?raw.applied:[]).filter(id=>typeof id==='string'&&id.length<100).slice(-80);
    for(const [source,sequence] of Object.entries(raw.receipts||{})){
      if(!/^[a-zA-Z0-9_-]{1,64}$/.test(source)||['__proto__','constructor','prototype'].includes(source)||!Number.isSafeInteger(sequence)||sequence<0)throw new Error('TARGETS_RECEIPT_INVALID');
      out.receipts[source]=sequence;
    }
    return out;
  }
  function entry(doc,goal,now=Date.now()){
    const p=period(goal.period,now),key=`${goal.id}:${p.key}`;
    return doc.entries[key]||{goal:goal.id,metric:goal.metric,period:goal.period,amount:goal.amount,...p,value:0,achieved:0};
  }
  function apply(doc,operation){
    const sequenced=typeof operation.stream==='string';
    if(sequenced){
      if(!/^[a-zA-Z0-9_-]{1,64}$/.test(operation.stream)||['__proto__','constructor','prototype'].includes(operation.stream)||!Number.isSafeInteger(operation.sequence)||operation.sequence<1)throw new Error('TARGETS_SEQUENCE_INVALID');
      const cursor=doc.receipts[operation.stream]||0;
      if(operation.sequence<=cursor)return [];
      if(operation.sequence!==cursor+1)throw new Error('TARGETS_SEQUENCE_GAP');
    }else if(doc.applied.includes(operation.id))return [];
    const achieved=[];
    if(operation.settings){
      const settings=normalize(operation.settings);
      for(const key of ['enabled','banner','chime','sound','goals'])doc[key]=settings[key];
    }
    const captured=Array.isArray(operation.goals)?normalize({goals:operation.goals}).goals:null;
    if(captured||doc.enabled){
      for(const goal of captured||doc.goals){
        const saved=operation.calendar?.[goal.id],p=saved&&/^\d{4}-\d{2}-\d{2}$/.test(saved.key)&&Number.isFinite(saved.start)&&Number.isFinite(saved.end)?saved:null;
        const e=p?(doc.entries[`${goal.id}:${p.key}`]||{goal:goal.id,metric:goal.metric,period:goal.period,amount:goal.amount,...p,value:0,achieved:0}):entry(doc,goal,operation.at),key=`${goal.id}:${e.key}`;
        const timed=['time','active'].includes(goal.metric),span=operation.intervals?.[goal.metric];
        if(timed&&e.achieved)continue;
        if(timed&&Array.isArray(span)){
          if(!Array.isArray(e.intervals)){e.intervals=[];e.timeBase=e.value}
          e.intervals=mergeIntervals([...e.intervals,[Math.max(e.start,span[0]),Math.min(e.end,span[1])]]);
          e.value=e.timeBase+e.intervals.reduce((sum,[from,to])=>sum+(to-from)/1000,0);
        }else e.value=Math.max(-1e10,Math.min(1e10,e.value+(Number(operation.delta?.[goal.metric])||0)));
        if(!e.achieved&&e.value>=e.amount){e.achieved=operation.at;achieved.push({...e})}
        if(timed&&e.achieved){delete e.intervals;delete e.timeBase}
        doc.entries[key]=e;
      }
    }
    doc.applied=[...doc.applied,operation.id].slice(-80);
    if(sequenced)doc.receipts[operation.stream]=operation.sequence;
    // The bounded history and compact interval unions fit Targets' dedicated
    // 256 KiB ceiling; ordinary UI preferences retain their existing ceiling.
    const entries=Object.entries(doc.entries).sort((a,b)=>a[1].end-b[1].end);
    doc.entries=Object.fromEntries(entries.slice(-240));
    return achieved;
  }
  function progress(doc,now=Date.now()){
    return doc.enabled?doc.goals.map(g=>{const e=entry(doc,g,now);return {...e,value:Math.max(0,e.value),id:g.id,ratio:e.achieved?1:Math.max(0,Math.min(1,e.value/e.amount))}}):[];
  }
  function count(text,locale='en'){
    text=String(text||'').replace(/\r\n/g,'\n');
    let words=0;
    if(typeof Intl.Segmenter==='function')for(const part of new Intl.Segmenter(locale,{granularity:'word'}).segment(text)){if(part.isWordLike)words++}
    else words=text.trim()?text.trim().split(/\s+/u).length:0;
    return {words,characters:Array.from(text).length};
  }
  // A hidden window never accrues time. Long scheduler gaps (sleep) are ignored.
  function elapsed(from,to,{visible,focused,lastActivity,idle=45000}){
    if(!visible||!focused||to<=from||to-from>10000)return {time:0,active:0};
    return {time:(to-from)/1000,active:Math.max(0,Math.min(to,lastActivity+idle)-from)/1000};
  }
  const api={metrics,periods,sounds,period,defaults,normalize,entry,apply,progress,count,elapsed};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else window.BreadmakerTargetsModel=api;
})();
