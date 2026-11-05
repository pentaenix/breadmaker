# Low-cost k6 load testing

Run OSS k6 on your machine against a runner-owned, disposable Breadmaker HTTP
server. The default uses memory adapters; an explicit option runs the real
Google Firestore adapter against an owned local emulator. There is no Grafana
Cloud subscription, production URL option, GCP
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
default backing adapters are **MemoryDocumentStore and MemoryObjectStore**, and
login uses synthetic opaque test sessions. The optional emulator path uses the
real **GoogleFirestoreDocumentStore** and local gRPC. Object storage and login
remain synthetic in both paths. No Firebase login occurs.

## Firestore emulator validation

Use an existing Java 21+ executable and explicitly install the official emulator
JAR outside the repository. The harness never downloads or upgrades software.
It accepts only Firestore emulator **1.22.0**, exactly **136,707,194 bytes**, with
SHA-256 `9b6498b7f62714d67f48f59b3818883cd682dbcd46b9f59511de81c97bb5166c`.
The [official Firebase manifest](https://github.com/firebase/firebase-tools/blob/master/src/emulator/downloadableEmulatorInfo.json)
publishes the size, checksum and download URL. A mismatch fails before execution.

```sh
# One explicit local prerequisite download; no gcloud login or component update.
curl --fail --location --proto '=https' --tlsv1.2 --max-time 120 \
  --max-filesize 140000000 --output /absolute/local/path/firestore-1.22.0.jar \
  https://storage.googleapis.com/firebase-preview-drop/emulator/cloud-firestore-emulator-v1.22.0.jar

./load-test --adapter firestore-emulator \
  --emulator-jar /absolute/local/path/firestore-1.22.0.jar \
  --java /absolute/path/to/java21-or-later/bin/java \
  --profile smoke --users 2 --vus 2 --chapters 2 --blocks 5
```

The runner starts a new JVM for every VU level, caps its heap at 512 MiB and
reported active processors at two, and pins Standard Native mode. All listeners
bind IPv4 loopback. A fresh `demo-bm-k6-` project ID and strict single-project
mode prevent accidental reuse of a real project. Client Security Rules deny all
access; the Python server SDK uses the emulator's local administrative protocol.
This tests Breadmaker's application authorization, not production IAM or Rules.

The Python client explicitly receives AnonymousCredentials and an exact
`127.0.0.1:port` endpoint. The SDK must confirm its emulator transport before any
RPC. ADC, real project configuration, proxies, Java injection options, credentials
and remote outputs are excluded from child environments. There is no flag to
connect to a pre-existing emulator or external Firestore service.

After the fixture stops, the runner deletes only its fresh demo database through
the owned emulator endpoint, checks that users/projects are empty, terminates
and reaps the JVM, verifies that its API port is closed, then removes the
temporary directory. These outcomes are recorded in `status.json`; any unverified
cleanup fails the run. No import/export flag or persistent data directory is used.

The Google adapter activates the application's existing account lifecycle fence.
For an otherwise matched memory comparison, add `--lifecycle-fence` to a memory
run. The experiment and server observations record this difference, and regression
comparison rejects different adapters, fence settings, emulator versions or Java
runtimes. Changing an adapter is a separate experiment, not a code regression.
The original memory baseline remains an unfenced historical measurement.

For diagnosis, `--workload read-only` keeps only chapter reads, project navigation
and map listing. It uses the same session/authority boundary and applies read
thresholds without requiring mutation samples. Keep this distinct from a mixed
capacity test; regression comparison rejects different workloads.

`--workload single-writer` gives VU 1 the mixed cycle while the other VUs only
read. It isolates read traffic around one writing user from concurrent writers.
Its write rate is lower than `mixed`; it cannot establish multi-writer capacity
or substitute for a failed mixed run. Both modes keep the same latency/error
gates and bounds.

### Isolating emulator query locks

The inspected emulator/SDK combination reproduced transaction-lock aborts with
native SDK queries under five **disjoint parent paths** sharing the child
collection ID `blocks`. Returned document paths and recorded RunQuery parent
fields stayed scoped to each worker, with no cross-worker read/write document
overlap. Point reads, batch reads and queries over distinct collection IDs did
not reproduce the failure. Distinct filter values in the same collection ID
still failed. Running the query outside the transaction also avoided it in a
diagnostic control. The failure therefore does not require Breadmaker's HTTP,
domain, session or adapter operations. This supports an emulator collection-query
locking explanation; it is not production capacity evidence or proof about a
specific emulator internal implementation.

Run the bounded controls with the same verified local prerequisites:

```sh
"$BREADMAKER_TEST_PYTHON" tools/loadtest/diagnose_firestore.py \
  --emulator-jar /absolute/local/path/firestore-1.22.0.jar \
  --java /absolute/path/to/java21-or-later/bin/java \
  --results .loadtest-results/query-lock-controls
```

The default is nine distinct controls, each with a fresh owned emulator, five
workers and at most four transactions per worker. A worker stops after its first
error. Transaction controls permit one attempt and a three-second Commit RPC
deadline to expose provider aborts without retry amplification; each child has a
40-second watchdog. Normal k6 application runs retain the application's existing
retry behavior and five-second HTTP deadline. The controls record valid provider
errors separately from invalid probe errors, actual query parents, document
paths and cleanup evidence. `--cases sdk_point sdk_query` selects a bounded
subset; duplicates are rejected. Observed transaction failures make the command
exit nonzero even when evidence collection and cleanup complete successfully.

The renamed-collection and outside-transaction cases are diagnostic controls.
Do not change production collection names, move authoritative reads outside a
transaction, or relax correctness/latency gates to turn an emulator test green.
The application's chapter save must continue checking the complete chapter set,
membership and expected project revision atomically. Keep failed multi-writer
results distinct from passing read-only or single-writer results, and confirm
actual hosted behavior only through a separately authorized bounded experiment.

Emulator transactions and query RPCs replace the memory adapter's whole-store
copy and global scan behavior. They still do not reproduce production indexes,
quotas, distributed contention, cold starts, IAM, network/region latency, KMS or
GCS persistence. JVM JIT and transaction retry warm-up can affect short first
runs; retain cold results and use longer bounded runs for warm comparisons.
The emulator can differ from production transaction timing and limits. Google
documents that write contention can hold emulator locks for up to 30 seconds;
the harness retains its five-second user-facing timeout rather than relaxing
latency expectations to obtain a pass. See
[Google's emulator limitations](https://firebase.google.com/docs/emulator-suite/connect_firestore#how_the_cloud_firestore_emulator_differs_from_production).

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
- Emulator startup has a 30-second watchdog. Seeding/app startup has a 120-second watchdog, and k6 has an independent process
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

Emulator observations additionally count transaction callback attempts (including
retries) and track committed document count/serialized size without retaining
document bodies or adding inventory RPCs to timed traffic. Low-level SDK RPC
retries are not individually counted. App CPU/RSS cover the Python fixture only;
the separate JVM's limits are recorded in experiment metadata, and its CPU/RSS
are not included in the app-process figures.

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
rates, k6 versions, platforms, adapters, lifecycle fences or emulator runtimes
and requires matching passing VU runs. Absolute
thresholds still apply even when no earlier baseline exists. The reporter writes
a sanitized `report.json` beside the raw summaries. Do not compare the small
population baseline with the larger-project experiment as a code regression.

## Next isolated hosted validation

This repository intentionally cannot point k6 at production. Before a hosted
experiment, use the private infrastructure repository to review a disposable
design compatible with the existing single-production policy. Do not create a
persistent staging environment or broaden IAM/ingress as a side effect. Any
required access change needs its specific confirmation. The next lower-cost
option is the credential-free local Firestore emulator path above. Validate its
results and limitations before spending on any hosted calibration.

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
