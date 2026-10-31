// Public model availability is DEVICE state, not a server-side download/install.
import {HostedPublicAssets} from './hosted-assets.mjs';
const MODEL='models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/';

export class HostedNarrationDevice {
  constructor({project,account,client,manifest,check,onContextLost,consentGPU,fetcher=globalThis.fetch.bind(globalThis),origin=globalThis.location.origin}){
    if(!/^[a-f0-9]{64}$/.test(manifest)||typeof check!=='function'||typeof consentGPU!=='function')throw Error('NARRATION_DEVICE_CONTEXT_REQUIRED');
    Object.assign(this,{project,account,client,manifest,check,onContextLost,consentGPU,fetcher,origin,gpuChoice:null,pending:null});
  }
  async open(){
    this.check();
    if(!this.pending)this.pending=HostedPublicAssets.open({project:this.project,account:this.account,client:this.client,expectedDigest:this.manifest,onContextLost:this.onContextLost,fetcher:this.fetcher,origin:this.origin}).catch(error=>{this.pending=null;throw error});
    const assets=await this.pending;this.check();return assets;
  }
  async authorize(){const assets=await this.open();await assets.reauthorize();this.check()}
  async chooseGPU(){
    const assets=await this.open(),path=MODEL+'onnx/model.onnx';
    if(!assets.assets.has(path))return false;
    if(await assets.installed(path)){this.check();return true}
    if(this.gpuChoice===null)this.gpuChoice=!!await this.consentGPU(assets.assets.get(path).size);
    this.check();return this.gpuChoice;
  }
  async voices(catalog){
    const assets=await this.open(),voices=[];
    for(const voice of catalog.voices){const path=MODEL+'voices/'+voice.id+'.bin',asset=assets.assets.get(path);if(!asset||asset.sha256!==voice.sha256||asset.size!==voice.size)throw Error('NARRATION_VOICE_PIN_MISMATCH');voices.push({...voice,installed:await assets.installed(path)})}
    this.check();return {...catalog,voices};
  }
  async downloadVoice(voice){
    const assets=await this.open();await assets.reauthorize();this.check();
    const path=MODEL+'voices/'+voice.id+'.bin',asset=assets.assets.get(path);
    if(!asset||asset.size!==voice.size||asset.sha256!==voice.sha256)throw Error('NARRATION_VOICE_PIN_MISMATCH');
    const response=await assets.fetch(new URL('/static/vendor/narration/'+path,this.origin));
    const reader=response.body.getReader();let length=0;
    try{while(true){const {value,done}=await reader.read();if(done)break;this.check();length+=value.byteLength;if(length>asset.size)throw Error('NARRATION_VOICE_SIZE_INVALID')}}
    finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
    if(length!==asset.size)throw Error('NARRATION_VOICE_SIZE_INVALID');this.check();return {...voice,installed:true};
  }
}
