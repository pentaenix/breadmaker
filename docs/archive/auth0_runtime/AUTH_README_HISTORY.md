## 10.3.47 — Identity boundary during serverless rewrite

Milestone 3 adds only the hosted persistence primitive for future Identity Platform bindings: provider UID hashes map server-side to provider-independent Breadmaker `usr_…` users, the exact UID is retained for collision/mismatch detection, and email remains metadata rather than identity/merge authority. Authentication runtime is not switched in this milestone; Identity Platform remains a later feasibility-gated replacement for Auth0 and local Breadmaker remains accountless.
## 10.3.41 — Staging Auth0 composition

Checkpoint 12 promotes the reviewed Auth0 boundary to the `hosted` staging runtime. Development keeps `env:` secret references; staging accepts only strict same-project Secret Manager version references via ADC; production remains disabled. Local runtime code does not instantiate this boundary.

# Auth runtime — Checkpoint 11
Only the real Auth0 development path remains. The temporary pre-integration auth preview was removed before Checkpoint 11; `run-hosted-auth-dev` requires explicit development tenant credentials and fails closed otherwise.

## 10.3.37 — Auth UI fidelity + local review surface


## 10.3.27 — Auth UI preview / real-auth separation


## 10.3.26 — Checkpoint 10.6: stable project identity and hosted project URLs

Schema 42 intentionally breaks every earlier pre-alpha project. Each current project now stores one immutable 32-hex `project_uid` inside SQLite; it must exactly match the portable manifest `project_id` and workspace folder/registry id. There is no migration or compatibility path. Hosted project URLs use `/p/:projectId` (plus `/canvas`, `/maps`, `/tasks`, `/reviews` suffixes), direct links resume the requested authorized project, and the hosted root resumes the account's last-opened project. An account with zero projects receives an in-memory blank manuscript; the first meaningful write creates the project, assigns its ID, persists the draft, and replaces the URL. The existing All Projects modal remains the only project browser. Local Breadmaker keeps its open-and-write behavior and does not gain authentication.

## 10.3.25 — Checkpoint 10.5

Auth mechanics remain owned by the Checkpoint 10 services. Checkpoint 10.5 adds presentation only plus JSON redirect-preparation seams for browser navigation; it does not move credential or step-up authority into JavaScript.

## 10.3.24 — Checkpoint 10

`security.py` owns hosted-development account-security mechanics: step-up transactions/grants, linked identity inspection/unlink, Auth0-owned MFA management, password-change handoff, and session-security operations. It is not product UI and must never be imported by the accountless local composition. TOTP enrollment uses a Breadmaker-signed authorization intent whose secret is shared only with the Auth0 Action; the browser can carry the signed parameters but cannot mint them.

## 10.3.23 — Checkpoint 9

`linking.py` owns explicit dual-proof account linking. It binds a fresh primary Breadmaker session to a one-shot secondary Auth0 OIDC transaction, forces reauthentication, derives provider identifiers from the verified subject, invokes the separate server-only Auth0 link operator, then attaches the secondary issuer+subject to the existing internal user. It never searches by email and never merges two existing Breadmaker users. `accounts.py` owns the stable first-identity primary and conflict-safe attachment primitive. Product UI remains deferred to Checkpoint 10.5.

## 10.3.22 — Checkpoints 7+8

`database.py` adds the Auth0 database identity mapper: only verified `auth0|…` subjects with verified email can become Breadmaker accounts. Passwords/hashes are never visible to this package.

`hosted_login.py` adds the real Universal Login adapter used only by `./run-hosted-auth-dev`. It composes the Checkpoint 4 Auth0 boundary, Checkpoint 3 OIDC verifier, Google/Apple/database mappers, the hosted account registry, and opaque sessions. The adapter is development-only and rejects unknown Auth0 subject families.

TOTP itself is intentionally absent from this repository: Auth0 owns OTP enrollment/challenge and recovery codes. The paired Cloud Post-Login Action enforces mandatory database TOTP and sticky opt-in social TOTP. Local Breadmaker still uses null auth services.

## 10.3.21 — Checkpoint 6

- `apple.py` — Checkpoint 6 Apple/Auth0 identity mapping. It accepts only already-verified Auth0 OIDC identities whose subject uses the `apple|` strategy, preserves the full Auth0 issuer+subject identity key, accepts private-relay email as profile metadata, and prevents later Apple profile omission from clearing stored email/name state. It owns no Apple signing keys or provider tokens.

Checkpoint 6 keeps hosted login disabled. `auth0.py` can construct the generic OIDC client, while `google.py` and `apple.py` can map verified social identities, but no `/auth/login` or `/auth/callback` route invokes them yet. Database/TOTP/linking/UI remain later checkpoints.

# Authentication ownership

`auth/` owns server-side identity/session primitives. It must not make local Breadmaker account-dependent.

- `accounts.py` — hosted-only external identity registry keyed by `(issuer, subject)` and mapped to stable Breadmaker `usr_…` users. Email is metadata, never merge authority.
- `sessions.py` — opaque server-side hosted sessions, rotation/revocation, idle/absolute lifetime.
- `oidc.py` — provider-neutral OIDC/BFF protocol engine: Authorization Code + PKCE S256, state/nonce, discovery, token exchange, JWKS/RS256 verification, and strict issuer/audience/time validation. It exposes no HTTP login route yet. JOSE/JWT signature verification and normative OIDC claim validation use pinned Authlib 1.8.0 / JOSERFC.
- `auth0.py` — Checkpoint 4 non-secret Auth0 application boundary. It freezes isolated environment profiles, exact callback/logout URLs, Universal Login/regular-web assumptions, and injects the client secret through a caller-owned secret loader before delegating to `OidcClient`. It exposes no HTTP route and gives normal runtime no Auth0 Management API authority.
- `google.py` — Checkpoint 5 Google/Auth0 identity mapping. It accepts only already-verified Auth0 OIDC identities whose subject uses the `google-oauth2|` strategy, preserves the full Auth0 issuer+subject identity key, and delegates account persistence without email-based linking. It owns no Google API tokens or Drive scopes.
- `identity.py` — request identity/authorization helpers.

`BREADMAKER_RUNTIME=local` uses `NullSessionService` and `NullAccountIdentityService`. Local Breadmaker must not create `account-identities/`, require an account, or depend on network identity. Breadmaker Cloud Backup is a separate future paid service connection and must not turn local editing into hosted account-session mode.

Checkpoint 5 keeps hosted login disabled. `auth0.py` can construct the generic OIDC client and `google.py` can map a verified Google-via-Auth0 identity, but no `/auth/login` or `/auth/callback` route invokes either yet. Apple/database/TOTP/linking/UI remain later checkpoints; Google Drive authorization remains independent.


Auth UI theme parity applies only to the real Auth0 Universal Login page template. The Breadmaker-owned shell uses the Editor `light → dusk → dark` token sets and the same half-filled-circle theme control, while the Auth0-owned credential widget remains on the exact Breadmaker Light theme because Breadmaker does not rewrite Auth0 widget DOM. There is no local auth preview or preview runtime; adopting per-user runtime themes inside the Auth0 widget would require a separately reviewed ACUL boundary.
