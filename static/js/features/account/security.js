// Hosted-only account/security product UI.
// Identity Platform is the sole hosted authority. Interactive credentials and TOTP
// values live only in transient browser/Firebase memory; Breadmaker receives only a
// freshly verified proof and keeps authority in its HttpOnly server cookie.

function accountSecurityMode(){return String(state.runtime?.auth?.auth_mode||'')}
function accountSecurityAvailable(){return runtimeSupports('account_sessions')&&accountSecurityMode()==='identity_platform'}
function accountSecurityProviderLabel(provider){return ({google:'Google',apple:'Apple ID',email_password:'Email & password'})[provider]||String(provider||'Sign-in method')}
function accountSecurityProviderMark(provider){return provider==='google'?'G':provider==='apple'?'A':provider==='email_password'?'@':'•'}
function accountSecurityAuthMethodLabel(method){return ({google:'Google',apple:'Apple ID',email_password_totp:'Email & password',account_link:'Linked identity',email_password:'Email & password',security_totp:'Security verification',security_totp_disabled:'Security verification',security_unlink_identity:'Security verification',security_password_change:'Security verification'})[method]||String(method||'Session')}
function accountSecurityTime(value){const seconds=Number(value||0);if(!seconds)return i18nText('account_security.unknown_time');return i18nDateTime(seconds*1000)}

