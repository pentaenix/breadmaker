# Testing Breadmaker

Run the full Editor verification suite from the repository root:

```sh
./verify-release
```

It reports unit, Core/API, current-browser, and durable end-to-end results, including a compact failure digest and the version being verified. The suite uses fixed per-test timeouts; a failed or timed-out item makes the release fail. Tests use synthetic projects and test identity adapters rather than live production accounts.

`./verify-release` may install development dependencies, launch Chromium, and query vulnerability-advisory services. It is a **release gate**, not part of running the offline local editor. CI executes this gate on pull requests and pushes to `main`. The private Cloud pipeline has its own full verification gate; production deployment must stop if either repository fails.

For focused work, developers can run a relevant test module or item first, but that does not replace the full suite. Do not loosen watchdogs or suppress errors to obtain a green result. When the full suite fails, use the failure digest and the saved report to locate the underlying test or application fault.

## Historical test notes

The material below documents earlier authentication providers, harness changes, and reliability decisions. It should not be treated as the current setup for CI or Identity Platform.

<details>
<summary>Older verification contracts and decisions</summary>

# Breadmaker verification — 10.3.61 Milestone 7C corrective

`./verify-release` remains the release-operator-owned gate. Automated engineering changes do not run it. Current verification still uses the four-layer model below and preserves its hard per-item watchdogs. M7C adds credential-free contracts for Identity Platform-only hosted authority, transient in-memory browser proof, Secure/HttpOnly server authority, same-UID fresh reauthentication, verified-email+TOTP password policy, Auth0 retirement, accountless local runtime, and the existing M3/M4/M5 persistence firewalls. Real provider credentials are not required for repository verification.

# Breadmaker verification — Checkpoint 12
The normal verifier remains credential-free and offline with respect to Auth0/Google/Apple/GCP secret payloads. It validates the staging composition, Secret Manager reference boundary, zero-secret local runtime, single-worker/HTTPS/Host-pin requirements, and the frozen Checkpoint 11 adversarial contracts. It does **not** deploy staging or run the live provider smoke.

# Breadmaker verification — Checkpoint 11
The normal verifier now includes the Checkpoint 11 adversarial contracts. Real authentication testing uses `./run-hosted-auth-dev` with development tenant credentials; there is no preview-only auth path.

UI verification protects the real Auth0 page-template contract only; the temporary local auth preview is retired and must remain absent. `./run-hosted-auth-dev` requires real development Auth0 credentials, while offline repository contracts protect shell branding, locale restart safety, provider ownership, enumeration resistance, and the 15-attempt server-side abuse policy without contacting Auth0.

## 10.3.27 — Checkpoint 10.6 corrective testing


## 10.3.26 — Checkpoint 10.6: stable project identity and hosted project URLs

Schema 42 intentionally breaks every earlier pre-alpha project. Each current project now stores one immutable 32-hex `project_uid` inside SQLite; it must exactly match the portable manifest `project_id` and workspace folder/registry id. There is no migration or compatibility path. Hosted project URLs use `/p/:projectId` (plus `/canvas`, `/maps`, `/tasks`, `/reviews` suffixes), direct links resume the requested authorized project, and the hosted root resumes the account's last-opened project. An account with zero projects receives an in-memory blank manuscript; the first meaningful write creates the project, assigns its ID, persists the draft, and replaces the URL. The existing All Projects modal remains the only project browser. Local Breadmaker keeps its open-and-write behavior and does not gain authentication.

## 10.3.25 Identity Checkpoint 10.5

Verification now protects the hosted-only Account & Security UI, Auth0-owned credential boundary, JSON prepare endpoints, login branding desired state, enumeration-resistant copy, and the 15-attempt identifier+IP brute-force threshold. It must also keep local runtime accountless. Project ID routing is not part of 10.5.

## 10.3.24 Identity Checkpoint 10

Checkpoint 10 contracts cover fresh single-use step-up, reserved OIDC parameter protection, signed social-TOTP enrollment intent, rollback-safe identity unlink, mandatory database TOTP, owned-session-only revocation, Auth0-before-local global sign-out, hosted-dev-only route composition, and continued absence of Security UI/local-runtime authentication. Full verifier execution remains the release operator's action.

## 10.3.23 Identity Checkpoint 9

Checkpoint 9 tests cover dual-proof link state, primary-session binding/freshness, forced secondary reauthentication, provider/subject derivation, rejection of email-based linking, refusal to merge a secondary already owned by another Breadmaker user, narrow server-only Auth0 Management authority, and session rotation after success. There is intentionally no product linking UI yet; Checkpoint 10.5 owns UI verification.

