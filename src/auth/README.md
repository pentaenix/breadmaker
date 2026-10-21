# Authentication ownership — Milestone 7C

Hosted Breadmaker uses Google Identity Platform/Firebase Authentication as its sole authentication authority. Local Breadmaker remains accountless.

- `identity_platform.py` owns server-side ID-token verification, admission, provider UID -> Breadmaker user mapping, session-cookie minting/verification and provider-wide revocation.
- `identity_platform_security.py` owns provider-neutral Account & Security policy, fresh same-UID reauthentication and verified-email/TOTP invariants.
- `accounts.py` stores provider-independent Breadmaker identity bindings; email is metadata, never merge authority.
- `sessions.py` retains only null/test/session primitives used outside real hosted Identity Platform authentication.
- `oidc.py` is retained as a provider-neutral historical utility but is not part of the hosted authentication composition.

First-time hosted admission can consume an operator-managed Firestore greenlist
grant for the verified token's email and permitted provider. The grant atomically
binds to exactly one Identity Platform UID; email never looks up or merges an
existing Breadmaker account. Disabling/deleting a bound grant denies both new
sessions and existing cookies. Existing UID admissions and operator invitations
remain supported; no public endpoint can create or edit greenlist grants.

Automatic email linking remains disabled. If Google supplies only a provider
email, first sign-in sends Firebase's verify-before-update email action. The
tester verifies it and signs in again; only Firebase's fresh signed, verified
primary-email claim can consume a grant. Browser profile data never authorizes
admission, and no existing account is looked up or merged by email.

The retired Auth0 runtime implementation is preserved only as historical text under `docs/archive/auth0_runtime/`; no active source imports or selects it. Provider credentials, TOTP secrets, recovery material and Firebase tokens are never Breadmaker persistence.
