from __future__ import annotations
"""Review HTTP domain package; behavior lives in explicit subdomain owners."""
from fastapi import APIRouter
from . import presets, portable, reader, questionnaires, feedback, participants, markers, lifecycle
router=APIRouter()
for _owner in (presets, portable, reader, questionnaires, feedback, participants, markers, lifecycle):
    router.include_router(_owner.router)
