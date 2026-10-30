// Explicit one-time installation. Runtime inference never contacts the network.
import {cp,mkdir,writeFile,readFile,readdir,realpath} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {downloadPinned,matchesPinned} from './download.mjs';
import {createRequire} from 'node:module';
import {dirname} from 'node:path';
import {build} from 'esbuild';
const root=fileURLToPath(new URL('../../',import.meta.url)),base=join(root,'static/vendor/narration');
await mkdir(base,{recursive:true});
const packages=join(root,'tools/narration/node_modules');
await cp(join(packages,'@met4citizen/headtts/modules'),join(base,'modules'),{recursive:true});
// Pinned upstream's 30s/10s loader deadlines can expire while fp32 shaders or
// WASM sessions initialize. Keep a bounded, reproducible runtime patch.
const headttsPath=join(base,'modules/headtts.mjs');let headtts=await readFile(headttsPath,'utf8');
for(const [before,after] of [
  ['setTimeout(rejectedTimeout, 30000)','setTimeout(rejectedTimeout, endpointLowerCase === "webgpu" ? 90000 : 60000)'],
  ['setTimeout(rejectedTimeout, 10000)','setTimeout(rejectedTimeout, endpointLowerCase === "webgpu" ? 90000 : 30000)'],
  ['connectionErrorLog.push( "Failed to start Module Web Worker." );','connectionErrorLog.push( "Failed to start Module Web Worker: " + String(error?.message || error) );']
]){if(!headtts.includes(before))throw Error('Pinned HeadTTS loader contract changed');headtts=headtts.replace(before,after)}
await writeFile(headttsPath,headtts);
await cp(join(packages,'@met4citizen/headtts/dictionaries'),join(base,'dictionaries'),{recursive:true});
await cp(join(packages,'@met4citizen/headtts/LICENSE'),join(base,'HEADTTS-LICENSE'));
await mkdir(join(base,'transformers'),{recursive:true});
const dist=join(packages,'@huggingface/transformers/dist');
for(const name of await readdir(dist))if(name==='transformers.web.min.js'||/^ort-wasm.*\.(wasm|mjs)$/.test(name))await cp(join(dist,name),join(base,'transformers',name));
const ort=dirname(createRequire(await realpath(join(packages,'@huggingface/transformers/package.json'))).resolve('onnxruntime-web'));
for(const name of await readdir(ort))if(/^ort-wasm.*\.(wasm|mjs)$/.test(name))await cp(join(ort,name),join(base,'transformers',name));
await build({entryPoints:[join(dist,'transformers.web.min.js')],outfile:join(base,'transformers/transformers.web.min.js'),bundle:true,format:'esm',platform:'browser',minify:true,logLevel:'warning'});
await cp(join(packages,'@huggingface/transformers/LICENSE'),join(base,'TRANSFORMERS-LICENSE'));
await build({entryPoints:[join(root,'tools/narration/spanish.mjs')],outfile:join(base,'spanish.mjs'),bundle:true,format:'esm',platform:'browser',minify:true,logLevel:'warning'});
await build({entryPoints:[join(root,'tools/narration/languages.mjs')],outfile:join(base,'languages.mjs'),bundle:true,format:'esm',platform:'browser',minify:true,logLevel:'warning'});
await cp(join(packages,'@piper-plus/g2p/LICENSE.md'),join(base,'SPANISH-G2P-LICENSE'));
await cp(join(packages,'n2words/LICENSE'),join(base,'SPANISH-NUMBERS-LICENSE'));
await writeFile(join(base,'transformers-local.mjs'),`import {env} from './transformers/transformers.web.min.js';
env.allowRemoteModels=false;
env.allowLocalModels=true;
env.localModelPath='/static/vendor/narration/models/';
env.backends.onnx.wasm.numThreads=globalThis.crossOriginIsolated?Math.min(4,Math.max(1,Math.floor((navigator.hardwareConcurrency||2)/2))):1;
env.backends.onnx.wasm.proxy=false;
env.backends.onnx.wasm.wasmPaths='/static/vendor/narration/transformers/';
export * from './transformers/transformers.web.min.js';
`);
const model='onnx-community/Kokoro-82M-v1.0-ONNX-timestamped',revision='dd4401a9add81ac692d20e240d22ec9dda82cc29';
const checksums=JSON.parse(await readFile(new URL('./model-checksums.json',import.meta.url),'utf8'));
const catalog=JSON.parse(await readFile(join(root,'static/narration-voices.json'),'utf8'));
if(catalog.model!==model||catalog.revision!==revision)throw Error('Pinned narration catalog/model mismatch');
const download=async file=>{
  const target=join(base,'models',model,file),pin=file.startsWith('voices/')?catalog.voices.find(voice=>file==='voices/'+voice.id+'.bin'):checksums[file];
  if(!pin)throw Error('Unpinned narration model file: '+file);
  await mkdir(dirname(target),{recursive:true});
  await downloadPinned({url:`https://huggingface.co/${model}/resolve/${revision}/${file}`,target,...pin});
};
if(process.argv.includes('--with-gpu')||process.argv.includes('--gpu-only')){
  console.log('Downloading optional fp32 GPU model');
  await download('onnx/model.onnx');
}
const files=['config.json','tokenizer.json','tokenizer_config.json','onnx/model_quantized.onnx',...catalog.voices.filter(voice=>voice.default||voice.id==='af_bella').map(voice=>'voices/'+voice.id+'.bin'),'README.md'];
for(const file of process.argv.includes('--runtime-only')||process.argv.includes('--gpu-only')?[]:files.filter(file=>!process.argv.includes('--voices-only')||file.startsWith('voices/'))){
  console.log('Checking/installing',file);await download(file);
}
const gpu=await matchesPinned(join(base,'models',model,'onnx/model.onnx'),checksums['onnx/model.onnx']);
await writeFile(join(base,'installation.json'),JSON.stringify({model,revision,headtts:'1.3.0',transformers:'4.3.2',spanishG2P:'0.4.2',spanishNumbers:'6.3.0',dtype:'q8',gpu}));
console.log('Kokoro installed for offline browser generation.');