let accountSecurityIdentityModulePromise=null;
function accountSecurityIdentityModule(){
  if(!accountSecurityIdentityModulePromise)accountSecurityIdentityModulePromise=import('/static/js/features/account/identity-platform-client.js?v=10.5.39');
  return accountSecurityIdentityModulePromise;
}
function accountSecurityTotpCodeDialog(title='Two-step verification'){
  return textDialog(title,{label:'Authenticator code',placeholder:'123456',confirmLabel:'Continue'});
}
async function accountSecurityFreshIdentityUser(snapshot,purpose){
  const client=await accountSecurityIdentityModule();
  try{
    const session=(Array.isArray(snapshot?.sessions)?snapshot.sessions:[]).find(item=>String(item.session_id||'')===String(snapshot?.current_session_id||''))||snapshot?.sessions?.[0]||{};
    const method=String(session.auth_method||'');
    let result;
    if(method==='email_password'){
      const identity=(snapshot.identities||[]).find(item=>item.provider==='email_password');
      const password=await textDialog('Verify your password',{label:'Password',confirmLabel:'Verify'});
      if(password===null)throw new Error('Security verification cancelled.');
      result=await client.freshReauthenticate('password',{email:identity?.email||'',password,getCode:accountSecurityTotpCodeDialog});
    }else if(method==='apple'){
      result=await client.freshReauthenticate('apple.com',{getCode:accountSecurityTotpCodeDialog});
    }else{
      result=await client.freshReauthenticate('google.com',{getCode:accountSecurityTotpCodeDialog});
    }
    const user=result?.user||result;
    await client.serverVerifyFresh(user,purpose);
    return {client,user};
  }catch(error){
    try{await client.signOutClient()}catch{}
    throw error;
  }
}
function accountSecurityProviderChoice(snapshot){
  const linked=new Set((snapshot?.identities||[]).map(item=>item.provider));
  const enabled=snapshot?.provider_availability||{};
  const available=[];
  if(enabled.google!==false&&!linked.has('google'))available.push(['google','Google']);
  if(enabled.apple===true&&!linked.has('apple'))available.push(['apple','Apple ID']);
  if(enabled.email_password!==false&&!linked.has('email_password'))available.push(['email_password','Email & password']);
  if(!available.length)return Promise.resolve(null);
  return new Promise(resolve=>{
    let settled=false;const finish=value=>{if(settled)return;settled=true;currentModalDismiss=null;resolve(value);closeModal(false)};
    showNestedModal('Add sign-in method',`<div class="account-security-provider-picker">${available.map(([id,label])=>`<button type="button" data-identity-provider="${id}">${escapeHtml(label)}</button>`).join('')}</div>`,`<button id="identityProviderCancel">${escapeHtml(i18nText('profile.cancel'))}</button>`,()=>finish(null));
    $('#identityProviderCancel').onclick=()=>finish(null);
    $$('[data-identity-provider]').forEach(button=>button.onclick=()=>finish(button.dataset.identityProvider));
  });
}
function accountSecurityTotpEnrollmentDialog(enrollment,title='Set up authenticator'){
  return new Promise(resolve=>{
    let settled=false;const finish=value=>{if(settled)return;settled=true;currentModalDismiss=null;resolve(value);closeModal(false)};
    showNestedModal(title,`<p class="small-muted">Add this key to your authenticator app, then enter its current code. Breadmaker never stores this key.</p><div class="field"><label>Authenticator key</label><code class="account-security-totp-secret">${escapeHtml(enrollment.secretKey)}</code></div><div class="field"><label>Authenticator code</label><input id="identitySecurityTotpCode" inputmode="numeric" autocomplete="one-time-code" maxlength="8"></div>`,`<button id="identitySecurityTotpCancel">${escapeHtml(i18nText('profile.cancel'))}</button><button id="identitySecurityTotpSave">${escapeHtml(i18nText('account_security.enable'))}</button>`,()=>finish(null));
    $('#identitySecurityTotpCancel').onclick=()=>finish(null);$('#identitySecurityTotpSave').onclick=()=>finish($('#identitySecurityTotpCode').value);
  });
}
async function accountSecurityVerifyLinkedEmail(client,user){
  if(user.emailVerified)return user;
  await client.sendVerification(user);
  const confirmed=await confirmDialog('Verify email','Identity Platform sent a verification link. Verify this address, then continue.',{confirmLabel:'I verified my email'});
  if(!confirmed)throw new Error('Email/password linking requires a verified email.');
  await client.reloadUser(user);
  if(!user.emailVerified)throw new Error('Email is not verified yet.');
  return user;
}
async function accountSecurityWithIdentityProof(snapshot,purpose,operation){
  const {client,user}=await accountSecurityFreshIdentityUser(snapshot,purpose);
  try{return await operation(client,user)}
  finally{try{await client.signOutClient()}catch{}}
}
async function accountSecurityIdentityLink(snapshot){
  return accountSecurityWithIdentityProof(snapshot,'link_identity',async(client,user)=>{
    const choice=await accountSecurityProviderChoice(snapshot);if(!choice)return false;
    if(choice==='email_password'){
      const email=await textDialog('Add email & password',{label:'Email',confirmLabel:'Continue'});if(email===null)return false;
      const password=await textDialog('Choose password',{label:'Password',confirmLabel:'Link'});if(password===null)return false;
      await client.linkPassword(user,email,password);
      await accountSecurityVerifyLinkedEmail(client,user);
      // Password credentials cannot be left without the mandatory TOTP factor.
      if(!(await client.enrolledTotp(user)).length){
        const enrollment=await client.beginTotpEnrollment(user),code=await accountSecurityTotpEnrollmentDialog(enrollment);
        if(code===null)throw new Error('Email/password linking requires two-step verification.');
        await enrollment.finish(code,'Breadmaker authenticator');
      }
    }else await client.linkProvider(user,choice);
    await client.syncSecuritySession(user,'link_identity');return true;
  });
}
async function accountSecurityIdentityUnlink(snapshot,providerId){
  return accountSecurityWithIdentityProof(snapshot,'unlink_identity',async(client,user)=>{
    await client.unlinkProvider(user,providerId);await client.syncSecuritySession(user,'unlink_identity');
  });
}
async function accountSecurityIdentityPassword(snapshot){
  return accountSecurityWithIdentityProof(snapshot,'change_password',async(client,user)=>{
    const password=await textDialog('Change password',{label:'New password',confirmLabel:'Change password'});if(password===null)return false;
    await client.changePassword(user,password);await client.syncSecuritySession(user,'change_password');return true;
  });
}
async function accountSecurityIdentityEnableTotp(snapshot){
  return accountSecurityWithIdentityProof(snapshot,'enable_totp',async(client,user)=>{
    const enrollment=await client.beginTotpEnrollment(user),code=await accountSecurityTotpEnrollmentDialog(enrollment);if(code===null)return false;
    await enrollment.finish(code,'Breadmaker authenticator');await client.syncSecuritySession(user,'enable_totp');return true;
  });
}
async function accountSecurityIdentityReplaceTotp(snapshot,oldId){
  return accountSecurityWithIdentityProof(snapshot,'replace_totp',async(client,user)=>{
    const enrollment=await client.beginTotpEnrollment(user),code=await accountSecurityTotpEnrollmentDialog(enrollment,'Replace authenticator');if(code===null)return false;
    // Enroll the replacement before removing the old factor so there is never a
    // password-only gap in the mandatory-MFA policy.
    await enrollment.finish(code,'Breadmaker authenticator');await client.unenrollTotp(user,oldId);await client.syncSecuritySession(user,'replace_totp');return true;
  });
}
async function accountSecurityIdentityDisableTotp(snapshot,id){
  return accountSecurityWithIdentityProof(snapshot,'disable_optional_totp',async(client,user)=>{
    await client.unenrollTotp(user,id);await client.syncSecuritySession(user,'disable_optional_totp');return true;
  });
}
async function accountSecurityIdentitySignOutEverywhere(snapshot){
  await accountSecurityWithIdentityProof(snapshot,'sign_out_all_sessions',async(client,user)=>client.signOutEverywhere(user));
  location.assign('/');
}

