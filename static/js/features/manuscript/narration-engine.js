// Browser-only Kokoro adapter. No manuscript text leaves this origin.
class BreadmakerKokoroEngine {
  constructor({backend='auto'}={}){this.preferredBackend=backend;this.backend='wasm';this.pending=null;this.context=null;this.ready=false;this.generated=false;this.timing=new window.BreadmakerNarrationTiming()}
  dispose(){this.disposed=true;this.cancelLoad?.(Error('KOKORO_ENGINE_REPLACED'));this.cancelSynthesis?.(Error('KOKORO_ENGINE_REPLACED'));this.worker?.ww?.terminate();if(this.context?.state!=='closed')this.context?.close().catch(()=>{});this.ready=false}
  async chooseBackend(){
    if(this.preferredBackend==='wasm')return 'wasm';
    if(!globalThis.navigator?.gpu){this.fallbackReason='GPU_UNAVAILABLE';return 'wasm'}
    try{
      if(this.preferredBackend==='auto'&&window.sessionStorage?.getItem('breadmaker-narration-gpu-failed')){this.fallbackReason='GPU_PREVIOUSLY_FAILED';return 'wasm'}
      const installed=await fetch('/static/vendor/narration/installation.json').then(response=>response.ok?response.json():null);
      if(!installed?.gpu){this.fallbackReason='GPU_MODEL_MISSING';return 'wasm'}
      let timer;try{const backend=await Promise.race([navigator.gpu.requestAdapter().then(adapter=>adapter?'webgpu':'wasm'),new Promise(resolve=>{timer=setTimeout(()=>resolve('wasm'),1500)})]);if(backend==='wasm')this.fallbackReason='GPU_UNAVAILABLE';return backend}finally{clearTimeout(timer)}
    }catch{this.fallbackReason='GPU_UNAVAILABLE';return 'wasm'}
  }
  async discardGPU(){
    this.fallbackReason='GPU_FAILED';try{window.sessionStorage?.setItem('breadmaker-narration-gpu-failed','1')}catch{}
    if(this.pending)await this.pending.then(engine=>engine.ww?.terminate()).catch(()=>{});
    if(this.context?.state!=='closed')await this.context?.close().catch(()=>{});
    this.pending=null;this.ready=false;this.generated=false;this.preferredBackend='wasm';this.backend='wasm';this.timing=new window.BreadmakerNarrationTiming();
  }
  async load(){
    if(!this.pending)this.pending=(async()=>{
      const started=performance.now();this.loadStarted=started;
      const {HeadTTS}=await import('/static/vendor/narration/modules/headtts.mjs');
      this.backend=await this.chooseBackend();this.timing=new window.BreadmakerNarrationTiming(this.backend);
      if(this.disposed)throw Error('KOKORO_ENGINE_REPLACED');
      const connect=async backend=>{
        this.context=new AudioContext({sampleRate:24000});
        const engine=new HeadTTS({endpoints:[backend],audioCtx:this.context,transformersModule:new URL('/static/vendor/narration/transformers-local.mjs',location.origin).href,model:'onnx-community/Kokoro-82M-v1.0-ONNX-timestamped',dtypeWebgpu:'fp32',dtypeWasm:'q8',dictionaryURL:new URL('/static/vendor/narration/dictionaries/',location.origin).href,voiceURL:new URL('/static/vendor/narration/models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/voices/',location.origin).href,languages:['en-us'],voices:[],splitLength:400});
        this.worker=engine;
        let timer;
        try{await Promise.race([engine.connect(),new Promise((_,reject)=>{this.cancelLoad=reject;timer=setTimeout(()=>reject(Error('KOKORO_LOAD_TIMEOUT')),backend==='webgpu'?120000:90000)})]);return engine}
        catch(error){engine.ww?.terminate();if(this.context.state!=='closed')await this.context.close().catch(()=>{});throw error}
        finally{clearTimeout(timer);this.cancelLoad=null}
      };
      let engine;
      try{engine=await connect(this.backend)}catch(error){if(this.backend!=='webgpu'||error.message==='KOKORO_ENGINE_REPLACED')throw error;this.fallbackReason='GPU_INITIALIZATION_FAILED';this.fallbackDetail=String(error).slice(0,400);try{window.sessionStorage?.setItem('breadmaker-narration-gpu-failed','1')}catch{}this.backend='wasm';this.timing=new window.BreadmakerNarrationTiming();engine=await connect('wasm')}
      this.ready=true;this.timing.recordLoad(performance.now()-started);return engine;
    })().catch(error=>{this.ready=false;this.pending=null;this.loadStarted=null;throw error});
    return this.pending;
  }
  async generate(text,voice,onprogress=()=>{},onchunk=()=>{},pace=1,punctuationPauses=false){
    if(this.disposed)throw Error('KOKORO_ENGINE_REPLACED');
    if(this.generating)throw Error('KOKORO_ENGINE_BUSY');
    if(![.85,.9,.95,1,1.05,1.1,1.15].includes(pace))throw Error('KOKORO_PACE_INVALID');
    if(typeof punctuationPauses!=='boolean')throw Error('KOKORO_PUNCTUATION_INVALID');
    this.generating=true;
    try{
      try{return await this.synthesize(text,voice,onprogress,onchunk,pace,punctuationPauses)}catch(error){if(this.backend!=='webgpu'||this.disposed)throw error;onchunk({reset:true});await this.discardGPU();return await this.synthesize(text,voice,onprogress,onchunk,pace,punctuationPauses)}
    }finally{this.generating=false}
  }
  async synthesize(text,voice,onprogress,onchunk,pace=1,punctuationPauses=false){
    if(!this.ready)onprogress({phase:'loading',loading:true});
    const engine=await this.load();onprogress({phase:'synthesis',completed:0,total:1});
    const started=performance.now(),cold=!this.generated;
    // Smart apostrophes otherwise bypass HeadTTS's contraction dictionary.
    // This one-for-one normalization preserves UTF-16 positions in original prose.
    const spoken=text.replace(/[‘’ʼ]/g,"'");
    let input=spoken;
    if(['ef_dora','em_alex','em_santa'].includes(voice)){
      this.spanish??=(await import('/static/vendor/narration/spanish.mjs')).prepareSpanish;
      input=this.spanish(spoken);
    }else if(['f','i','j','p','z'].includes(voice[0])){
      const language=({f:'fr',i:'it',j:'ja',p:'pt-BR',z:'zh-CN'})[voice[0]];
      const project=typeof currentProjectIdentity==='function'?currentProjectIdentity():null;
      const pronounce=async data=>{const options={method:'POST',headers:{'Content-Type':'application/json',...(project?{'X-Breadmaker-Project':project}:{})},body:JSON.stringify(data)};if(typeof api==='function')return api('/api/narration-pronunciation',options);const response=await fetch('/api/narration-pronunciation',options);if(!response.ok)throw Error('NARRATION_PRONUNCIATION_FAILED');return response.json()};
      if(language==='ja'||language==='zh-CN')input=(await pronounce({language,text:spoken})).items;
      else{this.languages??=await import('/static/vendor/narration/languages.mjs');input=language==='it'?await this.languages.prepareItalian(spoken,pronounce):this.languages.prepareRomance(spoken,language)}
    }
    // Non-English frontends supply IPA + original subtitles; the English worker
    // is bypassed by HeadTTS's supported phonetic-input interface.
    // HeadTTS 1.3.0's WASM queue overwrites per-request voice with ttsSetup.
    // Its public setup queue must be awaited before submitting synthesis.
    if(this.disposed)throw Error('KOKORO_ENGINE_REPLACED');
    const chunks=[],timings=[];let offset=0,cursor=0;
    let watchdog,rejectStall,active=true;
    const armWatchdog=()=>{clearTimeout(watchdog);watchdog=setTimeout(()=>rejectStall(Error(this.backend==='webgpu'?'KOKORO_GPU_STALLED':'KOKORO_CPU_STALLED')),this.backend==='webgpu'?90000:180000)};
    const consume=message=>{
      if(!active||this.disposed)return;
      if(message.type!=='audio'||!message.data?.audio)return;
      armWatchdog();
      const data=message.data,localTimings=[];let buffer=data.audio,pcm=buffer.getChannelData(0);
      for(let i=0;i<(data.words||[]).length;i++){
        const word=String(data.words[i]).trim();if(!word)continue;
        const start=spoken.toLocaleLowerCase().indexOf(word.toLocaleLowerCase(),cursor);
        if(start<0)continue;cursor=start+word.length;
        const time=Math.max(0,Number(data.wtimes[i])||0)/1000,duration=Math.max(0,Number(data.wdurations[i])||0)/1000;
        localTimings.push({start,end:cursor,time,duration});
      }
      let aligned=localTimings;
      if(punctuationPauses){const adjusted=window.applyNarrationPunctuation(text,pcm,localTimings,buffer.sampleRate||24000);aligned=adjusted.timings;if(adjusted.pcm!==pcm){pcm=adjusted.pcm;buffer=this.context.createBuffer(1,pcm.length,data.audio.sampleRate||24000);buffer.copyToChannel(pcm,0)}}
      chunks.push(pcm);timings.push(...aligned.map(word=>({...word,time:offset+word.time})));
      offset+=buffer.duration;
      onchunk({buffer,timings:[...timings],duration:offset,context:this.context});
      onprogress({phase:'synthesis',completed:message.metaData.part+1,total:message.metaData.partsTotal});
    };
    let messages;
    try{
      const stalled=new Promise((_,reject)=>{rejectStall=reject;this.cancelSynthesis=reject;armWatchdog()});
      const run=async()=>{await engine.setup({voice,language:'en-us',speed:pace,audioEncoding:'wav'});return engine.synthesize({input,voice,language:'en-us',speed:pace,audioEncoding:'wav'},consume)};
      messages=await Promise.race([run(),stalled]);
    }catch(error){
      if(!this.disposed)engine.ww?.terminate();this.pending=null;this.ready=false;
      if(this.context?.state!=='closed')await this.context?.close().catch(()=>{});
      throw error;
    }finally{active=false;clearTimeout(watchdog);this.cancelSynthesis=null}
    if(messages.some(message=>message.type!=='audio'||!message.data?.audio)||!chunks.length)throw Error('KOKORO_GENERATION_FAILED');
    const inferenceMs=performance.now()-started;this.timing.recordGeneration(text.length,voice,inferenceMs,cold,messages.length);this.generated=true;
    const count=chunks.reduce((n,c)=>n+c.length,0),wav=new ArrayBuffer(44+count*2),view=new DataView(wav),str=(at,value)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i))};
    str(0,'RIFF');view.setUint32(4,36+count*2,true);str(8,'WAVE');str(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,24000,true);view.setUint32(28,48000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);str(36,'data');view.setUint32(40,count*2,true);
    let at=44;for(const chunk of chunks)for(const sample of chunk){const s=Math.max(-1,Math.min(1,sample));view.setInt16(at,s<0?s*32768:s*32767,true);at+=2}
    return {blob:new Blob([wav],{type:'audio/wav'}),timings,metrics:{inferenceMs,chars:text.length,cold,threads:this.timing.threads,backend:this.backend}};
  }
}
window.BreadmakerKokoroEngine=BreadmakerKokoroEngine;
