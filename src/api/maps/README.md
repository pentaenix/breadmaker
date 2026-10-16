# Story Maps ownership

`api.maps` is a router-composition boundary only.

- `domain.py` — shared Map constants and row/name/reference primitives.
- `anchors.py` — manuscript chronology projection, anchors, detachment, and detail projection.
- `lifecycle.py` — Map create/read/update/delete/duplicate/reorder and Scene memberships.
- `lines.py` — Line create/update/delete/reorder.
- `items.py` — Stop/Span creation, mutation, and deletion.
- `junctions.py` — branch/merge endpoint validation and routes.
- `layout.py` — explicit automatic arrangement.
- `populate.py` — Populate, source-line sync, and manual duplication.

Cross-domain callers target the canonical owner directly. Do not recreate `api/maps.py`
or re-export implementation helpers from `api.maps.__init__`.
