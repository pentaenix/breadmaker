# Synthetic installation telemetry authority

This package is a dormant development model, not a service or collection system.
It is separate from `workspace` because importing that package constructs a real
Workspace singleton. Importing this module must not initialize app data or import
the editor, identities, hosted metrics, manuscripts, or project storage.

`installation_preview.SyntheticInstallationTelemetry` requires an explicit database
path in an existing trusted private scratch directory and `synthetic_only=True`.
There is no default application-home path, runtime registration, HTTP route,
browser connection, timer, network code, sink or deployment. Unit tests alone feed
invented activity. Do not point it at an actual installation or feed real events.

## What it models

- **One local installation authority:** windows, browser profiles and local
  processes opening the same explicit database share consent and one random UUID
  v4. No account, project, existing remote-writer identity, device properties or
  fingerprint is reused. Pending/declined creates no token or activity rows.
- **Representative follower activity:** there is no browser leader. Every synthetic
  foreground window may submit a fixed workspace enum. Fifteen minute windows
  store minute bitsets; overlapping windows contribute one installation active
  minute and at most one minute per workspace per clock minute. No window/tab ID
  is stored. Simultaneous Manuscript and Canvas use can count toward both workspace
  totals, while installation active minutes stay deduplicated.
- **Session continuity:** first qualifying activity starts a session; subsequent
  windows and process restarts continue it until at least 30 minutes of inactivity.
  Session starts do not follow window ownership. No session UUID or event stream.
- **Shared delivery budget:** preparing a synthetic batch transactionally reserves
  the budget for all connections/processes. At most one reservation per 15 minutes,
  at most four completed windows and 4,096 UTF-8 bytes. The cooldown survives
  process restart, owner changes, failed acknowledgements and decline/re-enable.
  Clock rollback cannot reset the budget or mutate already prepared windows.
- **Revocation:** a successful decline atomically deletes all queued windows,
  clears the installation token and session activity, and invalidates prepared
  reservations. A new affirmative grant creates a new token. Repeated grant while
  already enabled preserves the current token/session. Every operation checks the
  current database consent. Already-returned batch bytes cannot be recalled.
- **Bounded queue:** at most 96 windows. Expired windows are removed on subsequent
  activity/snapshot/batch/ack operations after 24 hours. **No timer runs, so idle files can retain rows beyond
  24 hours until the next operation.** This is not a 24-hour physical-erasure promise.
- **Failure handling:** busy SQLite waits at most 50 ms, then activity/reservation/
  acknowledgement operations return false/empty silently. Failed preference
  persistence disables that instance; it cannot truthfully guarantee withdrawal
  in other processes. Explicit scratch setup errors can raise to the developer.
  Operations do not recreate a scratch database that has been erased.

The database has an application ID and schema marker. Unknown or unrelated SQLite
files are rejected rather than migrated. New files are created with POSIX mode
0600; the caller must provide a private directory. SQLite secure deletion is
requested for deleted cells. Neither this nor file removal guarantees forensic
erasure, cleanup of external backups, filesystem snapshots or Windows ACL policy.
Do not import/export this file with projects. No existing project/package code is
changed and no live database file has been created by this package's tests.

## Payload and truthful labels

The separate **synthetic v2** report schema contains only fixed schema/notice,
random installation UUID, first-observed app release for that window, coarse UTC
window, session starts, active minutes and five `workspace_minutes` counts.
Minute bitsets and precise consent/activity/budget metadata remain local. No text,
paths, project IDs, account IDs, input events, stack traces, IP, device or location
fields exist. These minimized signals are pseudonymous, not guaranteed anonymous.

Use **Consenting local installations**, **Session starts (30-minute inactivity)**,
**Active installations by 15-minute window**, and **Workspace active minutes**.
This schema intentionally replaces the earlier synthetic browser `feature_visits`
estimate with deduplicated workspace minutes. Do not merge v1/v2 fixture totals
or silently relabel minutes as visits. Distinct database tokens do not establish
distinct people, devices or license installs. Reinstall, opt-out/re-enable, copying
a database, multiple data homes and backups can distort counts.

`prepare_synthetic_batch()` returns immutable bytes plus an opaque local reservation.
It does not transmit data. Acknowledgement deletes only windows carrying the latest
reservation. Retries can return the same logical windows; a future receiver needs
idempotency on installation token + window + schema. Prepared batches from a revoked
token must never be sent. A future transport needs a fresh consent check and
cancellation at the dispatch boundary; this model provides no such transport.

See `tests/fixtures/app_telemetry/synthetic_installation_reports_v2.json` for private
admin mock data. Dashboard blocks remain primary, with raw rows one level below.

## Activation blockers

1. Explicit approval of consent notice v2, translations, controller/contact,
   retention and deletion policy, jurisdictions, children and security. This
   model's persisted queue differs from the original memory-only preview, so the
   original consent cannot silently authorize its storage or revised signals.
2. A reviewed installation-wide path/authority, private directory/ACL enforcement,
   crash and corruption handling, erasure integration, retention scheduling and
   durable failed-withdrawal behavior. No workspace binding is supplied here.
3. A reviewed bounded transport/receiver: consent recheck, cancellation, idempotency,
   abuse controls, credentials/referrer/redirect policy, IP/access logs and private
   admin authorization. No endpoint or sender exists in this checkpoint.
4. Foreground and idle semantics tested in actual desktop wrappers and browsers.
   The required `foreground=True` here is synthetic input, not a verified detector.
   No actual window, focus, visibility, keystroke or save hooks exist.
5. Full Editor release verification before any release. Focused tests do not certify
   deployment readiness or worldwide compliance.

## Checkpoint validation (2026-11-05)

The focused run passed all **18 pytest entries**: 11 installation-authority cases,
the wrapper for 17 deterministic browser-foundation/coordinator cases, and 6 real
browser consent/coordination cases. Runtime was 27.96 seconds. Coverage includes
two actual Python processes contending for one reservation, concurrent follower
connections, process reopen/session continuity, withdrawal and stale acknowledgements,
bounded queue/bytes, lock contention, rollback clocks, unrelated database rejection,
and no recreation after erasure. The earlier browser path remains dormant on boot.

The v2 illustrative four-row compact JSON serialization is approximately 1.3 KiB;
the hard reservation ceiling is 4 KiB every 15 minutes across connections (up to
384 KiB/day in repeated backlog reservations). These are synthetic preparation
limits, not measured network use. Actual telemetry traffic remains zero. No full
release gate, real collection, native/browser bridge, endpoint or deployment was
performed. Generated code navigation and diff whitespace checks passed.
