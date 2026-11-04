# Workspace Tools ownership

`api.workspace_tools` is composition-only.

- `references.py` — universal cross-feature references.
- `bookmark.py` — manuscript bookmark persistence.
- `ui_state.py` — collaborator-scoped UI preferences. Targets keys additionally require a value revision, checked inside the local mutation transaction; other preference replacement semantics are unchanged. The same revision helper is shared with hosted member state, where the check runs within Firestore's transaction/CAS boundary.
- `documents.py` — additional document lifecycle and manuscript promotion.
- `contracts.py` — request payloads owned by this domain.

Callers target the canonical owner directly; do not recreate `api/workspace_tools.py`.
