// Project Settings, language, and retention presentation.

function projectSettingsRow(id,label,description,{danger=false,translated=false}={}){const title=translated?label:i18nSource(label),copy=translated?description:i18nSource(description);return `<button id="${id}" class="project-settings-row${danger?' danger':''}" type="button"><span><b>${escapeHtml(title)}</b><small>${escapeHtml(copy)}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>`}
function normalizeLocaleSearch(value){return String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase()}
function localeDisplayName(locale){const native=String(locale?.native_name||locale?.english_name||locale?.code||'').trim(),english=String(locale?.english_name||native).trim();return native&&english&&native.localeCompare(english,undefined,{sensitivity:'accent'})!==0?`${native} · ${english}`:(native||english||String(locale?.code||''))}
async function openLanguageSettings(onDismiss=null){
  let response;try{response=await api('/api/locales')}catch(error){toast(error?.message||i18nText('settings.language.load_error'));onDismiss?.();return}
  const locales=Array.isArray(response?.locales)?response.locales:[],current=String(i18nCatalog?.code||response?.default||'en');
  showModal(i18nText('settings.language.modal_title'),`<div class="language-picker"><div class="field language-picker-search"><label for="languageSearch">${escapeHtml(i18nText('settings.language.search_label'))}</label><input id="languageSearch" type="search" autocomplete="off" autofocus placeholder="${escapeHtml(i18nText('settings.language.search_placeholder'))}"></div><div id="languageList" class="language-picker-list" role="listbox" aria-label="${escapeHtml(i18nText('settings.language.available_label'))}"></div></div>`,'',onDismiss);
  const input=$('#languageSearch'),list=$('#languageList');
  const render=()=>{const query=normalizeLocaleSearch(input?.value),matches=locales.filter(locale=>!query||normalizeLocaleSearch(`${locale.english_name||''} ${locale.native_name||''} ${locale.code||''}`).includes(query));list.innerHTML=matches.map(locale=>{const code=String(locale.code||''),native=String(locale.native_name||locale.english_name||code),english=String(locale.english_name||native),different=native.localeCompare(english,undefined,{sensitivity:'accent'})!==0,active=code===current;return `<button class="language-picker-row${active?' active':''}" type="button" role="option" aria-selected="${active?'true':'false'}" data-language-code="${escapeHtml(code)}"><span class="language-picker-copy"><b>${escapeHtml(native)}</b><small>${escapeHtml(english)}</small></span>${active?`<span class="language-picker-check" aria-label="${escapeHtml(i18nText('settings.language.current_label'))}">✓</span>`:''}</button>`}).join('')||`<div class="language-picker-empty">${escapeHtml(i18nText('settings.language.no_results'))}</div>`;for(const button of $$('[data-language-code]',list))button.onclick=async()=>{const code=button.dataset.languageCode;if(code===String(i18nCatalog?.code||'')){closeModal();return}for(const row of $$('[data-language-code]',list))row.disabled=true;try{await switchLocale(code);closeModal();toast(i18nText('settings.language.changed',{language:localeDisplayName(locales.find(item=>String(item.code)===code))}))}catch(error){for(const row of $$('[data-language-code]',list))row.disabled=false;toast(error?.message||i18nText('settings.language.load_error'),4000)}}};
  input.oninput=render;render();queueMicrotask(()=>input?.focus());
}
async function openLocalHistoryRetention(onDismiss=null){
  let settings;try{settings=await api('/api/history/retention')}catch(error){toast(error?.message||i18nText('history.retention_load_failed'),4200);onDismiss?.();return}
  const current=settings?.local_revision_history_retention||'90_days';
  const option=(value,label)=>`<option value="${value}" ${value===current?'selected':''}>${escapeHtml(i18nText(label))}</option>`;
  showModal(i18nText('history.retention_title'),`<div class="field"><label>${escapeHtml(i18nText('history.retention_local_label'))}</label><select id="localHistoryRetention">${option('30_days','backup.retention_30_days')}${option('90_days','backup.retention_90_days')}${option('1_year','backup.retention_1_year')}${option('forever','backup.retention_forever')}</select><small class="small-muted">${escapeHtml(i18nText('history.retention_local_help'))}</small></div>`,`<button id="localHistoryRetentionSave">${escapeHtml(i18nSource('Save'))}</button>`,onDismiss);
  $('#localHistoryRetentionSave').onclick=async()=>{try{await api('/api/history/retention',{method:'PUT',body:JSON.stringify({value:$('#localHistoryRetention').value})});toast(i18nText('history.retention_saved'));closeModal();onDismiss?.()}catch(error){toast(error?.message||i18nText('history.retention_save_failed'),4200)}};
}