async function resumeAccountSecurityAction(){return}

function accountSecurityIdentityRows(snapshot){
  const identities=Array.isArray(snapshot?.identities)?snapshot.identities:[];
  if(!identities.length)return `<div class="account-security-empty">${escapeHtml(i18nText('account_security.no_methods'))}</div>`;
  return identities.map(identity=>{
    const label=accountSecurityProviderLabel(identity.provider),email=identity.email?escapeHtml(identity.email):escapeHtml(i18nText('account_security.no_email'));
    const primary=identity.primary?`<span class="account-security-badge primary">${escapeHtml(i18nText('account_security.primary'))}</span>`:'';
    const unlink=identity.can_unlink?`<button type="button" data-security-unlink="${escapeHtml(identity.subject)}" class="danger">${escapeHtml(i18nText('account_security.unlink'))}</button>`:'';
    const password=identity.provider==='email_password'?`<button type="button" data-security-password="${escapeHtml(identity.subject)}">${escapeHtml(i18nText('account_security.change_password'))}</button>`:'';
    return `<div class="account-security-row"><div class="account-security-provider" aria-hidden="true">${escapeHtml(accountSecurityProviderMark(identity.provider))}</div><div class="account-security-copy"><strong>${escapeHtml(label)}${primary}</strong><small>${email}</small></div><div class="account-security-actions">${password}${unlink}</div></div>`;
  }).join('')
}
function accountSecurityMfaRows(snapshot){
  const mfa=snapshot?.mfa||{},methods=Array.isArray(mfa.methods)?mfa.methods.filter(item=>item?.type==='totp'):[];
  if(!mfa.totp_enrolled)return `<div class="account-security-row"><div class="account-security-provider" aria-hidden="true">#</div><div class="account-security-copy"><strong>${escapeHtml(i18nText('account_security.totp'))}</strong><small>${escapeHtml(i18nText('account_security.totp_off_help'))}</small></div><div class="account-security-actions"><button id="securityEnableTotp" type="button">${escapeHtml(i18nText('account_security.enable'))}</button></div></div>`;
  const method=methods[0]||{},mandatory=mfa.totp_mandatory===true;
  return `<div class="account-security-row"><div class="account-security-provider" aria-hidden="true">#</div><div class="account-security-copy"><strong>${escapeHtml(i18nText('account_security.totp'))}<span class="account-security-badge primary">${escapeHtml(i18nText('account_security.enabled'))}</span></strong><small>${escapeHtml(mandatory?i18nText('account_security.totp_required_help'):i18nText('account_security.totp_optional_help'))}</small></div><div class="account-security-actions">${method.id?`<button type="button" data-security-replace-totp="${escapeHtml(method.id)}">${escapeHtml(i18nText('account_security.replace'))}</button>`:''}${method.id&&mfa.totp_can_disable?`<button type="button" data-security-disable-totp="${escapeHtml(method.id)}" class="danger">${escapeHtml(i18nText('account_security.disable'))}</button>`:''}</div></div>`
}
function accountSecuritySessionRows(snapshot){
  const sessions=Array.isArray(snapshot?.sessions)?snapshot.sessions:[],current=String(snapshot?.current_session_id||'');
  if(!sessions.length)return `<div class="account-security-empty">${escapeHtml(i18nText('account_security.no_sessions'))}</div>`;
  return sessions.map(item=>{const isCurrent=String(item.session_id||'')===current,canRevoke=snapshot?.selective_session_revocation_supported!==false&&!isCurrent;return `<div class="account-security-row"><div class="account-security-provider" aria-hidden="true">◉</div><div class="account-security-copy"><strong>${escapeHtml(isCurrent?i18nText('account_security.this_session'):i18nText('account_security.other_session'))}${isCurrent?`<span class="account-security-badge primary">${escapeHtml(i18nText('account_security.current'))}</span>`:''}</strong><small>${escapeHtml(accountSecurityAuthMethodLabel(item.auth_method))}${item.last_seen_at?` · ${escapeHtml(i18nText('account_security.last_active',{time:accountSecurityTime(item.last_seen_at)}))}`:''}</small></div><div class="account-security-actions">${canRevoke?`<button type="button" data-security-revoke-session="${escapeHtml(item.session_id)}" class="danger">${escapeHtml(i18nText('account_security.revoke'))}</button>`:''}</div></div>`}).join('')
}

