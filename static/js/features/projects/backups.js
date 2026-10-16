// Backup Destination configuration and backup-management UI.

function backupDestinationStatusLabel(destination){
  const key={up_to_date:'backup.destinations_status_up_to_date',pending:'backup.destinations_status_pending',backing_up:'backup.destinations_status_backing_up',unavailable:'backup.destinations_status_unavailable',needs_attention:'backup.destinations_status_attention'}[destination?.status]||'backup.destinations_status_pending';
  return i18nText(key);
}
function backupDestinationIsSynced(destination){return destination?.destination_kind==='synced_folder'}
function backupDestinationIsRemote(destination){return destination?.provider==='webdav'||destination?.provider==='google_drive'}
function backupDestinationIsGoogleDrive(destination){return destination?.provider==='google_drive'}
function backupDestinationKindLabel(destination){return i18nText(backupDestinationIsGoogleDrive(destination)?'backup.destinations_type_google':(destination?.provider==='webdav'?'backup.destinations_type_webdav':(backupDestinationIsSynced(destination)?'backup.destinations_type_synced':'backup.destinations_type_local')))}
function backupDestinationLocation(destination){if(backupDestinationIsGoogleDrive(destination))return i18nText('backup.destinations_google_location');return destination?.endpoint||destination?.resolved_path||destination?.path||''}
function backupDestinationLastVerified(destination){
  if(backupDestinationIsRemote(destination))return destination.last_verified?.created_at?i18nText('backup.destinations_last_remote_verified',{date:i18nDateTime(destination.last_verified.created_at)}):i18nText('backup.destinations_never_remote');
  if(backupDestinationIsSynced(destination))return destination.last_verified?.created_at?i18nText('backup.destinations_last_local_verified',{date:i18nDateTime(destination.last_verified.created_at)}):i18nText('backup.destinations_never_local');
  return destination.last_verified?.created_at?i18nText('backup.destinations_last_verified',{date:i18nDateTime(destination.last_verified.created_at)}):i18nText('backup.destinations_never');
}
function backupDestinationWarning(destination){
  if(destination?.status==='needs_attention'){
    if(backupDestinationIsRemote(destination)){
      if(backupDestinationIsGoogleDrive(destination)){
        if(destination.reason==='google_auth_failed'||destination.reason==='backup_credentials_missing')return i18nText('backup.destinations_google_auth_warning');
        if(destination.reason==='google_permission_denied')return i18nText('backup.destinations_google_permission_warning');
        if(destination.reason==='google_tls_failed'||destination.reason==='google_redirect_forbidden')return i18nText('backup.destinations_remote_security_warning');
      }else{
        if(destination.reason==='webdav_auth_failed'||destination.reason==='backup_credentials_missing')return i18nText('backup.destinations_remote_auth_warning');
        if(destination.reason==='webdav_tls_failed'||destination.reason==='webdav_redirect_forbidden')return i18nText('backup.destinations_remote_security_warning');
      }
      if(destination.reason==='identity_mismatch')return i18nText('backup.destinations_remote_identity_warning');
      return i18nText('backup.destinations_remote_attention');
    }
    return i18nText(destination.reason==='backup_failed'?'backup.destinations_write_failed':(destination.reason==='folder_missing'?'backup.destinations_folder_missing':(destination.reason==='unsupported_destination_kind'?'backup.destinations_kind_invalid':'backup.destinations_identity_warning')));
  }
  if(destination?.status==='unavailable')return i18nText(backupDestinationIsRemote(destination)?'backup.destinations_remote_unavailable_help':'backup.destinations_unavailable_help');
  if(backupDestinationIsSynced(destination)&&destination?.same_disk===true)return i18nText('backup.destinations_synced_same_disk_warning');
  if(backupDestinationIsSynced(destination)&&destination?.same_disk===null&&destination?.available)return i18nText('backup.destinations_synced_disk_unknown_warning');
  if(destination?.same_disk===true)return i18nText('backup.destinations_same_disk_warning');
  if(destination?.same_disk===null&&destination?.available&&!backupDestinationIsRemote(destination))return i18nText('backup.destinations_disk_unknown_warning');
  return '';
}
function backupDestinationNotice(destination,updated=false){
  if(backupDestinationIsGoogleDrive(destination))return i18nText(updated?'backup.destinations_google_updated':'backup.destinations_google_added');
  if(destination?.provider==='webdav')return i18nText(updated?'backup.destinations_remote_updated':'backup.destinations_remote_added');
  if(backupDestinationIsSynced(destination)){
    if(destination.same_disk===true)return i18nText('backup.destinations_synced_same_disk_warning');
    if(destination.same_disk===null)return i18nText('backup.destinations_synced_disk_unknown_warning');
    return i18nText(updated?'backup.destinations_synced_updated':'backup.destinations_synced_added');
  }
  if(destination.same_disk===true)return i18nText('backup.destinations_same_disk_warning');
  if(destination.same_disk===null)return i18nText('backup.destinations_disk_unknown_warning');
  return i18nText(updated?'backup.destinations_updated':'backup.destinations_added');
}
async function chooseBackupDestinationFolder(){
  if(!runtimeSupports('native_filesystem'))return '';
  const picked=await api('/api/backup-destinations/pick-folder',{method:'POST'});return picked.cancelled||!picked.path?'':picked.path;
}
async function chooseBackupDestinationKind(current=null,includeRemote=true){
  return new Promise(resolve=>{
    let settled=false;const settle=value=>{if(settled)return;settled=true;resolve(value);closeModal(false)};
    const selected=current||'';
    const googleChoice=`
      <button class="project-settings-row backup-kind-choice${selected==='google_drive'?' active':''}" type="button" data-backup-kind="google_drive"><span><b>${escapeHtml(i18nText('backup.destinations_type_google'))}</b><small>${escapeHtml(i18nText('backup.destinations_type_google_help'))}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>`;
    const remoteChoices=includeRemote&&runtimeSupports('direct_remote_backups')?`${googleChoice}
      <button class="project-settings-row backup-kind-choice${selected==='webdav'?' active':''}" type="button" data-backup-kind="webdav"><span><b>${escapeHtml(i18nText('backup.destinations_type_webdav'))}</b><small>${escapeHtml(i18nText('backup.destinations_type_webdav_help'))}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>`:'';
    const localChoices=runtimeSupports('native_filesystem')?`
      <button class="project-settings-row backup-kind-choice${selected==='local'?' active':''}" type="button" data-backup-kind="local"><span><b>${escapeHtml(i18nText('backup.destinations_type_local'))}</b><small>${escapeHtml(i18nText('backup.destinations_type_local_help'))}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>
      <button class="project-settings-row backup-kind-choice${selected==='synced_folder'?' active':''}" type="button" data-backup-kind="synced_folder"><span><b>${escapeHtml(i18nText('backup.destinations_type_synced'))}</b><small>${escapeHtml(i18nText('backup.destinations_type_synced_help'))}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>`:'';
    showNestedModal(i18nText('backup.destinations_choose_type'),`<div class="project-settings-list">${localChoices}${remoteChoices}
    </div>`,'',()=>{if(!settled){settled=true;resolve(null)}});
    $$('[data-backup-kind]').forEach(button=>button.onclick=()=>settle(button.dataset.backupKind));
  });
}

