## 10.3.37 — Real Auth0 localhost security testing
`./run-hosted-dev` remains available for Schema 42/project-ID behavior without Auth0. `./run-hosted-auth-dev` is now the only auth UI path and always requires the development Auth0 regular-web application; the temporary UI preview has been removed.


The real page keeps credential fields inside Auth0, while the outer Breadmaker shell supplies the Breadmaker Editor-native Light/Dusk/Dark surfaces, actual Breadmaker logo, footer directly beneath the card, Auth0-native provider buttons, and seven-language selector. No staging/production login is enabled by either local workflow.

## 10.3.27 Checkpoint 10.6 corrective local testing


## 10.3.26 Checkpoint 10.6 project-ID sandbox

The hosted development launchers now default to fresh Schema 42 data homes so pre-alpha Schema 41 test data cannot masquerade as a current project. `./run-hosted-dev` uses a fixed developer identity and needs no Auth0/GCP deployment, which makes it the fastest way to exercise project creation, `/p/:projectId` routing, last-project resume, and the zero-project first-write flow locally. `./run-hosted-auth-dev` exercises the real Auth0 development tenant on localhost; it still requires the development issuer/client/client-secret because Breadmaker will not silently replace real authentication with a fake fallback. No GCP deployment is required for either launcher.

## 10.3.25 Checkpoint 10.5 authentication UI

The localhost hosted-auth sandbox uses the same Auth0 Universal Login flow, but the branded Auth0 page appears only after the development tenant is promoted with the versioned branding desired state. `./run` remains local/accountless. The Account & Security modal appears only after authenticated Auth0 boot.

## 10.3.24 Checkpoint 10 security-operation sandbox additions

The real localhost Auth0 sandbox remains `./run-hosted-auth-dev`; normal `./run` is unchanged. Basic login still needs only the existing development Auth0 issuer/client/client-secret settings. Checkpoint 10 Management API operations are lazy and additionally use `BREADMAKER_AUTH0_SECURITY_MGMT_CLIENT_ID` plus `BREADMAKER_AUTH0_SECURITY_MGMT_CLIENT_SECRET` (or its symbolic ref). Social TOTP enable/replace also needs `BREADMAKER_AUTH0_SECURITY_INTENT_SECRET`; configure the **same high-entropy value** as the Auth0 Action secret `BREADMAKER_SECURITY_INTENT_SECRET`. None of these values belongs in Git or OpenTofu. Missing Checkpoint 10 credentials do not prevent ordinary localhost login; only the corresponding security operation fails closed.

No security-management UI is shipped yet. Checkpoint 10.5 will call these endpoints after this backend contract has been verified.

## 10.3.23 account-linking sandbox additions

The existing localhost Auth0 sandbox can exercise Checkpoint 9 linking once a dedicated development Auth0 Management API machine-to-machine client is provided. Set `BREADMAKER_AUTH0_LINK_MGMT_CLIENT_ID` and `BREADMAKER_AUTH0_LINK_MGMT_CLIENT_SECRET` (or override `BREADMAKER_AUTH0_LINK_MGMT_CLIENT_SECRET_REF`). That client is server-only and must be granted only `update:users`. Add the exact development callback `http://127.0.0.1:8766/auth/link/callback` beside the normal `/auth/callback`. These values are not required merely to boot/login; missing linking credentials fail only when a link is attempted. No linking UI exists until Checkpoint 10.5.

## 10.3.22 real-auth localhost sandbox

Use `./run-hosted-auth-dev` to exercise the real Auth0 development tenant before staging. It is separate from both normal local Breadmaker and the fixed developer-identity `./run-hosted-dev` target.

Required shell values are `BREADMAKER_AUTH0_ISSUER`, `BREADMAKER_AUTH0_CLIENT_ID`, and `BREADMAKER_AUTH0_CLIENT_SECRET`. The launcher defaults to port 8766, origin `http://127.0.0.1:8766`, callback `http://127.0.0.1:8766/auth/callback`, and disposable repository-local `.dev/hosted-auth-data`. The Auth0 development regular-web application must use that exact callback and have the intended database, Google, and Apple connections plus the Checkpoint 8 TOTP Action enabled. Do not put the client secret in repository files.

