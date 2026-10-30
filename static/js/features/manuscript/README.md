# Manuscript frontend ownership

The Manuscript browser domain is split by responsibility while preserving Breadmaker's ordered classic-script runtime:

- `view.js` — Manuscript text/font preferences, the Manuscript View popover, and Manuscript UI-state restoration.
- `bookmarks.js` — project bookmark state, bookmark presentation, selection bookmarking, and bookmark navigation controls.
- `navigation.js` — Documents, Chapter navigation/tree state, and document-level actions.
- `rendering.js` — Chapter/Book/Scene rendering, block wrappers, outline visibility, gutter presentation, view selection, and viewport/location state.
- `history.js` — Manuscript/Scene working-session edit snapshots used by the application-wide history coordinator in `core/history.js`.
- `workspace.js` — Focus Mode, Manuscript tooltips, Checkpoint toolbar presentation, and Editor's Eye.
- `persistence.js` — dirty-state tracking, autosave/flush coordination, conflict handling, status counts, and bounded active-editor hosted read-cache maintenance (no periodic polling or dependency of saving on cache availability).
- `editing.js` — block editing, selection handling, and edit interaction wiring.
- `scenes.js` — selection actions, Scene assignment/splitting/movement, and Scene-focused editor actions.
- `annotations.js` — Markers, annotations, Notes/comments presentation, selection markup, and related gutter behavior.
  Stamped experimental editing results convert canonical Unicode code-point
  anchors to DOM UTF16 ranges without changing legacy annotation interpretation.
  Marker menu visibility is a browser-local per-user/project display preference,
  applied to marker paint and marker-navigation paint without deleting annotations.
- `find_replace.js` — the Manuscript inline Find/Replace navigator entry surface.
- `imports.js` — document-tab append import, Main Manuscript import, new-project import, import destination selection, and import progress UI.

These are runtime owners, not compatibility wrappers. Application Undo/Redo/structural history belongs to `static/js/core/history.js`; routing/view discovery belongs to `core/routing.js`; generic collection-rail helpers belong to `core/collections.js`. Do not move implementations back to `static/app.js`, `core/ui.js`, or unrelated features. The dedicated Notes/comments workspace lives under `static/js/features/notes/`, and History/Cuts lives under `features/durability/`.
