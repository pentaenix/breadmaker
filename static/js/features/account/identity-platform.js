import {
  identityClient,signInProvider,signInEmail,createEmailAccount,sendVerification,reloadUser,
  sendPasswordReset,enrolledTotp,beginTotpEnrollment,exchangeServerSession,signOutClient
} from './identity-platform-client.js?v=10.5.68';

const $=selector=>document.querySelector(selector);
const message=$('#identityMessage'),totp=$('#identityTotp'),social=$('#identitySocial');
let signup=false,localeSequence=0;
const languages=[['en','English'],['es','Español'],['fr','Français'],['it','Italiano'],['ja','日本語'],['pt-BR','Português'],['zh-CN','简体中文']];
const themes=['light','dusk','dark'];
function authText(key,fallback){return i18nReady?i18nText(key):fallback}
function label(selector,key,fallback){const el=$(selector);el.dataset.authKey=key;el.textContent=authText(key,fallback)}
function refreshLabels(){
  if(i18nReady)document.querySelectorAll('[data-auth-key]').forEach(el=>{el.textContent=i18nText(el.dataset.authKey)});
  const theme=document.documentElement.dataset.theme||'light';
  $('#identityTheme').title=`${authText('global.theme_title','Theme')}: ${authText(`global.theme_${theme}`,theme)}`;
  $('#identityTheme').setAttribute('aria-label',$('#identityTheme').title);
  const language=$('#identityLanguage summary');
  language.title=authText('settings.language.modal_title','Language');language.setAttribute('aria-label',language.title);
  document.querySelectorAll('[data-locale]').forEach(el=>el.setAttribute('aria-current',String(el.dataset.locale===document.documentElement.lang)));
}
function setMode(value){
  signup=!!value;hideMessage();
  label('#identityHeading',signup?'auth.signup.title':'auth.login.title',signup?'Create your account':'Login');
  label('#identityEmailSignIn',signup?'auth.signup.submit':'auth.login.submit',signup?'Create account':'Sign in');
  label('#identitySocialDivider span',signup?'auth.signup.or_social':'auth.login.or_social',signup?'Or sign up with':'Or sign in with');
  label('#identityModePrompt',signup?'auth.signup.have_account':'auth.login.no_account',signup?'Already have an account?':'Don’t have an account?');
  for(const selector of ['#identityHeaderMode','#identityEmailSignUp'])label(selector,signup?'auth.login.action':'auth.signup.action',signup?'Log in':'Sign up');
  $('#identityPassword').autocomplete=signup?'new-password':'current-password';
  $('#identityForgotPassword').hidden=signup;
}
async function chooseLanguage(code){
  const sequence=++localeSequence,payload=await fetchLocalePayload(code);
  if(sequence!==localeSequence)return;
  installLocalePayload(payload,{persist:true});refreshLabels();$('#identityLanguage').open=false;
}
function setupPresentation(){
  let theme='light',language='en';
  try{theme=localStorage.getItem('breadmaker-theme')||theme;language=localStorage.getItem('breadmaker-language')||language}catch{}
  document.documentElement.dataset.theme=themes.includes(theme)?theme:'light';
  $('#identityTheme').onclick=()=>{
    const next=themes[(themes.indexOf(document.documentElement.dataset.theme)+1)%themes.length];
    document.documentElement.dataset.theme=next;try{localStorage.setItem('breadmaker-theme',next)}catch{};refreshLabels();
  };
  for(const [code,name] of languages){
    const button=document.createElement('button');button.type='button';button.textContent=name;button.dataset.locale=code;
    button.onclick=()=>chooseLanguage(code).catch(error=>showMessage(error.message,'error'));$('#identityLanguages').append(button);
  }
  $('#identityHeaderMode').onclick=$('#identityEmailSignUp').onclick=()=>setMode(!signup);
  $('#identityShowPassword').onclick=()=>{
    const shown=$('#identityPassword').type==='password';$('#identityPassword').type=shown?'text':'password';
    $('#identityShowPassword').setAttribute('aria-pressed',String(shown));
    label('#identityShowPassword',shown?'auth.login.hide':'auth.login.show',shown?'Hide':'Show');
  };
  setMode(false);refreshLabels();
  chooseLanguage(languages.some(([code])=>code===language)?language:'en').catch(()=>{/* Keep usable English labels when offline. */});
}
function showMessage(text,kind='info'){message.hidden=false;message.className=`identity-message ${kind}`;message.textContent=String(text||'')}
function hideMessage(){message.hidden=true;message.textContent=''}
function safeReturn(){
  const value=new URL(location.href).searchParams.get('return_to')||'/';
  // Backslash is a slash in special-URL parsing; '/\\host' can become an
  // external network-path reference despite passing a simple startsWith check.
  if(!value.startsWith('/')||value.startsWith('//')||/[\\\u0000-\u001f\u007f]/.test(value))return '/';
  try{
    const target=new URL(value,location.origin);
    if(target.origin!==location.origin||!target.pathname.startsWith('/')||target.pathname.startsWith('//'))return '/';
    return target.pathname+target.search+target.hash;
  }catch{return '/'}
}
function busy(on){document.querySelectorAll('button,input').forEach(el=>{el.disabled=!!on||el.dataset.providerUnavailable==='true'})}
async function askTotp({displayName}){
  return new Promise(resolve=>{
    totp.hidden=false;totp.innerHTML='<h2>Two-step verification</h2><p>Enter the current code from <span id="identityTotpDisplayName"></span>.</p><div class="identity-code-row"><input id="identityTotpCode" inputmode="numeric" autocomplete="one-time-code" maxlength="8" autofocus><button id="identityTotpContinue" type="button">Continue</button></div>';
    $('#identityTotpDisplayName').textContent=String(displayName||'your authenticator app');
    $('#identityTotpContinue').onclick=()=>{const value=$('#identityTotpCode').value;totp.hidden=true;resolve(value)};
  });
}
async function requireVerifiedEmail(user){
  if(user.emailVerified)return user;
  await sendVerification(user);
  showMessage('Check your inbox and verify your email. Keep this page open, then choose “I verified my email”.');
  await new Promise((resolve,reject)=>{
    totp.hidden=false;totp.innerHTML='<button id="identityVerifiedContinue" type="button">I verified my email</button>';
    $('#identityVerifiedContinue').onclick=async()=>{try{await reloadUser(user);if(!user.emailVerified)throw new Error('Email is not verified yet.');totp.hidden=true;resolve()}catch(error){showMessage(error.message,'error')}};
  });
  return user;
}
async function requirePasswordTotp(user){
  if((await enrolledTotp(user)).length)return user;
  const enrollment=await beginTotpEnrollment(user);
  const code=await new Promise(resolve=>{
    totp.hidden=false;totp.innerHTML='<h2>Set up an authenticator</h2><p>Email/password accounts require two-step verification.</p><label>Authenticator key<code id="identityEnrollmentSecret" class="identity-secret"></code></label><p class="identity-help">Add this key to your authenticator app, then enter its current code. The key exists only in this browser flow and is never sent to Breadmaker.</p><div class="identity-code-row"><input id="identityEnrollCode" inputmode="numeric" autocomplete="one-time-code" maxlength="8"><button id="identityEnrollContinue" type="button">Enable two-step verification</button></div>';
    $('#identityEnrollmentSecret').textContent=String(enrollment.secretKey||'');
    $('#identityEnrollContinue').onclick=()=>resolve($('#identityEnrollCode').value);
  });
  await enrollment.finish(code,'Breadmaker authenticator');totp.hidden=true;return user;
}
async function finish(user,{passwordAccount=false}={}){
  if(passwordAccount){await requireVerifiedEmail(user);await requirePasswordTotp(user)}
  // Social admission is verified server-side. Never send a second email merely
  // because Firebase omitted a Google user's primary email.
  await exchangeServerSession(user);
  await signOutClient();location.assign(safeReturn());
}
async function withBusy(fn){hideMessage();busy(true);try{await fn()}catch(error){try{await signOutClient()}catch{};const text=error?.message||'Sign-in failed.';showMessage(text.includes('PRIVATE_ALPHA_')?'This account is not approved for Breadmaker access.':text,'error')}finally{busy(false)}}
async function renderSocial(){
  const {config}=await identityClient();const buttons=[];
  // Official artwork served locally: https://developers.google.com/identity/branding-guidelines
  if(config.providers.google)buttons.push('<button type="button" data-provider="google" class="provider"><img class="identity-provider-icon" src="/static/google_signin_logo.png" width="20" height="20" alt="" aria-hidden="true"><span>Continue with Google</span></button>');
  buttons.push(`<button type="button" data-provider="apple" class="provider"${config.providers.apple?'':' disabled aria-disabled="true" data-provider-unavailable="true"'}><span class="identity-provider-icon identity-apple-icon" aria-hidden="true"></span><span>Continue with Apple</span></button>`);
  social.innerHTML=buttons.join('');
  social.querySelectorAll('[data-provider]:not(:disabled)').forEach(button=>button.onclick=()=>withBusy(async()=>{const result=await signInProvider(button.dataset.provider,askTotp);await finish(result.user)}));
}
$('#identityEmailForm').addEventListener('submit',event=>{event.preventDefault();withBusy(async()=>{
  let result;
  if(signup){try{result=await createEmailAccount($('#identityEmail').value,$('#identityPassword').value)}catch{throw new Error('Could not create an account with those credentials.')}}
  else result=await signInEmail($('#identityEmail').value,$('#identityPassword').value,askTotp);
  await finish(result.user,{passwordAccount:true});
})});
$('#identityForgotPassword').onclick=()=>withBusy(async()=>{const email=$('#identityEmail').value;if(!email)throw new Error('Enter your email first.');await sendPasswordReset(email);showMessage('If this address has a password account, Identity Platform has sent the reset email.')});
setupPresentation();
renderSocial().catch(error=>showMessage(error.message,'error'));
