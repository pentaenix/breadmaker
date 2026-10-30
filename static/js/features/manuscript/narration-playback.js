// Saved WAV playback plus transient, gapless-when-ready AudioBuffer playback.
// Only a complete WAV is published to the project cache.
class BreadmakerNarrationPlayback {
  constructor(native=new Audio()){
    this.native=native;this.stream=null;this.rate=1;this.pitch=0;this.pitchGraphs=new WeakMap();
    for(const event of ['play','pause','timeupdate','ended'])native['on'+event]=()=>{if(!this.stream)this['on'+event]?.()};
  }
  get paused(){return this.stream?!this.stream.playing:this.native.paused}
  get ended(){return this.stream?this.stream.ended:this.native.ended}
  get waiting(){return !!(this.stream?.playing&&this.stream.waiting)}
  get currentTime(){const s=this.stream;return s?s.playing&&!s.waiting?Math.min(s.duration,s.position+(s.context.currentTime-s.anchor)*this.rate):s.position:this.native.currentTime}
  set currentTime(value){const s=this.stream;if(!s){this.native.currentTime=value;return}s.position=Math.max(0,Math.min(s.duration,Number(value)||0));s.ended=false;if(s.playing)this.schedule();this.ontimeupdate?.()}
  get playbackRate(){return this.rate}
  set playbackRate(value){const position=this.currentTime;this.rate=Number(value)||1;this.native.playbackRate=this.rate;this.updatePitch();if(this.stream){this.stream.position=position;if(this.stream.playing)this.schedule()}}
  async pitchGraph(context){
    if(!this.pitchGraphs.has(context))this.pitchGraphs.set(context,(async()=>{await context.audioWorklet.addModule('/static/js/features/manuscript/narration-pitch-worklet.js');const node=new AudioWorkletNode(context,'breadmaker-narration-pitch',{outputChannelCount:[1]});node.connect(context.destination);return node})());
    return this.pitchGraphs.get(context);
  }
  updatePitch(){this.nativePitch?.parameters.get('semitones').setTargetAtTime(this.pitch,this.nativeContext.currentTime,.02);this.stream?.pitchNode?.parameters.get('semitones').setTargetAtTime(this.pitch-12*Math.log2(this.rate),this.stream.context.currentTime,.02)}
  async setPitch(value){
    if(!Number.isFinite(value)||value<-4||value>4)throw Error('NARRATION_PITCH_INVALID');
    if(value!==0&&!this.nativePitch){
      this.nativeContext??=new AudioContext();const node=await this.pitchGraph(this.nativeContext);
      this.nativeSource??=this.nativeContext.createMediaElementSource(this.native);this.nativeSource.connect(node);this.nativePitch=node;
    }
    const stream=this.stream;
    if(value!==0&&stream&&!stream.pitchNode){const node=await this.pitchGraph(stream.context);if(this.stream===stream)stream.pitchNode=node}
    this.pitch=value;this.updatePitch();if(stream?.playing&&this.stream===stream){stream.position=this.currentTime;this.schedule()}
    if(!this.native.paused)await this.nativeContext?.resume();
  }
  set src(value){this.clearStream();this.native.src=value;this.native.playbackRate=this.rate}
  startStream(context){this.native.pause();this.clearStream();this.stream={context,chunks:[],duration:0,position:0,anchor:0,playing:false,waiting:false,finished:false,ended:false,nodes:new Set(),version:0}}
  clearNodes(){const s=this.stream;if(!s)return;s.version++;for(const node of s.nodes){node.onended=null;try{node.stop()}catch{}node.disconnect()}s.nodes.clear()}
  clearStream(){if(!this.stream)return;this.clearNodes();clearInterval(this.stream.timer);this.stream=null}
  appendStream(buffer){
    const s=this.stream;if(!s)return;const previous=s.duration;s.chunks.push({buffer,start:previous});s.duration+=buffer.duration;
    if(s.playing){const position=s.waiting?previous:Math.min(previous,this.currentTime);s.position=position;this.schedule()}
    this.ontimeupdate?.();
  }
  schedule(){
    const s=this.stream;if(!s?.playing)return;this.clearNodes();s.anchor=s.context.currentTime;s.waiting=s.position>=s.duration;
    const version=s.version;
    for(const chunk of s.chunks){
      if(chunk.start+chunk.buffer.duration<=s.position)continue;
      const node=s.context.createBufferSource();node.buffer=chunk.buffer;node.playbackRate.value=this.rate;node.connect(s.pitchNode||s.context.destination);s.nodes.add(node);
      node.onended=()=>{s.nodes.delete(node);node.disconnect();if(this.stream!==s||s.version!==version||s.nodes.size)return;s.position=s.duration;s.waiting=true;this.checkEnd();this.ontimeupdate?.()};
      node.start(s.anchor+Math.max(0,chunk.start-s.position)/this.rate,Math.max(0,s.position-chunk.start));
    }
    this.checkEnd();
  }
  checkEnd(){const s=this.stream;if(s?.playing&&s.finished&&s.waiting&&!s.ended){s.ended=true;s.playing=false;clearInterval(s.timer);this.onended?.()}}
  finishStream(){if(!this.stream)return;this.stream.finished=true;this.checkEnd();this.ontimeupdate?.()}
  async play(){
    const s=this.stream;if(!s){await this.nativeContext?.resume();return this.native.play()}
    if(this.pitch!==0&&!s.pitchNode){const node=await this.pitchGraph(s.context);if(this.stream!==s)return;s.pitchNode=node;this.updatePitch()}
    await s.context.resume();if(this.stream!==s)return;
    if(s.ended){s.position=0;s.ended=false}s.playing=true;this.schedule();if(!s.playing)return;clearInterval(s.timer);s.timer=setInterval(()=>this.ontimeupdate?.(),100);this.onplay?.();
  }
  pause(){const s=this.stream;if(!s){this.native.pause();return}const position=this.currentTime;s.position=position;s.playing=false;s.waiting=false;this.clearNodes();clearInterval(s.timer);this.onpause?.()}
}
window.BreadmakerNarrationPlayback=BreadmakerNarrationPlayback;

function findNarrationRecording(source,records,voice,anyVoice=false){
  return records.filter(record=>String(record.block_id)===String(source.block_id)&&record.source_text===source.source_text&&record.start===source.start&&record.end===source.end&&(anyVoice||record.voice===voice)).sort((a,b)=>Number(b.voice===voice)-Number(a.voice===voice)||String(a.id).localeCompare(String(b.id)))[0];
}
window.findNarrationRecording=findNarrationRecording;
