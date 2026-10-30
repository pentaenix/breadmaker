// Settings-owned voice library. Fixed samples only; no manuscript publication.
async function openNarrationVoiceSettings(){
  try{
    const pid=currentProjectIdentity(),catalog=await loadNarrationVoiceCatalog(true);if(pid!==currentProjectIdentity())return;
    const parentVoice=$('#narrationSettingsVoice'),toggle=$('[data-narration-setting="showAllLanguages"]'),all=toggle?toggle.getAttribute('aria-checked')==='true':narrationPreferences.value.showAllLanguages;
    let language=narrationInterfaceLanguage(),active=true,busy=false,operation=0,previewURL=null,previewVoice=null,downloadJob=false,stopDownloads=false,message='',packProgress=null;
    const audio=new Audio(),cache=new Map(),chosen=()=>parentVoice?.value||narrationEffectiveVoice(),live=()=>active&&pid===currentProjectIdentity();
    const languageLabel=code=>i18nText(`narration.language_${code==='en-us'?'en':code.replace('-','_')}`);
    const available=narrationLanguageVoices(catalog.voices,all);
    const cleanup=()=>{active=false;operation++;audio.pause();if(previewURL)URL.revokeObjectURL(previewURL);cache.clear();queueMicrotask(()=>{if(pid===currentProjectIdentity())syncNarrationVoiceChoices()})};
    showNestedModal(i18nText('narration.voice_library'),`<div class="narration-voice-library">
      <p class="small-muted">${escapeHtml(i18nText('narration.library_help'))}</p>
      <div class="narration-settings-grid"><div class="field"><label for="narrationVoiceSearch">${escapeHtml(i18nText('narration.search_voices'))}</label><input id="narrationVoiceSearch" type="search" autocomplete="off" placeholder="${escapeHtml(i18nText('narration.search_voices'))}"></div>
      <div class="field"><label for="narrationVoiceLanguage">${escapeHtml(i18nSource('Language'))}</label><span class="narration-select"><select id="narrationVoiceLanguage" ${all?'':'disabled'}>${NARRATION_LANGUAGES.filter(code=>available.some(voice=>voice.language===code)).map(code=>`<option value="${code}">${escapeHtml(languageLabel(code))}</option>`).join('')}</select><svg class="view-menu-chevron" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></span></div></div>
      <div class="narration-voice-pack"><span id="narrationVoiceCount" class="small-muted"></span><button id="narrationVoicePack" class="soft-button view-menu-button" type="button" aria-haspopup="menu"><span>${escapeHtml(i18nText('narration.download_pack'))}</span><svg class="view-menu-chevron" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></button></div>
      <p class="small-muted">${escapeHtml(i18nText('narration.checked_voices'))}</p><div id="narrationVoiceList" class="narration-voice-list" aria-label="${escapeHtml(i18nText('narration.voice_library'))}"></div>
      <div class="narration-voice-sample"><small class="small-muted">${escapeHtml(i18nText('narration.sample_text'))}</small><p id="narrationVoiceSample"></p></div>
      <progress id="narrationVoicePackProgress" class="hidden" max="1" value="0"></progress><p id="narrationVoiceStatus" class="small-muted" role="status" aria-live="polite"></p>
    </div>`,`<button id="narrationVoiceStop" class="soft-button">${escapeHtml(i18nText('narration.stop_preview'))}</button><button id="narrationVoiceDone" class="soft-button">${escapeHtml(i18nText('narration.done'))}</button>`,cleanup);
    const search=$('#narrationVoiceSearch'),languageSelect=$('#narrationVoiceLanguage'),list=$('#narrationVoiceList'),status=$('#narrationVoiceStatus');languageSelect.value=language;
    const stopPreview=()=>{operation++;audio.pause();previewVoice=null};
    const render=()=>{
      if(!live())return;
      const voices=available.filter(voice=>voice.language===language),query=search.value.trim().toLocaleLowerCase(i18nCatalog.code),shown=new Set(narrationPreferences.value.visibleVoices);
      const matches=voices.filter(voice=>narrationVoiceOptionLabel(voice).toLocaleLowerCase(i18nCatalog.code).includes(query)).sort((a,b)=>Number(b.id===chosen())-Number(a.id===chosen())||Number(b.default)-Number(a.default)||Number(b.installed)-Number(a.installed)||a.name.localeCompare(b.name,i18nCatalog.code));
      $('#narrationVoiceCount').textContent=i18nText('narration.voices_downloaded',{current:voices.filter(voice=>voice.installed).length,total:voices.length});
      list.innerHTML=matches.map(voice=>`<div class="narration-voice-row ${chosen()===voice.id?'active':''}" data-voice-id="${voice.id}">
        <label class="narration-voice-show"><input type="checkbox" data-voice-show="${voice.id}" aria-label="${escapeHtml(i18nText('narration.show_named_voice',{name:voice.name}))}" ${shown.has(voice.id)?'checked':''} ${busy||!voice.installed||narrationEffectiveVoice()===voice.id||chosen()===voice.id?'disabled':''}><span class="narration-voice-copy"><b>${escapeHtml(voice.name)}</b><small>${escapeHtml(i18nText(`narration.accent_${voice.accent}`))} · ${escapeHtml(i18nText(`narration.gender_${voice.gender}`))}</small></span></label>
        <span class="narration-voice-row-actions"><button class="bare-button" data-voice-preview="${voice.id}" aria-label="${escapeHtml((previewVoice===voice.id&&!audio.paused?i18nText('narration.stop_preview'):i18nText('narration.preview_named_voice',{name:voice.name})))}" ${busy||!voice.installed||narration.busy||narration.queueLoading?'disabled':''}>${previewVoice===voice.id&&!audio.paused?'■':'▷'}</button>
        <button class="soft-button" ${voice.installed?'data-voice-use':'data-voice-download'}="${voice.id}" ${busy||(voice.installed&&chosen()===voice.id)?'disabled':''}>${escapeHtml(i18nText(!voice.installed?'narration.download_voice':chosen()===voice.id?'narration.selected_voice':'narration.use_voice'))}</button></span>
      </div>`).join('')||`<p class="small-muted narration-voice-no-results">${escapeHtml(i18nText('narration.no_voices'))}</p>`;
      $('#narrationVoiceSample').textContent=catalog.samples[language];
      status.textContent=message;$('#narrationVoicePack').disabled=busy||!voices.some(voice=>!voice.installed);
      languageSelect.disabled=!all||busy;search.disabled=downloadJob;
      $('#narrationVoiceStop').disabled=downloadJob?stopDownloads:!previewVoice&&audio.paused;$('#narrationVoiceStop').textContent=i18nText(downloadJob?'narration.stop_downloads':'narration.stop_preview');
      const progress=$('#narrationVoicePackProgress');progress.classList.toggle('hidden',!packProgress);if(packProgress)progress.value=packProgress.current/packProgress.total;
    };
    const find=id=>catalog.voices.find(voice=>voice.id===id);
    const persistVisible=ids=>saveNarrationPreferences({visibleVoices:[...new Set([...narrationPreferences.value.visibleVoices,...ids])]});
    const download=async (voices,isPack=false)=>{
      if(!live()||busy)return;const pending=voices.filter(voice=>!voice.installed);if(!pending.length)return;stopPreview();busy=true;downloadJob=true;stopDownloads=false;packProgress={current:0,total:pending.length};message='';render();
      const installed=[];
      const showInstalled=()=>persistVisible(installed.filter(id=>!isPack||find(id).default));
      try{
        for(const voice of pending){
          if(!live()||stopDownloads)break;
          message=i18nText('narration.pack_progress',{current:packProgress.current+1,total:pending.length});render();
          if(!voice.installed)Object.assign(voice,await api(`/api/narration-voices/${voice.id}/download`,{method:'POST',headers:{'X-Breadmaker-Project':pid}}));
          installed.push(voice.id);packProgress.current++;render();
        }
        if(live()){await showInstalled();message=i18nText(stopDownloads?'narration.downloads_stopped':'narration.pack_ready')}
      }catch{if(live()){message=i18nText('narration.voice_download_failed');if(installed.length)await showInstalled().catch(()=>{})}}
      finally{busy=false;downloadJob=false;packProgress=null;render()}
    };
    list.onclick=async event=>{
      const button=event.target.closest('button');if(!button||button.disabled||!live()||busy)return;
      const id=button.dataset.voiceDownload||button.dataset.voiceUse||button.dataset.voicePreview,voice=find(id);if(!voice)return;
      if(button.dataset.voiceDownload){await download([voice]);return}
      if(button.dataset.voiceUse){
        busy=true;render();
        try{await persistVisible([voice.id]);if(!live())return;if(parentVoice){closeModal();syncNarrationVoiceChoices();parentVoice.value=voice.id}else{await saveNarrationPreferences({voice:voice.id});closeModal()}}
        catch{message=i18nText('narration.failed')}finally{busy=false;render()}return;
      }
      if(previewVoice===voice.id&&!audio.paused){stopPreview();render();return}
      const token=++operation;audio.pause();cancelNarrationEarly();narration.audio.pause();previewVoice=voice.id;busy=true;narration.queueLoading=true;syncNarrationActions();message=i18nText('narration.voice_preparing');render();
      try{
        let blob=cache.get(voice.id);
        if(!blob){const output=await narrationEngine().generate(catalog.samples[voice.language],voice.id,()=>{},()=>{},1,false);blob=output.blob;if(live())cache.set(voice.id,blob)}
        if(!live()||token!==operation)return;if(previewURL)URL.revokeObjectURL(previewURL);previewURL=URL.createObjectURL(blob);audio.src=previewURL;await audio.play();message='';
      }catch{if(live())message=i18nText('narration.failed')}
      finally{busy=false;narration.queueLoading=false;syncNarrationActions();render()}
    };
    list.onchange=async event=>{
      const input=event.target.closest('[data-voice-show]');if(!input||!live()||busy)return;
      busy=true;render();try{const ids=narrationPreferences.value.visibleVoices;await saveNarrationPreferences({visibleVoices:input.checked?[...ids,input.dataset.voiceShow]:ids.filter(id=>id!==input.dataset.voiceShow)})}catch{message=i18nText('narration.failed')}finally{busy=false;render()}
    };
    $('#narrationVoicePack').onclick=event=>{
      if(busy||!live())return;const voices=available.filter(voice=>voice.language===language),defaults=voices.filter(voice=>voice.default);
      const item=(key,pack)=>{const missing=pack.filter(voice=>!voice.installed),size=new Intl.NumberFormat(i18nCatalog.code,{maximumFractionDigits:1}).format(missing.reduce((total,voice)=>total+voice.size,0)/1024/1024);return {label:i18nText(key,{count:pack.length,size}),disabled:!missing.length,action:()=>download(pack,true)}};
      const items=[item('narration.pack_defaults',defaults)];if(defaults.length!==voices.length)items.push(item('narration.pack_all',voices));
      const rect=event.currentTarget.getBoundingClientRect();openContextMenu(rect.left,rect.bottom,items,{anchorRect:rect});
    };
    search.oninput=()=>render();languageSelect.onchange=()=>{stopPreview();language=languageSelect.value;search.value='';message='';render()};
    $('#narrationVoiceStop').onclick=()=>{if(downloadJob){stopDownloads=true;render();return}stopPreview();message='';render()};$('#narrationVoiceDone').onclick=()=>closeModal();audio.onended=()=>{previewVoice=null;render()};render();
  }catch{toast(i18nText('narration.voice_catalog_failed'))}
}
