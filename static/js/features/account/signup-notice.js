// Legal copy is plain text plus a tiny inline-token vocabulary, never remote HTML.
const origin='https://breadmaker.studio';
export function noticeLanguage(language){return language==='es'?'es':'en'}
export function renderSignupNotice(container,payload,locale){
  const suffix=locale==='es'?'es/':'';
  const allowed=new Set(['mailto:privacy@breadmaker.studio',`${origin}/privacy/${suffix}`,`${origin}/age-requirements/${suffix}`]);
  const seen=new Set();let strong=0;
  const validText=text=>typeof text==='string'&&text.length<=48000&&!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]|\{\{|\}\}/.test(text);
  const keys=(object,expected)=>object&&typeof object==='object'&&!Array.isArray(object)&&Object.keys(object).sort().join(',')===expected.sort().join(',');
  if(!keys(payload,['version','locale','title','paragraphs'])||payload.version!=='9'||payload.locale!==locale||!validText(payload.title)||!payload.title.trim()||!Array.isArray(payload.paragraphs)||payload.paragraphs.length!==4)throw new Error('SIGNUP_NOTICE_INVALID');
  const section=document.createElement('section');section.lang=locale;section.dataset.noticeVersion='9';section.setAttribute('aria-labelledby','signup-privacy-title');
  const title=document.createElement('h2');title.id='signup-privacy-title';title.textContent=payload.title;section.append(title);
  payload.paragraphs.forEach((parts,index)=>{
    if(!Array.isArray(parts)||!parts.length||parts.length>100)throw new Error('SIGNUP_NOTICE_INVALID');
    const p=document.createElement('p');
    for(const part of parts){
      if(typeof part==='string'){
        if(!validText(part))throw new Error('SIGNUP_NOTICE_INVALID');
        p.append(document.createTextNode(part));continue;
      }
      if(!part||!['a','strong'].includes(part.tag)||!validText(part.text)||!part.text.trim()||!keys(part,part.tag==='a'?['tag','text','href']:['tag','text']))throw new Error('SIGNUP_NOTICE_INVALID');
      const node=document.createElement(part.tag);node.textContent=part.text;
      if(part.tag==='a'){
        if(!allowed.has(part.href))throw new Error('SIGNUP_NOTICE_INVALID');
        node.href=part.href;node.rel='noopener noreferrer';seen.add(part.href);
      }else{if(index!==0)throw new Error('SIGNUP_NOTICE_INVALID');strong++}
      p.append(node);
    }
    if(!p.textContent.trim())throw new Error('SIGNUP_NOTICE_INVALID');
    section.append(p);
  });
  if(strong!==1||seen.size!==allowed.size)throw new Error('SIGNUP_NOTICE_INVALID');
  container.replaceChildren(section);
}