async function openNarrationSettings(onDismiss=null){
  try{
    const context=narrationSnapshot(),pid=context.pid,value=await loadNarrationPreferences();requireNarrationContext(context);
    const toggle=(key,title,help)=>`<div class="project-settings-switch-row"><span class="project-settings-switch-copy"><b>${escapeHtml(i18nText(title))}</b><small>${escapeHtml(i18nText(help))}</small></span><button class="structure-switch ${value[key]?'active':''}" data-narration-setting="${key}" type="button" role="switch" aria-checked="${value[key]}" aria-label="${escapeHtml(i18nText(title))}"><span aria-hidden="true"></span></button></div>`;
    const section=(title,body)=>`<section class="project-settings-section"><h3 class="project-settings-section-title">${escapeHtml(i18nText(title))}</h3>${body}</section>`;
    showModal(i18nText('narration.title'),`<div class="narration-settings-form">
      ${section('narration.voices',`<div class="narration-settings-voice"><div class="field"><label for="narrationSettingsVoice">${escapeHtml(i18nText('narration.voice'))}</label><span class="narration-select"><select id="narrationSettingsVoice">${$('#narrationVoice').innerHTML}</select><svg class="view-menu-chevron" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></span></div><button id="narrationSettingsMoreVoices" class="soft-button" type="button">${escapeHtml(i18nText('narration.more_voices'))}</button></div>${toggle('showAllLanguages','narration.show_all_languages','narration.show_all_languages_help')}`)}
      ${section('narration.playback_settings',`<div class="narration-settings-grid">
      <div class="field"><label for="narrationSettingsSpeed">${escapeHtml(i18nText('narration.default_speed'))}</label><span class="narration-select"><select id="narrationSettingsSpeed">${NARRATION_SPEEDS.map(speed=>`<option value="${speed}">${speed}×</option>`).join('')}</select><svg class="view-menu-chevron" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></span><small class="small-muted">${escapeHtml(i18nText('narration.pacing_help'))}</small></div>
      <div class="field"><label for="narrationSettingsPitch">${escapeHtml(i18nText('narration.pitch'))}</label><span class="narration-select"><select id="narrationSettingsPitch">${NARRATION_PITCHES.map(pitch=>`<option value="${pitch}">${pitch>0?'+':''}${pitch}</option>`).join('')}</select><svg class="view-menu-chevron" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></span><small class="small-muted">${escapeHtml(i18nText('narration.pitch_help'))}</small></div></div>
      ${toggle('autoplay','narration.autoplay','narration.autoplay_help')}${toggle('continueChapters','narration.continue_chapters','narration.continue_help')}
      <button id="narrationSettingsExport" class="soft-button" type="button">${escapeHtml(i18nText('narration.export_audio'))}</button>`)}
      ${section('narration.generation_settings',`
      <div class="field"><label for="narrationSettingsPace">${escapeHtml(i18nText('narration.generation_pace'))}</label><span class="narration-select"><select id="narrationSettingsPace">${NARRATION_PACES.map(pace=>`<option value="${pace}">${pace}×</option>`).join('')}</select><svg class="view-menu-chevron" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></span><small class="small-muted">${escapeHtml(i18nText('narration.generation_pace_help'))}</small></div>
      ${toggle('punctuationPauses','narration.punctuation_pauses','narration.punctuation_pauses_help')}`)}
      ${section('narration.highlight_settings',toggle('passage','narration.highlight_passage','narration.highlight_passage_help')+toggle('word','narration.highlight_word','narration.highlight_word_help'))}
      ${section('narration.performance_settings',toggle('gpu','narration.gpu','narration.gpu_help')+'<small id="narrationSettingsBackend" class="small-muted" role="status"></small>'+toggle('earlyPlayback','narration.early','narration.early_help'))}
      <p class="small-muted">${escapeHtml(i18nText('narration.settings_help'))}</p><p id="narrationSettingsBusy" class="small-muted ${narration.busy?'':'hidden'}">${escapeHtml(i18nText('narration.settings_busy'))}</p></div>`,
      `<button id="narrationSettingsCancel" class="soft-button">${escapeHtml(i18nSource('Cancel'))}</button><button id="narrationSettingsSave" class="soft-button" ${narration.busy?'disabled':''}>${escapeHtml(i18nSource('Save'))}</button>`,onDismiss);
    $('#narrationSettingsVoice').value=narrationEffectiveVoice();$('#narrationSettingsSpeed').value=String(value.speed);$('#narrationSettingsPace').value=String(value.generationPace);$('#narrationSettingsPitch').value=String(value.pitch);syncNarrationBackend();
    const exportContext={chapterId:String(state.chapter?.id||narrationChapterSources()[0]?.chapter_id||''),documentId:state.activeDocumentId},selection=narration.selection.map(source=>({...source}));
    $('#narrationSettingsExport').onclick=()=>{if(narrationCurrent(context))openNarrationExportSettings(exportContext,selection,context)};
    $('#narrationSettingsMoreVoices').onclick=()=>{if(narrationCurrent(context))openNarrationVoiceSettings()};
    $('#narrationSettingsCancel').onclick=()=>closeModal();
    for(const button of $$('[data-narration-setting]'))button.onclick=()=>{const next=button.getAttribute('aria-checked')!=='true';button.setAttribute('aria-checked',String(next));button.classList.toggle('active',next);if(button.dataset.narrationSetting==='showAllLanguages'){const select=$('#narrationSettingsVoice'),current=select.value;select.innerHTML=narrationVoiceOptions(narrationLanguageVoices(narrationVoiceCatalog.value.voices.filter(voice=>value.visibleVoices.includes(voice.id)),next));applyI18n(select);select.value=[...select.options].some(option=>option.value===current&&!option.disabled)?current:narrationEffectiveVoice({...value,showAllLanguages:next})}};
    $('#narrationSettingsSave').onclick=async()=>{
      if(narration.busy||!narrationCurrent(context))return;const button=$('#narrationSettingsSave');button.disabled=true;button.dataset.saving='1';
      const patch={voice:$('#narrationSettingsVoice').value,speed:Number($('#narrationSettingsSpeed').value),generationPace:Number($('#narrationSettingsPace').value),pitch:Number($('#narrationSettingsPitch').value)};for(const toggle of $$('[data-narration-setting]'))patch[toggle.dataset.narrationSetting]=toggle.getAttribute('aria-checked')==='true';
      try{await narration.audio.setPitch(patch.pitch);requireNarrationContext(context);if(patch.gpu&&!value.gpu&&narration.device)narration.device.gpuChoice=null;await saveNarrationPreferences(patch,context);if(!narrationCurrent(context))return;closeModal(false);onDismiss?.()}catch(error){if(narrationCurrent(context)){await narration.audio.setPitch(narrationPreferences.value.pitch).catch(()=>{});delete button.dataset.saving;button.disabled=false;toast(error?.message||i18nText('narration.failed'),5000)}}
    };
  }catch(error){toast(error?.message||i18nText('narration.failed'),5000);onDismiss?.()}
}

