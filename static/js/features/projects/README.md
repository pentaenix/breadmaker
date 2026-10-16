# Projects frontend ownership

Project administration is package-owned in 10.3.5. The package keeps destructive, backup, and transfer responsibilities explicit instead of accumulating them in one Project shell file.

- `menu.js` — brand Project menu, submenus, and rename entry point.
- `settings.js` — Project Settings, language selection, and local History retention.
- `backups.js` — Backup Destination configuration, provider setup, retention, and backup-management UI.
- `lifecycle.js` — experimental flags, project create/rename, package/JSON/Cuts import, metadata, destructive actions, project list, and trash.
- `export_recovery.js` — PDF export, project verification, and Recovery-backup UI.

Do not recreate `features/projects.js` as a compatibility facade. Destructive controls remain reachable from Project Settings even when project state is unhealthy, matching Breadmaker's pre-alpha recovery rules.
