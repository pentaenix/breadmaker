# Storage ownership

Breadmaker storage is split by persistence responsibility. Import the owning module directly; there is no compatibility facade or storage service-locator module.

| Owner | Responsibility |
| --- | --- |
| `context.py` | Request/task-local project binding and canonical project paths |
| `coordination.py` | Project-switch and Recovery privacy synchronization boundaries |
| `schema.py` | SQLite schema/version |
| `schema_contracts.py` | History and project-generation table coverage contracts |
| `values.py` | Canonical timestamps, IDs, and content/revision hashes |
| `database.py` | SQLite connections, process instance locks, and schema initialization |
| `mutations.py` | Canonical transactions, generation CAS, and commit listeners |
| `history.py` | Immutable local Revision Store integration |
| `undo.py` | Working-session structural Undo/Redo journal |
| `assets.py` | Checkpoint/project assets and durable asset publication |
| `invariants.py` | Canonical project structural invariants |
| `integrity.py` | Deep integrity and disk-health diagnostics |
| `recovery.py` | Verified local Recovery packages, retention, and scheduler authority |

Cross-owner calls use module-qualified imports so monkeypatches, diagnostics, and mutable process state have one authoritative owner. Mutable project path globals belong only to `storage.context`; callers should prefer `current_*()` accessors rather than importing path scalars.
