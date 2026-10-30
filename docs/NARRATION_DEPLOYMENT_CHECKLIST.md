# Narration: Cloud handoff and local validation

Updated: October 30, 2026. Editor source version: 10.5.66.

**Not a deployment approval.** Cloud code and infrastructure were not changed in
this pass. Local tests are not proof of hosted authorization, paid entitlement,
encrypted storage, production-browser inference, or overnight reliability.

## Architecture the Cloud agent should preserve

- Kokoro inference runs in the user's browser worker, not a paid speech API or a
  Cloud Run synthesis job. CPU uses q8; optional WebGPU uses fp32 and retries CPU
  once on failure. Speech model delivery, storage and pronunciation still have
  infrastructure costs. Do not describe the hosted feature as cost-free.
- Model: `onnx-community/Kokoro-82M-v1.0-ONNX-timestamped`, revision
  `dd4401a9add81ac692d20e240d22ec9dda82cc29`. HeadTTS 1.3.0,
  Transformers.js 4.3.2; exact JS graph: `tools/narration/pnpm-lock.yaml`.
- Seven speech languages: English, Spanish, French, Italian, Japanese,
  Brazilian Portuguese and Mandarin. Catalog: `static/narration-voices.json`.
  Voice counts: English 28, Spanish 3, French 1, Italian 2, Japanese 3,
  Portuguese 3, Mandarin 4. Four English voices are enabled by default:
  Heart/Michael (US), Emma/George (UK). Do not invent unavailable voices.
- Voice choices follow the **interface language**, not manuscript detection.
  The project settings override exposes other languages. Disabled/uninstalled
  voices are not usable until explicitly installed/enabled.
- English, Spanish, French and Portuguese pronunciation is browser-side.
  Italian unknown words, Japanese and Mandarin use a bounded same-origin
  pronunciation endpoint. Those passages **are sent to the app backend**;
  they are never sent to an outside speech service. Preserve this distinction
  in privacy statements and avoid logging text or IPA.
- The optional language dependencies are in
  `tools/narration/requirements-languages.txt`; imports stay lazy. Writing,
  saving, startup and recovery must not depend on model availability, Internet,
  accounts, optional dictionaries, or narration activation.

## Activation checklist — Cloud agent owns these items

- [ ] Keep narration disabled in hosted mode until the storage, entitlement,
  pronunciation and asset-delivery adapters pass hosted tests. Local routes
  currently return 404 outside local mode; the main UI also gates on local mode.
  Do not remove those guards before the replacement is complete.
- [ ] Authorize every list/read/upload/delete/Undo/export source against the
  authenticated account, project membership/role and paid entitlement. A browser
  project ID, record hash, voice setting or backend label is not authorization.
  Reviewers and revoked collaborators must not gain writing access.
- [ ] Use the existing hosted project/storage owners, not filesystem paths or
  SQLite in Cloud Run. Scope all objects, tombstones and metadata to the bound
  project. Never make recordings public or let a different project restore them.
- [ ] Treat audio, selected text, full source text and timings as private user
  data. Integrate the project's actual encryption/privacy contract; never
  silently introduce a plaintext exception. No manuscript/audio/text in logs,
  error reports, analytics, URLs or untrusted model requests.
- [ ] Store recordings as **project-owned regenerable derivatives**, outside
  canonical image assets, manuscript history/checkpoints, Recovery backups,
  portable project exports and whole-account backups. Normal restore must work
  with no recordings. OS backups are outside Breadmaker's control.
- [ ] Apply request limits **before multipart spooling**, also without
  Content-Length; enforce finite JSON and PCM/timing/range validation server-side.
  Current local limits: 32 MiB WAV, 34 MiB total request, 400,000 metadata
  characters, 20,000 selected characters, 100,000 source characters, 10,000
  timing entries. Choose documented hosted quotas and cap aggregate project
  storage, concurrent uploads, pronunciation requests and downloads too.
- [ ] Use short-lived authorized audio delivery, private/no-store responses,
  exact-origin CSRF checks and existing editing-lease/version boundaries.
  Test chunked uploads, cross-project access, signed-URL expiry, revoked access,
  same-ID races, retries and stale source/title rejection.
- [ ] Publish audio and metadata with failure-safe replacement/conditional writes.
  Do not retire the previous recording before the replacement is committed.
  Use idempotency/version preconditions for multiple Cloud instances. Undo must
  not overwrite a newer same-ID recording. No confirmation; return an Undo token.