## 10.3.21 Apple identity boundary

Hosted development now contains dormant Auth0, Google, and Apple identity mapping adapters, but no real provider login route is enabled. Apple mapping consumes only an already-verified Auth0 OIDC identity, requires the `apple|` subject strategy, accepts private-relay profile metadata, and preserves first-login-only metadata on later omission. Apple provider credentials/signing keys remain deployment/Auth0 concerns and never enter local or ordinary hosted runtime configuration.

## 10.3.20 Google identity boundary

Hosted development now contains dormant Auth0 and Google identity mapping adapters, but no real provider login route is enabled. Google mapping consumes only an already-verified Auth0 OIDC identity and requires the `google-oauth2|` subject strategy. Google Drive/API authorization remains separate. Development may use explicit loopback Auth0/OIDC fixtures; staging/production still require HTTPS exact origins when those environments are later wired.

## 10.3.18 corrective baseline

Hosted behavior and image/security boundaries are unchanged from 10.3.17; this patch only fixes marker-hover timing reliability.

## 10.3.17 hosted image UI boundary

Hosted runtime attestation narrows Manuscript and Canvas file pickers plus Manuscript image drag/drop to PNG/JPEG/WebP. This is presentation defense-in-depth only; hosted server-side structural validation remains authoritative. Hosted login remains disabled at Identity Checkpoint 3.

## 10.3.16 verification-tool correction

Hosted application behavior and the 10.3.15 hostile-image boundary are unchanged. Release verification now guarantees Python 3.10+ before resolving the pinned security dependency stack.

## 10.3.15 hosted image hardening

Hosted image upload accepts only structurally validated PNG/JPEG/WebP. Default upload limit: 12 MiB; hard configurable maximum: 25 MiB; maximum axis: 8192 px; maximum geometry: 20 MP. HEIF/HEIC/AVIF are disabled. The image does not intentionally contain ImageMagick/libheif/pillow-heif and the Docker build runs `scripts/assert_hosted_image_stack.py`. The hosted web process does not decode project images; PDF/DOCX exports use placeholders until a separately sandboxed image worker is deployed.

## 10.3.14 hosted security hardening

The hosted codebase retains the dormant provider-neutral OIDC engine while upgrading the framework/parser/crypto security baseline. Security-sensitive request routing uses the ASGI scope path, streamed body ceilings remain outside framework parsers, and hosted login is still intentionally disabled. No Auth0 adapter or login/callback route is enabled in 10.3.14. The local editor remains authentication-free.

# Breadmaker hosted development runtime — 10.0

`hosted_dev` remains the non-production server runtime from the same Breadmaker repository. It now supports three identity modes:

- `BREADMAKER_AUTH_MODE=developer` — the explicit 9.5 fixed developer identity; requires `BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED=1`.
- `BREADMAKER_AUTH_MODE=test` — CI/test-only server sessions. Requires `BREADMAKER_ENV=test` and a dedicated `BREADMAKER_TEST_AUTH_SECRET` of at least 32 characters.
- `BREADMAKER_AUTH_MODE=session` — fail-closed authenticated session runtime with no login adapter yet. This is the seam the upcoming Auth0/OIDC callback will use.


## Production hard stop

`developer` identity is never valid with `BREADMAKER_ENV=production`. Breadmaker fails before Workspace initialization even when `BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED=1`. A future public deployment must use authenticated account sessions; the opt-in flag is only a development/staging escape hatch.

## Hosted resource guardrails

