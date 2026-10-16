# Canvas frontend ownership

Canvas browser behavior is split by UI responsibility while preserving classic-script load order and shared global runtime semantics.

- `hierarchy.js` — Canvas hierarchy, selection, Structure Versions, Populate, Canvas-level UI state, and the Canvas-specific compact rail label wrapper.
- `nodes.js` — Canvas node/card creation, editing, positioning, rendering, and interactions.
- `connectors.js` — Canvas connector creation, editing, routing, rendering, and interactions.

Generic collection-rail helpers (`compactRailLabel()` and `appendCollectionQuickAdd()`) belong to `core/collections.js`; Manuscript UI-state restoration belongs to Manuscript, not Canvas. Do not move Canvas implementation back into `static/app.js`. Cross-feature source contracts should read the runtime frontend boundary rather than physical files unless they intentionally verify ownership.
