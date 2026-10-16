# Reviews domain

`api.reviews` is a package because the Review feature contains several independently navigable subdomains. The package boundary (`__init__.py`) only composes routers; implementation belongs to the narrowest owner below.

- `domain.py` — shared Review primitives: names, participants, events, selection anchors, scope, permission normalization, questionnaire/marker lookup helpers.
- `snapshots.py` — immutable Review rounds, checkpoint-backed frozen manuscript snapshots, and snapshot refresh state.
- `presets.py` — Review-cycle templates and Populate.
- `portable.py` — `.breadreview` export/import, temporary portable sessions, package locking/encryption flow, portable reader writes.
- `reader.py` — reviewer-facing participant reader/session routes.
- `questionnaires.py` — reusable questionnaires and active-round questionnaire synchronization.
- `feedback.py` — feedback forms, highlights, comments, suggestions, writer processing, suggestion acceptance/rejection.
- `participants.py` — writer-side participant administration.
- `markers.py` — Review Marker labels and active-round marker synchronization.
- `lifecycle.py` — Review aggregate payloads, scope, create/update/duplicate/start, round lifecycle, archive, delete.

## Ownership rules

Callers import the canonical owner directly. Do not re-export helpers from `api.reviews.__init__`, and do not recreate a `reviews.py` facade. Cross-feature lazy calls name the submodule owner (for example `feature_call("reviews.lifecycle", "_review_rows")`). Shared mutable/process state belongs below the API layer, not in this package.