function promptWebDAVConnection(destination=null){
  return new Promise(resolve=>{
    let settled=false;const settle=value=>{if(settled)return;settled=true;resolve(value);closeModal(false)};
    const endpoint=destination?.endpoint||'';
    showNestedModal(i18nText(destination?'backup.destinations_remote_reconnect':'backup.destinations_remote_connect'),`<div class="field"><label>${escapeHtml(i18nText('backup.destinations_remote_url'))}</label><input id="backupRemoteEndpoint" type="url" value="${escapeHtml(endpoint)}" placeholder="https://cloud.example.com/remote.php/dav/files/name/Breadmaker/"></div><div class="field"><label>${escapeHtml(i18nText('backup.destinations_remote_username'))}</label><input id="backupRemoteUsername" autocomplete="username"></div><div class="field"><label>${escapeHtml(i18nText('backup.destinations_remote_password'))}</label><input id="backupRemotePassword" type="password" autocomplete="current-password"></div>`,`<button id="backupRemoteConnect">${escapeHtml(i18nText(destination?'backup.destinations_remote_reconnect':'backup.destinations_remote_connect'))}</button>`,()=>{if(!settled){settled=true;resolve(null)}});
    $('#backupRemoteConnect').onclick=()=>{const value={endpoint:$('#backupRemoteEndpoint').value.trim(),username:$('#backupRemoteUsername').value,password:$('#backupRemotePassword').value};if(!value.endpoint)return;settle(value)};
  });
}

