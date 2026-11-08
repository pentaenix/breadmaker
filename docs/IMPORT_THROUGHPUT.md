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
concurrency per preparation request can double; total RPC count and provider cost
do not fall. Production quota/latency observation is required before claiming
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
- Every variant: 6,980 authority checks, 1,745 writes, 1,745 verified reads, with
  identical returned hashes.

For a uniform-latency control (no injected stragglers), run the same command with
`--straggler-every 0`. That run produced fixed eight 1.9326 seconds, refilled eight
1.8636 seconds, and refilled sixteen 1.0186 seconds, with the same RPC counts and
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
passed, followed by 96 native import and browser-contract tests passed. The full
release verifier and private Cloud verification have not been run for this
checkpoint. No version bump, push, deployment, or live re-import was performed.
