# Workspace backup ownership

`workspace.backups` composes backup behavior onto `Workspace`; it is not an implementation dumping ground.

- `destinations.py` — destination configuration, normalization, provider lookup, add/update/remove.
- `authority.py` — immutable publication authority observation, status, and revocation.
- `state.py` — attempted/transferred/verified/published generation state and retry recording.
- `publication.py` — local publication records, package publication, scrub bookkeeping.
- `catch_up.py` — automatic destination catch-up orchestration.
- `inventory.py` — destination inventory/discovery/materialization.

`Workspace` remains the public service object. New backup behavior belongs to its owning mixin module; do not recreate `workspace/backups.py`.
