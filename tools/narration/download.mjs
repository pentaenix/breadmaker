// Install to a private staging directory; never replace a working file on failure.
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {mkdtemp,rename,rmdir,stat,unlink} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {execFileSync} from 'node:child_process';

export async function matchesPinned(path,{size,sha256}){
  try{
    if((await stat(path)).size!==size)return false;
    const hash=createHash('sha256');for await(const chunk of createReadStream(path))hash.update(chunk);
    return hash.digest('hex')===sha256;
  }catch(error){if(error.code==='ENOENT')return false;throw error}
}
export async function downloadPinned({url,target,size,sha256},download=(url,path,size)=>execFileSync('curl',[
  '--fail','--location','--proto','=https','--proto-redir','=https','--connect-timeout','20','--max-time','600',
  '--max-filesize',String(size),'--retry','2','--silent','--show-error',url,'--output',path],{stdio:'inherit'})){
  if(!Number.isSafeInteger(size)||size<1||!/^[0-9a-f]{64}$/.test(sha256))throw Error('NARRATION_DOWNLOAD_MANIFEST_INVALID');
  if(await matchesPinned(target,{size,sha256}))return;
  const temporary=await mkdtemp(join(dirname(target),'.download-')),path=join(temporary,'payload');
  try{
    await download(url,path,size);
    if(!await matchesPinned(path,{size,sha256}))throw Error('NARRATION_DOWNLOAD_CHECKSUM_INVALID');
    await rename(path,target);
  }finally{await unlink(path).catch(error=>{if(error.code!=='ENOENT')throw error});await rmdir(temporary)}
}
