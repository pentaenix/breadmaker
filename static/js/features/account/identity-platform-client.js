const FIREBASE_VERSION='13.0.0';
const APP_URL=`https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-app.js`;
const AUTH_URL=`https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-auth.js`;
let cachedClient=null;

async function loadModules(){
  const [appMod,authMod]=await Promise.all([import(APP_URL),import(AUTH_URL)]);
  return {appMod,authMod};
}
async function loadConfig(){
  const response=await fetch('/api/auth/client-config',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
  if(!response.ok)throw new Error(`Identity configuration unavailable (${response.status})`);
  return response.json();
}
export async function identityClient(){
  if(cachedClient)return cachedClient;
  cachedClient=(async()=>{
    const [{appMod,authMod},config]=await Promise.all([loadModules(),loadConfig()]);
    const app=appMod.getApps().find(item=>item.name==='breadmaker-identity-platform')||appMod.initializeApp(config.firebase,'breadmaker-identity-platform');
    const auth=authMod.getAuth(app);
    await authMod.setPersistence(auth,authMod.inMemoryPersistence);
    await auth.authStateReady();
    return {app,auth,authMod,config};
  })();
  return cachedClient;
}
export function providerFor(authMod,name){
  if(name==='google'){
    const provider=new authMod.GoogleAuthProvider();
    // Firebase defaults to profile only; admission requires a verified email.
    provider.addScope('email');
    provider.setCustomParameters({prompt:'select_account'});
    return provider;
  }
  if(name==='apple'){
    const provider=new authMod.OAuthProvider('apple.com');
    provider.addScope('email');provider.addScope('name');
    return provider;
  }
  throw new Error('Unsupported sign-in provider');
}
export async function resolveTotpChallenge(error,getCode){
  const {auth,authMod}=await identityClient();
  if(error?.code!=='auth/multi-factor-auth-required')throw error;
  const resolver=authMod.getMultiFactorResolver(auth,error);
  const hint=(resolver.hints||[]).find(item=>item.factorId===authMod.TotpMultiFactorGenerator.FACTOR_ID);
  if(!hint)throw new Error('This account requires an unsupported second factor.');
  const code=String(await getCode({displayName:hint.displayName||'Authenticator app'})||'').trim();
  if(!/^\d{6,8}$/.test(code))throw new Error('Enter the current authenticator code.');
  const assertion=authMod.TotpMultiFactorGenerator.assertionForSignIn(hint.uid,code);
  return resolver.resolveSignIn(assertion);
}
export async function signInProvider(name,getCode){
  const {auth,authMod,config}=await identityClient();
  if(!config.providers?.[name])throw new Error(`${name==='apple'?'Apple':'Google'} sign-in is not enabled yet.`);
  try{return await authMod.signInWithPopup(auth,providerFor(authMod,name));}
  catch(error){return resolveTotpChallenge(error,getCode)}
}
export async function signInEmail(email,password,getCode){
  const {auth,authMod}=await identityClient();
  try{return await authMod.signInWithEmailAndPassword(auth,String(email||'').trim(),String(password||''));}
  catch(error){return resolveTotpChallenge(error,getCode)}
}
export async function createEmailAccount(email,password){
  const {auth,authMod}=await identityClient();
  return authMod.createUserWithEmailAndPassword(auth,String(email||'').trim(),String(password||''));
}
export async function sendVerification(user){
  const {authMod}=await identityClient();
  if(user.email){
    await authMod.sendEmailVerification(user);
    return;
  }
  throw new Error('An email/password account requires a primary email address.');
}
export async function reloadUser(user){
  const {authMod}=await identityClient();
  await authMod.reload(user);return user;
}
export async function sendPasswordReset(email){
  const {auth,authMod}=await identityClient();
  return authMod.sendPasswordResetEmail(auth,String(email||'').trim());
}
export async function enrolledTotp(user){
  const {authMod}=await identityClient();
  return (authMod.multiFactor(user).enrolledFactors||[]).filter(item=>item.factorId===authMod.TotpMultiFactorGenerator.FACTOR_ID);
}
export async function beginTotpEnrollment(user){
  const {authMod}=await identityClient();
  const session=await authMod.multiFactor(user).getSession();
  const secret=await authMod.TotpMultiFactorGenerator.generateSecret(session);
  const email=String(user.email||'writer');
  return {
    secretKey:secret.secretKey,
    qrUri:secret.generateQrCodeUrl(email,'Breadmaker'),
    async finish(code,displayName='Authenticator app'){
      const value=String(code||'').trim();
      if(!/^\d{6,8}$/.test(value))throw new Error('Enter the current authenticator code.');
      const assertion=authMod.TotpMultiFactorGenerator.assertionForEnrollment(secret,value);
      await authMod.multiFactor(user).enroll(assertion,displayName);
      await authMod.reload(user);
      return user;
    }
  };
}
export async function getFreshIdToken(user){
  if(!user)throw new Error('Authentication proof is missing.');
  return user.getIdToken(true);
}
async function csrf(){
  const response=await fetch('/api/auth/csrf',{credentials:'same-origin',cache:'no-store'});
  if(!response.ok)throw new Error('Could not prepare secure authentication request.');
  return (await response.json()).csrf_token;
}
export async function exchangeServerSession(user,{invitationToken=''}={}){
  const [idToken,csrfToken]=await Promise.all([getFreshIdToken(user),csrf()]);
  const response=await fetch('/api/auth/session/exchange',{
    method:'POST',credentials:'same-origin',cache:'no-store',
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},
    body:JSON.stringify({idToken,invitationToken:String(invitationToken||'').trim()||null}),
  });
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(body?.detail||'Breadmaker could not create the server session.');
  return body;
}
export async function signOutClient(){
  const {auth,authMod}=await identityClient();
  await authMod.signOut(auth);
}
export async function freshReauthenticate(providerId,{email,password,getCode}={}){
  const {auth,authMod,config}=await identityClient();
  let credential;
  if(providerId==='password'){
    if(!auth.currentUser)credential=await signInEmail(email,password,getCode);
    else{
      const proof=authMod.EmailAuthProvider.credential(String(email||auth.currentUser.email||'').trim(),String(password||''));
      try{return await authMod.reauthenticateWithCredential(auth.currentUser,proof)}
      catch(error){return resolveTotpChallenge(error,getCode)}
    }
    return credential;
  }
  const providerName=providerId==='apple.com'?'apple':'google';
  if(!config.providers?.[providerName])throw new Error(`${providerName==='apple'?'Apple':'Google'} verification is not enabled yet.`);
  const provider=providerFor(authMod,providerName);
  if(!auth.currentUser)return signInProvider(providerName,getCode);
  try{return await authMod.reauthenticateWithPopup(auth.currentUser,provider)}
  catch(error){return resolveTotpChallenge(error,getCode)}
}
export async function serverVerifyFresh(user,purpose){
  const [idToken,csrfToken]=await Promise.all([getFreshIdToken(user),csrf()]);
  const response=await fetch('/api/auth/security/proof',{
    method:'POST',credentials:'same-origin',cache:'no-store',
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},
    body:JSON.stringify({idToken,purpose}),
  });
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(body?.detail||'Fresh security verification failed.');
  return {idToken,body};
}
export async function syncSecuritySession(user,purpose){
  const [idToken,csrfToken]=await Promise.all([getFreshIdToken(user),csrf()]);
  const response=await fetch('/api/auth/security/sync',{
    method:'POST',credentials:'same-origin',cache:'no-store',
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},
    body:JSON.stringify({idToken,purpose}),
  });
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(body?.detail||'Could not synchronize account security.');
  return body;
}
export async function linkProvider(user,providerName){
  const {authMod,config}=await identityClient();
  if(providerName==='email_password')throw new Error('Email/password linking requires an email and password.');
  if(!config.providers?.[providerName])throw new Error(`${providerName==='apple'?'Apple':'Google'} linking is not enabled yet.`);
  return authMod.linkWithPopup(user,providerFor(authMod,providerName));
}
export async function linkPassword(user,email,password){
  // With Identity Platform improved email-enumeration protection enabled, Firebase's
  // Web SDK intentionally disables linkWithCredential for EmailAuthCredential. The
  // supported replacement is accounts:signUp with the already-authenticated user's
  // ID token. Returned provider tokens stay in this transient call and are discarded.
  const {authMod,config}=await identityClient();
  const idToken=await user.getIdToken(true);
  const response=await fetch(`https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=${encodeURIComponent(config.firebase.apiKey)}`,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({idToken,email:String(email||'').trim(),password:String(password||''),returnSecureToken:true}),
  });
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error('Could not add email/password to this account.');
  // Do not expose or persist the REST response's idToken/refreshToken. Reload the SDK
  // user record from provider authority and force the next proof to mint a fresh token.
  await authMod.reload(user);await user.getIdToken(true);return user;
}
export async function unlinkProvider(user,providerId){
  const {authMod}=await identityClient();
  return authMod.unlink(user,providerId);
}
export async function changePassword(user,password){
  const {authMod}=await identityClient();
  const value=String(password||'');if(value.length<8)throw new Error('Use at least 8 characters.');
  return authMod.updatePassword(user,value);
}
export async function unenrollTotp(user,enrollmentId){
  const {authMod}=await identityClient();
  return authMod.multiFactor(user).unenroll(String(enrollmentId||''));
}
export async function currentFirebaseUser(){const {auth}=await identityClient();return auth.currentUser}
export async function signOutEverywhere(user){
  const [idToken,csrfToken]=await Promise.all([getFreshIdToken(user),csrf()]);
  const response=await fetch('/api/auth/security/sign-out-everywhere',{
    method:'POST',credentials:'same-origin',cache:'no-store',
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},
    body:JSON.stringify({idToken,purpose:'sign_out_all_sessions'}),
  });
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(body?.detail||'Could not sign out everywhere.');
  return body;
}
