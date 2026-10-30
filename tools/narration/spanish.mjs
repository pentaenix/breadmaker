// Browser Spanish adapter. Bundle only these two MIT language modules, not Piper.
import {SpanishG2P} from '@piper-plus/g2p/es';
import {toCardinal} from 'n2words/es';

const g2p=new SpanishG2P();
const punctuation={',':',','.':'.',';':';',':':':','!':'!','?':'?','—':'—','–':'—','-':' ','…':'…','"':'"','“':'"','”':'"','«':'"','»':'"','(':'(',')':')'};

function numberWords(value){
  // Unambiguous cardinal integers and decimal fractions. Do not infer dates,
  // currency, or thousands grouping from ambiguous punctuation.
  const [integer,...fractions]=value.split(/[.,]/);
  const words=integer.length>1&&integer.startsWith('0')?[...integer].map(digit=>toCardinal(digit)).join(' '):toCardinal(integer);
  return words+fractions.map(part=>' coma '+[...part].map(digit=>toCardinal(digit)).join(' ')).join('');
}

export function prepareSpanish(text){
  const items=[];
  for(const match of text.matchAll(/[\p{L}\p{M}]+|\d+(?:[.,]\d+)*|[^\p{L}\p{M}\d]+/gu)){
    const original=match[0];
    if(!/^[\p{L}\d]/u.test(original)){
      const value=[...original].map(char=>/\s/u.test(char)?' ':punctuation[char]||'').join('');
      if(value){if(items.length)items.at(-1).value+=value;else items.push({type:'phonetic',value,subtitles:''})}
      continue;
    }
    let word=/^\d/.test(original)?numberWords(original):original.normalize('NFC');
    // Common proper-name x exception; retain original spelling in subtitles.
    word=word.replace(/méxico/gi,'méjico').replace(/mexican/gi,'mejican');
    const value=g2p.phonemize(word).tokens.join('').replace(/\uE01D/g,'r').replace(/\uE054/g,'ʧ');
    if(!value||value.length>=350)throw Error('SPANISH_PRONUNCIATION_INVALID');
    // Original subtitles keep accented/decomposed text and number offsets intact.
    items.push({type:'phonetic',value,subtitles:original});
  }
  if(!items.some(item=>item.subtitles))throw Error('SPANISH_TEXT_EMPTY');
  return items;
}
