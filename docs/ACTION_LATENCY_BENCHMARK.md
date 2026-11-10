# Action latency coverage and controlled benchmark

## Scope and reproduction

`scripts/benchmark_action_latency.py --output /absolute/path/results.json` runs
against the real hosted-dev app using memory-backed storage, synthetic text, a
fresh headless Chromium profile, and an ephemeral loopback server. It does not
use production or the user's browser profile. Run it with the installed test
Python environment, outside the release suite. Startup has a standalone 60-second
bound for seeding the large fixture; existing suite watchdogs are unchanged.
The server/browser are closed in `finally`, and temporary fixture files removed.

The fixture has a 34-chapter novel, 68 scenes, 1,360 generated prose paragraphs,
an initial blank paragraph, and a separate one-paragraph guide tab. Paragraphs
contain repeated synthetic text, so this is a size/structure fixture, not a
semantic substitute for a real novel.

Completion clocks use `time.perf_counter()` around real browser inputs and an
explicit view/editing gate or content assertion. Autosave completion requires
`dirty == false` and no save in flight. All three chapter writes are verified as
PUT requests, and changed text must survive reload. Input-to-two-animation-frame
measurements are a rendering-scheduling proxy, not actual paint timing or INP.
Request lists include background traffic occurring within the window and should
not be interpreted as a pure causal waterfall.

## November 10, 2026 controlled sample

Source: f6217eb plus task read coalescing in this change. No injected network
latency. Most repeated actions have three samples; cold actions have one.

| Action | Milliseconds | Completion condition |
|---|---:|---|
| Cold project opening | 691 | Initial chapter editable |
| Cold Full Text | 3,538 | Complete book, no load, editing enabled |
| Cached Full Text to chapter | 84 / 97 / 105 | Correct chapter, editing enabled |
| Cached chapter to Full Text | 168 / 143 / 151 | Complete book, editing enabled |
| First guide tab opening | 440 | Guide text and editing enabled |
| Repeated guide opening | 88 / 112 | Guide text and editing enabled |
| Guide to cached novel | 161 / 178 / 181 | Complete book, editing enabled |
| Cached Full Text to scene | 86 | Correct scene and editing enabled |
| Type 27-character sample | 22 / 20 / 22 | Changed text visible |
| Autosave after typing | 799 / 812 / 793 | Actual write completed, clean state |
| Reload changed chapter | 582 | Changed text survives reload |
| Open Tasks | 42 | Task creation control visible |
| Create task | 385 / 389 / 390 | Created task visible |
| Delete disposable task | 408 / 397 / 408 | Task absent |
| Undo task deletion | 399 / 376 / 384 | Task restored |
| Redo task deletion | 377 / 363 / 378 | Task absent again |

82 input events, including preparation: median 25.1 ms and maximum 33.6 ms to
two animation frames. Zero uncaught page errors. These are local observations,
not production estimates, performance budgets, or a statistically robust p95.

## Verified optimization

Structural task Undo/Redo entered Tasks and explicitly refreshed it while the
first uncached list request was still pending, causing two identical GETs.
The task cache now shares a pending read within the current project and cache
epoch. Invalidation and forced refresh advance the epoch; stale results cannot
refill the cache. Rejections clear the pending request so a later attempt retries.

All six large-fixture Undo/Redo samples changed from two task-list requests to
one. Local end-to-end latency did not show a clear improvement; the proven
benefit is eliminating a redundant backend request. No change to mutations,
Undo durability, or authorization checks.

Regression tests cover coalescing, ordinary cached reuse, mutation invalidation,
late results, project switches, forced reads, failure/retry, and actual browser
Undo/Redo restoration. The existing hosted first-write/workspace test also passes.

## Scene-save reliability fix

On f6217eb, editing prose in scene view returned HTTP 409
`HOSTED_ATOMIC_BULK_OPERATION_UNAVAILABLE`, leaving `Save failed` and dirty state.
This was reproduced both in hosted-dev and in an explicitly authorized tiny
production fixture. Two live scene PUT requests returned 409; reload retained
original text. The deployed source confirms the intentional unsupported route.
The generic 409 UI incorrectly attributed this to another browser tab.

This change implements bounded atomic in-place scene prose saves: revision
checks, current membership/actor checks, immutable object staging, a single
publication transaction, idempotent receipts, and durable Undo/Redo. Browser
regressions cover direct scene editing and cached Full Text-to-scene editing,
including reload and preservation of untouched text. Structural inserts,
deletes, reorders, private codecs and reference reanchoring remain unsupported;
this is not a claim that every scene editing operation is now implemented.

## Production observations and remaining coverage

Read-only Chrome samples on the retained 67,655-word Treasure Island fixture:
- Warm guide-to-novel Full Text: 797 / 719 / 750 ms.
- Warm novel-to-guide: 620 / 606 / 613 ms.
- Warm Canvas populated card: 100 / 83 / 73 ms.
- Warm Tasks populated row: 61 / 65 / 65 ms.
- Task search fill-to-filter: 50 / 30 / 32 ms; clear: 24 / 21 / 20 ms.

These include browser-control overhead. Cold and Notes-panel timings were
inconclusive because visibility tooling was inconsistent. No original fixture
content was edited or deleted, and no audio played.

Production was verified from read-only service and registry metadata to be on
Editor 54ec736, not the merged PR #44/f6217eb. Infrastructure identifiers are
retained only in the private investigation record.

A separate eight-record native fixture eventually published, but its finish
request timed out after 60 seconds; status recovery took another 35.7 seconds.
This is eventual recovery, not healthy import latency. Scene prose
saving failed as described above. An empty fixture's chapter prose saved.
Both disposable projects were moved to recoverable Trash after the test;
the original novel and durable reusable baseline were not modified. These
were functional checks, not robust end-to-end latency samples.

A larger native fixture failed during upload/preparation before any published
project was verified: initial HTTP 503 after 30.156 seconds; one built-in Resume
ended in HTTP 504 after 60.006 seconds. No further retry was attempted. The existing
preparation path can publish all preparation groups in one request; subsequent
staging throughput improvements do not remove that request-boundary problem.
JSON fragment import is explicitly unsupported in hosted mode and currently
produces an unhandled rejection rather than a clear failure notice.

## Coverage limits and priorities

1. Reliability before speed: atomic scene saving, accurate error presentation,
   and durable bounded large-import preparation.
2. Cold Full Text rendering is the largest measured local interaction delay;
   compare realistic production cold/warm data after deployment verification.
3. Task read coalescing removes redundant backend work with no claimed local
   end-to-end speedup.

Coverage remains representative, not every control: production chapter typing,
scene save and project creation/deletion were functional checks; controlled
latencies cover typing/autosave and task creation/deletion/Undo/Redo. Canvas/Map
creation, checkpoints, exports, reviews, mobile and broader failure recovery
need dedicated measurements. Do not generalize task deletion to other entity
deletion or local memory-backed saves to Firestore durability.
