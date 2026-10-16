# Durability HTTP ownership

`api.durability` is composition-only. Runtime behavior lives in explicit owners:

- `cuts.py` — Cuts library, insertion helpers, and `.breadcuts` import/export.
- `history.py` — immutable local/remote History list, compare, checkpoint, restore, retention, sync, GC, and repair.
- `undo.py` — short-lived structural Undo transaction endpoints.
- `history_baseline.py` — editing-session History baseline creation.
- `recovery.py` — Recovery status and trash-project restoration.
- `backup_destinations.py` — destination CRUD, local folder selection, Google Drive OAuth, and WebDAV connections.
- `recovery_backups.py` — explicit Recovery backup scheduling.
- `backup_inventory.py` — destination backup inventory, discovery, and restore-as-project.
- `backups.py` — verified Recovery backup creation, integrity, listing, restore, and external-backup scheduling service.

Callers target the canonical owner directly. Do not recreate `api/history_backups.py` or re-export its old mixed namespace.