function promptGoogleDriveConnection(destination=null){
  return new Promise(resolve=>{
    let settled=false,pollTimer=null;const settle=value=>{if(settled)return;settled=true;if(pollTimer)clearTimeout(pollTimer);resolve(value);closeModal(false)};
    const title=i18nText('backup.destinations_type_google');
    const action=i18nText(destination?'backup.destinations_google_reconnect':'backup.destinations_google_connect');
    showNestedModal(title,`<div class="field"><label>${escapeHtml(i18nText('backup.destinations_google_client_id'))}</label><input id="backupGoogleClientId" type="text" autocomplete="off" spellcheck="false"></div><div class="field"><label>${escapeHtml(i18nText('backup.destinations_google_client_secret'))}</label><input id="backupGoogleClientSecret" type="password" autocomplete="off" spellcheck="false"></div><div id="backupGoogleStatus" class="small-muted"></div>`,`<button id="backupGoogleConnect">${escapeHtml(action)}</button>`,()=>{if(!settled){settled=true;if(pollTimer)clearTimeout(pollTimer);resolve(null)}});
    const button=$('#backupGoogleConnect'),clientIdInput=$('#backupGoogleClientId'),clientSecretInput=$('#backupGoogleClientSecret');
    api('/api/backup-destinations/google-drive/config').then(config=>{if(!settled&&clientIdInput&&!clientIdInput.value)clientIdInput.value=String(config?.client_id||'')}).catch(()=>{});
    button.onclick=async()=>{
      const clientId=clientIdInput.value.trim();let clientSecret=clientSecretInput.value.trim();if(!clientId){clientIdInput.focus();return}
      if(!clientSecret&&!destination){clientSecretInput.focus();return}
      clientSecretInput.value='';button.disabled=true;const statusEl=$('#backupGoogleStatus');statusEl.textContent=i18nText('backup.destinations_google_starting');
      try{
        const requestBody=JSON.stringify({destination_id:destination?.id||null,client_id:clientId,client_secret:clientSecret});clientSecret='';
        const started=await api('/api/backup-destinations/google-drive/oauth/start',{method:'POST',body:requestBody});
        const popup=window.open(started.authorization_url,'breadmaker-google-oauth','popup,width=720,height=760');statusEl.innerHTML=`${escapeHtml(i18nText('backup.destinations_google_waiting'))} <a id="backupGoogleOpen" href="${escapeHtml(started.authorization_url)}" target="_blank" rel="noopener">${escapeHtml(i18nText('backup.destinations_google_open'))}</a>`;
        const poll=async()=>{if(settled)return;try{const state=await api(`/api/backup-destinations/google-drive/oauth/status/${encodeURIComponent(started.state)}`);if(state.status==='success'){try{popup?.close()}catch{};settle(state.destination||null);return}if(state.status==='error'){button.disabled=false;statusEl.textContent=i18nText('backup.destinations_google_failed',{error:state.error||''});return}}catch{}pollTimer=setTimeout(poll,650)};poll();
      }catch(error){button.disabled=false;statusEl.textContent=String(error?.message||'')||i18nText('backup.destinations_google_failed',{error:''})}
    };
  });
}


