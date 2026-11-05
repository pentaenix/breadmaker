# Canvas frontend ownership

Canvas browser behavior is split by UI responsibility while preserving classic-script load order and shared global runtime semantics.

- `hierarchy.js` — Canvas hierarchy, selection, Structure Versions, Populate, Canvas-level UI state, and the Canvas-specific compact rail label wrapper.
  Phone navigation uses the shared `core/collections.js` drawer; selecting a
  Canvas dismisses it. Phone visibility never persists a desktop collapse change.
- `nodes.js` — Canvas node/card creation, editing, positioning, rendering, and interactions.
- `connectors.js` — Canvas connector creation, editing, routing, rendering, and interactions.

Generic collection-rail helpers (`compactRailLabel()` and `appendCollectionQuickAdd()`) belong to `core/collections.js`; Manuscript UI-state restoration belongs to Manuscript, not Canvas. Do not move Canvas implementation back into `static/app.js`. Cross-feature source contracts should read the runtime frontend boundary rather than physical files unless they intentionally verify ownership.

Canvas uses direct selection: left-click selects, left-drag on empty space draws a
selection rectangle, and Space-drag or right-drag pans over cards and connectors.
A stationary right-click keeps the existing context menu. Empty-space touch drags
pan; drawing tools and object gestures retain their existing touch behavior. The
viewport stores zoom and scroll position, without a persistent hand/pointer mode.
Space on buttons, menus, fields outside the board and modals keeps native keyboard
behavior; Canvas text editing can still convert a held Space into a pan gesture.

The 18px dotted background uses a 1 CSS-pixel radius in normal and Split Canvas.
The old subpixel radius could rasterize to no visible pixels at device scale 1;
real-pixel browser checks cover light/dusk/dark and three zoom levels. Pointer,
Space-pan, right-drag and stationary context menus retain their existing owners.
