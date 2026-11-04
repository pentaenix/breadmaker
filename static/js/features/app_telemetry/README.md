# Optional free-app telemetry — review checkpoint

This foundation is **dormant**. Neither script nor CSS is included in `static/index.html`;
there is no boot hook, real observation, endpoint, SDK, network implementation,
deployment, or production consent solicitation. Only synthetic test calls populate
reports. Do not connect this to real activity before the reviews below.

`foundation.js` owns consent and finite, projected synthetic aggregates.
`consent.js` owns the first-use bottom banner and Settings dialog preview, using the
real shared `showModal`, `soft-button`, `ui-close-button`,
`project-settings-switch-row` and 34×20 `structure-switch` patterns.
The only new CSS geometry is the requested bottom banner.

## Consent behavior

- Initially pending/off. No random identifier, observations, reports or delivery
  before an affirmative choice.
- **Allow app usage** grants this version of the optional purpose. **Not now** and
  **×** both save declined/off. Neither dismissal nor continuing to write grants
  permission; a saved refusal does not trigger another banner.
- The Settings switch starts off unless this exact notice version was accepted.
  Switching on is an affirmative choice; switching off stops collection, aborts
  any synthetic delivery, clears memory reports and removes the installation ID
  from the saved preference. Re-enabling creates a new random ID.
- The local receipt stores notice version, decision, exact decision UTC timestamp
  and (only while granted) random UUID v4. Decision timestamp is **not** in reports.
  Invalid/outdated preferences return to pending/off. Storage failures fail closed
  in the current instance. If both writing and removal fail, other open tabs or a
  restarted instance cannot be guaranteed to see a withdrawal; native coordination
  must address this. No consent record is claimed to be a server audit trail.
- Every observation and delivery rechecks storage. The UI additionally responds to
  storage events to clear other-tab state immediately. A future native app must
  coordinate all browser surfaces through one installation-level owner.

The preview visibly says nothing is collected or sent. It does not represent a
final privacy notice. English wording is provisional; translations must be
reviewed before users are asked to consent. The future Settings row should use
`projectSettingsRow` and existing child-dialog navigation, including recovery and
zero-project states. This checkpoint does not modify that shared integration file.

## Exact report fields

| Field | Meaning / bounds |
| --- | --- |
| `schema` | Fixed `bm-app-telemetry-preview-v1` |
| `installation_id` | Random UUID v4 created after consent; never an account/client/project ID |
| `notice_version` | Fixed reviewed-purpose version (`preview-1` currently) |
| `app_version` | Numeric release triple only; invalid input becomes `0.0.0` |
| `bucket_utc_ms` | UTC start of a 15-minute window, no event timestamps |
| `sessions` | Synthetic instance-start count, 0–15 |
| `active_minutes` | Synthetic active minutes, 0–15 |
| `feature_visits` | Counts 0–255 for the fixed names manuscript/canvas/maps/tasks/reviews only |

No free text or arbitrary event names enter these reports. No manuscript, word
counts, project titles/IDs, paths, keystrokes, account IDs, email, locale, device
properties, IP, location, fingerprint, crash stack or session identifier is read
or included. Ingress infrastructure would inherently see IP and network metadata;
logging and retention of that information must be separately reviewed.

This is independent of the premium account-linked project metrics validator in
`src/hosted/security/metrics.py`. There is no reuse of its identity or word counts.
Targets/Canvas work is untouched; enum labels carry no Canvas objects or Targets.

## Limits and failure behavior

Reports remain in memory, expire after 24 hours and are capped at 96 windows.
There is no persisted queue; closing loses unsent reports by design. Only completed
windows can enter a batch. A synthetic sink receives at most four reports and
4,096 UTF-8 bytes per attempt, at most once per 15 minutes per instance. Hanging
sinks time out after 500 ms with an abort signal. Rejections, exceptions and timeout
produce no toast, console error, rejected promise, edit/save dependency or immediate
retry. The explicit Settings persistence error is distinct from background delivery.
Successful synthetic delivery removes only that batch; failed batches expire or
are dropped at the queue ceiling. Withdrawal cannot retract bytes already accepted
by a future server; production erasure policy must explain this.