function renderBackups(destinations,onDismiss=null,refreshOnly=false){
  destinations=Array.isArray(destinations)?destinations:[];
  const rows=destinations.map(destination=>{
    const last=backupDestinationLastVerified(destination);
    const diskFlag=backupDestinationIsRemote(destination)?'':(backupDestinationIsSynced(destination)?(destination.same_disk===true?` · ${i18nText('backup.destinations_synced_same_disk_short')}`:(destination.same_disk===null&&destination.available?` · ${i18nText('backup.destinations_synced_disk_unknown_short')}`:'')):(destination.same_disk===true?` · ${i18nText('backup.destinations_same_disk_short')}`:(destination.same_disk===null&&destination.available?` · ${i18nText('backup.destinations_disk_unknown_short')}`:'')));const detail=`${backupDestinationKindLabel(destination)} · ${backupDestinationStatusLabel(destination)}${diskFlag} · ${last}`;
    const location=backupDestinationLocation(destination);return `<button class="project-settings-row backup-destination-row" type="button" data-backup-destination="${escapeHtml(destination.id)}"><span><b>${escapeHtml(destination.name||i18nText('backup.destinations_local_name'))}</b><small>${escapeHtml(detail)}</small><small class="backup-destination-path" title="${escapeHtml(location)}">${escapeHtml(location)}</small></span><span class="project-settings-row-caret" aria-hidden="true">›</span></button>`;
  }).join('');
  const body=`<div class="backup-destination-list">${rows||`<div class="recovery-backup-empty">${escapeHtml(i18nText('backup.destinations_empty'))}</div>`}</div>`;
  const canAddDestination=runtimeSupports('native_filesystem')||runtimeSupports('direct_remote_backups');
  const actions=`<button id="backupUpdateAll" class="quiet-button">${escapeHtml(i18nText('backup.destinations_update_all'))}</button>${canAddDestination?`<button id="backupAddDestination">${escapeHtml(i18nText('backup.destinations_add'))}</button>`:''}`;
  if(refreshOnly)renderModal(i18nText('backup.destinations_title'),body,actions,onDismiss);else showModal(i18nText('backup.destinations_title'),body,actions,onDismiss);
  $('#backupUpdateAll').onclick=async()=>{const button=$('#backupUpdateAll');button.disabled=true;try{await api('/api/recovery-backups/update',{method:'POST'});toast(i18nText('backup.destinations_update_queued'));setTimeout(()=>openBackups(onDismiss,true),250)}catch(error){button.disabled=false;toast(error?.message||i18nText('backup.destinations_failed'),4200)}};
  const addDestination=$('#backupAddDestination');if(addDestination)addDestination.onclick=async()=>{const button=addDestination,label=button.textContent;button.disabled=true;try{const destinationKind=await chooseBackupDestinationKind();if(!destinationKind){button.disabled=false;return}button.textContent=i18nSource('Creating…');let result;if(destinationKind==='google_drive'){const destination=await promptGoogleDriveConnection();if(!destination){button.disabled=false;button.textContent=label;return}result={destination}}else if(destinationKind==='webdav'){const connection=await promptWebDAVConnection();if(!connection){button.disabled=false;button.textContent=label;return}result=await api('/api/backup-destinations/webdav',{method:'POST',body:JSON.stringify(connection)})}else{const path=await chooseBackupDestinationFolder();if(!path){button.disabled=false;button.textContent=label;return}result=await api('/api/backup-destinations',{method:'POST',body:JSON.stringify({path,destination_kind:destinationKind})})}const destination=result.destination||{};toast(backupDestinationNotice(destination,false),destination.same_disk===false?3200:5600);openBackups(onDismiss,true)}catch(error){button.disabled=false;button.textContent=label;toast(error?.message||i18nText('backup.destinations_failed'),4200)}};
  $$('[data-backup-destination]').forEach(button=>button.onclick=()=>{const id=button.dataset.backupDestination;const destination=destinations.find(item=>String(item.id)===String(id));if(!destination)return;closeModal(false);openBackupDestinationDetails(destination,onDismiss)});
}
async function openBackups(onDismiss=null,refreshOnly=false){
  const payload=await api('/api/backup-destinations');
  return renderBackups(payload.destinations||[],onDismiss,refreshOnly);
}
async function openBackupRetentionConfigure(destination,onDismiss=null){
  const history=destination.remote_history_retention||'90_days',recovery=destination.recovery_retention_profile||'standard',remote=backupDestinationIsRemote(destination);
  const option=(value,current,label)=>`<option value="${value}" ${value===current?'selected':''}>${escapeHtml(i18nText(label))}</option>`;
  const historyField=remote?`<div class="field"><label>${escapeHtml(i18nText('backup.retention_remote_history'))}</label><select id="backupHistoryRetention">${option('30_days',history,'backup.retention_30_days')}${option('90_days',history,'backup.retention_90_days')}${option('1_year',history,'backup.retention_1_year')}${option('forever',history,'backup.retention_forever')}</select><small class="small-muted">${escapeHtml(i18nText('backup.retention_remote_history_help'))}</small></div>`:'';
  const body=`${historyField}<div class="field"><label>${escapeHtml(i18nText('backup.retention_recovery'))}</label><select id="backupRecoveryRetention">${option('compact',recovery,'backup.retention_compact')}${option('standard',recovery,'backup.retention_standard')}${option('extended',recovery,'backup.retention_extended')}</select><small class="small-muted">${escapeHtml(i18nText('backup.retention_recovery_help'))}</small></div>`;
  showNestedModal(i18nText('backup.retention_title'),body,`<button id="backupRetentionSave">${escapeHtml(i18nSource('Save'))}</button>`);
  $('#backupRetentionSave').onclick=async()=>{const payload={recovery_retention_profile:$('#backupRecoveryRetention').value};if(remote)payload.remote_history_retention=$('#backupHistoryRetention').value;const result=await api(`/api/backup-destinations/${encodeURIComponent(destination.id)}`,{method:'PUT',body:JSON.stringify(payload)});toast(i18nText('backup.retention_saved'));closeModal(false);openBackupDestinationDetails(result.destination||destination,onDismiss,true)};
}
async function openBackupDestinationDetails(destination,onDismiss=null,refreshOnly=false){
  const warning=backupDestinationWarning(destination);let backups=[];
  if(destination.available){try{backups=await api(`/api/backup-destinations/${encodeURIComponent(destination.id)}/discover`)}catch{}}
  const groups=[];const byProject=new Map();
  for(const backup of backups){const key=backup.project_id||`invalid:${backup.file}`;if(!byProject.has(key)){const group={project_id:backup.project_id||'',name:backup.name||i18nText('backup.recovery_unknown'),items:[]};byProject.set(key,group);groups.push(group)}byProject.get(key).items.push(backup)}
  const backupRows=groups.map(group=>`<section class="backup-recovery-project"><div class="backup-recovery-project-title">${escapeHtml(i18nText('backup.destinations_project',{name:group.name}))}</div>${group.items.map(b=>{const title=b.created_at?i18nDateTime(b.created_at):i18nText('backup.recovery_unknown');const stateLabel=b.candidate?i18nText('backup.destinations_candidate_unverified'):i18nText('verify.invalid_backup');return `<div class="recovery-backup-row"><div class="recovery-backup-copy" title="${escapeHtml(b.file||'')}"><b>${escapeHtml(title)}</b><small>${escapeHtml(stateLabel)} · ${formatBytes(b.bytes||0)}</small></div>${b.candidate?`<button class="quiet-button recovery-backup-restore" data-destination-backup="${escapeHtml(b.file)}">${escapeHtml(i18nText('backup.recovery_restore'))}</button>`:`<span class="danger recovery-backup-error">${escapeHtml(i18nText('verify.invalid_backup'))}</span>`}</div>`}).join('')}</section>`).join('');
  const last=backupDestinationLastVerified(destination);const kind=backupDestinationKindLabel(destination);const location=backupDestinationLocation(destination);const scope=i18nText(backupDestinationIsRemote(destination)?'backup.destinations_remote_verified_scope':(backupDestinationIsSynced(destination)?'backup.destinations_synced_verified_scope':'backup.destinations_local_verified_scope'));
  const remoteNotice=backupDestinationIsGoogleDrive(destination)?'backup.destinations_google_notice':'backup.destinations_remote_notice';const locationLabel=backupDestinationIsGoogleDrive(destination)?'backup.destinations_google_location_label':(backupDestinationIsRemote(destination)?'backup.destinations_remote_url':'backup.destinations_location');
  const body=`<div class="backup-folder-card"><div class="backup-folder-copy"><small>${escapeHtml(i18nText('backup.destinations_type'))}</small><b>${escapeHtml(kind)}</b></div>${backupDestinationIsRemote(destination)?'':`<button id="backupDestinationKind" class="soft-button" type="button">${escapeHtml(i18nText('backup.destinations_change_type'))}</button>`}</div><div class="backup-folder-card"><div class="backup-folder-copy"><small>${escapeHtml(i18nText(locationLabel))}</small><b title="${escapeHtml(location)}">${escapeHtml(location)}</b></div><button id="backupDestinationChange" class="soft-button" type="button">${escapeHtml(i18nText(backupDestinationIsGoogleDrive(destination)?'backup.destinations_google_reconnect':(backupDestinationIsRemote(destination)?'backup.destinations_remote_reconnect':'backup.destinations_change')))}</button></div>${backupDestinationIsRemote(destination)?`<p class="small-muted">${escapeHtml(i18nText(remoteNotice))}</p><p class="small-muted">${escapeHtml(i18nText('backup.destinations_cloud_retention'))}</p>`:(backupDestinationIsSynced(destination)?`<p class="small-muted">${escapeHtml(i18nText('backup.destinations_synced_notice'))}</p>`:'')}${warning?`<div class="backup-disk-warning">${escapeHtml(warning)}</div>`:''}<div class="backup-status-line"><b>${escapeHtml(backupDestinationStatusLabel(destination))}</b> · ${escapeHtml(last)}</div><p class="small-muted">${escapeHtml(scope)}</p>${destination.available?`<div class="backup-recovery-heading">${escapeHtml(i18nText('backup.destinations_recoverable_projects'))}</div>`:''}<div class="recovery-backup-list">${backupRows||(destination.available?`<div class="recovery-backup-empty">${escapeHtml(i18nText('backup.destinations_no_copies'))}</div>`:`<div class="recovery-backup-empty">${escapeHtml(i18nText('backup.destinations_connect_to_view'))}</div>`)}</div>`;
  const actions=`<button id="backupDestinationRemove" class="quiet-button">${escapeHtml(i18nText('backup.destinations_remove'))}</button><button id="backupDestinationConfigure" class="quiet-button">${escapeHtml(i18nText('backup.retention_configure'))}</button><button id="backupDestinationNow" ${destination.available?'':'disabled'}>${escapeHtml(i18nText('backup.destinations_now'))}</button>`;
  const back=()=>{closeModal(false);openBackups(onDismiss)};
  if(refreshOnly)renderModal(destination.name||i18nText('backup.destinations_local_name'),body,actions,back);else showModal(destination.name||i18nText('backup.destinations_local_name'),body,actions,back);
  if($('#backupDestinationKind'))$('#backupDestinationKind').onclick=async()=>{try{const kind=await chooseBackupDestinationKind(destination.destination_kind||'local',false);if(!kind||kind===destination.destination_kind)return;const result=await api(`/api/backup-destinations/${encodeURIComponent(destination.id)}`,{method:'PUT',body:JSON.stringify({destination_kind:kind})});const updated=result.destination||destination;toast(i18nText('backup.destinations_type_updated'));openBackupDestinationDetails(updated,onDismiss,true)}catch(error){toast(error?.message||i18nText('backup.destinations_failed'),4200)}};
  $('#backupDestinationChange').onclick=async()=>{try{let result;if(backupDestinationIsGoogleDrive(destination)){const updated=await promptGoogleDriveConnection(destination);if(!updated)return;result={destination:updated}}else if(destination?.provider==='webdav'){const connection=await promptWebDAVConnection(destination);if(!connection)return;result=await api(`/api/backup-destinations/${encodeURIComponent(destination.id)}/webdav`,{method:'PUT',body:JSON.stringify(connection)})}else{const path=await chooseBackupDestinationFolder();if(!path)return;result=await api(`/api/backup-destinations/${encodeURIComponent(destination.id)}`,{method:'PUT',body:JSON.stringify({path})})}const updated=result.destination||destination;toast(backupDestinationNotice(updated,true),updated.same_disk===false?3200:5600);openBackupDestinationDetails(updated,onDismiss,true)}catch(error){toast(error?.message||i18nText('backup.destinations_failed'),4200)}};
  $('#backupDestinationConfigure').onclick=()=>openBackupRetentionConfigure(destination,onDismiss);
  $('#backupDestinationRemove').onclick=async()=>{await api(`/api/backup-destinations/${encodeURIComponent(destination.id)}`,{method:'DELETE'});toast(i18nText('backup.destinations_removed'));openBackups(onDismiss,true)};
  $('#backupDestinationNow').onclick=async()=>{const button=$('#backupDestinationNow'),label=button.textContent;button.disabled=true;button.textContent=i18nSource('Creating…');try{const result=await api(`/api/backup-destinations/${encodeURIComponent(destination.id)}/now`,{method:'POST'});toast(i18nText('backup.destinations_created'));const updated=(result.destinations||[]).find(item=>String(item.id)===String(destination.id))||destination;openBackupDestinationDetails(updated,onDismiss,true)}catch(error){button.disabled=false;button.textContent=label;toast(error?.message||i18nText('backup.destinations_failed'),4200)}};
  $$('[data-destination-backup]').forEach(button=>button.onclick=async()=>{if(!await confirmDialog(i18nText('backup.recovery_restore_title'),i18nText('backup.recovery_restore_body'),{confirmLabel:i18nText('backup.recovery_restore_confirm')}))return;const result=await api(`/api/backup-destinations/${encodeURIComponent(destination.id)}/backups/${encodeURIComponent(button.dataset.destinationBackup)}/restore-as-project`,{method:'POST'});openWorkspaceProject(result?.project?.id||'')});
}

