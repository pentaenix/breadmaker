# Uncomposed saved-word metadata collector

`GoogleFirestoreSavedWordsProducer` accepts an existing Firestore client, a trusted
policy reader, a bounded provider-account callback, and the fixed configured
Identity Platform issuer. It constructs no credentials or clients and defaults
off. `collect(deadline=<absolute monotonic>)` returns the raw aggregate envelope;
disabled returns None without reads. No runtime, route or service enables it.

The injected policy reader must implement load_policy(deadline)->WordPolicy and
read_policy_revision(deadline)->str from independently authoritative server state.
Missing policy must be unavailable, never a fabricated empty policy. The injected
account callback is keyword-only issuer/subject/deadline and returns exactly
issuer,subject,created_at_ms,tenant_id. It must verify the fixed issuer and queried
subject, return no email/name, and enforce its own absolute deadline with at most
four provider/network RPCs and four seconds per call. A normal unbounded Firebase
get_user call does not meet that contract unchanged.

## Read scope and bounds

The collector reads only:
- active project roots: status and generation, limit 101, twice;
- project word ledgers: the six internal schema/revision/count fields;
- owner memberships: user_id,role,created_at, filtered role=owner, limit 2;
- hosted user profiles: user_id,status,created_at;
- identity bindings: issuer,subject,user_id,status,creation epoch and timestamp;
- independently verified policy and provider-account incarnation metadata.

There is no text, title, object, history, asset, KMS, or collection-group read.
Every SDK request uses explicit projection, timeout<=4 seconds and retry=None.
Streams are closed. Project roots must match before and after collection. Missing
or stale ledger/owner evidence is unknown, never zero. Fresh policy-proof drift
also suppresses all totals; current policy revision is re-read before output.
The downstream cache still needs atomic policy-head validation at publication
and use; these before/after observations alone do not close that race.

For up to 100 projects and 32 excluded owners, the conservative Firestore bound is
668 document/minimum-query reads: root projections 202, ledgers 100, owner queries
200, unique hosted profiles 132, bindings 32 and policy reads 2. At most 368 Firestore
method/RPC calls include those two policy reads. Provider callbacks add at most
128 network RPCs under their separately enforced contract. Failed attempts can
consume reads. These are logical operation bounds, not a hard-dollar guarantee;
credential handling and provider billing must be considered by composition.

The absolute deadline is cooperatively checked around SDK/injected calls; it
cannot cancel an unbounded injected callback, SDK credential preparation, or a
provider operation after it has started. The private adapter must enforce its
transport bounds and hold any in-flight gate until actual completion. Sequential
calls across 100 projects may time out rather than return an incorrect partial
total. No performance or full-population completion claim is made without latency
verification. Keep the existing shared 30 attempts/month and 900-second manual
cooldown, with 900-second observed freshness and visible as_of, not live-current
claims. Policy changes invalidate old policy-bound cached totals immediately.

All live access, composition, provider readers and durable policy/cache fencing
require separate readiness checks and approval. This source performs no live scan
or activation merely by being imported or deployed with its default configuration.
