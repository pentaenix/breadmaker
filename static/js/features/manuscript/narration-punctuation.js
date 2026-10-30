// Deterministic, generation-time punctuation gaps. Original source offsets stay
// intact; only PCM and subsequent word times change. No text rewriting/model call.
function narrationPunctuationPause(text,word){
  const suffix=text.slice(word.end).match(/^[\s"'”’»\)\]\}」』,.!?…;:。，、；：！？]*/u)?.[0]||'';
  const prefix=text.slice(0,word.end)+suffix,match=prefix.match(/([,;:.!?…。，、；：！？]+)[\s"'”’»\)\]\}」』]*$/u);
  if(!match)return 0;
  const marks=match[1],at=match.index;
  // Decimal and thousands separators are not spoken punctuation boundaries.
  if(/^[,.]$/.test(marks)&&/\d/.test(text[at-1]||'')&&/\d/.test(text[at+1]||''))return 0;
  if(marks==='.'){
    const before=prefix.slice(0,at+1),token=before.match(/([\p{L}.]+)\.$/u)?.[1]?.toLocaleLowerCase();
    if(/(?:^|[^\p{L}])(?:\p{L}\.)+$/u.test(before)||['mr','mrs','ms','dr','prof','sr','sra','srta','dra','ud','uds','st','jr','vs','etc','pág','núm','vol','fig','a.m','p.m','e.g','i.e'].includes(token))return 0;
  }
  if(/…|\.{2,}/.test(marks))return .35;
  if(/[.!?。！？]/.test(marks))return .3;
  if(/[;:；：]/.test(marks))return .18;
  return .12;
}
function applyNarrationPunctuation(text,pcm,words,sampleRate=24000){
  const edits=[];
  for(let i=0;i<words.length;i++){
    const word=words[i],minimum=narrationPunctuationPause(text,word);if(!minimum)continue;
    const next=words[i+1],end=word.time+word.duration;
    if(!Number.isFinite(end)||end<0||end>pcm.length/sampleRate+.02)continue;
    const at=next?Math.round(next.time*sampleRate):pcm.length;
    if(at<Math.round(end*sampleRate)||at>pcm.length)continue; // Ambiguous/overlapping alignment: do not cut speech.
    let gap=at/sampleRate-end;
    if(!next){let quiet=at;const limit=Math.max(0,at-Math.ceil(minimum*sampleRate));while(quiet>limit&&Math.abs(pcm[quiet-1])<.006)quiet--;gap=Math.max(gap,(at-quiet)/sampleRate)}
    const count=Math.max(0,Math.ceil((minimum-gap)*sampleRate));
    if(count>sampleRate*.005)edits.push({at,count});
  }
  if(!edits.length)return {pcm,timings:words,duration:pcm.length/sampleRate};
  const output=new Float32Array(pcm.length+edits.reduce((n,edit)=>n+edit.count,0));let from=0,to=0;
  for(const edit of edits){output.set(pcm.subarray(from,edit.at),to);to+=edit.at-from+edit.count;from=edit.at}
  output.set(pcm.subarray(from),to);
  const timings=words.map(word=>({...word,time:word.time+edits.filter(edit=>edit.at<=Math.round(word.time*sampleRate)).reduce((n,edit)=>n+edit.count/sampleRate,0)}));
  return {pcm:output,timings,duration:output.length/sampleRate};
}
window.applyNarrationPunctuation=applyNarrationPunctuation;
