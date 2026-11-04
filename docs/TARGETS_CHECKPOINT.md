# Optional Targets review checkpoint

Targets is integrated with Canvas in the isolated 10.5.81 candidate on
`integration/canvas-next`, based on deployed main `8dcf79e`. It includes the
Targets owner's checkpoints `4613591` and `bc2cef2`; original owner worktrees
remain preserved. This candidate is not deployed. All 23 parameter combinations
now have explicit one-item browser entries for the unchanged release verifier.
Full integrated release verification remains required before promotion.

## Product behavior

- The Project menu places Targets between Project Settings and Language.
- Targets starts disabled. The modal initially shows only its activation switch
  and Save/Cancel. Disabling preserves saved goals/history and hides all status UI.
- Up to eight optional goals can mix words, characters, foreground app time and
  active time, each recurring daily, weekly or monthly.
- Writing goals measure net new Manuscript prose after creation. Deleting text
  reduces the period's underlying total; replacing deleted baseline text does not
  earn extra credit. Visible progress never shows a negative value. Native input,
  paste/cut, custom Enter/paragraph joins, and Manuscript Undo/Redo are covered.
  Navigation, importing, remote edits and programmatic rendering are not credited.
- Words use `Intl.Segmenter` with the interface locale (including Chinese and
  Japanese). Characters are Unicode code points, including spaces and paragraph
  separators. Chapter titles and image metadata are excluded.
- Calendar boundaries use the device's local timezone. Weeks start Monday.
  Daily boundaries honor daylight-saving changes; months are calendar months.
  Changing a goal's measure, period or amount creates a fresh goal identity and
  preserves its historical snapshots.
- Foreground time requires both a visible document and window focus. Active time
  additionally requires recent trusted input, a click, navigation, scrolling or
  dragging in an app workspace. It includes a 45-second thinking pause. Hovering
  alone does not keep it alive. Switching away pauses both; sleep/scheduler gaps
  over ten seconds are not counted. Timer heartbeats split midnight correctly.
  Overlapping clock intervals from multiple devices are unioned, so the same
  elapsed second is not counted twice. Completed time goals stop collecting
  intervals and retain their achieved total.
- The top Saved area has one quiet aggregate ring and a status hover. The
  Manuscript footer has compact individual progress, with horizontal scrolling
  under constraint. Achieved goals become green checks, then the text contracts.
  Focus mode uses quiet text/checks at the bottom without extra rings.
- The modal shows recent tracked goal periods and achievement frequency, retaining
  at most 240 period snapshots and displaying the latest 30. Periods when the app
  never runs are not manufactured as missed-goal records.
- Banner and chime are independently off by default. They fire once per newly
  achieved goal period (one combined sound for simultaneous achievements).
  The six supplied MP3s are bundled for offline use: Moss, Fern, Oak, Palm,
  Monstera and Pothos. Preview is explicit. No external notifications or messages.
- All new UI text is localized in en, es, fr, it, ja, pt-BR and zh-CN.

## Ownership and durability

`static/js/features/projects/targets-model.js` owns pure recurrence/counting rules;
`targets-journal.js` owns the shared durable outbox; `targets.js` owns the feature
lifecycle. The shared Manuscript modal, structure switch, delayed hover, toast and
theme variables are reused. The four additions
in `core/history.js` publish successful prose Undo/Redo snapshots; Targets never
writes manuscript content. The existing collaborator UI-state store is canonical.
Local keys additionally include the local profile identity.
The existing local schema contract excludes `ui_state` from manuscript generation
and History restore; hosted member-state generations also remain separate from the
project generation. Tests verify that successful/rejected Targets writes leave the
project generation unchanged.

Pending operations enter a synchronous localStorage pre-journal, then a shared
IndexedDB outbox. One IDB transaction allocates a persistent per-device/project/
profile sequence and stores its immutable operation. A newly opened tab recovers
both committed IDB records and interrupted pre-journal records; recovery does not
depend on the original tab successfully sending a pagehide or blur request.
Acknowledgment removes the pre-journal before its IDB identity. If cleanup fails,
the identity is retained to prevent an old pre-journal from becoming a new write.

The canonical document retains per-stream sequence receipts independently of its
bounded 80-ID legacy/preview window. Replaying an older recovered operation cannot
increase progress again. Stream gaps fail closed rather than discard missing work.
Captured goals and calendar windows travel with each contribution: late writing
for a retired goal updates that goal's historical period, never a replacement goal.
Explicit settings saves use last-accepted-save semantics while retaining progress
and history from other streams.

Targets GET responses include a value revision. PUT requires the expected revision;
missing tokens return 428 and stale tokens return 409. Local SQLite checks inside
the existing `BEGIN IMMEDIATE` transaction. Hosted Firestore checks in the member
state transaction; encrypted mode also rechecks after outside-transaction sealing.
On conflict the browser rereads, reapplies unacknowledged operations, and retries
up to eight times. It cannot overwrite another tab/device's intervening progress.
Ordinary UI preference routes retain their previous replacement behavior. Targets
alone has a 256 KiB value ceiling for compact interval unions; oversized records
are rejected before mutation. No storage schema or manuscript transaction changes.

Enabled progress syncs every 15 seconds; Save, focus, blur and pagehide also request
a sync. Disabled goals do not poll in the background. Idle word-only goals do not
generate clock writes. The existing hosted negative
account precondition prevents redirecting an old draft into a replacement account.
Recovery assumes the browser retains usable site storage. Clearing site data or
closing a private browser context discards that browser's unacknowledged outbox.
Time after the last recorded heartbeat (at most roughly one second under normal
scheduling) cannot be reconstructed safely and is not invented on reopening.

## Verification and release follow-up

Focused model and actual local/hosted browser checks cover defaults, goal editing,
real writing and completion, save/refresh, history, disabling, 390/820/1360px modal
layout, response-loss retry, Undo/Redo, custom paragraphs, active/idle timing and
blur cleanup. Pure rules also cover duplicate operations, DST, leap February,
Monday/month rollovers, Unicode and bounded history. Light desktop and light/dark
390px phone screenshots were inspected internally. The seven locale catalogs and
release identity preflight pass. Audio was never played aloud during development;
browser audio tests replace playback with a silent stub.

The durability follow-up passed 32 focused checks: 28 model/UI/integrity/encrypted
member-state cases together, two WebKit pre-journal recovery cases, and two
simultaneous-tab cases sharing one atomic IDB sequence. A forced
cross-device race with overlapping timer intervals passed in both runtimes. The
existing integrated feature smoke passed all 21 local/hosted cases with no skips
or recovered failures; report: `/tmp/breadmaker-targets-feature-smoke.json`.
These tests use synthetic hosted Firestore/GCS adapters and disposable local
projects. They do not establish live provider/deployment or real iPhone acceptance.

Before release:

1. Run the full `./verify-release` gate after integration; the focused checks do
   not replace it. Reconcile the version/cache tags with the parallel phone release.
2. Check actual iPhone Safari focus/background/keyboard behavior and chime permission.
   Headless Chromium can report focus in multiple offscreen windows, so that test
   drives the real blur handler while controlling `document.hasFocus()` explicitly.
3. Structural History/Cuts restore and Find/Replace are not credited as writing
   goals; ordinary prose Undo/Redo is. Confirm whether those bulk operations should
   affect net writing totals before expanding their activity accounting.

No new cloud service, schema migration, paid resource, publishing step, out-of-app
notification, or manuscript storage dependency was introduced.
