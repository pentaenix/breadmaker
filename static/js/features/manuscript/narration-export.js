// Export is client-side, saved audio only: no synthesis, server archive, or
// canonical manuscript mutation. Keep PCM chunks as Blobs rather than decoding
// a whole book into Web Audio's floating-point buffers.
function narrationExportClip(source,records,voice){
  const record=records.filter(record=>record.block_id===source.block_id&&record.source_text===source.source_text&&record.start<=source.start&&record.end>=source.end).sort((a,b)=>Number(b.voice===voice)-Number(a.voice===voice)||(a.end-a.start)-(b.end-b.start)||String(a.id).localeCompare(String(b.id)))[0];
  if(!record)return null;
  if(source.start===record.start&&source.end===record.end)return {record,start:0,end:record.duration};
  const start=source.start-record.start,end=source.end-record.start,words=(record.timings||[]).filter(word=>word.end>start&&word.start<end);
  // Never claim sample-exact sub-word trimming. Boundaries must coincide with
  // complete spoken words (punctuation/whitespace at the edges is harmless).
  if(!words.length||words[0].start<start||words.at(-1).end>end)return null;
  return {record,start:source.start===record.start?0:words[0].time,end:source.end===record.end?record.duration:words.at(-1).time+words.at(-1).duration};
}
async function narrationWavPCM(blob,start=0,end=Infinity){
  const header=await blob.slice(0,12).arrayBuffer(),view=new DataView(header),tag=(view,at)=>String.fromCharCode(...new Uint8Array(view.buffer,at,4));
  if(header.byteLength!==12||tag(view,0)!=='RIFF'||tag(view,8)!=='WAVE')throw Error('NARRATION_EXPORT_FORMAT');
  let at=12,valid=false;
  while(at+8<=blob.size){
    const chunk=new DataView(await blob.slice(at,at+8).arrayBuffer()),kind=tag(chunk,0),size=chunk.getUint32(4,true),body=at+8;
    if(body+size>blob.size)throw Error('NARRATION_EXPORT_FORMAT');
    if(kind==='fmt '){const fmt=new DataView(await blob.slice(body,body+Math.min(size,16)).arrayBuffer());valid=size>=16&&fmt.getUint16(0,true)===1&&fmt.getUint16(2,true)===1&&fmt.getUint32(4,true)===24000&&fmt.getUint16(14,true)===16}
    if(kind==='data'){
      if(!valid||size%2)throw Error('NARRATION_EXPORT_FORMAT');
      const from=Math.max(0,Math.min(size,Math.floor(start*24000)*2)),to=Math.max(from,Math.min(size,Math.ceil(end*24000)*2));
      return blob.slice(body+from,body+to);
    }
    at=body+size+(size%2);
  }
  throw Error('NARRATION_EXPORT_FORMAT');
}
function narrationJoinWav(parts){
  const size=parts.reduce((total,part)=>total+part.size,0);if(!size||size>0xffffffff-36)throw Error('NARRATION_EXPORT_SIZE');
  const header=new ArrayBuffer(44),view=new DataView(header),str=(at,value)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i))};
  str(0,'RIFF');view.setUint32(4,36+size,true);str(8,'WAVE');str(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,24000,true);view.setUint32(28,48000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);str(36,'data');view.setUint32(40,size,true);
  return new Blob([header,...parts],{type:'audio/wav'});
}
async function exportNarrationAudio(target,selection,onProgress=()=>{}){
  const pid=currentProjectIdentity(),documentId=target.documentId,voice=narrationEffectiveVoice();
  if(narration.busy||narration.queueLoading||documentId!==state.activeDocumentId)throw Error('NARRATION_EXPORT_BUSY');
  const check=()=>{if(pid!==currentProjectIdentity()||documentId!==state.activeDocumentId)throw Error('NARRATION_EXPORT_CHANGED')};
  narration.queueLoading=true;syncNarrationActions();
  try{
    await flushSave();check();const book=await loadBookData(documentId,{force:true});check();
    const chapters=(book.chapters||[]).filter(chapter=>target.scope==='book'||String(chapter.id)===target.chapterId);
    const sources=target.scope==='selection'?selection:chapters.flatMap(chapter=>[...(!chapter.is_implicit&&String(chapter.title||'').trim()?[narrationTitleSource(chapter.id,chapter.title)]:[]),...(book.blocks||[]).filter(block=>String(block.chapter_id)===String(chapter.id)&&block.block_type!=='image'&&String(block.text_content||'').trim()).map(block=>({block_id:block.id,source_text:block.text_content,start:0,end:block.text_content.length}))]);
    // Validate captured selections against canonical current data, not stale DOM.
    if(!sources.length||sources.some(source=>!String(source.block_id).startsWith('chapter-title:')&&!(book.blocks||[]).some(block=>String(block.id)===String(source.block_id)&&block.text_content===source.source_text)))throw Error('NARRATION_EXPORT_MISSING');
    const records=await narrationStore.list(pid);check();const clips=sources.map(source=>narrationExportClip(source,records,voice));
    if(clips.some(clip=>!clip))throw Error('NARRATION_EXPORT_MISSING');
    const parts=[];let bytes=0;
    for(let i=0;i<clips.length;i++){
      const clip=clips[i],blob=await narrationStore.audio(pid,clip.record.id);check();
      const pcm=await narrationWavPCM(blob,clip.start,clip.end);bytes+=pcm.size;if(bytes>0xffffffff-36)throw Error('NARRATION_EXPORT_SIZE');parts.push(pcm);onProgress(i+1,clips.length);
    }
    check();const audio=narrationJoinWav(parts),url=URL.createObjectURL(audio),anchor=document.createElement('a');
    const title=target.scope==='chapter'?chapters[0]?.title:activeWorkspaceProject()?.name;
    anchor.href=url;anchor.download=((title||'Breadmaker')+'-'+target.scope).replace(/[\\/:*?"<>|\u0000-\u001f]/g,'-').slice(0,160)+'.wav';document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  }finally{narration.queueLoading=false;syncNarrationActions()}
}
