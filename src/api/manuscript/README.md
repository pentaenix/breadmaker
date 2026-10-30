# Manuscript domain ownership

`api.manuscript` is a package boundary, not an implementation facade.

- `navigation.py` — bootstrap navigation projections and bookmark payloads.
- `document.py` — bootstrap, document reads, project title, and basic Chapter metadata.
- `images.py` — image blocks, rows, and image movement.
- `anchors.py` — bookmark/annotation/range reanchoring and repair.
- `chapter_structure.py` — paragraph-to-Chapter structural split.
- `chapters.py` — Chapter block persistence, dissolve/delete, and ordering.
- `scenes.py` — Scene ownership, ranges, persistence, and movement.
- `narration.py` — local project-derived WAV/word-timing cache for paragraphs and validated chapter-title targets, excluded from canonical assets/history/backups. Hosted routes remain disabled; Cloud adapter requirements are in `tools/narration/README.md`.
  Uses the existing request-context root and the canonical `document.manuscript_source_text` reader; it adds no direct local storage import/debt. The Cloud adapter must replace this explicitly local boundary, not call the local reader in hosted mode.
  Its route class bounds streamed request bodies before multipart spooling. Save revalidates current source after upload, uses integer UTF-16 timing offsets and rolls back existing audio on failed metadata publication. Corrupt cache/Undo metadata fails closed. Deployment/remaining limits: `docs/NARRATION_DEPLOYMENT_CHECKLIST.md`.
  Validated `generation_pace` distinguishes non-default synthesis cache identities; missing/default 1 preserves prior IDs. Playback pitch/speed never change stored audio or identities. Export joins authorized saved downloads in the client; no archive route is added.
  `punctuation_pauses` is a strict boolean (metadata defaults false); true appends `-punctuation-v1` to the derived engine/cache identity. The client preference defaults on for new generation; old recordings are reused until explicit regeneration.
- `narration_voices.py` — local-only device voice catalog and explicit pinned downloads into existing installed model assets, separate from project recordings. Fixed IDs/revision/size/SHA-256, bounded reads, HTTPS redirect restrictions and atomic publication; no arbitrary URL/path or manuscript transmission.
- `narration_boundary.py` — shared local-only HTTP boundary for narration cache, voice and pronunciation routes. Validates loopback Host authorities before reads (DNS-rebinding defense) and bounds actual incoming body bytes before parsing: 34 MiB audio upload, 256 KiB control/pronunciation requests. Existing project/lease/Origin middleware still applies; no hosted activation.
- `narration_pronunciation.py` — optional, lazy offline Japanese/Mandarin/Italian pronunciation adapters. Strict bounded JSON and model-token checks, local project context only, no recording/manuscript writes or external requests. Hosted is disabled; Cloud must supply its authorized adapter before activation. Dependencies/setup/notices live in `tools/narration/`.

Callers import the canonical owner directly. Do not re-export implementation symbols from `api.manuscript.__init__`.
