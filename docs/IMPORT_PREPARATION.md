# Durable native import preparation

This source checkpoint retains the validated canonical plan for file-free
continuations. Local measurements below are not production throughput or
release readiness. No timeout or integrity policy was weakened.

## Diagnosis

`PackageImport.begin` validates and plans the complete archive before creating
the job, then pins the complete plan hash. Previously it published every plan
group before returning. The durable eight-plan checkpoints made replay safe,
but did not make the HTTP request bounded by a single group. Replaying after a
timeout parsed the complete archive again before skipping the committed prefix.

The earlier resumable-import branch is included in native release PR #41.
Import-throughput PR #42 builds on that release and changes record-preparation
scheduling/concurrency; it does not fix the all-groups preparation request.

All import endpoints also shared two expensive-request slots. If two lengthy
preparation calls occupied them, even status and cancellation waited. Thread
offloading already protects the event loop; admission contention is a distinct
failure mode. A proxy timeout does not prove that its worker stopped.

## Durable capture and continuation

- Begin validates the complete original archive before reserving the job, then
  pins the complete plan fingerprint before publishing content. It groups existing plan
  batches into immutable snapshots targeting 16 MiB decoded per chunk. A valid
  larger batch remains whole under the existing batch limit plus bounded
  snapshot-wrapper overhead; no product payload is rewritten or truncated.
- A capture request publishes at most eight objects. Captures fitting eight
  chunks retain only the canonical snapshots, not the original archive. Larger
  captures retain the already validated original once and use seven snapshot
  slots per request, including on retries. Additional capture requests can
  rebuild from that verified server-held source without browser retransmission.
- Snapshot encoding/publication uses one worker per capture request to limit
  simultaneous JSON, gzip and provider-spool allocations. The complete 7/8-item
  group and atomic cursor commit stay unchanged. Ordinary plan publication and
  record preparation retain their existing eight/sixteen-worker limits.
- Once capture is complete, `POST .../jobs/{job_id}/prepare` reads only the
  required immutable snapshot chunk and publishes at most eight ordinary v1
  plan files. It does not parse the original archive. The existing per-record
  staging and final publication formats are unchanged.
- Snapshot layout, source hash, actor, job, project, operation, format and
  complete-plan fingerprint are pinned. Exact encoded size, generation, SHA,
  bounded decoded size, canonical JSON and wrapper/batch shapes are checked.
  Individual descriptors stay outside the small job document.
- The existing modal, cancellation, ownership, and session-recovery patterns are
  reused. Failed HTTP calls and stalled/malformed preparation responses pause
  for explicit Resume. They do not start automatic retries or a new job.
- Status, cancellation and constant-size atomic finish have a separate two-request control lane; expensive
  operations remain limited to two requests. Neither lane is unbounded.
- Complete archive validation, complete-plan hash pinning, immutable object
  generation/size/hash verification, fresh authority checks, expiry, erasure
  fencing, receipt chains, and atomic final project visibility are unchanged.
- Committed source-plan prefixes are not rewritten on replay. An interrupted
  uncommitted group may repeat conditional writes, with the original immutable
  verification safeguards intact. When legacy and snapshot preparation overlap
  at different group boundaries, a missing suffix can commit only after every
  existing prefix descriptor and the exact prefix chain match.
- Retained reads use the provider's existing guarded verified-read path up to
  2 MiB. Larger reads use 4 MiB chunks with the same generation, exact length,
  full SHA and fresh before/after authority checks. There is no fallback after
  an integrity failure and no process-local job or persistent content cache.

## Limitations and rollout

This bounds provider work per request, not wall-clock duration. Initial complete
validation and snapshot encoding still run synchronously in the bounded worker
lane. Captures larger than one group repeat native planning for additional
capture groups, but do not reupload the original from the browser; subsequent
ordinary preparation never reparses it. Slow provider calls or expensive pure
planning can still exceed an HTTP boundary. The parser's existing SQLite budget
does not impose a deadline on all Python processing.

Current browser code explicitly requests `preparation_version=3`. An open old
tab receives a clear refresh-and-reselect-the-same-file response before any
preparation work, rather than repeatedly advancing one group then failing its
staging call. Refresh preserves the source-bound idempotency key and resumes the
same job. Pre-version-gate servers can ignore the extra query and return their
legacy result. Protocol-2 servers explicitly reject protocol 3 and lack the new
prepare route: mixed routing or rollback can therefore pause safely until the
client reaches a coherent supported revision. Refresh alone cannot upgrade a
server. Missing routes retain the job/key and never trigger a duplicate import.
Existing v1 jobs can upgrade from their committed prefix. Jobs already in staging
remain readable by older servers because ordinary plan/receipt formats did not
change. A deployment must not claim seamless mixed-version progress.

