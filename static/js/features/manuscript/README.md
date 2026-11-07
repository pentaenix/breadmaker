# Manuscript frontend ownership

The Manuscript browser domain is split by responsibility while preserving Breadmaker's ordered classic-script runtime:

- `view.js` — Manuscript text/font preferences, the Manuscript View popover, and Manuscript UI-state restoration.
- `bookmarks.js` — project bookmark state, bookmark presentation, selection bookmarking, and bookmark navigation controls.
- `navigation.js` — Documents, Chapter navigation/tree state, and document-level actions.
  At phone widths the same chapter rail opens through `core/collections.js` as a temporary drawer; its
  visibility is independent of the saved desktop collapse preference. Escape,
  the backdrop and navigation choices close it, with keyboard focus returned
  to the toolbar control. The writing pane is inert while the drawer is open.
- `rendering.js` — Chapter/Book/Scene rendering, block wrappers, outline visibility, gutter presentation, view selection, and viewport/location state.
- `history.js` — Manuscript/Scene working-session edit snapshots used by the application-wide history coordinator in `core/history.js`.
- `workspace.js` — Focus Mode, Manuscript tooltips, Checkpoint toolbar presentation, and Editor's Eye.
- `narration.js` — Narration UI, selection/chapter/book queues, missing-only continuous generation, stop-after-passage handling, playback, project-cache adapter, transient gutter controls, and non-persistent passage/word highlighting. Queue preparation shows indeterminate progress and supports Stop; panel-loading ownership is independent of playback tokens. Generation completes after publication, independently of the player's metadata/audio reads or browser audio activation. An unfinished publication keeps the Saving status even after the estimate expires. New generation invalidates older automatic player loads; media events own the existing Play/Pause state. Navigation suspends automatic continuation before queued media events can fetch in a departing document. A cancelled navigation preserves saved audio and the queue for explicit playback resume.
- `narration-engine.js` — browser-worker Kokoro inference, optional WebGPU/fp32 with WASM/q8 fallback, incremental audio callbacks and complete timestamped WAV output. Pinned installation and Cloud handoff: `tools/narration/README.md`.
- `narration-punctuation.js` — optional generation-time punctuation gap top-ups with unchanged source offsets and shifted audio word timings; the engine uses identical processed samples for streaming and saved WAVs.
- `narration-playback.js` — saved audio and early chunk playback, buffering, seek/speed/pause, and transient audio cleanup; never stores a partial recording.
- `narration-pitch-worklet.js` — optional playback-only pitch shifting for saved WAV and early streams; no recording mutation or timing rescaling.
- `narration-export.js` — saved-audio scope resolution, word-boundary selection trimming, and lossless single-WAV downloads. No generation, server archive, or backup inclusion.
- `narration-preferences.js` — normalized, project/collaborator-scoped narration UI-state preferences and serialized writes. The settings dialog is owned by Projects; narration UI applies preferences to inference, playback, and View highlights.
- `narration-voices.js` — curated voice catalog/status loading and localized voice choices; `static/narration-voices.json` pins supported IDs, sizes and checksums. Optional voices stay out of the main selector until enabled in settings.
  Hosted visible voices are selectable before device caching and use the existing verified public-asset transport on demand. Local missing voices still require installation. A preference change preserves saved audio; explicit Regenerate/Recreate replaces it using the selected voice.
  All selectors follow the interface language unless the saved `showAllLanguages` override is on. Per-language choices are remembered; the 44-voice catalog covers seven speech languages with only the original four English voices enabled by default. French/Portuguese use bundled browser pronunciation; Japanese/Mandarin/Italian use the local-only lazy pronunciation API, never a paid text service.
- `narration-progress.js` — smooth, monotonic estimated progress and browser-local numeric startup/inference calibration, separated by backend/CPU-thread count, language and cold/warm synthesis.
- `persistence.js` — dirty-state tracking, autosave/flush coordination, conflict handling, status counts, and bounded active-editor hosted read-cache maintenance (no periodic polling or dependency of saving on cache availability).
- `editing.js` — block editing, selection handling, and edit interaction wiring.
- `scenes.js` — selection actions, Scene assignment/splitting/movement, and Scene-focused editor actions.
- `annotations.js` — Markers, annotations, Notes/comments presentation, selection markup, and related gutter behavior.
  Stamped experimental editing results convert canonical Unicode code-point
  anchors to DOM UTF16 ranges without changing legacy annotation interpretation.
  Marker menu visibility is a browser-local per-user/project display preference,
  applied to marker paint and marker-navigation paint without deleting annotations.
- `find_replace.js` — the Manuscript inline Find/Replace navigator entry surface.
- Chapter deletion uses the existing structural Undo transaction without a confirmation modal, then exposes Undo in the toast. Hosted deletion commits a bounded chapter in one transaction, without downloading its prose; larger chapters fail before deleting content.
- `imports.js` — document-tab append import, Main Manuscript import, new-project import, import destination selection, and import progress UI. Existing text requires five-character confirmation. Hosted text-only replacement/append publishes one bounded transaction and uses structural Undo; imports exceeding the journal budget or containing images explicitly require the resumable new-project path until segmented publication/recovery is implemented.

These are runtime owners, not compatibility wrappers. Application Undo/Redo/structural history belongs to `static/js/core/history.js`; routing/view discovery belongs to `core/routing.js`; generic collection-rail helpers belong to `core/collections.js`. Do not move implementations back to `static/app.js`, `core/ui.js`, or unrelated features. The dedicated Notes/comments workspace lives under `static/js/features/notes/`, and History/Cuts lives under `features/durability/`.

At phone widths, the existing toolbar actions are exposed through the compact Manuscript tools menu. The original controls still own each action and pressed state. Tool panels open as bottom sheets using half the available workspace; the writing area remains above them. Global phone viewport/menus live in `core/collections.js` and shared application controls.

Marker creation reconciles its committed response directly, like icon-marker creation. It does not start a whole-bootstrap refresh after releasing the structural Undo barrier, which could overwrite a later icon action. Duplicate submission is disabled while saving; closing the form keeps its existing behavior, and a completed old request cannot close a newer dialog or update a different project/profile. Existing modal controls, styling, authorization and Undo transactions are retained. Delayed-refresh and interrupted-form regressions cover local Chromium and hosted phone-width WebKit.
