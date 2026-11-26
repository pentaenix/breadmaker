// Hosted account preference only. No presence, activity listeners or telemetry.
const usageConsentPath='/api/account/usage-consent';
let usageConsentValue=null,usageConsentBusy=false,usageConsentEpoch=0,usageConsentStarted=false;
function usageConsentAvailable(){return state.runtime?.mode==='hosted'&&runtimeSupports('account_sessions')&&state.runtime?.auth?.auth_mode==='identity_platform'}
function usageConsentText(key){return i18nText(`usage_consent.${key}`)}
function usageConsentDetailsMarkup(){return `<details class="usage-consent-details"><summary>${escapeHtml(usageConsentText('details'))}</summary><p>${escapeHtml(usageConsentText('scope'))}</p><p>${escapeHtml(usageConsentText('exclusions'))}</p><p>${escapeHtml(usageConsentText('retention'))} <a href="https://breadmaker.studio/privacy" target="_blank" rel="noopener noreferrer">${escapeHtml(usageConsentText('privacy_notice'))}</a></p></details>`}
function usageConsentPromptMarkup(){return `<div class="usage-consent-copy"><b id="usageConsentTitle">${escapeHtml(usageConsentText('title'))}</b><p>${escapeHtml(usageConsentText('summary'))}</p><small>${escapeHtml(usageConsentText('remembered'))}</small></div>${usageConsentDetailsMarkup()}<div class="usage-consent-actions"><button type="button" class="soft-button" data-usage-choice="allow">${escapeHtml(usageConsentText('allow'))}</button><button type="button" class="soft-button" data-usage-choice="refuse">${escapeHtml(usageConsentText('refuse'))}</button></div><p class="usage-consent-error" role="alert" hidden></p>`}
function clearUsageConsent(){usageConsentEpoch++;usageConsentValue=null;usageConsentBusy=false;document.getElementById('usageConsentBanner')?.remove();renderUsageConsentPrivacy()}
function renderUsageConsentBanner(){
  let panel=document.getElementById('usageConsentBanner');
  if(!usageConsentAvailable()||!usageConsentValue?.needs_choice){panel?.remove();return}
  if(!panel){panel=document.createElement('section');panel.id='usageConsentBanner';panel.className='usage-consent-banner';panel.setAttribute('aria-labelledby','usageConsentTitle');panel.innerHTML=usageConsentPromptMarkup();(document.getElementById('app')||document.body).appendChild(panel);
    panel.querySelectorAll('[data-usage-choice]').forEach(button=>button.onclick=()=>saveUsageConsent(button.dataset.usageChoice==='allow',panel));
  }
  panel.querySelectorAll('button').forEach(button=>button.disabled=usageConsentBusy);
}
async function loadUsageConsent(){
  if(!usageConsentAvailable()){clearUsageConsent();return null}
  const epoch=usageConsentEpoch,result=await api(usageConsentPath);
  if(epoch!==usageConsentEpoch||usageConsentBusy)return usageConsentValue;
  usageConsentValue=result;renderUsageConsentBanner();return result;
}
async function saveUsageConsent(choice,host){
  if(usageConsentBusy||!usageConsentAvailable()||!usageConsentValue)return;
  const epoch=++usageConsentEpoch,previous=usageConsentValue;
  usageConsentBusy=true;
  const error=host?.querySelector('.usage-consent-error');if(error)error.hidden=true;
  host?.querySelectorAll('button').forEach(button=>button.disabled=true);
  try{
    const result=await api(usageConsentPath,{method:'PUT',body:JSON.stringify({choice,revision:previous.revision,notice_version:previous.notice_version})});
    if(epoch!==usageConsentEpoch)return;
    usageConsentValue=result;
    toast(usageConsentText(choice?'saved_on':'saved_off'));
  }catch(failure){
    if(epoch!==usageConsentEpoch)return;
    if(failure.status===401||failure.status===403){clearUsageConsent();return}
    if(failure.status===409){
      try{const latest=await api(usageConsentPath);if(epoch!==usageConsentEpoch)return;usageConsentValue=latest}catch{}
    }
    if(error?.isConnected){error.textContent=usageConsentText(failure.status===409?'changed':'save_failed');error.hidden=false}
  }finally{
    if(epoch===usageConsentEpoch){usageConsentBusy=false;host?.querySelectorAll('button').forEach(button=>button.disabled=false);renderUsageConsentBanner();renderUsageConsentPrivacy()}
  }
}
function usageConsentPrivacyMarkup(){
  return `<div id="usageConsentPrivacy" class="usage-consent-privacy"><div class="project-settings-switch-row"><span class="project-settings-switch-copy"><b id="usageConsentSwitchLabel">${escapeHtml(usageConsentText('statistics'))}</b><small>${escapeHtml(usageConsentText('summary'))}</small></span><button type="button" id="usageConsentSwitch" class="structure-switch" role="switch" aria-labelledby="usageConsentSwitchLabel" aria-checked="false"><span aria-hidden="true"></span></button></div><p id="usageConsentStatus" class="small-muted" aria-live="polite"></p>${usageConsentDetailsMarkup()}<p class="usage-consent-error" role="alert" hidden></p></div>`;
}
function renderUsageConsentPrivacy(){
  const button=document.getElementById('usageConsentSwitch');if(!button)return;
  const on=!!usageConsentValue?.allowed;button.classList.toggle('active',on);button.setAttribute('aria-checked',String(on));button.disabled=usageConsentBusy||!usageConsentValue;
  const status=document.getElementById('usageConsentStatus');if(status)status.textContent=usageConsentText(!usageConsentValue?'loading':usageConsentValue.needs_choice?'pending':on?'on':'off');
}
async function openUsagePrivacy(){
  if(!usageConsentAvailable())return;
  closeUserProfilePopover();
  showModal(usageConsentText('privacy'),usageConsentPrivacyMarkup(),`<button type="button" id="usageConsentClose">${escapeHtml(usageConsentText('close'))}</button>`);
  document.getElementById('usageConsentClose').onclick=()=>closeModal();
  document.getElementById('usageConsentSwitch').onclick=()=>saveUsageConsent(!usageConsentValue?.allowed,document.getElementById('usageConsentPrivacy'));
  renderUsageConsentPrivacy();
  try{await loadUsageConsent();renderUsageConsentPrivacy()}
  catch{const error=document.querySelector('#usageConsentPrivacy .usage-consent-error');if(error){error.textContent=usageConsentText('load_failed');error.hidden=false}}
}
function startUsageConsent(){
  if(!usageConsentAvailable())return;
  loadUsageConsent().catch(()=>{}); // Preference outages never block writing.
  if(usageConsentStarted)return;usageConsentStarted=true;
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&!usageConsentBusy)loadUsageConsent().catch(()=>{})});
}
