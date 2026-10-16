"""Breadmaker workspace package.

Stable workspace types live at the package boundary. Internal implementation belongs
in focused owner modules; backend/API code should import an owner directly when it
needs an implementation helper.
"""

from backups.publication import PUBLICATION_SUFFIX

from .constants import APP_SCHEMA_VERSION, PACKAGE_VERSION
from .models import BackupOperation, BackupProvider, WorkspaceError
from .service import Workspace
from .filesystem import safe_name

workspace = Workspace()

__all__ = [
    'APP_SCHEMA_VERSION',
    'PACKAGE_VERSION',
    'PUBLICATION_SUFFIX',
    'BackupOperation',
    'BackupProvider',
    'WorkspaceError',
    'Workspace',
    'safe_name',
    'workspace',
]
