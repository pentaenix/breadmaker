# Annotations ownership

`api.annotations` is a composition boundary only.

- `routes.py` — HTTP/domain behavior.
- `contracts.py` — request payload models owned by this domain.

Callers that need implementation helpers target `routes` explicitly. Do not
recreate `api/annotations.py` or sibling `annotations_contracts.py` facades.
