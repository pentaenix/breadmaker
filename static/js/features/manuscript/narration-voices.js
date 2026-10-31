// Fixed voice catalog and device installation status. No manuscript is used for previews.
const narrationVoiceCatalog={value:null,pending:null};
const NARRATION_LANGUAGES=['en-us','es','fr','it','ja','pt-BR','zh-CN'];
function narrationInterfaceLanguage(){return ({en:'en-us',es:'es',fr:'fr',it:'it',ja:'ja','pt-BR':'pt-BR','zh-CN':'zh-CN'})[i18nCatalog.code]||'en-us'}
function narrationVoiceLanguage(id){return ({a:'en-us',b:'en-us',e:'es',f:'fr',i:'it',j:'ja',p:'pt-BR',z:'zh-CN'})[String(id)[0]]}
function narrationLanguageVoices(voices,all=narrationPreferences.value.showAllLanguages){return all?voices:voices.filter(voice=>voice.language===narrationInterfaceLanguage())}
function narrationEffectiveVoice(value=narrationPreferences.value){
  const language=narrationInterfaceLanguage(),catalog=narrationVoiceCatalog.value?.voices||[],available=narrationLanguageVoices(catalog.filter(voice=>value.visibleVoices.includes(voice.id)),value.showAllLanguages);
  const preferred=value.showAllLanguages||narrationVoiceLanguage(value.voice)===language?value.voice:value.voicesByLanguage[language];
  return available.find(voice=>voice.id===preferred&&voice.installed!==false)?.id||available.find(voice=>voice.installed!==false)?.id||available[0]?.id||'';
}
async function loadNarrationVoiceCatalog(refresh=false){
  if(!refresh&&narrationVoiceCatalog.value)return narrationVoiceCatalog.value;
  if(narrationVoiceCatalog.pending)return narrationVoiceCatalog.pending;
  const pid=currentProjectIdentity(),snapshot=typeof narrationSnapshot==='function'?narrationSnapshot():null;
  const pending=api('/api/narration-voices',{headers:{'X-Breadmaker-Project':pid,...(snapshot?.transport?{'X-Breadmaker-Narration-Account':snapshot.account}:{})}}).then(async value=>{
    if(pid!==currentProjectIdentity()||snapshot&&!narrationCurrent(snapshot))throw Error('NARRATION_ACCOUNT_CONTEXT_CHANGED');
    if(snapshot?.transport)value=await (await narrationDevice(snapshot)).voices(value);
    if(snapshot&&!narrationCurrent(snapshot))throw Error('NARRATION_ACCOUNT_CONTEXT_CHANGED');
    narrationVoiceCatalog.value=value;return value;
  }).finally(()=>{if(narrationVoiceCatalog.pending===pending)narrationVoiceCatalog.pending=null});
  narrationVoiceCatalog.pending=pending;return pending;
}
function narrationVoiceOptionLabel(voice){return `${voice.name} · ${i18nText(`narration.accent_${voice.accent}`)} · ${i18nText(`narration.gender_${voice.gender}`)}`}
function narrationVoiceOptions(voices){
  return NARRATION_LANGUAGES.map(language=>{const items=voices.filter(voice=>voice.language===language),languageKey='narration.language_'+(language==='en-us'?'en':language.replace('-','_'));return items.length?`<optgroup label="{{i18n:${languageKey}}}">${items.map(voice=>`<option value="${escapeHtml(voice.id)}" ${voice.installed===false?'disabled':''}>${escapeHtml(voice.name)} · {{i18n:${'narration.accent_'+voice.accent}}} · {{i18n:${'narration.gender_'+voice.gender}}}</option>`).join('')}</optgroup>`:''}).join('');
}
function syncNarrationVoiceChoices(){
  const catalog=narrationVoiceCatalog.value;if(!catalog)return;
  const value=narrationPreferences.value,visible=new Set(value.visibleVoices),select=$('#narrationVoice');
  select.innerHTML=narrationVoiceOptions(narrationLanguageVoices(catalog.voices.filter(voice=>visible.has(voice.id))));applyI18n(select);select.value=narrationEffectiveVoice();
  const settings=$('#narrationSettingsVoice');if(settings){const current=settings.value,toggle=$('[data-narration-setting="showAllLanguages"]'),all=toggle?toggle.getAttribute('aria-checked')==='true':value.showAllLanguages;settings.innerHTML=narrationVoiceOptions(narrationLanguageVoices(catalog.voices.filter(voice=>visible.has(voice.id)),all));applyI18n(settings);settings.value=[...settings.options].some(option=>option.value===current&&!option.disabled)?current:narrationEffectiveVoice({...value,showAllLanguages:all})}
}
