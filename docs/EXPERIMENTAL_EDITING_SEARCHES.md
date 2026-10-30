# Experimental English editing searches

Local-only, one-shot actions in Project Settings → Experimental Features →
Editor Highlights. The seven actions live in this separate settings screen. No
background scanning, rewriting, model download, account or network dependency.
Each action scans **all paragraph blocks in the active manuscript document**,
including offscreen chapters; not other documents, titles, notes or images.

| Action | Existing marker name |
| --- | --- |
| Highlight telling words | Telling words |
| Highlight filter words | Filter words |
| Highlight -ly adverbs | -ly adverbs |
| Highlight qualifiers | Qualifiers |
| Highlight wordy phrases | Wordy phrases |
| Highlight possible redundancies | Possible redundancies |
| Highlight named emotions | Named emotions |

Names are canonical English user-content labels; interface copy is translated in
all seven UI languages. Existing names are matched case-insensitively with outer
whitespace ignored. Existing descriptions, colours and manual highlights survive.
New markers use the ordinary recommended-colour algorithm: golden-angle candidates,
contrast checks against light/dark manuscript surfaces, readable text and separation
from existing colours. Dense palettes fall back to the most distinct readable unused
candidate, never an exact duplicate. Existing markers are not recoloured. An empty
search creates no empty marker.

`editing_descriptions.py` supplies a different English explanation for each marker:
why the category can weaken a passage, a practical revision check, and reasons to
keep an intentional use. These are original summaries informed by the user's course
guide, not copied lesson text. Rerunning a search upgrades the exact old generic
description only when that marker has a feature-owned annotation. User-written
descriptions are never replaced; the upgrade is part of the same Undo operation.

## Matching and limitations

`src/api/annotations/editing_lexicon.py` owns curated English vocabulary and explicit
inflections, including US/UK spellings and irregular verbs. `editing_search.py`
matches whole words and phrases with original Unicode offsets, case preserved,
flexible phrase whitespace and longest overlapping phrase preference. -ly uses an
allowlist, not a suffix test: friendly, family, reply, apply, lovely and similar
non-adverbs do not match. Qualifiers and emotion constructions have conservative
context rules. This is **not** a part-of-speech parser or semantic diagnosis.
Ambiguous words, intentional telling and useful adverbs can still match. Review
each suggestion in context; absence of a highlight does not establish good prose.
Expand vocabulary with positive and negative regression examples, not stemming.
These original lists are inspired by the course categories, not copied course text.

## Persistence, reruns and Undo

`POST /api/annotations/editing-search` accepts `{scan, tab_id, action}` (seven literal
categories, strict integer document ID; action defaults to `scan`, with `clear`,
`remove`, `recheck` also accepted). The canonical annotations route owns a
single project-bound SQLite transaction, reading current stored text. It validates
the document and limits the pass to 5 million total paragraph characters, 100,000
characters in any single paragraph, and 20,000 matches
before creating or changing a marker. Rejection/failure cannot publish a partial
pass. Normal CSRF, local project routing and editor-lease middleware still apply.
No browser-supplied text, patterns, file paths, SQL or source HTML are accepted.

Generated highlights use ordinary annotations with body `editing-search/v1:<scan>`.
Reruns keep exact owned results with stable IDs, remove obsolete/stale/duplicate
owned results in this document, and skip exact manual equivalents. Other categories,
documents and manual results are never cleaned by a normal scan.

Clicking an existing category in Editor Highlights opens Clear / Remove marker /
Recheck, also available by right-click. Clear deletes only stamped results in the
current document and keeps the marker, manual highlights and other documents.
Recheck computes and validates the new scan before replacing all owned results in
this document, preserving exact manual equivalents. Remove marker explicitly
confirms project-wide deletion of the marker and all its highlights, including
manual and other-document uses; linked items retain their content via existing
foreign-key SET NULL rules. All three use ordinary structural Undo/Redo.

`GET /api/annotations/editing-search?tab_id=...` returns current marker identity
and owned-result count for each category. The local `ui_state` registry key
`editing-search-markers/v1` binds each category to marker ID + precise creation
timestamp, so renamed or cleared markers remain identifiable. Registry hints are
always validated against current project labels; deleted/reused IDs cannot silently
bind a different marker. Owned annotations provide discovery for pre-registry
markers, then canonical names allow manual-marker reuse. UI state is excluded from
structural Undo, so the registry intentionally retains tombstoned hints: Undo can
restore the exact marker and be recognised again, while absent markers are ignored.
No new table, schema migration or hosted persistence is introduced.

The frontend flushes saves, runs the pass inside existing structural Undo, refreshes,
then leaves Editor Highlights open with a result count so multiple searches can run
consecutively. Ordinary marker navigation remains available in each marker's menu.
One Undo/Redo covers the entire pass. It
captures the project/document and rejects stale UI context; buttons cannot double-run.
Generated offsets are Python Unicode code points, as expected by backend anchors.
Only stamped highlights use the frontend code-point-to-UTF16 adapter, so emoji do
not shift these ranges and legacy annotations keep their established interpretation.

The ordinary marker ⋮/right-click menu also offers Hide highlights / Show highlights
for every marker, not just experimental ones. This is a browser-local per-user,
per-project display preference. It does not delete annotations, alter source text,
hide Canvas colour meanings or modify another collaborator's view. Both marker paint
and marker-navigation paint respect it; ordinary text Find still works independently.

## Integration boundary

Developed on `codex/experimental-editing-markers` in its own worktree, independent
of the deploying narration work. No Cloud checkout/runtime/deployment changes.
The UI is hidden outside local mode, and the handler denies non-local runtimes
before native storage. Hosted route-coverage tests explicitly inventory these two
local-only GET/POST routes while continuing to cover every hosted product route.

When merging alongside narration, retain both features' scripts, translations,
tests and explicit local-only route coverage; rebuild generated indexes and rerun
verification. This branch is not a production release or Cloud implementation.

## Development verification

The matcher, API, real-browser settings actions, Unicode CSS highlights, ordinary
marker navigation, rerun reuse, structural Undo/Redo, scope isolation and atomic
limits have focused regression coverage. All seven locale catalogs have the same
editing-search keys and interpolation placeholders. The existing protected-file
and hosted-boundary checks pass. The full release gate has not been run for this
experimental branch.

A broader history regression check encounters an unrelated old fixture mismatch:
`tests/test_v4131.py::test_v4131_unrelated_corrupt_history_object_cannot_veto_structural_undo`
queries the removed native `revisions` table and fails before exercising Undo.
That test and the history storage implementation are unchanged here. Do not treat
this branch as release-ready until the integrated baseline and full gate are checked.
