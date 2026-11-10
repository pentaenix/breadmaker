# Disabled inline import experiment

This branch is a local experiment, not release-ready. Ordinary HTTP/runtime
composition keeps inline off unless both explicit operator flags are set; default behavior is unchanged.
No production imports or configuration changes were performed.

## Blocking prerequisite

Production configuration was inspected read-only by the coordinating worker:
`blocks.content_html` and `blocks.text_content` inherit default indexing
(`usesAncestorConfig=true`, ancestor `__default__/fields/*`, collection ascending,
descending, and contains). The desired public index document is NOT deployment
proof. Do not enable this experiment until explicitly authorized Cloud changes
establish and verify ready single-field exemptions for both exact fields.

## Bounded experiment

Only unencrypted native-import manuscript blocks may be opted in. Canonicalized
public document JSON is limited to 4 KiB and final stored JSON to 8 KiB, far below
the documented 128 KiB inline soft limit. All other hybrids and larger paragraphs
retain the existing immutable object path. All current publication transactions,
fresh guards for actual object operations, receipt chains, and final project
visibility remain unchanged. Batch read accelerators are retained.

## Still required before enabling

- Verify deployed index exemptions, not only desired configuration.
- Implement bounded fast opening for mixed inline and object-backed documents.
  Current whole-book path fetches up to 10,000 complete documents before checking
  decoded size and rejects pointerless rows; increasing inline limits without
  bounding provider retrieval can create severe memory/read regressions.
- Prove edit transitions, native export/import, recovery and supported rollback
  readers preserve exact bodies and identity for mixed and legacy projects.
- Test cancellation, account freeze, retry across mixed deployment versions,
  request byte budgets and Firestore serialized document/index overhead.
- Complete required full verification and independent review. No full release
  verification or live provider measurement has been run for this experiment.

## Lifecycle checkpoint

121 focused tests passed after adding inline edit-to-object transition, portable
and native export body checks, independent recovery archive body checks, exact
10.5.96 rollback hydration method fixture, cross-version idempotent batch retries,
and cancellation/account-freeze publication fences. Editing inline content now
removes obsolete inline prose once the new immutable payload pointer commits.
The rollback fixture pins only the historical hydration method and uses unchanged
current module dependencies; it is not a complete old-server deployment test.

The bounded130-paragraph regression observes133 block-object publications before
versus3 retained batch accelerators with inline enabled. No injected latency or
live provider speed is inferred from that count. An attempted1,745-paragraph
in-memory regression exceeded the unchanged10-second test watchdog; the fixture
was reduced to keep regression checks bounded, not the watchdog relaxed.

## Default-off operator activation

HTTP native resumable imports now require BOTH exact operator environment values:

- `BREADMAKER_HOSTED_INLINE_IMPORT_BLOCKS=1`
- `BREADMAKER_HOSTED_INLINE_IMPORT_INDEXES_READY=1`

Unset, unknown and partial configurations keep inline off. The second flag is an
operator attestation, NOT a live index probe. The Cloud deployment preflight must
verify READY unindexed single-field configuration for both exact prose fields
before configuring either value. No production configuration was changed here;
known default indexes still block activation. Removing the enable flag restores
the legacy writer; already staged/imported inline rows remain readable.

## Final local verification status

After versioned test discovery registration, fresh pinned dependencies installed
and both production and hosted vulnerability audits passed. The unchanged full
release gate then failed browser preflight: Chromium local socket creation is
forbidden in this cloud executor, including after approved execution escalation.
The full unit/core/browser aggregate did NOT run. Separately, 580 unit/relevant
nonbrowser tests passed on the prior integrated checkpoint. Normal exact-head
CI remains mandatory before merge/deploy. Never weaken the browser gate.

The default-off activation checkpoint passed587 unit/relevant nonbrowser tests
in the fresh pinned environment, including all seven rollout-configuration cases.
These focused checks are not a replacement for the blocked full release gate.
