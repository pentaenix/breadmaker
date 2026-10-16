from __future__ import annotations

"""Workspace project service package. Composition only."""
from .recovery import ProjectRecoveryMixin
from .registry import ProjectRegistryMixin
from .packages import ProjectPackageMixin
from .trash import ProjectTrashMixin

class ProjectWorkspaceMixin(ProjectRecoveryMixin, ProjectRegistryMixin, ProjectPackageMixin, ProjectTrashMixin):
    pass

__all__=['ProjectWorkspaceMixin']
