# Projects frontend ownership

Project administration is package-owned in 10.3.5. The package keeps destructive, backup, and transfer responsibilities explicit instead of accumulating them in one Project shell file.

- `menu.js` — brand Project menu, submenus, and rename entry point.
- `settings.js` — Project Settings, language selection, local History retention, and the Narration settings dialog. Narration preference persistence/application belongs to Manuscript's `narration-preferences.js`/`narration.js`.
  Generation pace, playback-only pitch, and the nested selection/chapter/book audio-download prompt live here, not in the main narration panel. Export assembly belongs to Manuscript's `narration-export.js`.
  Punctuation pauses is a project-scoped, default-on generation preference; changing it does not rewrite saved recordings.
- `narration_voices.js` — nested searchable voice library: installed/default/current choices first, row previews, visibility checkboxes, Use voice into the parent settings draft, individual downloads and per-language default/all packs. Packs download only missing files sequentially, preserve successful downloads on failure, retry missing files only and support stop-after-current-file. Optional English pack voices remain unchecked unless explicitly enabled/used. No manuscript generation or project recording publication.
  It shares the interface-language filter and the Settings-only all-languages override with the panel. Unsaved override changes preview the filter before Save; nested-dialog cleanup preserves the parent setting.
  Narration settings put Voices first and group Playback, Generation, Text highlighting and Performance. Save/Cancel stay visible while the body scrolls. Browsing/selecting a voice preserves the parent's other unsaved settings; only Save changes the narrator, while downloads and visibility choices retain their existing immediate behavior.
  Native settings/language/export selectors reuse the Manuscript View chevron with a 12px logical-end inset and reserved text padding; the wrapper does not replace native select keyboard behavior. Forced-colors mode restores the native arrow. Section padding and the audio-download action keep controls clear of divider lines; regression checks measure desktop/mobile arrow and action spacing.
- `backups.js` — Backup Destination configuration, provider setup, retention, and backup-management UI.
- `lifecycle.js` — experimental flags, project create/rename, package/JSON/Cuts import, metadata, destructive actions, project list, and trash.
- `export_recovery.js` — PDF export, project verification, and Recovery-backup UI.

Do not recreate `features/projects.js` as a compatibility facade. Destructive controls remain reachable from Project Settings even when project state is unhealthy, matching Breadmaker's pre-alpha recovery rules.
