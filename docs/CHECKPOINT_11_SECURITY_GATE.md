# Identity Checkpoint 11 — Pre-deployment Security Gate

Checkpoint 11 adds no product feature. It is the mandatory adversarial gate between the completed hosted identity/project architecture and any live staging identity smoke.

The gate assumes Schema 42 and the Checkpoint 4–10.6 boundaries are frozen. It attacks replay, redirect confusion, cross-user project access, cross-provider substitution, account-link races and partial failure, MFA downgrade/replacement, session fixation/revocation, enumeration and abuse controls, Management API privilege, and local/hosted isolation.

## Frozen invariants

- Local Breadmaker remains accountless and never instantiates Auth0.
- Project IDs are routing identifiers, never bearer capabilities; direct `/p/<id>` access requires membership and unauthorized/nonexistent IDs share the same not-found class.
- OIDC state is single-use and nonce, PKCE S256, issuer, audience/azp, time and exact-callback validation stay mandatory.
- Return/logout paths reject absolute URLs, protocol-relative URLs, fragments, control characters, raw backslashes, and their percent-decoded normalization equivalents.
- Authentication rotates any pre-login Breadmaker cookie.
- Account linking proves both identities, never links by email, and compensates an Auth0 link if Breadmaker cannot commit local ownership.
- Sensitive actions require fresh Auth0 proof plus a short-lived, single-use server grant bound to user, session, operation and target.
- Database/password TOTP cannot be disabled. Social TOTP stays sticky once enrolled. TOTP replacement prepares its enrollment transaction before deleting the working factor and sets a transient Auth0 `app_metadata` recovery marker before deletion; if replacement is interrupted, the next social login must enroll TOTP rather than silently downgrade to no MFA. The marker is not an MFA source of truth and is cleared only after successful replacement completion or before an explicit optional disable.
- Passwords, TOTP secrets/codes, recovery codes, OAuth tokens/codes, Management credentials and raw session cookies never become browser or log state.
- Hosted mutating requests compare the complete Origin scheme + authority against the configured public origin; matching only the Host is insufficient.
- Auth0 Management authority stays split into narrow server-only operators and out of GCP OpenTofu.
- The temporary pre-integration auth preview is deleted completely; Checkpoint 11 tests the real Auth0 path and versioned desired state only.
- Staging/production hosted login remains disabled until Checkpoint 12 live smoke succeeds.

Failures in this checkpoint are security findings to harden, not reasons to weaken the gate.


## Second adversarial pass hardening

- Initial OIDC login state is bound to a short-lived HttpOnly browser nonce, preventing attacker-started login transactions from being completed in another browser.
- Public authorization transaction storage is bounded and `/auth/login` + `/auth/signup` starts are rate-limited to resist memory-exhaustion abuse.
- Social TOTP enrollment intents are HMAC-signed and bound to a SHA-256 hash of the intended Auth0 user id, blocking cross-user replay without exposing the subject in the URL.
- Real Auth0-mode mutating APIs require an Origin header in addition to SameSite cookies and Fetch Metadata checks.
- Uvicorn access logging is disabled for real-auth launch/deployment paths so OAuth authorization `code`/`state` query strings are not written to application access logs; global `Referrer-Policy: no-referrer` remains mandatory.

- Redirect validation repeats percent-decoding through a bounded normalization chain, rejecting double-encoded protocol-relative and backslash forms rather than assuming every intermediary decodes exactly once.
- Authenticated account-link transactions and step-up transactions/grants have explicit hard capacities; an authenticated attacker cannot grow those in-memory security stores without bound.
- Real-auth launch pins the HTTP `Host` header to `BREADMAKER_PUBLIC_ORIGIN`; unexpected authorities receive 421 before the application/router can use them.
- Public Auth0 callback failures use one generic `AUTH0_CALLBACK_REJECTED` response class rather than revealing whether a state, browser binding, provider, or token-validation phase was reached.
- Current hosted auth/session/OIDC security state is single-process. Multi-worker deployment is explicitly forbidden until these stores are replaced by shared atomic state; this is a deployment security invariant, not a scalability suggestion.

- The opaque Breadmaker session registry is bounded globally and per user; per-user pressure retires only that user's oldest sessions and global pressure fails issuance instead of evicting another user.

## Final consultant pass

- Locale/signup restarts now retire the OIDC transaction and browser-binding state they supersede instead of leaving abandoned authorization state alive until TTL expiry; `/auth/language` is covered by the same per-IP authorization-start limiter.
- The locale restart context store has an explicit hard capacity.
- Auth0 mode requires discovery authorization/token/JWKS endpoints to remain on the configured issuer origin, preventing discovery from redirecting confidential code exchange or JWKS trust to another authority.
- Account-link and step-up state ownership is checked before single-use consumption, so a different authenticated session cannot cancel the legitimate user's transaction by presenting a copied state value.
- Account-link transactions and step-up transactions/grants enforce per-user ceilings as well as global bounds, preventing one authenticated account from monopolizing shared in-memory capacity; wrapper-bind failures retire the underlying OIDC transaction immediately.
- The real-auth Host pin rejects missing or duplicate Host fields rather than accepting the first value and risking proxy/application authority disagreement.
- Hosted sliding-window rate-limit bookkeeping has a hard identity-bucket ceiling; source-address spray fails closed instead of growing process memory without bound.
- Login, account-link, and step-up callbacks expose only generic rejection classes; internal failure stages remain server-side.
- The manuscript bookmark toolbar is revealed only after asynchronous manuscript refresh completes, removing the confirmed hover-tooltip lifecycle race without changing tooltip timing.

