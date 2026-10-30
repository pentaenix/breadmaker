'use strict';

// 7.0 — UI localization. Project/manuscript content never passes through this layer.
let i18nCatalog={schema_version:1,code:'en',english_name:'English',native_name:'English',direction:'ltr',strings:{},css_variables:{}};
let i18nReady=false,i18nObserver=null;
const i18nMissingKeys=new Set();
const i18nTextNodes=new Set(),i18nAttrNodes=new Set();
const I18N_MARKER_RE=/\{\{i18n:([^}]+)\}\}/g;
const I18N_STORAGE_KEY='breadmaker-language';
const I18N_ATTRIBUTES=['title','aria-label','placeholder','aria-description','label'];
function validLocalePayload(payload){return !!payload&&payload.schema_version===1&&typeof payload.code==='string'&&!!payload.code&&payload.strings&&typeof payload.strings==='object'}
async function fetchLocalePayload(code){const safe=String(code||'en').replace(/[^A-Za-z0-9_-]/g,'')||'en',response=await fetch(`/locales/${encodeURIComponent(safe)}.json`,{cache:'no-store'});if(!response.ok)throw new Error(`Locale request failed (${response.status})`);const payload=await response.json();if(!validLocalePayload(payload))throw new Error(`Locale ${safe} is invalid.`);return payload}
function storeLocalePreference(code){try{localStorage.setItem(I18N_STORAGE_KEY,String(code||'en'))}catch{}}
function i18nText(key,vars={}){
  let value=i18nCatalog?.strings?.[key];
  if(value==null){
    if(!i18nReady)return `{{i18n:${key}}}`;
    if(!i18nMissingKeys.has(key)){i18nMissingKeys.add(key);console.warn(`Missing localization key: ${key}`)}
    return String(key);
  }
  value=String(value);
  return value.replace(/\{([A-Za-z0-9_]+)\}/g,(_,name)=>Object.prototype.hasOwnProperty.call(vars,name)?String(vars[name]):`{${name}}`);
}
function resolveI18nMarkers(value){if(typeof value!=='string'||!value.includes('{{i18n:'))return value;return value.replace(I18N_MARKER_RE,(_,key)=>i18nText(key))}
function i18nSource(value){if(typeof value!=='string')return value;const key=i18nCatalog?.source_index?.[value];return key?i18nText(key):resolveI18nMarkers(value)}
function bindI18nTextNode(node){const value=node?.nodeValue;if(!value?.includes?.('{{i18n:'))return;if(!node.__breadmakerI18nTemplate)node.__breadmakerI18nTemplate=value;i18nTextNodes.add(node);const resolved=resolveI18nMarkers(node.__breadmakerI18nTemplate);if(node.nodeValue!==resolved)node.nodeValue=resolved}
function bindI18nAttrs(node){if(node?.nodeType!==Node.ELEMENT_NODE)return;for(const attr of I18N_ATTRIBUTES){const value=node.getAttribute?.(attr);if(!value?.includes('{{i18n:'))continue;node.__breadmakerI18nAttrs=node.__breadmakerI18nAttrs||{};if(!node.__breadmakerI18nAttrs[attr])node.__breadmakerI18nAttrs[attr]=value;i18nAttrNodes.add(node);const resolved=resolveI18nMarkers(node.__breadmakerI18nAttrs[attr]);if(node.getAttribute(attr)!==resolved)node.setAttribute(attr,resolved)}}
function applyI18n(root=document){
  if(!root)return;if(root.nodeType===Node.TEXT_NODE){bindI18nTextNode(root);return}if(root.nodeType===Node.ELEMENT_NODE)bindI18nAttrs(root);
  if(root.nodeType!==Node.ELEMENT_NODE&&root.nodeType!==Node.DOCUMENT_NODE&&root.nodeType!==Node.DOCUMENT_FRAGMENT_NODE)return;
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT|NodeFilter.SHOW_ELEMENT);let current;while(current=walker.nextNode()){if(current.nodeType===Node.TEXT_NODE)bindI18nTextNode(current);else bindI18nAttrs(current)}
}
function applyI18nCssVariables(){for(const [cssVar,key] of Object.entries(i18nCatalog?.css_variables||{}))document.documentElement.style.setProperty(cssVar,JSON.stringify(i18nText(key)))}
function refreshI18nBindings(){const observer=i18nObserver;observer?.disconnect();try{applyI18nCssVariables();for(const node of [...i18nTextNodes]){if(!node.isConnected){i18nTextNodes.delete(node);continue}{const resolved=resolveI18nMarkers(node.__breadmakerI18nTemplate||node.nodeValue);if(node.nodeValue!==resolved)node.nodeValue=resolved}}for(const node of [...i18nAttrNodes]){if(!node.isConnected){i18nAttrNodes.delete(node);continue}for(const [attr,template] of Object.entries(node.__breadmakerI18nAttrs||{})){const resolved=resolveI18nMarkers(template);if(node.getAttribute(attr)!==resolved)node.setAttribute(attr,resolved)}}}finally{observer?.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:I18N_ATTRIBUTES})}}
function installLocalePayload(payload,{persist=true}={}){
  if(!validLocalePayload(payload))throw new Error('Breadmaker locale is invalid.');
  i18nCatalog=payload;i18nReady=true;document.documentElement.lang=payload.code||'en';document.documentElement.dir=payload.direction==='rtl'?'rtl':'ltr';if(persist)storeLocalePreference(payload.code||'en');
  if(i18nObserver)refreshI18nBindings();else{applyI18nCssVariables();applyI18n(document);i18nObserver=new MutationObserver(records=>{for(const record of records){if(record.type==='characterData'){bindI18nTextNode(record.target)}else if(record.type==='attributes'){bindI18nAttrs(record.target)}else for(const node of record.addedNodes)applyI18n(node)}});i18nObserver.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:I18N_ATTRIBUTES})}
  return i18nCatalog;
}
async function switchLocale(code){const payload=await fetchLocalePayload(code),installed=installLocalePayload(payload,{persist:true});refreshRuntimeLocalizedUi();return installed}
async function ensureI18n(){
  if(i18nReady)return i18nCatalog;
  let payload;
  try{payload=await (window.__breadmakerLocalePromise||fetchLocalePayload('en'))}catch(error){throw new Error(`Breadmaker could not load a locale: ${error?.message||error}`)}
  return installLocalePayload(payload,{persist:true});
}

