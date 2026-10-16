# Maps frontend ownership

Maps browser behavior lives here rather than in `static/app.js` or shared UI infrastructure.

- `view.js` — ruler scale, label text size/font preferences, and persisted Map presentation controls.
- `workspace.js` — Map workspace state, tree/navigation, Map/Line compact rail-label wrappers, line/item/junction editing, rendering, inspector behavior, viewport persistence, and Map interactions.
- `metro.js` — Metro View derived-poster modeling and rendering.

The files remain ordered classic scripts. Shared typography primitives come from `core/typography.js`; generic compact-label parsing and collection quick-add construction come from `core/collections.js`; Map-specific wrappers/preferences stay in this package.