function openNarrationExportSettings(context,selection,captured=null){
  const snapshot=requireNarrationContext(captured||narrationSnapshot()),option=(scope,key,disabled=false)=>`<option value="${scope}" ${disabled?'disabled':''}>${escapeHtml(i18nText(key))}</option>`;
  showNestedModal(i18nText('narration.export_audio'),`<p class="small-muted">${escapeHtml(i18nText('narration.export_help'))}</p><div class="field"><label for="narrationExportScope">${escapeHtml(i18nText('narration.export_scope'))}</label><span class="narration-select"><select id="narrationExportScope">${option('selection','narration.export_selection',!selection.length)}${option('chapter','narration.export_chapter',!context.chapterId)}${option('book','narration.export_book')}</select><svg class="view-menu-chevron" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></span></div><p id="narrationExportStatus" class="small-muted" role="status"></p>`,`<button id="narrationExportDownload" class="soft-button">${escapeHtml(i18nText('narration.export_download'))}</button>`);
  $('#narrationExportScope').value=state.viewMode==='book'?'book':context.chapterId?'chapter':'book';
  const button=$('#narrationExportDownload'),status=$('#narrationExportStatus');
  button.onclick=async()=>{
    if(!narrationCurrent(snapshot))return;button.disabled=true;
    try{await exportNarrationAudio({...context,scope:$('#narrationExportScope').value},selection,(current,total)=>{if(narrationCurrent(snapshot))status.textContent=i18nText('narration.export_progress',{current,total})},snapshot);if(narrationCurrent(snapshot))status.textContent=i18nText('narration.export_complete')}
    catch(error){status.textContent=i18nText(error.message==='NARRATION_EXPORT_MISSING'?'narration.export_missing':error.message==='NARRATION_EXPORT_BUSY'?'narration.settings_busy':error.message==='NARRATION_EXPORT_SIZE'?'narration.export_too_large':'narration.failed')}
    finally{button.disabled=false}
  };
}

