# Frontend JavaScript ownership

10.3.x is Breadmaker's frontend architecture phase. The browser remains build-step-free: JavaScript is served as ordered classic scripts, so local/offline development does not require npm, a bundler, or a compile step.

`static/app.js` is now a **startup shim only**, not a feature owner or source boundary. New behavior belongs in the narrowest existing `core/` or `features/` owner. Historical source-contract tests use `tests.source_contracts.frontend_source()` so they assert the complete runtime frontend instead of requiring one physical monolith.

## CSS boundary after 10.3.7

JavaScript ownership remains unchanged. CSS has an ordered ownership tree under `../css/` since 10.3.6, and 10.3.7 performs only mechanically provable cascade cleanup inside that boundary; see `../css/README.md`. JavaScript source contracts continue to use `frontend_source()`, while CSS source contracts use `frontend_styles_source()`. Neither boundary should be inferred from one physical file. 10.3.8 adds the reuse-before-create component catalog at `../css/shared/COMPONENTS.md`; JavaScript-generated controls should emit those canonical class stacks instead of inventing lookalikes.

## Frontend architecture freeze after 10.3.9

10.3.9 closes the ownership phase. The remaining cross-feature services have canonical owners:

- `core/history.js` — application-wide Undo/Redo stacks, structural Undo transactions/rollback, command serialization, and cross-workspace restore coordination.
- `features/manuscript/history.js` — Manuscript/Scene edit snapshots only; it supplies snapshot capture/restoration to the shared history coordinator.
- `core/routing.js` — primary-view routing plus `currentView()`.
- `core/collections.js` — generic compact collection-rail labels and collection quick-add construction.
- `features/manuscript/view.js` — Manuscript view preferences and Manuscript UI-state restoration.
- `features/durability/history.js` — History/Cuts workspace plus Cuts synchronization.
- Canvas and Maps keep only their own rail-label wrappers.

The frontend is now **architecture-frozen**. Do not split files merely because they are large, create compatibility facades, move feature behavior into `core/`, or replace the build-step-free classic-script runtime without a concrete product requirement. New controls remain reuse-before-create via `../css/shared/COMPONENTS.md`.

## Runtime ownership in 10.3.5

10.3.5 is the cohesion pass after the 10.3.4 extraction checkpoint. It does not reopen the build model: the browser still loads ordered classic scripts directly from `static/index.html`.

- `core/typography.js` owns the shared font/size primitives consumed by Manuscript, Reviews, Canvas, and Maps.
- `core/ui.js` is shared UI infrastructure only: toast/modal/context-menu infrastructure, transient UI coordination, local-profile presentation, theme/save-state presentation, and runtime localization refresh.
- `features/reviews/` replaces the former `features/reviews.js` monolith with `view.js`, `reader.js`, `workspace.js`, `details.js`, `panels.js`, and `lifecycle.js`.
- `features/projects/` replaces the former `features/projects.js` monolith with `menu.js`, `settings.js`, `backups.js`, `lifecycle.js`, and `export_recovery.js`.
- `features/manuscript/` now owns `view.js` and `bookmarks.js`; `rendering.js` no longer owns browser history, workspace presentation, or persistence, which live in `history.js`, `workspace.js`, and `persistence.js`.
- `features/maps/view.js` owns Map ruler/text/font preferences instead of `core/ui.js`.
- `../app.js` remains the 13-line startup shim.

Do not recreate retired single-file `reviews.js` or `projects.js` facades. Source-contract tests must follow the ordered runtime boundary from `static/index.html`. CSS remains intentionally monolithic until this JavaScript ownership checkpoint is green.

## Runtime load order in 10.3.4

10.3.4 finishes the JavaScript ownership migration while preserving the historical classic-script execution order. After Maps/Metro, the remaining browser responsibilities load as:

