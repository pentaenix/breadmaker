from __future__ import annotations

"""Checkpoint HTTP/domain package. Implementation helpers are not re-exported."""

from fastapi import APIRouter
from . import lifecycle, comparison, apply

router = APIRouter()
for _owner in (lifecycle, comparison, apply):
    router.include_router(_owner.router)
