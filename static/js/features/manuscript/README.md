# Manuscript frontend ownership

The Manuscript browser domain is split by responsibility while preserving Breadmaker's ordered classic-script runtime:

- `view.js` — Manuscript text/font preferences, the Manuscript View popover, and Manuscript UI-state restoration.
- `bookmarks.js` — project bookmark state, bookmark presentation, selection bookmarking, and bookmark navigation controls.
- `navigation.js` — Documents, Chapter navigation/tree state, and document-level actions.
- `rendering.js` — Chapter/Book/Scene rendering, block wrappers, outline visibility, gutter presentation, view selection, and viewport/location state.
- `history.js` — Manuscript/Scene working-session edit snapshots used by the application-wide history coordinator in `core/history.js`.
- `workspace.js` — Focus Mode, Manuscript tooltips, Checkpoint toolbar presentation, and Editor's Eye.
- `narration.js` — local Narration UI, selection/chapter/book queues, missing-only continuous generation, stop-after-passage handling, playback, project-cache adapter, transient gutter controls, and non-persistent passage/word highlighting.
- `narration-engine.js` — browser-worker Kokoro inference, optional WebGPU/fp32 with WASM/q8 fallback, incremental audio callbacks and complete timestamped WAV output. Pinned installation and Cloud handoff: `tools/narration/README.md`.
- `narration-punctuation.js` — optional generation-time punctuation gap top-ups with unchanged source offsets and shifted audio word timings; the engine uses identical processed samples for streaming and saved WAVs.
- `narration-playback.js` — saved audio and early chunk playback, buffering, seek/speed/pause, and transient audio cleanup; never stores a partial recording.
- `narration-pitch-worklet.js` — optional playback-only pitch shifting for saved WAV and early streams; no recording mutation or timing rescaling.
- `narration-export.js` — saved-audio scope resolution, word-boundary selection trimming, and lossless single-WAV downloads. No generation, server archive, or backup inclusion.
- `narration-preferences.js` — normalized, project/collaborator-scoped narration UI-state preferences and serialized writes. The settings dialog is owned by Projects; narration UI applies preferences to inference, playback, and View highlights.
- `narration-voices.js` — curated voice catalog/status loading and localized voice choices; `static/narration-voices.json` pins supported IDs, sizes and checksums. Optional voices stay out of the main selector until enabled in settings.
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
- `imports.js` — document-tab append import, Main Manuscript import, new-project import, import destination selection, and import progress UI.

These are runtime owners, not compatibility wrappers. Application Undo/Redo/structural history belongs to `static/js/core/history.js`; routing/view discovery belongs to `core/routing.js`; generic collection-rail helpers belong to `core/collections.js`. Do not move implementations back to `static/app.js`, `core/ui.js`, or unrelated features. The dedicated Notes/comments workspace lives under `static/js/features/notes/`, and History/Cuts lives under `features/durability/`.
