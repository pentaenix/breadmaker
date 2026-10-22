import {
  identityClient,signInProvider,signInEmail,createEmailAccount,sendVerification,reloadUser,
  sendPasswordReset,enrolledTotp,beginTotpEnrollment,exchangeServerSession,signOutClient
} from './identity-platform-client.js?v=10.5.49';

const $=selector=>document.querySelector(selector);
const message=$('#identityMessage'),totp=$('#identityTotp'),social=$('#identitySocial');
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
function busy(on){document.querySelectorAll('button,input').forEach(el=>{el.disabled=!!on})}
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
  else if(!user.emailVerified){
    await sendVerification(user);
    // Applying the email action can invalidate the original Firebase session.
    // Start a fresh Google sign-in rather than retaining/retrying that proof.
    throw new Error('Check your inbox and verify your email, then sign in with Google again.');
  }
  await exchangeServerSession(user);
  await signOutClient();location.assign(safeReturn());
}
async function withBusy(fn){hideMessage();busy(true);try{await fn()}catch(error){try{await signOutClient()}catch{};const text=error?.message||'Sign-in failed.';showMessage(text.includes('PRIVATE_ALPHA_')?'This account is not approved for Breadmaker access.':text,'error')}finally{busy(false)}}
async function renderSocial(){
  const {config}=await identityClient();const buttons=[];
  if(config.providers.google)buttons.push('<button type="button" data-provider="google" class="provider"><span>G</span>Continue with Google</button>');
  if(config.providers.apple)buttons.push('<button type="button" data-provider="apple" class="provider"><span></span>Continue with Apple</button>');
  social.innerHTML=buttons.join('');
  social.querySelectorAll('[data-provider]').forEach(button=>button.onclick=()=>withBusy(async()=>{const result=await signInProvider(button.dataset.provider,askTotp);await finish(result.user)}));
}
$('#identityEmailForm').addEventListener('submit',event=>{event.preventDefault();withBusy(async()=>{const result=await signInEmail($('#identityEmail').value,$('#identityPassword').value,askTotp);await finish(result.user,{passwordAccount:true})})});
$('#identityEmailSignUp').onclick=()=>withBusy(async()=>{let result;try{result=await createEmailAccount($('#identityEmail').value,$('#identityPassword').value)}catch{throw new Error('Could not create an account with those credentials.')}await finish(result.user,{passwordAccount:true})});
$('#identityForgotPassword').onclick=()=>withBusy(async()=>{const email=$('#identityEmail').value;if(!email)throw new Error('Enter your email first.');await sendPasswordReset(email);showMessage('If this address has a password account, Identity Platform has sent the reset email.')});
renderSocial().catch(error=>showMessage(error.message,'error'));
