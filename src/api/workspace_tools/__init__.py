from __future__ import annotations

"""Workspace Tools HTTP/domain package. Composition only."""
from fastapi import APIRouter
from . import references, bookmark, ui_state, documents
router=APIRouter()
for _owner in (references, bookmark, ui_state, documents):
    router.include_router(_owner.router)