The four-report synthetic fixture is 1,214 compact JSON bytes. A single report with
its envelope is about 350 bytes; continuous successful reporting is roughly 34 KiB
of JSON per day, excluding HTTP/TLS overhead. The conservative attempt ceiling is
384 KiB JSON/day (96 × 4,096 bytes) during repeated backlog failures. Current real
telemetry bandwidth is **zero** because no transport exists or is invoked.

## Private dashboard fixture contract

`tests/fixtures/app_telemetry/synthetic_reports.json` contains invented data for the
separately owned private admin dashboard. Keep dashboard blocks/graphs primary,
with a **View table** drilldown to these rows one level below. Do not add a public
editor dashboard or real data source as part of this change.

Use labels such as **Consenting browser installations**, **Active installations
(15-minute windows)** and **Session starts (approximate)**. A distinct installation
token is not a person or a reliable native installation: browser profiles/origins,
cleared storage, opt-out then opt-in and reinstalls create different counts.
Multiple tabs/reloads currently create separate synthetic session starts. Window
overlap measures approximate use, not exact simultaneous use; it can overstate
instantaneous concurrency. Periodicity may be derived from repeated UTC windows
or days for a consenting token; do not claim population retention or total installs.
No extrapolation to nonconsenting users is implemented.

Before live wiring, one installation-level coordinator must deduplicate sessions,
minutes, batches and tokens across tabs, enforce a global delivery budget, and
define foreground/idle behavior without inspecting keystrokes. The current
instance API does not claim to solve native installation ownership.

## Review required before any real collection or delivery

1. Controller/contact, single improvement purpose, consent copy and translations,
   retention/deletion, children, applicable jurisdictions and international transfer
   rules. Do not promise these mechanics establish worldwide legal compliance.
2. Security review of endpoint/transport, no credential/cookie/referrer attachment,
   redirect policy, abuse/rate limits, strict server validation, idempotency,
   IP/access logs, private admin authorization and small-cohort disclosure rules.
3. Random identifier lifetime/rotation, withdrawn data treatment, native multi-tab
   ownership and truthful installation/concurrency labels.
4. Explicit approval of a real endpoint and runtime event hooks. This source
   checkpoint provides neither. Full Editor release gate remains required for
   any release; this task runs only focused checks.

Consent design references (checked 2026-11-04):
[ICO valid consent](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/consent/what-is-valid-consent/),
[ICO storage/access consent practice](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-the-use-of-storage-and-access-technologies/how-do-we-manage-consent-in-practice/),
[EDPB Guidelines 05/2020](https://www.edpb.europa.eu/documents/guideline/guidelines-052020-on-consent-under-regulation-2016679_en).
These support affirmative choice, refusal and easy withdrawal; implementation and
regional legal review are still required.

## Focused verification / preview

```sh
node --test tests/app_telemetry_foundation.cjs
python -m pytest tests/unit/test_app_telemetry_foundation.py tests/test_v10566_app_telemetry_browser.py -q
```

Browser tests run the real accountless local editor with a temporary synthetic data
home, add scripts through same-origin URLs under the unchanged CSP, then explicitly
mount `mountAppTelemetryConsentPreview`. Production boot never invokes it.
The tests cover refusal, close, keyboard consent, withdrawal, no reports on consent
alone, continued writing, shared switch geometry, desktop/narrow/mobile layouts
with a Manuscript panel open, and light/dark themes.

Checkpoint validation: 10 Node foundation cases passed; the focused pytest run
passed all 5 entries (the Node wrapper plus 4 browser cases) in 12.15 seconds.
JavaScript syntax and diff whitespace checks passed. New browser coverage is in
the existing release discovery lane. The full release gate was not run.
