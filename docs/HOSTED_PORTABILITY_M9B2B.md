# Milestone 9B2B — Restricted historical portability

**Editor 10.3.70 / Cloud 10.4.38. USER verification pending.**

## Covered conversions

M9B2B extends the *portable local-v2 download only*. The immutable hosted-v3
recovery archive is unchanged and remains the authoritative comprehensive
recovery format for GCS/Firestore disaster recovery.

- **Manuscript Review rounds (limited):** A single, complete read-only
  manuscript round with a recognized hosted project snapshot is converted to
  local Schema-42 `review_snapshots`, frozen chapters/scenes/blocks and a linked
  restorable *derived* Book checkpoint. Snapshot rows are read from the frozen
  immutable historical payload, never the latest mutable block content. The
  converter recomputes the local-format frozen content hash and rebases all
  source chapter/tab/scene identities. Hosted 24-hex Review IDs are normalized
  to deterministic local 32-hex Review IDs; foreign references use the same map.
  The derived local checkpoint is labeled `portable baseline` and is not
  presented as an original user-created checkpoint.
- **Local version-1 checkpoint snapshots:** Recognized `version: 1` Book or
  Chapter checkpoints without frozen notes, annotations or markup are encoded
  as local zlib-compressed blobs with SHA-256 over canonical JSON and rebased
  nested chapter/tab/scene identities. Any referenced image requires verified
  project asset bytes, with local immutable checkpoint-asset relations rebuilt.
- **Manuscript Tabs:** `kind: manuscript` is explicitly translated to local
  `is_manuscript=1`. Other recognized secondary tab kinds map to `0`; unknown
  kinds or contradictory roles abort.
- **Firestore ancestry fields:** Ancestral IDs appearing *only* in a hosted
  document path may be excluded from a local child table if it has no matching
  column. Unknown data payload fields still abort; this is not an unchecked
  field-drop policy.

## Intentional refusals (not silently converted)

- Hosted Canvas-version frozen node/edge geometry, which cannot be represented
  by Schema-42 developmental placement history.
- Review rounds with frozen marker/comment/questionnaire history: hosted
  project snapshots do not contain the local-format immutable projections for
  selected markers and questions; any conversion claiming to restore them
  from present-day live state would invent history.
- Multiple hosted Review rounds without verifiable round ordinals, scoped or
  multitab Review rounds, review snapshots containing non-manuscript author
  material, or inconsistent Review lifecycle metadata.
- Hosted project-shaped checkpoint JSON and checkpoints with frozen
  annotation/comment histories. Merely wrapping such JSON in zlib is **not**
  a recognized local Checkpoint payload.
- Broken historical IDs, missing immutable asset bytes, dangling references,
  incompatible shapes, corrupt hashes, unrepresentable fields and oversized
  packages. All fail the export before a download is published.

This milestone is a **restricted history compatibility checkpoint**, not the
completion of full M9B portability. Complete advanced Review/Canvas history
parity requires a future explicit codec or a richer portable package version;
that must be designed deliberately rather than pretending incompatible
Schema-42 history is fully equivalent. M9C deletion and M9D scheduled backup
and recovery drills are still outstanding. M10 owns independent production
bucket policy/IAM; no live cloud is deployed.

## Verification and protected boundaries

`tests/test_v10370.py` adds operator-only credential-free hosted -> unchanged
local importer conformance for manuscript Tab role, frozen Review content vs
newer live text and linked checkpoint, native-version checkpoint SHA/content,
and deliberate refusal of unsupported history. The model/agent has **not** run
pytest, Playwright, the verifier or local app validation. The user's combined
full Editor + Cloud verifier is the release gate, covering the carried
unverified 10.3.68 and 10.3.69 changes as well.

Local Schema 42, local runtime/Workspace, release verifier, watchdogs,
frozen M3/M4 Firestore/GCS adapters and Cloud 10.4.38 are unchanged.
