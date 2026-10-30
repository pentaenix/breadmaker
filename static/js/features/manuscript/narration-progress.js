// Device-local numeric calibration only: no prose, recording IDs, or telemetry.
class BreadmakerNarrationTiming {
  constructor(backend='wasm'){
    this.threads=backend==='webgpu'?0:globalThis.crossOriginIsolated?Math.min(4,Math.max(1,Math.floor((navigator.hardwareConcurrency||2)/2))):1;
    this.key=`breadmaker-narration-timing-v1-${backend==='webgpu'?'webgpu':this.threads}`;this.data={loadMs:backend==='webgpu'?20000:8000,samples:{}};
    try{const saved=JSON.parse(window.localStorage?.getItem(this.key)||'null');if(saved&&Number.isFinite(saved.loadMs)&&saved.loadMs>0&&saved.loadMs<180000&&saved.samples&&typeof saved.samples==='object')this.data=saved}catch{}
  }
  parts(text,voice){return voice.startsWith('e')?Math.max(1,Math.ceil(text.length/300)):Math.max(1,(text.match(/[.!?] /g)||[]).length+1,Math.ceil(text.length/400))}
  estimate(chars,voice,cold=false,parts=1){
    const language=({a:'en',b:'en',e:'es',f:'fr',i:'it',j:'ja',p:'pt',z:'zh'})[voice[0]]||'en',key=language+(cold?'-cold':'-warm');
    const samples=this.data.samples[key]||this.data.samples[language+'-warm']||[];
    const rates=Array.isArray(samples)?samples.filter(sample=>sample&&Number.isFinite(sample.chars)&&sample.chars>0&&sample.chars<=20000&&Number.isFinite(sample.ms)&&sample.ms>0&&sample.ms<3600000).map(sample=>sample.ms/(sample.chars+40*(Number.isFinite(sample.parts)&&sample.parts>0&&sample.parts<1000?sample.parts:1))).sort((a,b)=>a-b):[];
    const rate=rates.length?rates[Math.floor(rates.length/2)]:(this.threads===0?20:this.threads===1?350:150);
    // First WebGPU inference compiles shaders even after model prewarming.
    const compilation=this.threads===0&&cold&&!this.data.samples[key]?.length?15000:0;
    return Math.max(750,(chars+40*parts)*rate+compilation);
  }
  recordLoad(ms){if(Number.isFinite(ms)&&ms>0&&ms<180000){const previous=Array.isArray(this.data.loads)?this.data.loads:[];this.data.loads=[...previous.filter(value=>Number.isFinite(value)&&value>0&&value<180000).slice(-7),ms];const sorted=[...this.data.loads].sort((a,b)=>a-b);this.data.loadMs=sorted[Math.floor(sorted.length/2)];this.save()}}
  recordGeneration(chars,voice,ms,cold,parts=1){
    if(!Number.isFinite(ms)||ms<=0||ms>=3600000||!Number.isFinite(chars)||chars<=0)return;
    const key=(({a:'en',b:'en',e:'es',f:'fr',i:'it',j:'ja',p:'pt',z:'zh'})[voice[0]]||'en')+(cold?'-cold':'-warm');
    const previous=Array.isArray(this.data.samples[key])?this.data.samples[key]:[];
    this.data.samples[key]=[...previous.slice(-7),{chars,ms,parts}];this.save();
  }
  save(){try{window.localStorage?.setItem(this.key,JSON.stringify(this.data))}catch{}}
}

class BreadmakerNarrationProgress {
  constructor(engine,sources,voice,update,clock=()=>performance.now()){
    this.clock=clock;this.update=update;this.index=0;this.complete=new Set();this.value=0;
    const firstMissing=sources.findIndex(source=>!source.cached);
    this.loadMs=engine.ready||firstMissing<0?0:engine.timing.data.loadMs;this.loaded=!!engine.ready||!this.loadMs;
    this.costs=sources.map(source=>source.cached?0:500);
    this.estimates=sources.map((source,index)=>source.cached?0:engine.timing.estimate(source.text.length,voice,!engine.generated&&index===firstMissing,engine.timing.parts(source.text,voice)));
    this.total=Math.max(1,this.loadMs+this.estimates.reduce((sum,ms,index)=>sum+ms+this.costs[index],0));
    this.phase=engine.ready?'synthesis':'loading';this.phaseStarted=engine.ready?clock():(engine.loadStarted??clock());this.actual=0;
    this.tick();this.timer=setInterval(()=>this.tick(),150);
  }
  event(index,status){
    const phase=status.phase||(status.loading?'loading':'synthesis');
    if(phase!==this.phase||index!==this.index){this.phaseStarted=this.clock();this.actual=0}
    this.index=index;this.phase=phase;if(phase!=='loading')this.loaded=true;
    if(status.total>0)this.actual=Math.max(this.actual,(status.completed||0)/status.total);this.tick();
  }
  completeSource(index){this.complete.add(index);this.phase='waiting';this.actual=0;this.tick()}
  tick(){
    let done=this.loaded?this.loadMs:0;
    for(const index of this.complete)done+=this.estimates[index]+this.costs[index];
    const elapsed=Math.max(0,this.clock()-this.phaseStarted),estimate=this.phase==='loading'?this.loadMs:this.phase==='saving'?this.costs[this.index]:this.phase==='synthesis'?this.estimates[this.index]:0;
    if(this.phase==='saving')done+=this.estimates[this.index];
    const fraction=estimate?Math.max(this.actual,Math.min(.94,1-Math.exp(-2.3*elapsed/estimate))):0;
    const target=Math.max(this.value,Math.min(.985,(done+estimate*fraction)/this.total),.005);
    this.value=Math.max(.005,this.value+(target-this.value)*.2);
    const overrun=estimate>0&&elapsed>estimate*1.25;
    const remaining=Math.max(0,this.total-done-Math.min(elapsed,estimate));
    this.update({fraction:this.value,phase:this.phase,remainingSeconds:Math.max(1,Math.ceil(remaining/1000)),overrun,current:this.index+1,total:this.estimates.length});
  }
  stop(success=false){clearInterval(this.timer);if(success)this.update({fraction:1,phase:'complete',remainingSeconds:0,overrun:false,current:this.estimates.length,total:this.estimates.length})}
}
window.BreadmakerNarrationTiming=BreadmakerNarrationTiming;
window.BreadmakerNarrationProgress=BreadmakerNarrationProgress;
