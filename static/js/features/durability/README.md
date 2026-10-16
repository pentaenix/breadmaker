# Durability frontend ownership

`history.js` owns the browser UI for immutable History, Cuts, and Principles-related durability workflows, including History comparison/restore presentation and the browser controls that open those surfaces.

Durability API/domain behavior remains under `src/api/durability/`. The startup shim must not become a durability service locator.
