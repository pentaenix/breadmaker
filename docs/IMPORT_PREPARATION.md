# Bounded native import preparation

This source checkpoint does not establish production import throughput or
release readiness. It changes request boundaries, not timeout or integrity
policy.

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

## Change and preserved guarantees

- Each begin call now publishes at most one existing eight-plan group and
  returns the committed `prepared_batches` cursor. The browser yields and
  continues the same source and idempotency key until staging is available.
- The existing modal, cancellation, ownership, and session-recovery patterns are
  reused. Failed HTTP calls and stalled/malformed preparation responses pause
  for explicit Resume. They do not start automatic retries or a new job.
- Status and cancellation have a separate two-request control lane; expensive
  operations remain limited to two requests. Neither lane is unbounded.
- Complete archive validation, complete-plan hash pinning, immutable object
  generation/size/hash verification, fresh authority checks, expiry, erasure
  fencing, receipt chains, and atomic final project visibility are unchanged.
- Committed source-plan prefixes are not rewritten on replay. An interrupted
  uncommitted group may repeat conditional writes, with the original immutable
  verification safeguards intact.

## Limitations and rollout

This bounds provider work per preparation request, not its wall-clock duration.
The complete archive is still uploaded, parsed, and hashed on each preparation
continuation. Slow provider calls or expensive pure planning can still exceed
an HTTP boundary. The existing parser's SQLite instruction/time budget does not
provide a deadline for all Python processing. No timeout was increased.

Old browser code may pause at its first staging call when a newer server returns
`preparing`; a refreshed current browser resumes the same job. New browser code
also accepts the older server's direct `staging` response. Mixed deployment is
safe for data, but this source change does not guarantee uninterrupted progress
for stale clients.

Measure pure planning separately on an authorized local archive:

```sh
python scripts/benchmark_package_import_preparation.py /path/to/synthetic.breadmaker
python scripts/benchmark_package_import_preparation.py /path/to/synthetic.breadmaker --profile
```

The script calls the same pure validator/planner, makes no provider operations,
and prints only timing/counts plus optional function statistics. Profiled timing
includes instrumentation overhead. It does not exercise hosted admission,
network upload, object publication, record staging, or final visibility.

If parsing itself approaches the HTTP limit, the next design must durably pin a
validated source and introduce bounded preparation cursors with full validation,
cross-replica replay, erasure/cancellation accounting, and unchanged final
visibility. Merely increasing timeouts or skipping validation is not a solution.

## Verification required

Synthetic backend tests cover one-group boundaries, cross-replica replay, no
duplicate committed plan writes, changed suffix rejection, invalid-source
rejection before reservation, preparation cancellation, and status/cancel during
expensive-lane saturation. Browser tests cover multiple groups, failure/Resume,
cancellation, and a stalled response, alongside existing lifecycle tests.

Run those tests and the unchanged complete release gate on the final commit.
Do not claim the large production fixture is fixed without approved end-to-end
measurement on the deployed revision.
