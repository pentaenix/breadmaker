import assert from 'node:assert/strict';
import {mkdtemp,readFile,readdir,rmdir,unlink,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {downloadPinned} from './download.mjs';
const directory=await mkdtemp(join(tmpdir(),'breadmaker-download-test-')),target=join(directory,'voice.bin');
const pin={url:'https://example.invalid/fixed',target,size:4,sha256:createHash('sha256').update('good').digest('hex')};
try{
  await writeFile(target,'old!');
  await assert.rejects(()=>downloadPinned(pin,async(url,path)=>{await writeFile(path,'bad!')}),/CHECKSUM/);
  assert.equal(await readFile(target,'utf8'),'old!');
  await assert.rejects(()=>downloadPinned(pin,async(url,path)=>{await writeFile(path,'part');throw Error('interrupted')}),/interrupted/);
  assert.deepEqual(await readdir(directory),['voice.bin']);
  await downloadPinned(pin,async(url,path)=>writeFile(path,'good'));
  await downloadPinned(pin,()=>{throw Error('must reuse verified installation')});
  assert.equal(await readFile(target,'utf8'),'good');
}finally{await unlink(target);await rmdir(directory)}
console.log('PASS: checksum verification, failed-download preservation, staging cleanup and verified reuse.');
