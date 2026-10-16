from __future__ import annotations

"""Durability HTTP/domain composition.

Cuts, History, Undo, Recovery, Backup Destinations, and verified backup operations
are separate owners. This package only preserves deterministic route composition order.
"""

from fastapi import APIRouter
from . import cuts, history, undo, history_baseline, recovery, backup_destinations, recovery_backups, backup_inventory, backups

router = APIRouter()
for _owner in (cuts, history, undo, history_baseline, recovery, backup_destinations, recovery_backups, backup_inventory, backups):
    router.include_router(_owner.router)