## Local fixture measurement

An authorized local fixture contained 11,445,734 archive bytes and produced
28,937 records in 468 plan batches. One memory-adapter run produced:

- Native planner: one call, 6.167632 seconds.
- Begin, including six snapshot chunks: 10.502041 seconds.
- File-free continuation: 62 calls, 28.721649 seconds total, 0.623343 seconds max.
- Total preparation: 39.223851 seconds.
- Retained snapshots: 20,594,616 bytes; original archive: zero retained bytes.
- Existing ordinary plan files: 20,856,500 bytes.
- All 468 prepared batches rehashed to the pinned full-plan fingerprint.
- Zero live provider operations and no project publication.

Chunk boundaries can yield fewer than eight plans in a request, hence 62 calls
rather than the theoretical minimum 59. This measures real validation/capture/
continuation code with explicit in-memory document/object ports, not network
upload, provider RPC latency, record staging or final publication. It is not a
before/after production speed claim. A preceding local run took 47.76 seconds;
local variation and subsequent read refinements are not an isolated A/B test.
Physical provider-request effects remain unmeasured.

These timings precede the capture-only one-worker memory refinement. Remeasure
begin latency and peak RSS on the final revision before production recovery.
Infrastructure source configures 512 MiB and one CPU; compressed object size is
not RSS evidence. The parsed complete plan, JSON intermediates and uploads read
before admission can coexist, and a valid oversized batch can exceed the 16 MiB
chunk target. One capture worker does not guarantee a process-wide memory bound.

Measure pure planning separately on an authorized local archive:

```sh
python scripts/benchmark_package_import_preparation.py /path/to/synthetic.breadmaker
python scripts/benchmark_package_import_preparation.py /path/to/synthetic.breadmaker --profile
python scripts/benchmark_durable_package_preparation.py /path/to/synthetic.breadmaker
```

The script calls the same pure validator/planner, makes no provider operations,
and prints only timing/counts plus optional function statistics. Profiled timing
includes instrumentation overhead. It does not exercise hosted admission,
network upload, object publication, record staging, or final visibility.

## Retention and incremental cost

All snapshot/source objects stay under the existing project/import prefix. Exact
new metadata shapes are classified in account erasure census; unknown formats
remain blockers. Cancellation and 24-hour expiry fence continuation but do not
purge bytes. Even a snapshot-only failed import remains discoverable. No delete,
TTL, bucket, queue, identity or new permission is introduced.

The reviewed infrastructure source defaults to regional us-central1 Standard
storage, versioning and 30-day soft-delete, with runtime objectCreator/viewer but
no delete permission or automatic object lifecycle rule. This is source evidence,
not live configuration attestation. Physical cleanup needs independent retention,
upload-quiescence, historical-version and authorized purge gates; cleanup tests
here verify accounting and absence of unauthorized deletion.

At approximately $0.020/GiB-month, this fixture's extra snapshots cost about
$0.000384 per retained import-month, $0.0384 for 100, or $0.384 for 1,000, before
shared free-tier credits. A separate maximum-size retained original adds at most
64 MiB, approximately $0.00125/month. Storage accumulates without cleanup.

Regional Class A writes are $0.005/1,000 and Class B reads $0.0004/1,000. The SDK
can issue metadata and multiple media GETs, so logical adapter counts are not
physical RPC counts. Existing canonical objects, Firestore guards/transactions,
Cloud Run duration, continuation reads, logging, network and current free-tier
consumption are excluded. These estimates do not guarantee a whole-app $10 cap.

Sources: [Cloud Storage pricing](https://cloud.google.com/storage/pricing),
[storage class defaults](https://docs.cloud.google.com/storage/docs/storage-classes),
[shared free-tier conditions](https://docs.cloud.google.com/free/docs/free-cloud-features#storage).

## Verification required

Synthetic tests cover parse-once continuation, replicas/lost responses, bounded
capture, retained-source fallback, concurrent capture and partial-prefix races,
generation/digest corruption, wrong bindings/versions, cancellation/expiry/
erasure, legacy upgrades, full fingerprints, and retention census. Browser tests
cover file-free continuations, reload/Resume, cancellation, dismissal, stalled
responses, explicit stale-client recovery and missing-route safe pauses.

Run those tests and the unchanged complete release gate on the final commit.
Do not claim the large production fixture is fixed without approved end-to-end
measurement on the deployed revision.
