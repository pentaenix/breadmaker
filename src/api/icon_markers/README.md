# Icon Markers ownership

`api.icon_markers` is a composition boundary only.

- `routes.py` — HTTP/domain behavior.
- `contracts.py` — request payload models owned by this domain.

Callers that need implementation helpers target `routes` explicitly. Do not
recreate `api/icon_markers.py` or sibling `icon_markers_contracts.py` facades.
