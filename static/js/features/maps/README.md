# Maps frontend ownership

Maps browser behavior lives here rather than in `static/app.js` or shared UI infrastructure.

- `view.js` — ruler scale, label text size/font preferences, and persisted Map presentation controls.
- `workspace.js` — Map workspace state, tree/navigation, Map/Line compact rail-label wrappers, line/item/junction editing, rendering, inspector behavior, viewport persistence, and Map interactions.
  Phone navigation uses the shared `core/collections.js` drawer and shows the
  expanded navigation independently of the saved desktop collapse preference.
- `metro.js` — Metro View derived-poster modeling and rendering.

The files remain ordered classic scripts. Shared typography primitives come from `core/typography.js`; generic compact-label parsing and collection quick-add construction come from `core/collections.js`; Map-specific wrappers/preferences stay in this package.

A newly committed hosted Map is already empty: creation seeds its detail cache
and starts from an empty viewport without duplicate detail/viewport reads. Local
creation loads its manuscript ruler projection once before selecting the Map.
Existing Map selection still restores its saved viewport. Structural Undo/Redo
and failed-finish rollback remain authoritative.

Context menus render general commands before optional Populate configuration
loads. Only the existing Populate submenu is filled asynchronously, with
project/selection/menu-lifetime guards; dismissed or replaced menus stay closed.
Line menus do not fetch Populate data they do not display. Selecting an inactive
Map still resolves that Map before line actions to retain correct action context.
