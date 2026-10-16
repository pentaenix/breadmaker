# Workspace Tools ownership

`api.workspace_tools` is composition-only.

- `references.py` — universal cross-feature references.
- `bookmark.py` — manuscript bookmark persistence.
- `ui_state.py` — collaborator-scoped UI preferences.
- `documents.py` — additional document lifecycle and manuscript promotion.
- `contracts.py` — request payloads owned by this domain.

Callers target the canonical owner directly; do not recreate `api/workspace_tools.py`.