async function openProjectSettings(){
  try{state.workspace=await api('/api/workspace')}catch{}
  const recovery=state.workspace?.recovery||null,active=activeWorkspaceProject();
  const candidate=recovery?(state.workspace?.recovery_candidates||[]).find(item=>String(item.id)===String(recovery.active_project_id)):null;
  const fallbackName=recovery?.active_project_name||i18nText('project.settings_unavailable_name');
  const displayName=active?.name||fallbackName;
  const displayPath=active?.path||candidate?.path||state.workspace?.root||'';
  if(recovery){
    showModal('Project Settings',`
      <div class="project-settings-overview">
        <div class="project-settings-name"><div><small>{{i18n:dynamic.html.text.small.4f798f98}}</small><h2>${escapeHtml(displayName)}</h2><p>${escapeHtml(i18nText('project.settings_unavailable_body'))}</p></div></div>
        <div class="project-settings-path" title="${escapeHtml(displayPath)}">${escapeHtml(displayPath)}</div>
      </div>
      <section class="project-settings-section"><div class="project-settings-section-title">${escapeHtml(i18nSource('Recovery'))}</div>
        ${projectSettingsRow('settingsRecovery','Recovery Backups','Restore a verified Recovery Backup as a separate project.')}
        ${projectSettingsRow('settingsBrowseProjects','Browse All Projects','Open another healthy local project.')}
        ${projectSettingsRow('settingsTrash',i18nText('trash.title'),i18nText('trash.description'),{translated:true})}
      </section>
      <section class="project-settings-section project-settings-danger"><div class="project-settings-section-title">{{i18n:dynamic.html.text.div.80514006}}</div>
        ${projectSettingsRow('settingsDeleteAll','Delete All Data…','Permanently remove all Breadmaker-managed local data on this computer.',{danger:true})}
      </section>`, '');
    const returnToSettings=()=>setTimeout(()=>openProjectSettings(),0);
    const child=openChild=>{closeModal(false);openChild(returnToSettings)};
    $('#settingsRecovery').onclick=()=>child(openRecovery);
    $('#settingsBrowseProjects').onclick=()=>child(openAllProjects);
    $('#settingsTrash').onclick=()=>child(openProjectTrash);
    $('#settingsDeleteAll').onclick=()=>{closeModal(false);runDangerDelete('delete_all_data',null,returnToSettings)};
    return;
  }
  if(!active)return;
  const chapterCount=visibleChapters().length,sceneCount=state.scenes.length,chapterSummary=i18nCount(chapterCount,'count.chapters.one','count.chapters.many'),sceneSummary=i18nCount(sceneCount,'count.scenes.one','count.scenes.many'),canDeleteProject=(active.membership_role||'owner')==='owner';
  showModal('Project Settings',`
    <div class="project-settings-overview">
      <div class="project-settings-name"><div><small>{{i18n:dynamic.html.text.small.4f798f98}}</small><h2>${escapeHtml(active.name)}</h2><p>${escapeHtml(chapterSummary)} · ${escapeHtml(sceneSummary)}</p></div><button id="settingsRenameProject" class="project-settings-pencil" type="button" title="{{i18n:dynamic.html.attr.title.10f1c39b}}" aria-label="{{i18n:dynamic.html.attr.aria.label.e02cb6a7}}">✎</button></div>
      <div class="project-settings-path" title="${escapeHtml(active.path||state.workspace?.root||'')}">${escapeHtml(active.path||state.workspace?.root||'')}</div>
    </div>
    <section class="project-settings-section"><div class="project-settings-section-title">{{i18n:dynamic.html.text.div.1cf975e3}}</div>
      ${projectSettingsRow('settingsMetadata','Metadata','Manuscript counts, editorial structure, Canvas, Maps, and project dates.')}
      ${projectSettingsRow('settingsHistory','History','Browse automatic recovery states, compare, export, or restore.')}
      ${projectSettingsRow('settingsBrowseProjects','Browse All Projects','Search and switch across the complete local workspace.')}
    </section>
    <section class="project-settings-section"><div class="project-settings-section-title">{{i18n:dynamic.html.text.div.e4013e42}}</div>
      ${projectSettingsRow('settingsExperimental','Experimental Features','Choose optional unfinished tools for this project. They are off by default.')}
    </section>
    ${state.runtime?.mode==='local'||runtimeSupports('narration')?`<section class="project-settings-section"><div class="project-settings-section-title">${escapeHtml(i18nText('narration.title'))}</div>${projectSettingsRow('settingsNarration',i18nText('narration.settings_title'),i18nText('narration.settings_description'),{translated:true})}</section>`:''}
    <section class="project-settings-section"><div class="project-settings-section-title">{{i18n:dynamic.html.text.div.a9cbf4a5}}</div>
      ${projectSettingsRow('settingsBackups',i18nText('backup.destinations_title'),i18nText('backup.destinations_description'),{translated:true})}
      ${projectSettingsRow('settingsHistoryRetention',i18nText('history.retention_title'),i18nText('history.retention_settings_description'),{translated:true})}
      ${projectSettingsRow('settingsRecovery',i18nText('backup.recovery_title'),i18nText('backup.recovery_description'),{translated:true})}
      ${projectSettingsRow('settingsTrash',i18nText('trash.title'),i18nText('trash.description'),{translated:true})}
      ${projectSettingsRow('settingsVerify','Verify Project','Check Current, Revision History, Checkpoints, Recovery, files, and disk usage.')}
    </section>
    <section class="project-settings-section project-settings-danger"><div class="project-settings-section-title">{{i18n:dynamic.html.text.div.80514006}}</div>
      ${canDeleteProject?projectSettingsRow('settingsDeleteProject','Delete Project…',i18nText('trash.delete_project_description'),{danger:true}):''}
      ${projectSettingsRow('settingsDeleteAll','Delete All Data…','Permanently remove all Breadmaker-managed local data on this computer.',{danger:true})}
    </section>`, '');
  const returnToSettings=()=>setTimeout(()=>openProjectSettings(),0);
  const child=openChild=>{closeModal(false);openChild(returnToSettings)};
  $('#settingsRenameProject').onclick=()=>child(renameCurrentProject);
  $('#settingsMetadata').onclick=()=>child(openProjectMetadata);
  $('#settingsHistory').onclick=()=>child(openHistory);
  $('#settingsBrowseProjects').onclick=()=>child(openAllProjects);
  $('#settingsExperimental').onclick=()=>child(openExperimentalFeatures);
  if($('#settingsNarration'))$('#settingsNarration').onclick=()=>child(openNarrationSettings);
  $('#settingsBackups').onclick=()=>child(openBackups);
  $('#settingsHistoryRetention').onclick=()=>child(openLocalHistoryRetention);
  $('#settingsRecovery').onclick=()=>child(openRecovery);
  $('#settingsTrash').onclick=()=>child(openProjectTrash);
  $('#settingsVerify').onclick=()=>child(verifyProject);
  if($('#settingsDeleteProject'))$('#settingsDeleteProject').onclick=()=>{closeModal(false);runDangerDelete('delete_project',active,returnToSettings)};
  $('#settingsDeleteAll').onclick=()=>{closeModal(false);runDangerDelete('delete_all_data',active,returnToSettings)};
}

