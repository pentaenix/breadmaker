from __future__ import annotations

"""Breadmaker HTTP/domain API package.

One feature owns each route module. Composition is deterministic and dependency bindings
are explicit in each feature source. See CODEMAP.md and dependency_manifest.json.
"""

import importlib
from typing import Any

ROUTER_MODULES = (
    "manuscript",
    "annotations",
    "comments",
    "reviews",
    "tasks",
    "canvas",
    "icon_markers",
    "maps",
    "workspace_tools",
    "find_replace",
    "checkpoints",
    "durability",
    "import_export",
)


def install_routers(app: Any) -> None:
    """Import and register feature routers in the historical deterministic order."""
    for module_name in ROUTER_MODULES:
        # Annotations also contains pure matching shared by hosted mode. Its
        # namespace must not import SQL; only native composition asks for routes.
        owner=f"api.{module_name}" + (".routes" if module_name=="annotations" else "")
        module=importlib.import_module(owner)
        app.include_router(module.router)
