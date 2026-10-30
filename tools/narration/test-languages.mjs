import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {prepareRomance,prepareItalian} from './languages.mjs';
const vocab=JSON.parse(await readFile(new URL('../../static/vendor/narration/models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/tokenizer.json',import.meta.url),'utf8')).model.vocab;
for(const [language,text] of [['fr','Le pain est prêt. Il y en a 12, et 3,14 euros. Cafe\u0301.'],['pt-BR','Olá, o pão está pronto. São 12 pães e 3,14 euros. Café.']]){
  const items=prepareRomance(text,language);
  for(const item of items){assert(text.includes(item.subtitles));for(const char of item.value)assert(char in vocab,`${language}: unknown token ${char}`)}
  assert(items.some(item=>item.subtitles==='12'));assert(items.some(item=>item.subtitles==='3,14'));
  assert(items.some(item=>item.value.includes('̃')));
  if(language==='fr'){assert.equal(items.find(item=>item.subtitles==='Le').value,'lə ');assert.equal(items.find(item=>item.subtitles==='est').value,'ɛ ')}
}
const italian=await prepareItalian('È aperta, alle 12. L’acqua.',async data=>{assert.equal(data.language,'it');assert(data.words.includes('dodici'));return {phonemes:data.words.map(()=>'a')}});
assert.deepEqual(italian.map(item=>item.subtitles),['È','aperta','alle','12','L','acqua']);
assert(italian.find(item=>item.subtitles==='12').value.endsWith('. '));
console.log('PASS: French and Portuguese token inventories, numbers, nasal vowels, function words and unchanged subtitles; Italian number expansion and elisions.');
