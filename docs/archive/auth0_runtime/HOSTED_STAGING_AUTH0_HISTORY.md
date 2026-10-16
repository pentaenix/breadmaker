# Hosted staging — Checkpoint 12

`BREADMAKER_RUNTIME=hosted` is the real single-instance staging target. It is intentionally
separate from `hosted_dev` and from the normal zero-secret local `./run` path.

## Fixed boundary

- environment: `staging` only; production refuses to boot;
- authentication: Auth0 only;
- public origin: `https://app-staging.breadmaker.studio`;
- issuer: `https://auth-staging.breadmaker.studio/`;
- exactly one application worker;
- persistent `BREADMAKER_HOME` supplied by the deployment;
- Auth0 secret values resolved from GCP Secret Manager through ADC;
- no development unauthenticated bypass;
- Uvicorn access logging disabled so OIDC callback query strings are not logged;
- Uvicorn proxy-header rewriting disabled; Breadmaker accepts the Google-appended client
  address only when the direct peer is inside the load-balancer GFE source ranges.

The hosted image is `deploy/Dockerfile.hosted` and installs `requirements-hosted.txt`. The
normal local requirements intentionally do not include the Google Secret Manager client.

## Secrets

The container receives only `secretmanager:` resource references. It never receives secret
payloads through the image, Git, OpenTofu variables/state, instance metadata, or startup
script. The runtime service account receives per-secret `secretAccessor` permission and the
application resolves the selected versions at runtime.

## Deployment

The paired `breadmaker-cloud` repository owns the GCP staging service, persistence, load
balancer, non-secret configuration, and operator runbook. Do not expose this image directly
on the Internet and do not add replicas: the current session/OIDC/security authorities are
process-local and the runtime enforces the one-worker constraint.

Checkpoint 12 is complete only after the credential-free release verifier is green and the
separate live staging Auth0/provider smoke checklist has passed.
