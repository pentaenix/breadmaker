from __future__ import annotations

"""Project-management HTTP composition boundary.

Implementation lives in explicit owners and is registered in the historical route order.
No implementation helpers are re-exported from this package.
"""

from fastapi import APIRouter
from . import profiles, workspace, metadata, destructive_actions, projects, members, packages, json_transfer, assets, health

router=APIRouter()
for _owner in (profiles, workspace, metadata, destructive_actions, projects, members, packages, json_transfer, assets, health):
    router.include_router(_owner.router)