async function openAccountSecurity(){
  if(!accountSecurityAvailable())return;
  closeUserProfilePopover();
  showModal(i18nText('account_security.title'),`<div class="account-security-loading">${escapeHtml(i18nText('account_security.loading'))}</div>`,`<button id="accountSecurityClose" type="button">${escapeHtml(i18nText('profile.cancel'))}</button>`);
  $('#accountSecurityClose').onclick=()=>closeModal();
  let snapshot;
  try{snapshot=await api('/api/auth/security')}catch(error){$('#modalBody').innerHTML=`<div class="account-security-warning">${escapeHtml(error.message||i18nText('account_security.unavailable'))}</div>`;return}
  const body=`<div class="account-security-shell"><p class="account-security-intro">${escapeHtml(i18nText('account_security.intro'))}</p>
  <section class="account-security-section"><div class="account-security-section-head"><div><h3>${escapeHtml(i18nText('account_security.signin_methods'))}</h3><p>${escapeHtml(i18nText('account_security.signin_methods_help'))}</p></div><button id="securityLinkMethod" class="quiet-button" type="button">${escapeHtml(i18nText('account_security.add_method'))}</button></div><div class="account-security-list">${accountSecurityIdentityRows(snapshot)}</div></section>
  <section class="account-security-section"><div class="account-security-section-head"><div><h3>${escapeHtml(i18nText('account_security.two_step'))}</h3><p>${escapeHtml(i18nText('account_security.two_step_help'))}</p></div></div><div class="account-security-list">${accountSecurityMfaRows(snapshot)}</div></section>
  <section class="account-security-section"><div class="account-security-section-head"><div><h3>${escapeHtml(i18nText('account_security.sessions'))}</h3><p>${escapeHtml(i18nText('account_security.sessions_help'))}</p></div></div><div class="account-security-list">${accountSecuritySessionRows(snapshot)}</div></section>
  <section class="account-security-section"><div class="account-security-section-head"><div><h3>${escapeHtml(i18nText('account_security.recovery'))}</h3><p>${escapeHtml(i18nText('account_security.recovery_help'))}</p></div></div><div class="account-security-row"><div class="account-security-provider" aria-hidden="true">?</div><div class="account-security-copy"><strong>${escapeHtml(i18nText('account_security.forgot_password'))}</strong><small>${escapeHtml('Use the sign-in screen to request an Identity Platform password reset.')}</small></div></div></section>
  </div>`;
  const actions=`<div class="account-security-modal-actions"><button id="securitySignOutEverywhere" type="button" class="account-security-danger">${escapeHtml(i18nText('account_security.sign_out_everywhere'))}</button><div class="right"><button id="accountSecurityDone" type="button">${escapeHtml(i18nText('account_security.done'))}</button></div></div>`;
  renderModal(i18nText('account_security.title'),body,actions);
  $('#accountSecurityDone').onclick=()=>closeModal();
  $('#securityLinkMethod').onclick=async()=>{try{await accountSecurityIdentityLink(snapshot);setTimeout(()=>openAccountSecurity(),80)}catch(error){toast(error.message||i18nText('account_security.action_failed'),6000)}};
  $('#securityEnableTotp')?.addEventListener('click',async()=>{const ok=await confirmDialog(i18nText('account_security.enable_totp_title'),i18nText('account_security.enable_totp_confirm'),{confirmLabel:i18nText('account_security.continue')});if(!ok)return;try{await accountSecurityIdentityEnableTotp(snapshot);setTimeout(()=>openAccountSecurity(),80)}catch(error){toast(error.message||i18nText('account_security.action_failed'),6000)}});
  $$('[data-security-unlink]').forEach(button=>button.onclick=async()=>{const ok=await confirmDialog(i18nText('account_security.unlink_title'),i18nText('account_security.unlink_confirm'),{confirmLabel:i18nText('account_security.unlink'),danger:true});if(ok)try{await accountSecurityIdentityUnlink(snapshot,button.dataset.securityUnlink);setTimeout(()=>openAccountSecurity(),80)}catch(error){toast(error.message||i18nText('account_security.action_failed'),6000)}});
  $$('[data-security-password]').forEach(button=>button.onclick=async()=>{try{await accountSecurityIdentityPassword(snapshot);setTimeout(()=>openAccountSecurity(),80)}catch(error){toast(error.message||i18nText('account_security.action_failed'),6000)}});
  $$('[data-security-disable-totp]').forEach(button=>button.onclick=async()=>{const ok=await confirmDialog(i18nText('account_security.disable_totp_title'),i18nText('account_security.disable_totp_confirm'),{confirmLabel:i18nText('account_security.disable'),danger:true});if(ok)try{await accountSecurityIdentityDisableTotp(snapshot,button.dataset.securityDisableTotp);setTimeout(()=>openAccountSecurity(),80)}catch(error){toast(error.message||i18nText('account_security.action_failed'),6000)}});
  $$('[data-security-replace-totp]').forEach(button=>button.onclick=async()=>{try{await accountSecurityIdentityReplaceTotp(snapshot,button.dataset.securityReplaceTotp);setTimeout(()=>openAccountSecurity(),80)}catch(error){toast(error.message||i18nText('account_security.action_failed'),6000)}});
  $('#securitySignOutEverywhere').onclick=async()=>{const ok=await confirmDialog(i18nText('account_security.sign_out_everywhere_title'),i18nText('account_security.sign_out_everywhere_confirm'),{confirmLabel:i18nText('account_security.sign_out_everywhere'),danger:true});if(ok)try{await accountSecurityIdentitySignOutEverywhere(snapshot)}catch(error){toast(error.message||i18nText('account_security.action_failed'),6000)}};
}