- [ ] Define cache deletion/expiry and object lifecycle explicitly. Local Undo
  tokens expire after 24 hours and cleanup is lazy; the UI exposes Undo briefly.
  Hosted cleanup must not rely on the user opening the panel. Purge recordings
  with project/account deletion; do not erase canonical manuscripts.
- [ ] Distribute immutable, pinned model/runtime assets. Installer checksums:
  `tools/narration/model-checksums.json` and the voice catalog. Failed downloads
  stage separately and cannot replace a working file. No arbitrary URL fetching,
  user-supplied model paths, redirect authority or remote script/CDN inference.
  Device-global local downloads need a hosted catalog/CDN equivalent, not writes
  to an ephemeral app container. Asset access/egress policy is a Cloud decision.
- [ ] Review CSP, worker-src, WebAssembly, AudioWorklet, HTTPS and asset headers
  on the real hosted origin. Local-only COOP/COEP and `wasm-unsafe-eval` changes
  are **not enabled for hosted mode**. Test Identity Platform/popup login before
  changing isolation headers. Preserve a single-thread CPU fallback without
  shared memory; do not add broad unsafe-eval or remote-script exceptions.
- [ ] Preserve project/collaborator-scoped preferences using the existing
  `/api/ui-state/narration-preferences` owner. They are UI state, not entitlement
  or recording data. Enforce privacy/size rules for that existing owner.
- [ ] Review and ship the notices in `tools/narration/THIRD_PARTY_NOTICES.md`.
  Pay particular attention to voice attribution and the exact Italian
  Wiktionary-derived dictionary license; package metadata alone is not legal
  clearance for redistribution or a paid launch.
- [ ] Add a hosted feature flag/kill switch and stage activation. Rollback hides
  narration/stops new jobs without deleting manuscript text or canonical data.
  Derived audio can be retained or regenerated; do not require a migration to
  open projects after rollback.

## Adapter contracts and behavior

Local HTTP contracts are specified in `tools/narration/README.md` and owned by
`src/api/manuscript/narration.py`, `narration_voices.py` and
`narration_pronunciation.py`. They are references, not a hosted filesystem API.

- Storage boundary: `narrationStore` in
  `static/js/features/manuscript/narration.js` provides `list`, `save`, `audio`,
  `remove`, `restore`. Responses must retain the established record fields.
- Upload accepts metadata JSON plus mono PCM16 WAV at 24 kHz. Source offsets are
  UTF-16 code units; word offsets are within selected text, times are seconds.
  Reject fractional/boolean offsets and NaN/Infinity. Save only complete audio.
- The server derives IDs from SHA-256 of compact JSON
  `[block_id,source_text,start,end,voice,engine]`. Engine identity distinguishes
  q8/fp32, language frontend, non-default generation pace and punctuation gaps.
  Playback speed/pitch do not affect stored audio or cache identity.
- Chapter titles are separate `target_type:'chapter_title'` records, with
  `chapter_id` and virtual `block_id:'chapter-title:<id>'`. Local chapter IDs are
  positive integers; Cloud must explicitly adapt its own chapter identity format
  and authorize the target. A virtual title is not a manuscript block or path.
- `replaces` must identify an authorized recording of the same target. Same-ID
  regeneration replaces, not duplicates. Changed-ID replacement retires only
  the chosen old record after successful publication, not unrelated selections.
- Generate chapter includes its title and only that chapter, regardless of
  Continue chapters. Narrate chapter may continue **saved playback** afterward,
  but must not generate later chapters. Book scope is the whole active document,
  not every document in the project. Snapshot menu context and selection ranges.
- Reuse complete current recordings. Missing-only generation, forced scoped
  regeneration, chapter/book Delete all and per-entry Delete/Undo retain their
  different semantics. The list shows the focused chapter unless in Full Text.
- Export is one lossless WAV of saved recordings; it does not generate speech.
  Scope can be selection/chapter/book, includes chapter titles, validates current
  source and word boundaries, and refuses a missing passage. Pitch/speed changes
  are playback-only and not baked into exported WAVs.
- Inference is serial and saves each completed passage. Closing the panel pauses
  playback but does not cancel an active job. Stop finishes the current passage.
  Mute/pause does not stop generation. Reload, sleep, tab suspension or process
  termination can interrupt it: rerunning reuses completed work. There is no
  durable overnight server queue or keep-awake guarantee.

