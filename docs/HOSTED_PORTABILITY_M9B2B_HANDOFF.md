# M9B2B handoff — Editor 10.3.70 / Cloud 10.4.38

The user asked to continue while away, so 10.3.68 and 10.3.69 have NOT
received individual green gates. 10.3.70 carries all these changes and is
**awaiting the user's combined full Editor + Cloud verifier**.

This checkpoint extends local portability with restricted historical codecs:
`src/hosted/portability_history.py` and `local_portability.py`. Read-only
single-round manuscript Review snapshots may be projected to frozen local
Review rows and derived checkpoint; locally compatible v1 checkpoints without
notes/markup may be re-encoded and their images protected in immutable
checkpoint-asset tables. Fields not representable in Schema 42 return specific
export errors. Advanced Canvas-version geometry and richer Review/checkpoint
history still require explicit future work. It is **not** safe to claim full
M9B closure or disaster recovery readiness yet.

New tests `tests/test_v10370.py` written but NEVER run by model; user runs full
Editor + Cloud verifier and supplies digest. Protected files: local Schema 42,
local Workspace/runtime, verifier/watchdogs, M3/M4 Firestore/GCS adapters,
Cloud 10.4.38. M9C safe deletion and M9D backup scheduling not started;
M10 deployment/infrastructure still not live.
