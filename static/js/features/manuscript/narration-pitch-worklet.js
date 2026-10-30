// Two overlapping, smoothly windowed delay lines shift pitch without changing
// the playback clock or persisted audio. Zero semitones is an exact bypass.
class NarrationPitchProcessor extends AudioWorkletProcessor {
  static get parameterDescriptors(){return [{name:'semitones',defaultValue:0,minValue:-16,maxValue:16,automationRate:'k-rate'}]}
  constructor(){super();this.ring=new Float32Array(Math.ceil(sampleRate*.15));this.write=0;this.phase=0}
  process(inputs,outputs,parameters){
    const input=inputs[0]?.[0],output=outputs[0]?.[0];if(!output)return true;
    const semitones=parameters.semitones[0],ratio=2**(semitones/12),window=sampleRate*.06,step=Math.abs(1-ratio)/window,size=this.ring.length;
    const read=delay=>{const position=(this.write-delay+size)%size,at=Math.floor(position),fraction=position-at;return this.ring[at]*(1-fraction)+this.ring[(at+1)%size]*fraction};
    for(let i=0;i<output.length;i++){
      const sample=input?.[i]||0;this.ring[this.write]=sample;
      if(Math.abs(semitones)<.001)output[i]=sample;
      else{const a=this.phase,b=(a+.5)%1,weight=Math.sin(Math.PI*a)**2,delay=phase=>128+window*(ratio>1?1-phase:phase);output[i]=read(delay(a))*weight+read(delay(b))*(1-weight);this.phase=(a+step)%1}
      this.write=(this.write+1)%size;
    }
    return true;
  }
}
registerProcessor('breadmaker-narration-pitch',NarrationPitchProcessor);
