# Manuscript domain ownership

`api.manuscript` is a package boundary, not an implementation facade.

- `navigation.py` — bootstrap navigation projections and bookmark payloads.
- `document.py` — bootstrap, document reads, project title, and basic Chapter metadata.
- `images.py` — image blocks, rows, and image movement.
- `anchors.py` — bookmark/annotation/range reanchoring and repair.
- `chapter_structure.py` — paragraph-to-Chapter structural split.
- `chapters.py` — Chapter block persistence, dissolve/delete, and ordering.
- `scenes.py` — Scene ownership, ranges, persistence, and movement.

Callers import the canonical owner directly. Do not re-export implementation symbols from `api.manuscript.__init__`.
