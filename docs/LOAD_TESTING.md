# Low-cost k6 load testing

Run OSS k6 on your machine against a runner-owned, disposable Breadmaker HTTP
server. There is no Grafana Cloud subscription, production URL option, GCP
credential, cloud provisioning, or paid CI job in this workflow. Provider cost
for these local runs is **USD 0**; existing machine/electricity costs remain.
The normal accountless, offline local editor is unchanged.

## Quick start

Install the repository's pinned test dependencies in your existing test virtual
environment and install [OSS k6](https://grafana.com/docs/k6/latest/set-up/install-k6/)
locally. The harness does not install anything. The initial baseline used k6
1.6.1; use that version when comparing its results. Do not silently upgrade the
toolchain between measurements.

```sh
export BREADMAKER_TEST_PYTHON=/absolute/path/to/test-venv/bin/python
./load-test --plan
./load-test --profile smoke --vus 2 --users 2 --chapters 2 --blocks 5
./load-test --profile concurrency --vus 1 5 10 20 --users 20 --seconds 30 \
  --results .loadtest-results/concurrency
python3 tools/loadtest/report.py .loadtest-results/concurrency
```

Smoke executes ten actions per VU, covering every operation. Concurrency uses a
closed model with one paced action per user per second. This models separate
people working in their own projects, with operations offset across users.
It does not model several collaborators editing the same project.

Check sustained arrivals separately: closed workloads reduce offered traffic
when responses slow down, which can hide saturation. The arrival profile keeps
a fixed arrival rate, has a fixed VU ceiling, and fails on dropped iterations.

```sh
./load-test --profile arrival --vus 10 --users 20 --rate 5 --seconds 30 \
  --results .loadtest-results/arrival
python3 tools/loadtest/report.py .loadtest-results/arrival
```

Run independent experiments for database population and project size. Keep the
other dimensions constant so the cause of any change is clear:

```sh
# More accounts/projects; same per-project content and active users.
./load-test --profile concurrency --vus 20 --users 100 --seconds 30 \
  --results .loadtest-results/population
# Larger projects: 4 x 64 paragraphs x 240 ASCII bytes per project.
./load-test --profile concurrency --vus 20 --users 20 --chapters 4 --blocks 64 \
  --seconds 30 --results .loadtest-results/larger-projects
```

Defaults seed 20 distinct accounts, one project each, two chapters per project,
20 paragraphs per chapter and 240 ASCII bytes per paragraph. Every seeded user
has a real test session and canonical project/membership records. Every VU gets
its own user, project and cookie. Seeding uses the hosted repositories and atomic
chapter mutation service before traffic starts; seeding time is excluded from
k6 latency and application CPU measurements. During seeding only, redundant
per-transaction memory rollback copies are omitted: a seed failure discards the
entire process/store before serving any request. Timed traffic restores the
normal driver's transaction, lock and rollback behavior. All content is synthetic prose.

## What is exercised

Each ten-action cycle makes fifteen HTTP calls: five chapter reads, one project
navigation read, one maps read, two chapter saves (read, atomic replacement with
revision CAS, read-back verification), and one map create/delete pair. This is
11 reads and four mutations, approximately 73% / 27%. Saves alter one paragraph
in an otherwise representative chapter. There are no automatic retries. Each
run also verifies anonymous denial and denial of another user's project.

The server is the real FastAPI app, one Uvicorn worker, with the real hosted
middleware, server-derived account/project authority, model validation,
generation checks, chapter saves, map mutations and HTTP serialization. The
backing adapters are **MemoryDocumentStore and MemoryObjectStore**, and login
uses synthetic opaque test sessions. No Firebase login occurs.

This is an application regression baseline, **not a Firestore or production
capacity benchmark**. The memory driver serializes transactions behind one
process lock, deep-copies the whole database for transaction rollback, and scans
the whole store for collection queries. Population-dependent slowdown can be a
test-adapter artifact. Do not use it to infer a production user limit or modify
production scaling. There is no network/region latency, actual Firestore index
behavior, provider transaction retry, production KMS encryption, GCS durability,
Cloud Run cold start/autoscaling, browser rendering, narration inference, media
upload, backup or recovery in these results. Test requests are HTTP/1.1 on the
same host as the load generator; CPU is not constrained to a production vCPU.

## Limits and cost controls

- Only the exact IPv4 loopback address created by the runner is accepted. There
  is no remote-target switch. HTTP redirects are disabled and requests time out
  after five seconds. The runner disables k6's control API and supplies an empty
  explicit config; credentials, proxies, k6 outputs and cloud settings are not
  inherited by either child.
- At most 20 VUs, 100 seeded users, eight chapters/project, 128 blocks/chapter,
  2,000 bytes/block and 20,000 blocks total. At most five distinct VU levels.
  Each level gets a fresh server and fresh data.
- Timed runs are capped at 120 seconds. Each VU starts at most one action/second,
  and each action makes at most three requests: at most 60 workload HTTP
  requests/second and `3 * VUs * (seconds + 1)` requests per timed run. Smoke is
  capped at `30 * VUs` requests and a 60-second executor deadline. Health checks
  and three preflight requests are additional local traffic.
- Seeding/startup has a 120-second watchdog, and k6 has an independent process
  watchdog. Error thresholds can stop early. The supervisor terminates/reaps
  its own children, closes the socket, and deletes the temporary home, manifest,
  cookies and data after success or failure. A forced interruption can omit the
  final server measurements; this is reported as a failed run.
- The harness changes no production limits, IAM, network ingress, database,
  billing or infrastructure. Hosted request quotas remain at application
  defaults; a 429 counts as an error, not a successful load-test response.

The private infrastructure source reviewed for planning uses 1 vCPU/512 MiB,
concurrency 20, zero minimum and two maximum application instances. Those are
source defaults, not a fresh attestation of deployed configuration. Its monthly
budget resource emits alerts at 50%, 80%, 100% and forecast 100%; **budget alerts
do not impose a spending cap**. They are not a substitute for bounded load.
Existing operator budget and approval boundaries are unchanged. Nothing in this
workflow authorizes additional persistent security access or a new environment.

## Latency gates and diagnosis

k6 produces p50/median, p90, p95, p99, max, error/check rates, throughput,
completed actions, 409 conflicts, 429 throttles and dropped arrival iterations.
Each endpoint has its own timing and request count in the JSON summary.
These initial thresholds are engineering regression targets, not a published
SLA, and should be calibrated against a representative isolated hosted baseline:

| Operation | p95 | p99 |
| --- | --- | --- |
| Reads and map delete | <500 ms | <1,000 ms |
| Atomic chapter save | <1,000 ms | <2,000 ms |
| Map create | <1,500 ms | <3,000 ms |

Every operation must have samples, all semantic checks must pass, errors must
stay below 1%, and arrival tests must drop zero iterations. The application-error
threshold aborts a bad run once evaluated after ten seconds. k6 exit codes and
failed thresholds are retained; failures are never converted to green reports.

`server-observations.json` records adapter get/query/transaction and transaction
method counts, returned documents, memory-scan counts, aggregate/max elapsed
time, app-process CPU seconds, peak RSS and final stored row/JSON volume. Query
scan counts describe the memory adapter only. Transaction elapsed time includes
lock wait and rollback copies. Timings overlap and are not additive. Logical
calls are **not billable Firestore operations** or exact Firestore retry counts.
The wrapper adds measurement overhead. Peak RSS includes seeding.

Use the evidence in this order:

1. Check 401/403/404 preflight, semantic failures, 409 and 429 counts; invalid or
   throttled traffic must not masquerade as successful capacity.
2. Compare per-endpoint p95/p99, action duration, throughput and dropped arrivals.
   A slow save with fast reads differs from every request queueing.
3. Compare app CPU/RSS and transaction/query time across otherwise equal runs.
   Large memory scans or rollback-copy costs explain fixture limitations; they
   require confirmation on Firestore before product performance conclusions.
4. For hosted validation, correlate a synthetic run identifier with existing
   request latency, status, Cloud Run CPU/memory/concurrency/startup/instance
   metrics and sampled Firestore RPC/query timings. Keep texts, titles, cookies
   and user identifiers out of telemetry. Prefer existing bounded metrics over
   verbose per-document logging. No monitoring resource is activated here.

Short tests are exploratory: fewer than 100 samples for an endpoint make p99
especially unstable. Use repeated runs on an otherwise quiet machine, compare
the same dataset/profile/tool versions, and keep latency evidence separate from
release correctness checks. A 30-second loop is not a soak test or a guarantee
that future incidents cannot occur.

## Local-first regression workflow

Before merging backend/storage changes, run focused correctness tests, the
two-user smoke, then the small concurrency/arrival experiments when relevant.
Run the full existing release gate for a release-ready candidate. This tooling
does not replace it and does not modify production CI or spend Actions minutes.
Keep generated evidence outside git (`.loadtest-results/` is ignored).

```sh
# A prior, passing result from the same machine and workload is required.
python3 tools/loadtest/report.py .loadtest-results/current \
  --compare .loadtest-results/previous
```

The comparison fails if any endpoint p95 increases by both more than 20% and
more than 20 ms. It rejects mismatched populations, profiles, durations, arrival
rates, k6 versions or platforms and requires matching passing VU runs. Absolute
thresholds still apply even when no earlier baseline exists. The reporter writes
a sanitized `report.json` beside the raw summaries. Do not compare the small
population baseline with the larger-project experiment as a code regression.

## Next isolated hosted validation

This repository intentionally cannot point k6 at production. Before a hosted
experiment, use the private infrastructure repository to review a disposable
design compatible with the existing single-production policy. Do not create a
persistent staging environment or broaden IAM/ingress as a side effect. Any
required access change needs its specific confirmation. The next lower-cost
option is a credential-free local Firestore emulator with the real Google
adapter; it is not installed or measured by this harness yet.

For an explicitly authorized disposable hosted experiment: pin the app/image
SHA and region, synthetic accounts and dataset; verify synthetic-only targeting
and isolation; inventory current grants before changing access; cap CPU,
instances, traffic, duration and total requests; exclude inference/media/backup
jobs; measure seed plus workload plus cleanup operations; prove cleanup and
remove only run-owned resources. Prefer zero minimum instances and no external
load-testing service. Start with one instance, 1 vCPU/512 MiB, <=10 VUs, <=5
actions/s for <=120 seconds. Test cold starts separately from warm latency.

Illustrative **USD** variable costs, before free tier, tax, exchange rate,
storage/egress, logs, builds/images, GCS, KMS, identity, startup/shutdown or
cleanup overhead: 120 active vCPU-seconds at $0.000024 plus 60 GiB-seconds at
$0.0000025, plus 1,800 requests at $0.40/million is about **$0.00375** for Cloud
Run. A hypothetical 100,000 document reads, 10,000 writes and 10,000 deletes at
Firestore Standard us-central1 rates ($0.03/$0.09/$0.01 per 100,000) adds about
**$0.040**. These are planning examples, not measured operation counts or an
all-in quote. A conservative proposed all-in experiment allocation is **$1**,
including seeding, cleanup and overhead, subject to an actual operation/egress
estimate and remaining shared budget. If that bound cannot be substantiated,
stay local. Do not assume free tier availability or treat a shared project
budget as money dedicated entirely to load tests.

Pricing checked 5 November 2026: [Cloud Run pricing](https://cloud.google.com/run/pricing),
[Firestore pricing](https://cloud.google.com/firestore/pricing). k6 references:
[thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/),
[constant arrival rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/).
