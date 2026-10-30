# Hosted narration

This package is not activated by importing it. Local narration stays accountless
and independent of Firestore, Google credentials and these quotas.

`transfers.py` owns cross-instance narration transfer admission. Its caller must
supply a READ-ONLY transaction authority callback checking current account
admission, project membership and closure fences in the SAME Firestore database.
Only literal `True` grants access. Missing/unavailable authority fails closed.
No browser-supplied account ID or entitlement is authoritative.

Each HTTP body attempt reserves its maximum known response length BEFORE object
reads/decryption/streaming. Maximum response is 2 MiB. A new attempt always pays
again, including Range requests, retries and disconnects: a client request ID is
never an allowance-replay key. Provider transaction retries debit only once.
An uncertain failed delivery is conservatively charged, not refunded. Cached
device playback/download reuse sends no request and uses no transfer allowance.

Initial account limits: audio 10 GiB/month, models 4 GiB/month, 20,000 response
attempts/month, four simultaneous transfers, a 32-request burst replenishing at
four requests/second. Alpha-wide safety limits: 40 GiB and 100,000 attempts/month.
Periods use UTC calendar months. Account limits span projects, tabs and replicas.
Global counters deliberately favor a small alpha; they are not a scalable pricing
entitlement. No daily hard limit is imposed without normal-use measurements.

The HTTP adapter MUST enforce a <=60-second total response deadline, including
backpressure, and finish its server-only permit in `finally`; leases last 90s
so crashed processes cannot permanently consume concurrency. An expired permit
cannot authorize further streaming. Finish never refunds bytes and is safe after
revocation; it can only remove its exact narration lease, not change authority.
Permits must never be accepted from browser JSON or reused for another response.

Ledger rows are minimal control data, outside canonical project generations,
History and backups. Unknown/corrupt rows fail closed. Do not TTL current counters
or active leases. Account erasure must census these rows before claiming physical
completion. No prose, audio, email, tokens, asset URLs or filenames are stored.

This primitive alone is NOT an active HTTP guard, total Google bill cap, anonymous
DoS shield, upload/storage quota, pronunciation limiter or production acceptance.
Cheap bounded process-local rejection must precede paid transactions under abuse.
Narration endpoint composition, private storage/model transport, source/CAS,
encryption, GC and release acceptance remain required before enabling the feature.
