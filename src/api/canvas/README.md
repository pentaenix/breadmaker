# Canvas ownership

`api.canvas` is a composition boundary only.

- `populate.py` — Populate catalog/templates and generated layouts.
- `developmental.py` — Developmental Canvas synchronization and snapshot services.
- `registry.py` — Canvas creation and read detail.
- `versions.py` — Structure Versions, developmental layout, and promotion.
- `lifecycle.py` — Canvas metadata, ordering, and deletion.
- `nodes.py` — Canvas node creation, mutation, positioning, and deletion.
- `edges.py` — Connector creation, mutation, validation, and deletion.
- `edge_styles.py` — Connector style contract constants.
- `structure.py` — Developmental preflight/apply and manuscript structure mutation.

Callers target the canonical owner directly. Do not recreate `api/canvas.py` or
re-export implementation helpers from `api.canvas.__init__`.
