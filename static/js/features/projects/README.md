# Projects frontend ownership

Project administration is package-owned in 10.3.5. The package keeps destructive, backup, and transfer responsibilities explicit instead of accumulating them in one Project shell file.

- `targets-model.js` / `targets-journal.js` / `targets.js` — default-off personal project writing/time goals, local-calendar recurrence, compact status indicators, optional in-app celebration, and bounded history. Shared IDB sequencing plus a synchronous pre-journal supports closed-tab recovery; transactional UI-state revisions and persistent stream receipts protect simultaneous devices and retries. Overlapping timer intervals count once. Uses the existing collaborator UI-state store (with a profile-specific key locally); manuscript bytes and generation owners remain untouched. See `docs/TARGETS_CHECKPOINT.md` for counting/activity semantics and verification limits.
- `menu.js` — brand Project menu, submenus, and rename entry point.
  Submenu hover opens only for mouse pointers. Touch opens on click so
  compatibility hover events cannot move a project row under the initial tap.
  Arrow-key entry/navigation and Escape return use the same menu builders.
- `settings.js` — Project Settings, language selection, local History retention, and the Narration settings dialog. Narration preference persistence/application belongs to Manuscript's `narration-preferences.js`/`narration.js`.
  Generation pace, playback-only pitch, and the nested selection/chapter/book audio-download prompt live here, not in the main narration panel. Export assembly belongs to Manuscript's `narration-export.js`.
  Punctuation pauses is a project-scoped, default-on generation preference; changing it does not rewrite saved recordings.
- `narration_voices.js` — nested searchable voice library: installed/default/current choices first, row previews, visibility checkboxes, Use voice into the parent settings draft, individual downloads and per-language default/all packs. Packs download only missing files sequentially, preserve successful downloads on failure, retry missing files only and support stop-after-current-file. Optional English pack voices remain unchecked unless explicitly enabled/used. No manuscript generation or project recording publication.
  It shares the interface-language filter and the Settings-only all-languages override with the panel. Unsaved override changes preview the filter before Save; nested-dialog cleanup preserves the parent setting.
  Narration settings put Voices first and group Playback, Generation, Text highlighting and Performance. Save/Cancel stay visible while the body scrolls. Browsing/selecting a voice preserves the parent's other unsaved settings; only Save changes the narrator, while downloads and visibility choices retain their existing immediate behavior.
  Native settings/language/export selectors reuse the Manuscript View chevron with a 12px logical-end inset and reserved text padding; the wrapper does not replace native select keyboard behavior. Forced-colors mode restores the native arrow. Section padding and the audio-download action keep controls clear of divider lines; regression checks measure desktop/mobile arrow and action spacing.
- `backups.js` — Backup Destination configuration, provider setup, retention, and backup-management UI.
- `lifecycle.js` — experimental flags, project create/rename, package/JSON/Cuts import, metadata, destructive actions, project list, and trash.
  Both rename controls save pending writing first. Hosted renames reopen the
  active route from server authority before accepting more input; a new project
  revision must never be attached to an old manuscript snapshot.
  Hosted project deletion uses the existing recoverable owner-only tombstone
  after confirmation and a successful manuscript save. Save conflicts keep their
  recovery dialog and text; failed deletion keeps the current project open.
  Global/account deletion and native typed-code confirmation remain separate.
- `editing_search.js` — local-only experimental editing actions, existing marker
  navigation and structural Undo orchestration; no continuous/background scan.
- `export_recovery.js` — PDF export, project verification, and Recovery-backup UI.

Do not recreate `features/projects.js` as a compatibility facade. Destructive controls remain reachable from Project Settings even when project state is unhealthy, matching Breadmaker's pre-alpha recovery rules.

Targets uses the same native settings controls and compact section spacing. A
single goal editor plus direct goal selector keeps all eight drafts available
without forcing the whole dialog to scroll. Only the bounded History region
scrolls (about 2.5 rows); its disclosure offers a list or observed-period activity
view for daily/weekly/monthly goals. Missing periods are not painted as failures
and unlike measures are not summed. Sound selection previews that choice, while
opening the modal stays silent; cancellation/blur/context changes stop preview.
Achieved indicators complete the existing ring with a quiet center dot, and
hover/accessibility descriptions retain actual current/target amounts.

Readable ZIP export uses the existing Export submenu and PDF modal styling. `export_recovery.js` owns its saved-first download, busy/error state and modal ownership; archive rendering belongs to backend format owners.
