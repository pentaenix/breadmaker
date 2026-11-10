# Native package import throughput checkpoint

This is a source-only optimization, not a production speed claim or release.

## Bottleneck and change

Native imports deliberately retain a canonical object per hybrid record for
rollback compatibility. Publishing each small object requires four fresh
before/after authority checks, one conditional object write, and one verified
media read. A paragraph bundle is an additional read accelerator, not a
replacement for canonical objects. These operations are not removed or cached.

Previously each 64-item step prepared records in fixed waves of eight. One slow
object held up the next wave even when seven threads were idle. The scheduler
now continuously refills completed slots, preserves input/result ordering, and
checks all completed results before submitting more work. Every submitted task
gets its own copied request context. Failures cancel queued work; running work
still finishes under its original before/after authority checks.

Record preparation is bounded at 16 workers. Plan uploads remain bounded at eight
and preserve their durable eight-plan preparation checkpoints. Batch size,
receipt chains, cancellation/expiry/account checks, object integrity checks,
HTTP admission limits, and atomic final visibility are unchanged. Peak provider
concurrency per preparation request can double; the logical adapter-operation count does not fall. Physical RPC counts and
provider cost are unmeasured: provider writes can reload metadata, and Firestore
transactions can involve additional calls and retries. Production quota/latency observation is required before claiming
an end-to-end gain. No production import was repeated for this work.

## Reproduce the synthetic benchmark

```
python scripts/benchmark_package_import_throughput.py --objects 1745 --latency-ms 1
```

The benchmark publishes synthetic paragraphs to an in-memory object adapter,
including all four guard callbacks and actual local integrity verification. It
injects one millisecond into each guard/object operation and an additional bounded
straggler every eight objects. It preserves 64-object step boundaries. The extra
straggler is a controlled stress scenario, not measured provider behavior.

One Mac run produced:

- Previous fixed eight: 3.4495 seconds.
- Refilled eight: 2.0739 seconds.
- Refilled sixteen: 1.1598 seconds.
- Every variant: 6,980 authority guard calls, 1,745 object-write adapter calls, 1,745
  verified-read adapter calls, with
  identical returned hashes.

For a uniform-latency control (no injected stragglers), run the same command with
`--straggler-every 0`. That run produced fixed eight 1.9326 seconds, refilled eight
1.8636 seconds, and refilled sixteen 1.0186 seconds, with the same logical-operation counts and
hashes. Refilling alone helped only about 4% in that distribution; the larger
uniform-case gain came from doubled bounded concurrency. The straggler interval
and multiplier are independently configurable via `--straggler-every` and
`--straggler-factor`.

This roughly 2.97x synthetic improvement is not a prediction for network upload,
whole-project import, deployment cold starts, or production Firestore/GCS.
The real fixture's earlier approximately 192-second import creation time is a
baseline only; there is no after-change production measurement.

## Verification

Scheduler regressions cover bounded concurrency, continuous refill without
head-of-line blocking, ordered results, context isolation, empty input, invalid
worker bounds, and failure stopping the unsubmitted suffix. Existing fresh-guard
and durable-import tests remain unchanged. A full release gate is still required
before release; focused tests do not establish release readiness.

Focused checks on this checkpoint: 37 scheduler/fast-read/durable-import tests
passed, followed by 96 native import and browser-contract tests passed. These focused checks used an existing development environment. A subsequent
unchanged full verifier required a fresh exactly pinned environment. Its
preflight/audits, 434 unit items, and 92 durable E2E items passed; Core/API had
293/345 execution jobs pass and 52 fail. The browser stage reached 79/373 jobs
(38 passed, 41 failed) before the doomed run was stopped under severe Mac load.
One inspected failure was a hard 10-second fixture-setup timeout while importing
app/workspace code, before the test assertion or this import scheduler ran.
Resource pressure is a hypothesis, not proof that every failure is environmental.
The aggregate gate is FAILED/INCOMPLETE, not green. Public exact-commit CI must
pass before release. No timeout or verifier was modified. Private Cloud
verification, deployment, and live re-import have not been performed.
