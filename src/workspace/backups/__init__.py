from __future__ import annotations

"""Workspace backup service package. Public composition only."""

from .destinations import BackupDestinationMixin
from .authority import BackupAuthorityMixin
from .state import BackupStateMixin
from .publication import BackupPublicationMixin
from .catch_up import BackupCatchUpMixin
from .inventory import BackupInventoryMixin

class BackupWorkspaceMixin(
    BackupDestinationMixin,
    BackupAuthorityMixin,
    BackupStateMixin,
    BackupPublicationMixin,
    BackupCatchUpMixin,
    BackupInventoryMixin,
):
    """Composes backup responsibilities onto Workspace without re-exporting internals."""
    pass

__all__=['BackupWorkspaceMixin']
