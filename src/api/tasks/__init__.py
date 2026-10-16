from __future__ import annotations

"""Tasks HTTP/domain package.

The package composes focused route owners in historical registration order and does
not re-export implementation helpers.
"""

from fastapi import APIRouter
from . import lifecycle, milestones, duplication

router=APIRouter()
for _owner in (lifecycle, milestones, duplication):
    router.include_router(_owner.router)
