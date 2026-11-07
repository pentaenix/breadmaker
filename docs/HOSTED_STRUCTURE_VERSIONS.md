# Hosted Structure Version activation

Imported native Structure Versions retain chapter/scene placements and their
saved free Canvas workspace. The hosted adapter in `hosted/canvas_versions.py`
applies those records to the current Canvas in one authorized, generation-fenced
Firestore transaction. It does not reorder or rewrite the Manuscript.

Activation restores placement, styles, free-object content and visibility,
connectors and missing-source lane reservations. A deleted manuscript source is
never recreated; the saved version keeps its historical missing item. Hosted
node/edge snapshot versions remain supported by the same atomic publication.

The UI uses opaque source IDs, the existing Structure Version list and ordinary
structural Undo/Redo. Repeat switches are locked while pending; modal dismissal
settles dirty-version confirmation and newer Canvas/project navigation wins over
late responses. Saved viewport values are actor-private presentation, restored
after canonical activation without widening the action journal's permissions.

The existing safety limits remain: no more than 100 changed canonical records
and 300 KB of before/after metadata in an activation plan (below the journal's
450 KB envelope). Larger affected sets fail explicitly before any change; this
is not unrestricted large-Canvas activation. Saved versions are not discarded.

Synthetic tests cover native chapter/scene layouts, 68 placements plus free
objects/edges, Undo/Redo, idempotent replay, missing history, member/reader denial,
generation races, injected provider failures and both limits. Executable
frontend tests cover opaque IDs, repeated/stale switches and modal dismissal.
The real hosted browser case uses two isolated synthetic native-shaped versions,
then clicks the existing version list, undoes/redoes and reloads against actual
hosted APIs. Local Structure behavior retains its existing browser regression.
