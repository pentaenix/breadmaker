from __future__ import annotations

"""Editing-session History baseline endpoint."""

from fastapi import APIRouter
from backend.history_runtime import commit

router = APIRouter()

@router.post('/api/history/edit-baseline')
def edit_history_baseline():
    rid=commit('Before editing session','safety',True)
    return {'id':rid}
