# Optional Targets review checkpoint

Targets is implemented on isolated branch `feature/optional-targets`, based on
`ad196f5` (the phone touch/menu release candidate, Editor 10.5.80). This checkpoint
does not change the release version and is not deployed. Integrate it with the
current release branch before assigning the next version/cache identity.

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
`targets.js` owns the feature lifecycle. The shared Manuscript modal, structure
switch, delayed hover, toast and theme variables are reused. The four additions
in `core/history.js` publish successful prose Undo/Redo snapshots; Targets never
writes manuscript content. The existing collaborator UI-state store is canonical.
Local keys additionally include the local profile identity.

Pending operations are journaled in a tab-specific browser outbox and retried in
bounded batches. Browser Web Locks serialize tab writes. Stable operation IDs and
immutable attempted operations protect response-loss retries. Ordinary updates
flush every 15 seconds; Save, blur and pagehide also request a flush. Disabled or
word-only idle goals do not generate unnecessary timer writes. Hosted writes carry
a negative account precondition, so an account switch cannot redirect an older
Targets draft into the replacement account.

## Verification and release follow-up

Focused model and actual local/hosted browser checks cover defaults, goal editing,
real writing and completion, save/refresh, history, disabling, 390/820/1360px modal
layout, response-loss retry, Undo/Redo, custom paragraphs, active/idle timing and
blur cleanup. Pure rules also cover duplicate operations, DST, leap February,
Monday/month rollovers, Unicode and bounded history. Light desktop and light/dark
390px phone screenshots were inspected internally. The seven locale catalogs and
release identity preflight pass. Audio was never played aloud during development;
browser audio tests replace playback with a silent stub.

Before release:

1. Run the full `./verify-release` gate after integration; the focused checks do
   not replace it. Reconcile the version/cache tags with the parallel phone release.
2. Check actual iPhone Safari focus/background/keyboard behavior and chime permission.
   Headless Chromium can report focus in multiple offscreen windows, so that test
   drives the real blur handler while controlling `document.hasFocus()` explicitly.
3. Simultaneous **different-device** writes are not atomic: the current generic
   UI-state endpoint has no compare-and-set contract. Web Locks protect tabs in
   one browser, not different devices. If multi-device simultaneous tracking is a
   release requirement, add a server-side atomic merge/CAS before shipping.
4. The browser outbox survives refresh in the same tab. An abruptly destroyed tab
   can lose the final unflushed interval; outbox recovery by a new tab is not yet
   implemented. Saved project data and manuscript recovery are independent.
5. Structural History/Cuts restore and Find/Replace are not credited as writing
   goals; ordinary prose Undo/Redo is. Confirm whether those bulk operations should
   affect net writing totals before expanding their activity accounting.

No new cloud service, schema migration, paid resource, publishing step, out-of-app
notification, or manuscript storage dependency was introduced.
