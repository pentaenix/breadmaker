'use strict';

// Explicit preview mount only. Not included in index.html or production boot.
function mountAppTelemetryConsentPreview(client,{host=document.body}={}) {
  const copy={title:'Help make Breadmaker better',
    summary:'Share optional app usage with Breadmaker to help us decide what to improve.',
    fields:'Shared: a random installation ID, app version, 15-minute activity windows, session counts, active minutes and workspace visit counts (Manuscript, Canvas, Maps, Tasks and Reviews).',
    excluded:'Never your writing, project titles, file paths, keystrokes or account details.',
    choice:'Your choice. Breadmaker works fully offline without sharing. Change this anytime in Settings → App usage.',
    preview:'Review preview: nothing is collected or sent. These choices are local to this browser.'};
  let banner=null;
  const text=value=>escapeHtml(value);
  const description=()=>`<p>${text(copy.summary)}</p><p class="small-muted">${text(copy.fields)}</p><p class="small-muted">${text(copy.excluded)}</p><p class="small-muted">${text(copy.choice)}</p><p class="small-muted">${text(copy.preview)}</p>`;
  function choose(allowed){
    const saved=client.setConsent(allowed);
    if(!saved)toast('App usage stays off because this browser could not save your choice.');
    render();
  }
  function render(){
    banner?.remove();banner=null;
    if(client.consent()!=='pending')return;
    banner=document.createElement('section');banner.className='app-telemetry-banner';
    banner.setAttribute('aria-label','Optional app usage');
    banner.innerHTML=`<div class="app-telemetry-copy"><b>${text(copy.title)}</b>${description()}</div><div class="app-telemetry-actions"><button type="button" class="soft-button" data-telemetry-allow>Allow app usage</button><button type="button" class="soft-button" data-telemetry-decline>Not now</button></div><button type="button" class="bare-button ui-close-button" aria-label="Close; keep app usage off" data-telemetry-close>×</button>`;
    host.appendChild(banner);
    banner.querySelector('[data-telemetry-allow]').onclick=()=>choose(true);
    banner.querySelector('[data-telemetry-decline]').onclick=()=>choose(false);
    // Closing saves a refusal, never permission. No repeat nag on next launch.
    banner.querySelector('[data-telemetry-close]').onclick=()=>choose(false);
  }
  function openSettings(onDismiss=null){
    const enabled=client.consent()==='granted';
    showModal('App usage',`${description()}<div class="project-settings-switch-row"><span class="project-settings-switch-copy"><b>Share optional app usage</b><small>Turning this off clears unsent reports and the installation ID.</small></span><button id="appTelemetrySwitch" class="structure-switch ${enabled?'active':''}" type="button" role="switch" aria-checked="${enabled}" aria-label="Share optional app usage"><span aria-hidden="true"></span></button></div>`,'',onDismiss);
    const button=$('#appTelemetrySwitch');
    button.onclick=()=>{choose(client.consent()!=='granted');syncSwitch()};
  }
  function syncSwitch(){const button=$('#appTelemetrySwitch');if(button){const enabled=client.consent()==='granted';button.classList.toggle('active',enabled);button.setAttribute('aria-checked',String(enabled))}}
  const unsubscribe=client.subscribe(()=>{render();syncSwitch()});
  const sync=()=>client.syncConsent();window.addEventListener('storage',sync);render();
  return Object.freeze({openSettings,dispose(){unsubscribe();window.removeEventListener('storage',sync);banner?.remove()}});
}
