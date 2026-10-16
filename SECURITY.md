# Security

## Report a vulnerability privately

Please don't put working exploits, credentials, private manuscripts, or unpatched vulnerabilities into public GitHub issues.

Use **GitHub private vulnerability reporting** from the [pentaenix/breadmaker Security tab](https://github.com/pentaenix/breadmaker/security) if the maintainer has enabled it. If it is unavailable, request a private reporting channel without posting exploit details. The maintainer needs to enable private reporting or provide a security contact before inviting public vulnerability reports.

Include the affected version, the component involved, and a reproduction using **invented data**. Please do not attach real manuscripts, tokens, or account credentials. Breadmaker is pre-alpha; no guaranteed response or remediation time is published.

## What is and is not protected

**Local edition.** Breadmaker listens on `127.0.0.1` and does not require a Breadmaker account. Saved data is on the user's computer, so device access, operating-system accounts, disk encryption, and the user's backups are important. Optional remote backup providers transmit data only when configured and require network access.

**Hosted edition.** Access depends on authenticated sessions and server-side project authorization. Hosted content is stored using Firestore and Cloud Storage. The standard runtime can read manuscript content on the server; provider encryption at rest does not make it end-to-end encrypted. Experimental private-encryption code is not active in the standard runtime.

**Publication and deployment.** This source repository does not grant anyone access to an operated Breadmaker service. GCP permissions, secrets, encryption-key policy, and deployment configuration belong to the private infrastructure repository. Passing source tests is not proof of a secure production deployment.

**Dependencies.** Release verification includes dependency vulnerability checks. Findings still need review and fixes; a passing scan cannot guarantee the absence of vulnerabilities.

Developers should start with [Architecture](ARCHITECTURE.md) and [hosted trust boundaries](docs/HOSTED_DATA_TRUST_BOUNDARIES_M12.md). For the scope and limits of the experimental private recovery implementation, see [M12 source closure](docs/security/M12_SOURCE_CLOSURE.md).

## Historical security notes

The entries below describe past threats, experiments, and design decisions; they are not current Auth0 or staging setup instructions.

---

<details>
<summary>View historical security decisions (historical engineering record)</summary>

## 10.3.41 — Checkpoint 12 staging security boundary

The local editor remains a zero-secret product. The new `hosted` runtime is staging-only, single-process, Auth0-only, HTTPS-only, and refuses the development unauthenticated bypass. Staging client secrets and the security-intent HMAC are supplied only as Secret Manager resource references; payloads are fetched with workload identity through ADC and are never required by `./run`. Production hosted activation is still blocked in code.

## 10.3.37 — Checkpoint 11 adversarial gate
Checkpoint 11 treats the real hosted identity path as hostile input: replay, redirect confusion, cross-user project IDs, cross-provider identity substitution, linking/MFA partial failure, session fixation/revocation, enumeration, privilege scope and local/hosted isolation are explicit release gates. The final consultant pass additionally prevents locale-restart state amplification, requires same-origin Auth0 discovery endpoints, rejects duplicate/missing Host authority, preserves legitimate link/step-up transactions when a different session presents their state, and makes every OIDC callback failure taxonomy generic at the HTTP boundary. The pre-integration auth preview has been deleted.
Authenticated account-link transactions and step-up transactions/grants also enforce per-user ceilings, and failed wrapper binds retire their underlying OIDC state instead of consuming shared capacity until TTL expiry.
Hosted rate-limit identity bookkeeping is hard-bounded too; a distributed source-address spray cannot grow the process-local bucket map indefinitely.


## 10.3.27 — Checkpoint 10.6 corrective verification


## 10.3.26 — Checkpoint 10.6: stable project identity and hosted project URLs

Schema 42 intentionally breaks every earlier pre-alpha project. Each current project now stores one immutable 32-hex `project_uid` inside SQLite; it must exactly match the portable manifest `project_id` and workspace folder/registry id. There is no migration or compatibility path. Hosted project URLs use `/p/:projectId` (plus `/canvas`, `/maps`, `/tasks`, `/reviews` suffixes), direct links resume the requested authorized project, and the hosted root resumes the account's last-opened project. An account with zero projects receives an in-memory blank manuscript; the first meaningful write creates the project, assigns its ID, persists the draft, and replaces the URL. The existing All Projects modal remains the only project browser. Local Breadmaker keeps its open-and-write behavior and does not gain authentication.

## 10.3.25 — Checkpoint 10.5 UI boundary

The browser may render security state and start operations, but it never receives passwords, provider tokens, TOTP seeds/codes, recovery codes, Management API credentials, or server-only step-up grants. Auth0 owns the credential widget. Public login uses the same user-facing failure for an unknown identifier and a bad password; public signup keeps the existing-email response generic; remaining brute-force attempts are never disclosed. Desired Auth0 brute-force protection blocks **identifier + originating IP after 15 failures**, with user notification/recovery and without account-wide lockout, reducing both brute-force usefulness and attacker-driven global account denial-of-service. Password reset/unblock remains Auth0-owned and does not disable TOTP. Project IDs are deferred to 10.6.

## 10.3.24 — Checkpoint 10 account-security boundary

Every sensitive Checkpoint 10 mutation requires a fresh Auth0 reauthentication transaction and a single-use server-side grant bound to the exact `usr_`, `ses_`, operation, and target. Step-up grants are never serialized to the browser. Individual-session revocation is authorized only for a session already owned by that Breadmaker user. Identity unlink forbids the primary and last identity; Breadmaker removes the secondary local authorization first and restores it if Auth0 unlink fails, preventing a detached provider credential from surviving as a stale local authorization.

Auth0 remains credential/recovery authority. Breadmaker never stores passwords, TOTP seeds, OTP codes, or recovery codes. Optional social TOTP enrollment is authorized by an HMAC-SHA256 signed, nonce-bearing, five-minute intent consumed by the Auth0 Post-Login Action; database/password users cannot disable mandatory TOTP. Authenticated password change uses a short-lived Auth0 ticket. `Sign out everywhere` first requires successful Auth0 user-session deletion and only then revokes Breadmaker sessions. The Management API operator is separate from runtime, linking, and deployment credentials and requests only `read:authentication_methods`, `delete:authentication_methods`, `delete:sessions`, `create:user_tickets`, and `update:users`. Product UI remains **Checkpoint 10.5**.

## 10.3.23 — account-linking security boundary

Manual linking requires two independent proofs: a fresh authenticated primary Breadmaker session and a fresh secondary Auth0 OIDC login. Link state is single-use, expires after five minutes, and is bound to the primary session id. The secondary flow requests `prompt=login` and `max_age=0` and requires a fresh `auth_time`. Email equality is irrelevant and browser-supplied Auth0 identifiers are never accepted.

Only the dedicated server-side linking operator may obtain Auth0 Management API `update:users`; its credential is separate from the normal web-app client and from deployment credentials. Linking never merges two existing Breadmaker users or projects. A conflicting secondary mapping fails before the provider mutation.

## 10.3.22 — Auth0 database/TOTP security boundary

Password and MFA material never cross into Breadmaker. The database provider mapper accepts only verified Auth0 OIDC identities with `auth0|…` subjects and verified email; email remains metadata, not an account key. Auth0 Universal Login and the versioned Post-Login Action own password handling, TOTP enrollment/challenge, and recovery codes. Database users must use OTP TOTP; email/SMS are not fallback factors. Social users are challenged with OTP only after they have enrolled one, making the opt-in sticky without a second Breadmaker MFA registry.

Real identity can be exercised locally only through the development hosted-auth sandbox. The sandbox uses a separate workspace root, loopback callback, opaque HttpOnly Breadmaker session cookie, and environment-delivered Auth0 client secret. `BREADMAKER_RUNTIME=local` continues to instantiate `NullSessionService` and `NullAccountIdentityService` and has no network identity dependency.

## 10.3.21 — Apple/Auth0 identity privacy boundary

Checkpoint 6 adds no direct Apple OAuth client to Breadmaker. `auth/apple.py` consumes only identities already verified by the Auth0/generic-OIDC boundary, accepts only `apple|…` subjects from the configured Auth0 issuer, and persists identity by issuer+subject. Matching email—including Apple private-relay email—never merges users.

Hide My Email is not an error state and Breadmaker must not require a personal address. Legacy `@privaterelay.appleid.com` and new `@private.icloud.com` Sign in with Apple relay values are accepted. Missing profile values on later authorization cannot erase values captured at first authorization. Apple signing-key material and provider tokens remain outside the normal Breadmaker runtime. Hosted login remains disabled.

## 10.3.20 — Google/Auth0 identity mapping without login enablement

Google identity is accepted only after the generic OIDC layer has verified an Auth0 ID token. The provider mapper requires Auth0's verified `google-oauth2|…` subject form, keeps the complete Auth0 `(issuer, subject)` as the identity key, and treats email/name only as mutable profile metadata. Matching email is never merge authority. The mapper cannot receive or persist Google access tokens, refresh tokens, Drive scopes, or Google OAuth client secrets. Hosted login remains disabled and local Breadmaker remains authentication-free.

## 10.3.19 — Auth0 boundary without login enablement

The Auth0 adapter is server-side only and receives a client-secret value only through an injected secret loader; non-secret boundary metadata contains a secret reference, never the secret payload. Staging/production origins and issuers must be HTTPS and non-loopback, callback routing is exact, browser token exposure remains forbidden, and no Auth0 Management API credential is introduced. Hosted login remains disabled and local Breadmaker remains authentication-free.

## 10.3.18 — security baseline unchanged

The marker-hover reliability correction does not relax dependency, request, image-ingress, session, or identity security boundaries. Identity remains at Checkpoint 3 and hosted login remains disabled.

## 10.3.17 — security contract preservation

The corrective patch does not relax the hosted image or dependency security boundaries. Local image UX may expose the historical browser-supported formats, but hosted runtime UI is narrowed to PNG/JPEG/WebP and the server remains authoritative. `cryptography` stays pinned to 50.0.1, Starlette stays pinned to 1.7.0, and hosted login remains disabled.

## 10.3.16 — security-gate interpreter integrity

The dependency security gate must execute under Python 3.10+ because the pinned patched framework stack requires it. Bundle verification propagates one selected compatible interpreter to both authoritative repositories, and stale older verifier virtual environments are rebuilt automatically.

# Breadmaker Editor security boundary

## 10.3.15 hostile-image boundary

Untrusted hosted images are data, never executable parser input in the main Breadmaker process. The hosted ingress accepts only PNG/JPEG/WebP after magic, structure, dimension, and pixel-count validation performed without Pillow/ImageMagick/libheif. Filename extensions and Content-Type are not trusted. The default encoded limit is 12 MiB, the absolute configurable ceiling is 25 MiB, each axis is capped at 8192 pixels, and decoded geometry is capped at 20 megapixels. HEIF/HEIC/AVIF are disabled.

The hosted container must not contain ImageMagick, libheif, or pillow-heif. Pillow remains required by local/export dependencies and is pinned/audited, but hosted project images are not decoded in-process: image-bearing PDF/DOCX exports render placeholders until an isolated throwaway worker exists. Re-enabling hosted decode requires an ephemeral least-privilege worker with no persistent-data mount, no application secrets, no network egress, hard CPU/memory/wall-time and I/O limits, sanitized-output-only publication, and alerts on repeated worker crashes. Local Breadmaker remains authentication-free and retains local export behavior.

## 10.3.14 security-hardening baseline

Breadmaker treats the local editor and the future hosted service as different threat surfaces. Local Breadmaker remains accountless and localhost-only. Hosted Breadmaker is internet-facing and therefore fails closed around request identity, request size, content parsing, dependency versions, and session/authorization boundaries.

### Canonical request path

Security decisions use the canonical ASGI `scope["path"]` that the router dispatches. They must not use a URL path reconstructed from `Host`, forwarding, or other request headers. This remains a defense-in-depth rule even while Starlette is pinned above the malformed-Host fixes.

### Request-size boundary

Hosted request limits are enforced twice: declared `Content-Length` is rejected early by the FastAPI request guard, and the outer `src/server.py` ASGI boundary counts streamed/chunked request bytes so omitting `Content-Length` cannot bypass the limit. Parser-level field/part limits remain additive protections rather than the only body-size defense.

### Known-vulnerability release gate

Production and security-critical transitive packages are explicitly pinned in `requirements.txt`. `verify-release` must use the declared versions and runs `pip-audit` against the production requirements before pytest. A known dependency advisory or an unavailable/failed audit blocks the release gate; vulnerabilities are not silently allowlisted.

Security-relevant pins include FastAPI/Starlette/Uvicorn, Pydantic/AnyIO, python-multipart, ReportLab, cryptography, Authlib, and JOSERFC. Dependency upgrades are ordinary release changes and must pass the full verifier.

### Native/parser boundaries

Breadmaker does not expose `eval`, `exec`, Python pickle deserialization, `os.system`, or shell-mode subprocess execution in runtime source. External/native parsers remain attack surfaces and must stay patched. Legacy `.doc` conversion is disabled in hosted mode; local-only LibreOffice conversion uses an argv array, isolated temporary profile, and timeout. ZIP/package imports retain bounded entry/expanded-size and traversal validation. SQLite extension loading is not exposed.

### Repository authority

The authoritative security surfaces are the `breadmaker` and `breadmaker-cloud` repositories. OpenTofu/bundle-root tooling is not part of the deployed Editor runtime. Files above the two repositories in combined archives are disposable release orchestration and must not own product secrets, runtime policy, or deployed behavior.


### Hosted container image baseline

The hosted image pins the explicit `python:3.14.7-slim-trixie` base tag. Patch/base-image changes are release changes; do not use a floating `python:3.14-slim` tag. The image build also fails if ImageMagick, libheif, or pillow-heif becomes available in the hosted container.


### Archive image ingress

The hosted image ceiling applies before image bytes are materialized from Cuts, portable BreadReview, and native project-package imports. Hosted project packages are rejected before activation if any `assets/` member exceeds the configured image ceiling or is not a structurally valid PNG/JPEG/WebP. Portable BreadReview decoding receives the same per-asset ceiling. These archive paths do not create a decoder exception.


Auth UI theme parity applies only to the real Auth0 Universal Login page template. The Breadmaker-owned shell uses the Editor `light → dusk → dark` token sets and the same half-filled-circle theme control, while the Auth0-owned credential widget remains on the exact Breadmaker Light theme because Breadmaker does not rewrite Auth0 widget DOM. There is no local auth preview or preview runtime; adopting per-user runtime themes inside the Auth0 widget would require a separately reviewed ACUL boundary.

Checkpoint 11 hardens interrupted TOTP replacement with a transient Auth0 `app_metadata` marker. It is not an MFA entitlement or source of truth: enrolled Auth0 factors remain authoritative. The marker only forces reenrollment after the old factor has been deliberately removed and replacement is interrupted; it is cleared after successful replacement or before an explicit optional disable.


Checkpoint 11 second-pass hardening adds browser-bound initial login state, bounded/rate-limited public authorization starts, Auth0-user-bound signed TOTP enrollment intents, required Origin on real-auth mutations, and suppression of OAuth callback query strings from Uvicorn access logs.

Checkpoint 11 round-two hardening additionally pins the real-auth Host header to the configured public origin, rejects repeatedly percent-decoded redirect confusion, bounds account-link and step-up state stores, keeps callback failures generic, and explicitly forbids multi-worker hosted authentication until the in-memory session/OIDC/security stores are replaced by shared atomic state.

- The opaque Breadmaker session registry is bounded globally and per user; per-user pressure retires only that user's oldest sessions and global pressure fails issuance instead of evicting another user.

</details>