## Test and release requirements

Fast/API/DSP narration regressions are now part of the normal `./verify-release`
discovery. Existing local project-tab and empty-paragraph navigation tests have
also been renamed into its versioned lanes; no verifier, hash firewall or per-test
watchdog was relaxed.

Focused command, from Editor root:

```sh
.venv/bin/python tools/narration/check.py --runtime --browser chromium --browser firefox --browser webkit
```

Use `--real` for short real-model UI, punctuation and IT/JA/ZH samples after
installing the pinned assets/dictionaries. It deliberately does not run a
full-book endurance test. The separate `verify-narration` GitHub workflow adds
runtime/dictionary/browser validation; **its first hosted CI execution is still
pending**. Make that job a required pre-release check; existing Cloud dispatch
does not by itself prove this additional job passed.

Before production enablement, the Cloud agent must run the full Editor and Cloud
release gates, hosted negative-access/quota/privacy tests, real CPU fallback and
GPU failure tests on supported browsers, popup login under actual headers,
download/playback/export checks and native-speaker listening QA. Playwright UI
success with a deterministic engine is not real Safari/Firefox model-inference
certification. Full-book endurance/sleep/resume testing is intentionally deferred.

### Results from this local pass

- Final focused narration API/frontend/DSP run: **68 passed**.
- Broader manuscript/project/unit/security/privacy run: **279 passed**. This
  overlaps the focused checks; these are separate runs, not an additive total.
- Existing project-tab/lease and empty-paragraph arrow regressions: **5 passed**;
  both now participate in normal release discovery. No untracked test modules.
- Deterministic narration UI workflows: Chromium, Firefox and WebKit passed,
  including settings, chapter scope, early playback, export, Delete/Undo,
  voice-library actions and startup gating. The Firefox fixture now establishes
  a fresh selection and allows tiny fractional-pixel rounding differences.
- Real CPU/model workflow: four English and three Spanish voices produced
  distinct non-silent recordings, word timings, cached replay, replacement and
  Delete/Undo, with no external browser requests. Real IT/JA/ZH Editor-bound
  samples and English/Spanish punctuation preview/WAV equivalence also passed.
- Installer reused and checksum-verified existing q8/fp32/config/default-voice
  files successfully; failed/corrupt/interrupted staging behavior has a regression.
- The initial full `./verify-release` stopped at the intentional
  `requirements.txt` joserfc 1.7.5 → 1.7.6 change, after both dependency audits
  passed. The owner subsequently approved the one-line upgrade and checksum
  update. Only the requirements entry in the protected manifest was updated;
  protected-file integrity now passes. The verifier and per-test watchdogs
  remain unchanged.
- The approved full rerun completed: **2,133 passed / 9 failed / 2 skipped**,
  2,144 executed, no process timeouts. Unit: 198 passed. Durable E2E: 92 passed.
  Core/API: 1,709 passed, 8 failed, 2 skipped. Current browser after the normal
  fresh-process retries: 134 passed, 1 failed. Preserve `verification-report.txt`
  as the evidence for this run; it is not a green release signoff.
- Follow-up corrected all eight failing core/API contracts, verified together
  with narration security/API/frontend/DSP coverage: **91 passed**. Fixes include
  complete computed locale keys (and seven-language generated-marker checks),
  exact eight-button toolbar/32-route inventories, and refreshed generated
  dependency declarations. Narration no longer imports storage directly: current
  source reads use the canonical manuscript-document owner and filesystem context
  uses the existing bound local request context. The frozen local-dependency
  inventory was preserved, not expanded or bypassed.
- Hosted route coverage now enumerates exactly the eight intentionally disabled
  narration routes; every other route retains its coverage requirement. New tests
  prove all eight reject both hosted modes before body parsing. Cloud runtime
  files were not edited. When Cloud intentionally activates these routes, update
  those contracts together with its authorized adapter and activation review.
- A manual focused rerun while the full browser lane was still busy had one
  project-fixture setup timeout under the unchanged 10s watchdog. After the full
  gate finished, the final isolated 91-test rerun passed. No timeout was relaxed.
- There has **not** been another entire full-gate run after these corrections.
  The confirmed hosted import failure below remains open for the Cloud agent;
  rerun the complete Editor/Cloud gates before deployment.
- One existing Starlette TestClient/httpx deprecation warning remains; it did
  not fail the runs and no framework/test-client replacement was attempted.

