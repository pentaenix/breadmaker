// Project/collaborator UI preferences, not recording data or model assets.
const NARRATION_DEFAULTS=Object.freeze({gpu:true,earlyPlayback:true,autoplay:true,continueChapters:false,punctuationPauses:true,passage:true,word:true,showAllLanguages:false,speed:1,generationPace:1,pitch:0,voice:'af_heart',voicesByLanguage:{},visibleVoices:["af_heart","am_michael","bf_emma","bm_george","ef_dora","em_alex","em_santa","ff_siwis","if_sara","im_nicola","jf_alpha","jf_gongitsune","jm_kumo","pf_dora","pm_alex","pm_santa","zf_xiaobei","zf_xiaoni","zm_yunjian","zm_yunxi"]});
const NARRATION_SPEEDS=[.8,.9,.95,1,1.05,1.1,1.25,1.5,1.75,2];
const NARRATION_PACES=[.85,.9,.95,1,1.05,1.1,1.15];
const NARRATION_PITCHES=[-4,-3,-2,-1,0,1,2,3,4];
const NARRATION_VOICES=["af_alloy","af_aoede","af_bella","af_heart","af_jessica","af_kore","af_nicole","af_nova","af_river","af_sarah","af_sky","am_adam","am_echo","am_eric","am_fenrir","am_liam","am_michael","am_onyx","am_puck","am_santa","bf_alice","bf_emma","bf_isabella","bf_lily","bm_daniel","bm_fable","bm_george","bm_lewis","ef_dora","em_alex","em_santa","ff_siwis","if_sara","im_nicola","jf_alpha","jf_gongitsune","jm_kumo","pf_dora","pm_alex","pm_santa","zf_xiaobei","zf_xiaoni","zm_yunjian","zm_yunxi"];
function normalizeNarrationPreferences(value){
  const result={...NARRATION_DEFAULTS};
  if(value&&typeof value==='object'){
    for(const key of ['gpu','earlyPlayback','autoplay','continueChapters','punctuationPauses','passage','word','showAllLanguages'])if(typeof value[key]==='boolean')result[key]=value[key];
    if(NARRATION_SPEEDS.includes(Number(value.speed)))result.speed=Number(value.speed);
    if(NARRATION_PACES.includes(value.generationPace))result.generationPace=value.generationPace;
    if(NARRATION_PITCHES.includes(value.pitch))result.pitch=value.pitch;
    if(NARRATION_VOICES.includes(value.voice))result.voice=value.voice;
    if(Array.isArray(value.visibleVoices)){const visible=[...new Set(value.visibleVoices.filter(voice=>NARRATION_VOICES.includes(voice)))];if(visible.length)result.visibleVoices=visible}
    if(!result.visibleVoices.includes(result.voice))result.visibleVoices=[...result.visibleVoices,result.voice];
    result.voicesByLanguage={};
    for(const [language,voice] of Object.entries(value.voicesByLanguage||{}))if(NARRATION_VOICES.includes(voice)&&narrationVoiceLanguage(voice)===language)result.voicesByLanguage[language]=voice;
    result.voicesByLanguage[narrationVoiceLanguage(result.voice)]=result.voice;
    // A previously saved English/Spanish preference still needs usable defaults
    // when the user first switches to one of the newly supported languages.
    for(const voice of NARRATION_DEFAULTS.visibleVoices){const language=narrationVoiceLanguage(voice);if(!result.visibleVoices.some(id=>narrationVoiceLanguage(id)===language))result.visibleVoices.push(...NARRATION_DEFAULTS.visibleVoices.filter(id=>narrationVoiceLanguage(id)===language))}
  }
  return result;
}
const narrationPreferences={project:null,value:{...NARRATION_DEFAULTS},loading:null};
async function loadNarrationPreferences(){
  const pid=currentProjectIdentity();
  if(narrationPreferences.project===pid&&narrationPreferences.loading)return narrationPreferences.loading;
  if(narrationPreferences.project===pid)return narrationPreferences.value;
  narrationPreferences.project=pid;narrationPreferences.value={...NARRATION_DEFAULTS};
  const loading=api('/api/ui-state/narration-preferences',{headers:{'X-Breadmaker-Project':pid}}).then(async response=>{
    if(typeof loadNarrationVoiceCatalog==='function')await loadNarrationVoiceCatalog().catch(()=>{});
    const value=normalizeNarrationPreferences(response.value);
    if(pid===currentProjectIdentity()&&narrationPreferences.project===pid)applyNarrationPreferences(value);
    return value;
  }).catch(error=>{if(narrationPreferences.project===pid)narrationPreferences.project=null;throw error}).finally(()=>{if(narrationPreferences.loading===loading)narrationPreferences.loading=null});
  narrationPreferences.loading=loading;return loading;
}
let narrationPreferenceWrite=Promise.resolve();
function saveNarrationPreferences(patch){
  const pid=currentProjectIdentity();
  const task=narrationPreferenceWrite.catch(()=>{}).then(async()=>{
    if(pid!==currentProjectIdentity())return;await loadNarrationPreferences();if(pid!==currentProjectIdentity())return;
    const value=normalizeNarrationPreferences({...narrationPreferences.value,...patch});
    await api('/api/ui-state/narration-preferences',{method:'PUT',headers:{'X-Breadmaker-Project':pid},body:JSON.stringify({value})});
    if(pid===currentProjectIdentity())applyNarrationPreferences(value);
  });
  narrationPreferenceWrite=task;return task;
}
