# Workspace ownership

`workspace` owns installation/project repository orchestration. It is not a generic service-locator namespace.

The package boundary (`workspace/__init__.py`) is deliberately small: it exposes the stable `Workspace` type, singleton `workspace`, public workspace errors/data contracts, and release/package constants used by long-lived callers. Implementation belongs to a focused owner module.

| Module | Owns |
| --- | --- |
| `service.py` | `Workspace` composition, installation paths/locks, registry initialization, manifest durability |
| `profiles.py` | installation-local profiles and avatar state |
| `memberships.py` | non-portable project membership/ownership sidecars and project registry reconciliation |
| `projects/` | project recovery/registry, `.breadmaker` package transfer, and trash/restore service owners |
| `remote_history.py` | Remote History repository sync, repair, retention and GC orchestration |
| `credentials.py` | encrypted backup credentials and Google Drive OAuth state |
| `backups/` | backup destinations, authority/status, publication state, local publication/scrub, catch-up, inventory/materialization |
| `erasure.py` | privacy-only backup erasure inventory and managed external deletion |
| `providers.py` | local/WebDAV/Google Drive Recovery-backup provider implementations |
| `filesystem.py` | filesystem/volume identity and durable file/package helper primitives |
| `models.py` | workspace records/errors/provider contracts |
| `constants.py` | project package/schema limits and portable-manifest allowlist |

`Workspace` is assembled from responsibility-specific mixins so callers keep one stable object while implementation ownership remains obvious. The mixins are an internal organization technique, not separate services or public state containers.

## Rules

- New workspace behavior goes in its owning module, not `__init__.py` or a generic helper file.
- Production code that needs an internal helper imports the owning module directly (`workspace.filesystem`, `workspace.models`, `workspace.constants`, etc.). Do not add package-level aliases merely to preserve an old source shape.
- Reassignable state lives on the `Workspace` instance or in its canonical owner. Do not copy it into module globals.
- Tests that monkeypatch an internal helper patch the module where the implementation resolves that name.
- `.breadmaker` packages contain portable project state only. Installation profiles, membership identity, backup credentials, and privacy-erasure inventory remain non-portable.
- Pre-alpha schema policy remains unchanged: unsupported project/storage formats fail closed; do not add migrations unless explicitly requested.
## Cross-owner runtime lookups

When a Workspace domain consumes platform-sensitive or intentionally patchable behavior owned by another Workspace module, resolve it through the owning module at call time. For example, backup destination naming and removable-volume relocation read volume metadata and mounted-volume roots through `workspace.filesystem` rather than retaining copied `_volume_metadata` / `_mounted_volume_roots` function bindings. This keeps runtime overrides/tests and production code aligned on one canonical owner.


`workspace/projects/` composes recovery/registry, package transfer, and trash owners. The old flat `workspace/projects.py` must not return.
