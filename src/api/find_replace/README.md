# Find Replace ownership

`api.find_replace` is a composition boundary only.

- `routes.py` — HTTP/domain behavior.
- `contracts.py` — request payload models owned by this domain.

Callers that need implementation helpers target `routes` explicitly. Do not
recreate `api/find_replace.py` or sibling `find_replace_contracts.py` facades.
