> **Historical document — do not deploy from this page.** This document describes a retired staging-era composition. The active single-production OpenTofu workflow and Identity Platform operator instructions live in the separately maintained private `breadmaker-cloud` repository. See [Security](../SECURITY.md) for the current hosted security boundary.

# Hosted staging — Identity Platform

The current hosted staging composition requires `BREADMAKER_AUTH_MODE=identity_platform`, HTTPS, Secure cookies, exact public-host pinning, one worker, Firestore and Cloud Storage. Identity Platform/Firebase Admin uses Application Default Credentials; browser configuration contains only the public restricted Firebase Web API key, project ID and auth domain.

No Auth0 runtime variables or provider secret payloads belong in the application environment. Google/Apple provider configuration is applied out-of-band by the Cloud provider operator; Apple remains disabled until its external prerequisites are satisfied. Production remains disabled pending later milestones.
