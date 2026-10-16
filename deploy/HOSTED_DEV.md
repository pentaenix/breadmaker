# Hosted Identity Platform development

Local Breadmaker (`./run`) remains accountless and does not use cloud identity. For the hosted development composition, use `./run-hosted-identity-dev` with Application Default Credentials and the public, browser-restricted Firebase Web API key.

Required environment: `BREADMAKER_GCP_PROJECT_ID` (or `GOOGLE_CLOUD_PROJECT`) and `BREADMAKER_FIREBASE_WEB_API_KEY`. `BREADMAKER_FIREBASE_AUTH_DOMAIN` defaults to `<project>.firebaseapp.com`. Google is enabled by default for this launcher; Apple remains opt-in because its external prerequisites must exist first.

The launcher uses `hosted_dev`, `BREADMAKER_AUTH_MODE=identity_platform`, loopback HTTP, and a non-Secure development cookie. Staging/real hosted mode requires HTTPS, Secure cookies, public-host pinning, Firestore and GCS. Auth0 is retired from active runtime code in Milestone 7C.
