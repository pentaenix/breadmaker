// Browser Romance frontends, plus number expansion for the offline Italian API.
import {FrenchG2P} from '@piper-plus/g2p/fr';
import {PortugueseG2P} from '@piper-plus/g2p/pt';
import {toCardinal as frenchNumber} from 'n2words/fr';
import {toCardinal as portugueseNumber} from 'n2words/pt-BR';
import {toCardinal as italianNumber} from 'n2words/it';
const frontends={fr:new FrenchG2P(),'pt-BR':new PortugueseG2P()};
const numbers={fr:frenchNumber,'pt-BR':portugueseNumber,it:italianNumber};
const punctuation={',':',','.':'.',';':';',':':':','!':'!','?':'?','—':'—','–':'—','-':' ','…':'…','"':'"','“':'"','”':'"','«':'"','»':'"','(':'(',')':')',"'":' '};
const pua={'\uE01D':'r','\uE01E':'y','\uE054':'ʧ','\uE055':'ʤ','\uE056':'ɛ̃','\uE057':'ɑ̃','\uE058':'ɔ̃','\uE063':'œ̃','\uE064':'ɐ̃'};
const frenchFunctionWords={le:'lə',de:'də',je:'ʒə',que:'kə',ce:'sə',ne:'nə',me:'mə',te:'tə',se:'sə',est:'ɛ',es:'ɛ',et:'e',les:'le',des:'de',mes:'me',tes:'te',ses:'se',ces:'se',un:'œ̃',une:'yn',nous:'nu',vous:'vu',ils:'il',elles:'ɛl'};
function expandedNumber(value,language){
  const cardinal=numbers[language],[integer,...fractions]=value.split(/[.,]/),digits=value=>[...value].map(digit=>cardinal(digit)).join(' ');
  return (integer.length>1&&integer.startsWith('0')?digits(integer):cardinal(integer))+fractions.map(part=>(language==='it'?' virgola ':language==='fr'?' virgule ':' vírgula ')+digits(part)).join('');
}
export function pronunciationTokens(text,language){
  const items=[];
  for(const match of text.matchAll(/[\p{L}\p{M}]+|\d+(?:[.,]\d+)*|[^\p{L}\p{M}\d]+/gu)){
    const original=match[0];
    if(!/^[\p{L}\d]/u.test(original)){
      const value=[...original].map(char=>/\s/u.test(char)?' ':punctuation[char]||'').join('');
      if(value){if(items.length)items.at(-1).suffix+=value;else items.push({original:'',word:'',suffix:value})}
    }else items.push({original,word:/^\d/.test(original)?expandedNumber(original,language):original.normalize('NFC'),suffix:''});
  }
  if(!items.some(item=>item.word))throw Error('NARRATION_TEXT_EMPTY');
  return items;
}
export function prepareRomance(text,language){
  return pronunciationTokens(text,language).map(item=>{
    let value='';
    if(item.word){
      const result=frontends[language].phonemizeWithProsody(item.word);
      value=result.tokens.map((token,index)=>(language==='pt-BR'&&result.prosody[index]?.a2===2?'ˈ':'')+(pua[token]||token)).join('').replace(/[ãẽĩõũ]/g,char=>({ã:'ã',ẽ:'ẽ',ĩ:'ĩ',õ:'õ',ũ:'ũ'})[char]);
      if(language==='fr')value=frenchFunctionWords[item.word.toLowerCase()]||value;
    }
    value+=item.suffix;
    if(!value||value.length>=350||/[\uE000-\uF8FF]/u.test(value))throw Error('NARRATION_PRONUNCIATION_INVALID');
    return {type:'phonetic',value,subtitles:item.original};
  });
}
export async function prepareItalian(text,pronounce){
  const tokens=pronunciationTokens(text,'it'),words=tokens.flatMap(item=>item.word?item.word.split(/\s+/u):[]),response=await pronounce({language:'it',words});let cursor=0;
  return tokens.map(item=>({type:'phonetic',value:(item.word?item.word.split(/\s+/u).map(()=>response.phonemes[cursor++]).join(' '):'')+item.suffix,subtitles:item.original}));
}