- `features/split_view.js` — shared Split View layout, chooser, resizing, and cross-feature split rendering.
- `features/manuscript/imports.js` — document-tab append import and Main Manuscript import browser flows.
- `features/checkpoints/workspace.js` — Checkpoint list/comparison/details/apply browser workspace.
- `features/durability/history.js` — History, Cuts, and related durability browser workflows.
- `features/notes/comments.js` — Notes/comments workspace and manuscript note interaction.
- `core/quick_open.js` — global Quick Open indexing, search, and navigation.
- `../app.js` — 13-line final startup shim: Find-mode normalization, BreadReview drop target installation, and `boot()`.

The former 721-line feature remainder is fully extracted. Concatenating scripts in `static/index.html` order remains the frontend source-contract boundary. `app.js` must stay startup-only.

## Runtime load order in 10.3.3

10.3.3 adds the `features/maps/` owner after Canvas in the historical browser execution order:

- `features/maps/workspace.js` — Map workspace navigation, editing, rendering, inspectors, UI-state persistence, and Map interactions.
- `features/maps/metro.js` — Metro View derived-poster layout and rendering.
- `../app.js` — transitional remainder now beginning at Split View.

10.3.3 reduces `app.js` from 1,477 lines to **721 lines** without rewriting runtime behavior. Concatenating scripts in `static/index.html` order remains the frontend source-contract boundary.

## Runtime load order in 10.3.2

The script order declared by `static/index.html` remains part of the runtime contract. After the 10.3.1 core/Project/Manuscript owners, 10.3.2 adds:

- `features/tasks.js` — Task board, Task CRUD, milestones, and Task interactions.
- `features/linking.js` — universal references/linking browser behavior shared across features.
- `features/canvas/` — Canvas hierarchy/UI state, node/card behavior, and connectors.
- `../app.js` — transitional remainder now beginning at Maps.

10.3.2 reduces `app.js` from 2,579 lines to **1,477 lines** without rewriting runtime behavior. Concatenating the scripts in browser load order remains the source-contract boundary.

## Runtime load order in 10.3.1

The script order declared by `static/index.html` is part of the runtime contract:

1. `core/i18n.js` — locale loading, marker binding, runtime translation primitives.
2. `core/runtime.js` — DOM primitives, client identity, API/error transport.
3. `core/state.js` — the single in-memory application state model.
4. `core/ui.js` — shared UI presentation, modal/toast, preferences, theme and localization refresh.
5. `core/routing.js` — primary view routing and initial workspace hydration.
6. `features/reviews.js` — Reviews and Reviewer Mode browser behavior.
7. `core/recovery.js` — crash journal and draft recovery.
8. `core/boot.js` — editing lease, runtime/bootstrap loading, boot and refresh coordination.
9. `features/projects.js` — Project Center/project menu, backup/recovery/settings/import-export shell.
10. `features/manuscript/` — Manuscript navigation, rendering, editing, Scenes, annotation UI, and inline Find/Replace.
11. `../app.js` — transitional remainder containing feature domains still awaiting extraction.

10.3.1 reduces `app.js` from 5,209 lines to **2,579 lines** without rewriting behavior. Concatenating the runtime scripts in browser load order remains the source-contract boundary.

## Ownership rules

- Preserve script order unless a dependency analysis proves the new order is safe.
- Do not create compatibility copies of moved functions in `app.js`.
- Do not add new feature behavior to `app.js` when an owning module exists.
- Shared browser infrastructure belongs in `core/`; product behavior belongs in `features/`.
- Keep the single shared `state` model in `core/state.js` until there is a concrete reason to introduce a stronger state abstraction.
- Cross-feature calls may continue to resolve through global function names during this build-step-free decomposition; ownership is determined by the file where the implementation lives, not by re-export wrappers.
- When source-contract tests need frontend implementation text, use `frontend_source()` / `frontend_source_from_app_path()`.

CSS ownership is now separate from JavaScript ownership. As of 10.3.6 use the ordered boundary under `static/css/` documented in `static/css/README.md`; 10.3.7 keeps that order stable and removes only provably redundant declarations; do not add feature CSS to `static/styles.css` or infer the complete CSS boundary from that one file.
