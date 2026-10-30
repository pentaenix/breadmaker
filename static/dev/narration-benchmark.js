// Explicit local diagnostic page; never enables a hosted narration feature.
const button=document.querySelector('#run'),result=document.querySelector('#result');
button.disabled=!['127.0.0.1','localhost','::1','[::1]'].includes(location.hostname);
button.onclick=async()=>{
  button.disabled=true;result.textContent='Preparing GPU test…';
  const engine=new window.BreadmakerKokoroEngine({backend:'webgpu'}),playback=new window.BreadmakerNarrationPlayback();
  const started=performance.now();let firstChunkMs=null;
  try{
    const samples=[];
    for(let i=0;i<2;i++){
      const t=performance.now();
      const output=await engine.generate('The bakery is open. Please come inside.','af_heart',()=>{result.textContent=JSON.stringify({backend:engine.backend,ready:engine.ready,elapsedMs:Math.round(performance.now()-started)})},part=>{
        if(part.reset){playback.pause();playback.clearStream();return}
        if(i===0){if(!playback.stream){playback.startStream(part.context);firstChunkMs=performance.now()-t}playback.appendStream(part.buffer);if(playback.paused)playback.play().catch(()=>{})}
      });
      if(i===0){playback.finishStream();document.querySelector('#sample').src=URL.createObjectURL(output.blob)}
      samples.push({totalMs:performance.now()-t,firstChunkMs:i===0?firstChunkMs:null,...output.metrics,seconds:output.blob.size/48000});
    }
    result.textContent=JSON.stringify({complete:true,backend:engine.backend,gpuAPI:!!navigator.gpu,fallback:engine.fallbackReason||null,detail:engine.fallbackDetail||null,isolated:crossOriginIsolated,samples},null,2);
  }catch(error){result.textContent=JSON.stringify({complete:true,error:String(error),backend:engine.backend,fallback:engine.fallbackReason||null})}
  finally{playback.pause();button.disabled=false}
};
