# Editor 10.5.96 native import acceptance

This change repairs native project-package import without activating the separate
experimental manuscript-revision storage format. Existing-project manuscript
replacement/append and large chapter Undo are separate work.

## Representative project

The offline acceptance project uses a public-domain novel and synthetic feedback:
67,655 main-manuscript words, 34 main chapters, 68 scenes, four writing tabs,
three images, eight Canvases, three Maps, 24 Tasks, eight Checkpoints, six Cuts,
two Structure Versions and three Reviews containing four frozen rounds.
No unpublished manuscript or account credentials are committed as a test fixture.

The original 4,690,625-byte package was imported into the hosted services using
MemoryDocumentStore/MemoryObjectStore. The new project stayed invisible until the
single final publication transaction. Repeated finish returned the same project.
All 2,731 planned records were compared by their complete parent/entity paths,
not just their leaf IDs. Association IDs can legitimately repeat across Reviews.
The actual provider export preserved every planned payload and all three image
byte streams. Its native export passed the native importer again, with identical
entity counts, 30 annotations and three assets. The original package was unchanged.

Frozen-history checks additionally verified all four original Review content
hashes, all eight Checkpoint snapshot hashes and compressed snapshot bytes, frozen
chapters/scenes/blocks/questions/markers, historical images, anchors, feedback,
event targets, all 259 cross-feature links and both saved Structure Version layouts.
Current live edits and newly created hosted chapters were tested separately to
ensure they survive without rewriting frozen history.

## Measured local timings

- Pure original-package validation: 1.32 seconds.
- Durable preparation: 1.94 seconds.
- 46 staging batches: 78.38 seconds total; slowest batch 4.86 seconds.
- Atomic publication: 0.048 seconds.
- Actual hosted-to-native export: 6.16 seconds.
- Re-import validation of the native result: 1.23 seconds.

These are correctness-test timings, **not Cloud latency measurements**. The memory
transaction driver copies the entire store even for read-only authority checks.
The import made 1,840 immutable writes and 1,886 object reads. Canonical per-block
copies remain available, alongside existing read bundles that accelerate later
reads.

A small native package also passed the real local hosted app's file picker,
resumable API, reload, source-preservation and re-export browser test. Native
Structure Version selection, atomic activation, Undo, Redo and reload passed a
separate real hosted browser test. Neither test mocks its HTTP transport.

## Remaining release requirements

Focused tests and the offline fixture do not replace the complete release gate,
protected-main CI, private Cloud checks or an authorized production deployment.
Production import speed remains unmeasured until a single coordinated live check
after deployment. Abandoned import content remains retained; no physical purge
or completed erasure is claimed by this feature.