### Confirmed hosted import failure — Cloud agent follow-up

`tests/test_v10551_browser.py::test_new_project_import_resumes_batches_and_preserves_source`
failed in both the initial and fresh-process retry. After a failed import batch
is resumed successfully into a new project, the test navigates back to the
original source project. At line 40 it expects the first `.manuscript-block` to
contain `Original must survive`; no matching element is found within the existing
5s assertion budget. This is a missing rendered paragraph assertion, **not proof
that canonical source data was deleted**. Diagnose source readback, boot/lease
state and navigation before concluding data loss. Reproduce with:

```sh
.venv/bin/python -m pytest -q tests/test_v10551_browser.py::test_new_project_import_resumes_batches_and_preserves_source
```

The simulated hosted runtime/import code and its test were deliberately left
unchanged in this local narration pass. Another startup/cache browser case,
`test_failed_workspace_discards_speculative_metadata_without_editable_ui`,
recovered on the verifier's normal retry and is not counted as a confirmed
failure. Neither result should be silently omitted from release review.

## Local stabilization and security pass

Fixed in this pass, with regression coverage:

- Stale source was only checked before an awaited audio upload; it is checked
  again immediately before publication. Cache reuse still validates source.
- Failed same-ID metadata publication could overwrite the previous WAV. Local
  publication now keeps a private hard-link rollback copy until both files are
  published. This handles in-process errors, **not an atomic two-file crash
  transaction**. A sudden OS/process crash may leave derived cache artifacts;
  canonical manuscripts are not written by this owner.
- Fractional timing offsets were accepted; offsets are now strict integers,
  nonfinite times are rejected, and unknown per-word fields are discarded.
- Malformed/mismatched cache metadata and corrupt Undo data fail closed. Expired
  Undo tokens cannot bypass cleanup by calling restore directly.
- The audio handler's size check happened after multipart spooling. The route
  now also bounds the incoming stream before parsing, including chunked bodies.
- Narration GETs now require a loopback Host authority, in addition to existing
  mutation Origin checks. Foreign/malformed authorities are rejected before any
  narration read, reducing DNS-rebinding exposure. The shared narration boundary
  also caps control/pronunciation bodies at 256 KiB. This is narration-scoped,
  not a claim that every older local endpoint has received the same Host audit.
- CPU inference had no stall deadline and disposal could leave a pending job.
  Both CPU/GPU synthesis now have inactivity deadlines (180/90 seconds), disposal
  rejects pending synthesis, concurrent calls are refused, and late callbacks
  are ignored. GPU has one bounded CPU fallback; failed workers are retired.
- Pronunciation contention now returns 429/Retry-After rather than parking all
  API worker threads behind a single dictionary lock.
- Voice-download redirects reject credentials/nonstandard ports. Initial model
  installation now verifies size/SHA-256 and stages downloads before publication.
- Export chooses the current effective language voice. Narration startup disables
  actions/voice selection until preferences and the catalog load, avoiding a
  choice being replaced by a delayed startup response.

Dependency checks on October 30:

- `pnpm audit --json` for the locked narration graph: no known advisories.
- Installed Python `pip-audit --strict`: initial findings in joserfc 1.7.5,
  pytest 9.0.2 and requests 2.32.5. Updated Editor pins to 1.7.6, 9.0.3 and
  2.33.0 respectively; repeat installed-environment audit: no known advisories.
  `pip check`: no broken requirements. These were existing shared dependencies,
  not demonstrated narration exploits. No advisory was suppressed.
- Advisory references:
  [joserfc](https://github.com/authlib/joserfc/security/advisories/GHSA-9pg5-8c7x-5hfx),
  [Requests](https://github.com/psf/requests/security/advisories/GHSA-gc5v-m9x4-r6x2),
  [pytest advisory](https://github.com/advisories/GHSA-6w46-j5rx-g56g).
  The fetched joserfc maintainer page named 1.7.5 as patched whereas the live
  scanner required 1.7.6; the stricter scanner result was followed and rechecked.
  Requests states ordinary HTTP usage is unaffected by its extraction-helper bug.

Audit results are time-bound and not a penetration-test certification. Remaining
local limits include no aggregate narration-cache disk quota, lazy tombstone
cleanup, possible hard-crash orphan files, client resource limits for large WAV
exports, and no whole-book endurance signoff. Local process/OS filesystem access
is trusted; this is not a sandbox against another process running as the user.