## 10.3.22 Identity Checkpoints 7+8

Verification now protects the database mapper, development-only Auth0 route composition, explicit `auth0` hosted session mode, separate localhost-auth launcher/data home, normal local null-auth composition, provider-family dispatch, and release/document synchronization. The real Auth0 tenant is never contacted by the normal verifier; live development login remains an operator smoke test using `./run-hosted-auth-dev`.

## 10.3.21 Identity Checkpoint 6 + hover reliability

Checkpoint 6 tests freeze Apple/Auth0 issuer+subject identity, `apple|` strategy enforcement, dotted provider identifiers, no email-based linking, both Sign in with Apple private-relay domain generations, preservation of first-authorization metadata after later omission, and the absence of Apple credential/provider-token ownership in Breadmaker. The durable v4.16.2 markup-hover workflow now waits for observable tooltip visibility with a bounded 1500 ms timeout instead of assuming the 300 ms product timer must fire inside a 40 ms scheduler margin.

## 10.3.20 Identity Checkpoint 5

The release suite now includes Google/Auth0 mapping contracts. They verify that only already-verified Auth0 identities with the `google-oauth2|` subject strategy are accepted as Google, the complete Auth0 issuer+subject remains the account key, same-email/different-subject identities remain separate users, no Google API/Drive scope or provider-token persistence is introduced, hosted login routes remain absent, and local runtime remains accountless. No test contacts Google or Auth0.

## 10.3.19 Identity Checkpoint 4

The release suite now includes deterministic Auth0-boundary tests on top of the Checkpoint 3 fake OIDC issuer. They verify exact callback/origin rules, minimal scopes, secret-reference injection, sanitized secret-loader failures, no hosted login routes, and unchanged local accountless runtime. No test contacts a live Auth0 tenant.

## 10.3.18 marker-hover durable timing

Durable hover tests must wait for observable UI state with bounded timeouts rather than assume JavaScript timers fire within a narrow scheduler margin. Marker-panel hover liveness is owned by enter/leave events and must not re-query CSS `:hover` from its delayed callback.

## 10.3.17 corrective verification

Historical contracts are forward-compatible with FastAPI's live included-router tree and security dependency upgrades. Tests must protect semantic/security floors rather than obsolete vulnerable exact pins. Local and hosted image-selection contracts are intentionally distinct.

## 10.3.16 verifier Python selection

`./verify-release` selects an available Python 3.10+ interpreter, rejects incompatible explicitly supplied interpreters, and recreates a stale `.venv` when it was built with Python <3.10. The bundle verifier forwards the exact interpreter it is running under to both repository gates.

## 10.3.15 hostile-image contracts

The verifier now protects the hosted image boundary: allowed formats, magic/structure validation, pixel/dimension/byte ceilings, Pillow pinning, container exclusion of ImageMagick/libheif/pillow-heif, and the rule that hosted PDF/DOCX export must not decode project images in-process. These contracts supplement the strict dependency advisory audit; they do not rely on CVE metadata alone.

## 10.3.14 dependency-security preflight

The dependency preflight runs `pip-audit --strict`; an unauditable dependency fails verification just like a known vulnerability.

`./verify-release` now requires the exact pinned production/test dependency set and runs `pip-audit` against `requirements.txt` before pytest. A known advisory or audit failure blocks verification. The 10.3.14 core contracts also protect ASGI-scope path routing, the streamed hosted body ceiling, and the absence of direct runtime code-execution/shell primitives.

## 10.3.13 Checkpoint 3 contracts

Checkpoint 3 adds deterministic fake-issuer/JWKS protocol tests for Authorization Code + PKCE S256, single-use state, nonce, issuer/audience/expiry failures, JWKS rotation, and the invariant that local mode remains authentication-free.

# Breadmaker testing and regression contracts

## Verification layers

`./verify-release` executes the regression model in this order:

1. **Unit** — pure/deterministic contracts. No browser, FastAPI `TestClient`, or project database lifecycle. This is the **first executable verification gate** and each item has a hard **3-second** setup + call + teardown budget.
2. **Core / API** — component, SQLite, persistence, service, and HTTP integration contracts. These retain the hard 10-second per-item budget.
3. **Current browser** — isolated Chromium contracts for the current product surface, with a 15-second per-item budget.
4. **Durable E2E** — historical end-to-end workflows sharing browser/server startup but receiving per-item project isolation, also with a 15-second per-item budget.

The release report and console stage accounting show executed/passed/failed/skipped totals for all four layers independently. Unit coverage supplements the existing integration and E2E suites; moving a behavior into Unit must not silently delete higher-level regression coverage that protects a distinct contract.