Hosted mode applies owner/project storage quotas, upload and archive-expansion limits, request-body ceilings, and write/heavy-operation rate buckets. Defaults are intentionally conservative and may be overridden with `BREADMAKER_HOSTED_MAX_PROJECTS_PER_OWNER`, `BREADMAKER_HOSTED_MAX_OWNER_BYTES`, `BREADMAKER_HOSTED_MAX_PROJECT_BYTES`, `BREADMAKER_HOSTED_MAX_REQUEST_BODY_BYTES`, `BREADMAKER_HOSTED_MAX_PROJECT_PACKAGE_BYTES`, `BREADMAKER_HOSTED_MAX_PROJECT_PACKAGE_EXPANDED_BYTES`, `BREADMAKER_HOSTED_MAX_BREADREVIEW_BYTES`, `BREADMAKER_HOSTED_MAX_DOCUMENT_BYTES`, `BREADMAKER_HOSTED_MAX_DOCUMENT_EXPANDED_BYTES`, `BREADMAKER_HOSTED_MAX_IMAGE_BYTES`, `BREADMAKER_HOSTED_MAX_CUTS_BYTES`, `BREADMAKER_HOSTED_WRITES_PER_MINUTE`, and `BREADMAKER_HOSTED_HEAVY_PER_MINUTE`.

The container launches `src.server:app`; this outer ASGI boundary counts streamed/chunked request bytes before FastAPI multipart/JSON parsing, so omitting `Content-Length` does not bypass the application limit. These process-local rate counters are valid only under the currently advertised single-worker deployment constraint.

## Automated test bootstrap

In test mode automation may call `POST /__test__/login` with `Authorization: Bearer <BREADMAKER_TEST_AUTH_SECRET>` and one fixture name: `owner`, `editor`, `second_owner`, or `unowned_user`. The response sets the normal HttpOnly Breadmaker session cookie. The bootstrap credential is never accepted by normal application APIs.

The test route is not registered outside test auth mode. Test auth in `BREADMAKER_ENV=production` is a startup error.

## Session security

Sessions are opaque random tokens. Breadmaker stores only token digests server-side, expires sessions on idle and absolute deadlines, and uses HttpOnly SameSite=Lax cookies. Production environments require Secure cookies. 10.0 sessions are intentionally process-local and therefore remain single-worker/development-only until a shared session backend is introduced.

## Persistence

The authoritative data home remains the server-owned `BREADMAKER_HOME` volume. The original 10.0 hosted baseline used Schema 41; the current 10.3.26 / Checkpoint 10.6 build intentionally requires Schema 42 with stable project identity. Browser clients receive no filesystem paths.

## Local developer launch

`./run-hosted-dev` still launches the fixed developer identity for manual private testing.

## Container

Build with `docker build -f deploy/Dockerfile.hosted-dev -t breadmaker-hosted-dev .`. Choose the auth mode through environment variables at runtime. Do not expose `developer` or `test` mode publicly.

## 10.3.12 identity/session foundation

`BREADMAKER_AUTH_MODE=session` now owns a hosted-only `(issuer, subject)` account registry plus stronger opaque-session lifecycle primitives, but still has **no login adapter**. `BREADMAKER_AUTH_MODE=test` remains the only adapter that can create sessions in this checkpoint. `BREADMAKER_RUNTIME=local` does not instantiate the account registry and remains authentication-free.


### Hosted container image baseline

The hosted image pins the explicit `python:3.14.7-slim-trixie` base tag. Patch/base-image changes are release changes; do not use a floating `python:3.14-slim` tag. The image build also fails if ImageMagick, libheif, or pillow-heif becomes available in the hosted container.


Auth UI theme parity applies only to the real Auth0 Universal Login page template. The Breadmaker-owned shell uses the Editor `light → dusk → dark` token sets and the same half-filled-circle theme control, while the Auth0-owned credential widget remains on the exact Breadmaker Light theme because Breadmaker does not rewrite Auth0 widget DOM. There is no local auth preview or preview runtime; adopting per-user runtime themes inside the Auth0 widget would require a separately reviewed ACUL boundary.
