# Editor Highlights: narration release handoff

User approved including this feature alongside narration. No production deployment
or Cloud repository changes were performed by the feature agent.

## Source locations

- Feature branch: `codex/experimental-editing-markers`, based on `fecfd56`.
- Feature worktree: `experimental-editing-markers/` beside the normal `breadmaker/`.
- Feature changes have also been applied selectively to normal `breadmaker/`,
  preserving its uncommitted narration source. That checkout is not the reconciled
  release worktree; do not replace current release files wholesale with its older
  baseline. The feature commit is the integration unit.
- Deployment agent's reconciled Editor checkout at handoff time:
  `LOCAL_ONLY/.deploy-work/auth-social-ui-worktree`, HEAD `661e3de`, version 10.5.68.
  The feature agent did not modify that worktree or its index.

## Product contract

Keep this English-only tool under Project Settings → Experimental Features →
Editor Highlights. It is seven one-shot actions, not enabled/disabled background
scanning: telling words, filter words, -ly adverbs, qualifiers, wordy phrases,
possible redundancies and named emotions. UI copy covers all seven interface
languages. Generated marker names/descriptions are intentionally English content.

Search scope is all paragraphs in the current document, including offscreen
chapters, not other documents or chapter titles. No source rewrites. Reuse markers,
descriptions/colours/manual highlights; recommended new colours are distinct and
readable. Dialog stays open between actions. Clicking an existing category offers:

- Clear: remove its generated highlights in this document, retaining the marker,
  manual highlights, other categories and other documents.
- Recheck: validate a fresh scan before atomically replacing the owned results.
- Remove marker: confirm project-wide deletion of the marker and all its
  highlights, including manual/other-document uses; linked items keep content.

Each action supports structural Undo/Redo. Renamed and cleared markers remain
tracked. Ordinary marker ⋮/right-click menus have Hide/Show for all markers, with
grey list styling while hidden. Visibility is browser-local per-user/project,
not deletion; it suppresses normal paint, marker-navigation paint and gutters.

## Native implementation / hosted work still required

The feature is implemented and tested natively, **not activated for hosted use**.
Do not remove the runtime guards without a real hosted implementation.

- Pure matching: `src/api/annotations/editing_search.py`, curated vocabulary in
  `editing_lexicon.py`; no model, paid service or network dependency.
- Practical marker explanations: `editing_descriptions.py`; existing custom copy
  survives. Only exact old feature boilerplate is replaced on rerun.
- Recommended-colour parity: `marker_colors.py` mirrors the browser picker.
- Native `GET /api/annotations/editing-search?tab_id=...` returns all category
  marker identities + counts. `POST` accepts `{scan, tab_id, action}`; action is
  `scan` (default), `clear`, `recheck`, or `remove`. Native tab IDs are integers;
  hosted contracts must accept canonical opaque document IDs instead.
- Native routes live in `api/annotations/routes.py`; no new native storage owner,
  table or schema migration. Limits: 5M total paragraph characters, 100K per
  paragraph, 20K matches. Native validation happens before annotation mutation.
- Ownership stamp: annotation body `editing-search/v1:<category>`. Preserve manual
  ranges and other documents. Marker identity registry `editing-search-markers/v1`
  lives in local project `ui_state`, keyed by category to ID + creation timestamp.
  Validate hints against existing project labels. Retained tombstoned hints allow
  Undo to recognise restored markers; current owned annotations and canonical names
  provide fallback discovery. Registry is not authorization.
- Browser owner: `features/projects/editing_search.js`; guards currently require
  local mode. Wire a real hosted capability only after the adapter is verified.
- Anchors are Unicode code-point offsets. Only stamped results use the new
  `markerRangeFromOffsets` UTF16 adapter, including marker navigation. Do not
  silently reinterpret legacy offsets. Preserve emoji/combining-mark tests.

Cloud integration must use existing authorized canonical project/document/marker
repositories, correct collaborator permissions, fresh-source revision checks and
bounded atomic publication/Undo. A 20K-result scan exceeds one Firestore batch:
do not publish partial results or depend on browser-loaded/lazy manuscript blocks.
Choose a project-shared tracking record, not an accidentally collaborator-private
preference, while preserving appropriate recovery/export ownership. Use existing
mutation/idempotency machinery and reject foreign IDs/revoked permissions before
reading or publishing manuscript data. No native SQLite/server filesystem bridge.

## Merge and verification

- Merge/cherry-pick the feature commit into the reconciled release lineage; resolve
  overlaps by preserving narration, authentication, frozen release gates and all
  locale keys. Do not copy whole old generated manifests over current release ones.
- `tests/test_v10353.py` must retain the eight explicit local-only narration routes
  plus both editing-search GET/POST routes until each real hosted adapter exists.
  No broad prefix exclusions or disabled security tests.
- Rebuild API/dependency/code indexes. Synchronize the new script's cache tag with
  the actual release version using the existing metadata tool (feature base is
  10.5.66, reconciled release was 10.5.68). No verifier/protected-hash weakening.
- Feature tests: `tests/unit/test_editing_search.py`, `test_marker_colors.py`,
  `tests/test_v10566_editing_search.py`, `test_v10566_editing_search_browser.py`.
  Latest isolated feature run: 25 API + 4 browser checks passed; matcher/colour,
  protected-file and hosted-boundary checks also passed in focused runs.
  Integration into the normal narration checkout passed 52 matcher/colour/API/
  boundary/integrity checks plus 11 browser/narration regression checks. All 41
  pre-existing files outside the intended overlapping merge paths retained their
  exact bytes. The reconciled release worktree remains untouched.
- Run the integrated narration + feature checks, then the full release gate/CI
  for the exact release revision. Focused success is not release approval.
- The feature branch's old base has a known unrelated history-fixture/schema
  mismatch in `test_v4131_unrelated_corrupt_history_object_cannot_veto_structural_undo`
  (queries removed `revisions` table). Preserve the newer reconciled baseline's
  corrections; do not reintroduce old fixtures while merging.

The approved dependency upgrades already present in narration's source are not
part of this feature-only commit. Preserve them and their reviewed integrity pin.