### Browser retry policy

Current-browser items are isolated subprocess contracts. If one fails on its initial pass, `./verify-release` schedules that exact item for **one** fresh-process retry after the initial browser/E2E stage has finished. A retry that passes is accepted as the logical final result, but it is never hidden: console accounting reports the recovered item and `verification-report.txt` preserves the first failure reason. A second failure is a confirmed release failure. Unit, Core/API, and Durable E2E are not covered by this generic retry policy; deterministic lower layers must remain strict, while Durable E2E already owns its sequential recovery/accounting model.

### What belongs in Unit

Prefer Unit for deterministic functions/classes whose contract can be evaluated without application boot or durable project state: parsing/validation, canonical hashing, retention policy, manifest normalization, safety/path rules, and deterministic coordination helpers. Keep filesystem/database/application-lifecycle behavior in Core/API even if the implementation function is small.

Breadmaker's browser tests should assert user-visible behavior without coupling themselves to incidental markup, animation timing, or state left by another test.

### Unit semantic fidelity

Unit tests are not a second specification. Their expected values must mirror the production contract already established by implementation and higher-level tests. For example, History retention `None` resolves through the configured/default policy, and normalized safe relative archive names such as `./relative` are acceptable unless they escape the archive root.

Focused browser contracts should avoid re-testing unrelated navigation. When a test owns Canvas gesture behavior rather than top-level routing, it may enter the Canvas view through `setView(..., {historyMode:'none'})` and then continue through real user-facing controls for the behavior under test.

## Stable locator policy

Use `data-testid` for controls or transient surfaces whose CSS classes, translated labels, or DOM nesting may legitimately change. Keep semantic product attributes (`data-map-item`, `data-selection-action`, IDs used by application code) for behavior and data identity; `data-testid` is the stable automation hook.

Current stable hooks include:

- `canvas-stage`
- `canvas-pointer-mode`
- `canvas-selection-rect`
- `manuscript-block-wrap`
- `manuscript-block`
- `manuscript-selection-toolbar`
- `manuscript-selection-highlight`
- `manuscript-selection-style`
- `manuscript-style-body` / `manuscript-style-chapter` / `manuscript-style-heading2` / `manuscript-style-heading3` / `manuscript-style-heading4`
- `manuscript-highlight-picker`
- `markup-panel`
- `marker-new`
- `marker-empty-new`
- `manuscript-navigator-menu`
- `navigator-add-scene` / `navigator-add-chapter`
- `navigator-toggle-scenes` / `navigator-toggle-chapters`
- `manuscript-view-scenes`
- `map-span-handle` (combine with `data-map-item-id` and `data-span-edge`)
- `map-line-route` (combine with `data-map-line-route`)
- `map-line-actions` (combine with `data-map-line-actions`)
- `map-add-station` / `map-add-span`

Do not use visible copy as the primary locator when the copy is translated or intentionally mutable.

## Synchronization policy

Prefer observable completion signals over elapsed time:

1. Wait for application state (`state.currentCanvas`, active view/mode, etc.) when the state itself is the contract.
2. For persistence, wait for/read the API result that the interaction is supposed to change.
3. For transient UI, wait for the relevant `data-testid`/state class.
4. Use a fixed sleep only when the duration itself is the behavior under test (for example a documented hover grace period).
5. SVG geometry is not ordinary box geometry: a visible stroked `<line>` may be reported as hidden because its layout box has zero height/width. Wait for attachment and convert SVG coordinates with `getScreenCTM()` when the contract is geometric manipulation.
6. Scroll/overflow tests must use fixtures that deterministically exceed the viewport (for example explicit line breaks), not text that merely tends to wrap on one machine/font.

A test must not make a successful assertion depend on a previous test's project data. Durable E2E items share browser/server startup for speed but receive a fresh active project.

## Scope policy

Keep one interaction contract per test item where practical. Large historical workflows should be split at stable behavioral boundaries rather than relying on timeout headroom. Unit pytest items have a hard 3-second setup + call + teardown budget; Core/API items have 10 seconds; browser and durable E2E items have 15 seconds to account for isolated server/Chromium lifecycle variance.

Property/stress coverage may be sharded more finely to preserve the same total workload while keeping deterministic teardown headroom. The total operation/reconstruction budget is the contract; the number of mutations packed into one pytest item is not.
## Reliability headroom policy

The per-item setup + call + teardown budget is a hard release invariant: 3 seconds for Unit, 10 seconds for Core/API, and 15 seconds for browser/E2E. Randomized/property suites must target comfortable headroom on slower machines, not merely pass close to the deadline on a fast machine. Preserve aggregate work when re-sharding: operation count, reconstruction count, backup checks, publication checks, and deliberately destructive/repair paths are the coverage contract.

For native browser interactions, prefer the browser gesture that a writer actually performs when that gesture itself establishes state. In particular, selection-toolbar tests should use native pointer/double-click selection when practical rather than synthesizing `Range` objects and manually invoking application internals. For segmented SVG routes, choose the route segment whose semantic `data-route-start` / `data-route-end` bounds contain the intended logical target; DOM order must not be treated as one continuous route.

### Repair tests must damage the exact authority they republish

A remote-history repair assertion is only valid when the test republishes an already-published revision whose remote object/checkpoint was removed or corrupted. Publishing a newer, never-published revision is a fresh publication and must not be asserted as `repaired=True`.

### Selection tests should separate browser Range creation from app behavior

Headless browsers do not guarantee that synthetic double-clicks produce native word selections. Tests may create an exact DOM `Range` deterministically, but should then cross Breadmaker's real selection-completion boundary (`showSelectionToolbarAfterPointer`) rather than calling downstream style/actions directly. This tests Breadmaker behavior without depending on OS/browser gesture synthesis.

### Map drag tests should target semantic geometry

Rendered SVG route segments are an implementation detail and can be split around Stops, Spans, or connections. When a drag contract is about a logical position, derive the client coordinate from the current Line plan (`mapLineDisplayAxisAtPosition`) and current SVG transform. Do not infer logical story position from the first/last rendered route node.
### Wait for browser boot, not just first content

Current-browser helpers must not return a page merely because manuscript content has rendered. Breadmaker continues restoring UI state after `refresh()`, including Canvas/Maps state and the final `setView(viewFromRoute())`. The HTML `data-initial-view` marker is a visual no-flash mechanism, not a harness readiness primitive (the synthetic browser harness strips index scripts entirely). `app.js` therefore exposes `window.__breadmakerBootPromise` and sets `data-breadmaker-boot-ready=true` only after `boot()` resolves. Current-browser helpers wait on that explicit boundary before driving the UI.


## Hosted authentication tests (9.6+)

Do not automate Auth0/Google login pages as part of normal Breadmaker verification. Hosted application tests use Breadmaker's test-session adapter instead:

- `BREADMAKER_RUNTIME=hosted_dev`
- `BREADMAKER_AUTH_MODE=test`
- `BREADMAKER_ENV=test`
- `BREADMAKER_TEST_AUTH_SECRET=<dedicated random secret, at least 32 characters>`
- HTTP-only local harnesses may additionally set `BREADMAKER_SESSION_COOKIE_SECURE=0`.

`POST /__test__/login` accepts only the named fixtures `owner`, `editor`, `second_owner`, and `unowned_user`, requires the secret as a Bearer credential, and then sets the normal HttpOnly Breadmaker session cookie. The secret is never accepted by normal APIs. Real-provider login should remain a small separate integration/smoke suite once Auth0 is connected.

## 10.3.12 Checkpoint 2 contracts

`tests/test_v10312.py` verifies the hosted-only `(issuer, subject)` identity registry, email non-authority, session replacement/rotation and user-wide revocation, and—most importantly—that local runtime retains null account/session services and creates no hosted identity state. These are part of the normal `./verify-release` core/API discovery; no separate auth command is required.


### Archive image ingress

The hosted image ceiling applies before image bytes are materialized from Cuts, portable BreadReview, and native project-package imports. Hosted project packages are rejected before activation if any `assets/` member exceeds the configured image ceiling or is not a structurally valid PNG/JPEG/WebP. Portable BreadReview decoding receives the same per-asset ceiling. These archive paths do not create a decoder exception.


Auth UI theme parity applies only to the real Auth0 Universal Login page template. The Breadmaker-owned shell uses the Editor `light → dusk → dark` token sets and the same half-filled-circle theme control, while the Auth0-owned credential widget remains on the exact Breadmaker Light theme because Breadmaker does not rewrite Auth0 widget DOM. There is no local auth preview or preview runtime; adopting per-user runtime themes inside the Auth0 widget would require a separately reviewed ACUL boundary.

Checkpoint 11 round-two hardening additionally pins the real-auth Host header to the configured public origin, rejects repeatedly percent-decoded redirect confusion, bounds account-link and step-up state stores, keeps callback failures generic, and explicitly forbids multi-worker hosted authentication until the in-memory session/OIDC/security stores are replaced by shared atomic state.

</details>
