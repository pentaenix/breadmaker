from __future__ import annotations

"""M9B final compatibility audit. NEVER claim that a fail-closed export
is a successful local restoration. The independent hosted-v3 archive remains
usable even when a local Schema-42 translation is structurally impossible.
"""

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass

from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.local_portability import LocalPortabilityError, to_local_package
from hosted.packages import HostedProjectPackageService


@dataclass(frozen=True)
class PortabilityAudit:
    project_id: str
    project_generation: int
    hosted_archive_sha256: str
    hosted_archive_bytes: int
    entity_counts: tuple[tuple[str, int], ...]
    local_schema42_compatible: bool
    local_export_blocker: str | None
    local_package_sha256: str | None


class HostedPortabilityAuditService:
    """Read-only authenticated snapshot audit; no writes or silent coercion."""

    def __init__(self, projects):
        self.projects = projects
        self.package = HostedProjectPackageService(projects)

    def audit(self, project_id: str, owner_user_id: str) -> PortabilityAudit:
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        before = self.projects.domain.project_generation(pid, uid)
        project = self.projects.authority.get_for_user(pid, uid)
        frozen = self.package.export_bytes(pid, uid)
        with zipfile.ZipFile(io.BytesIO(frozen)) as archive:
            entities = json.loads(archive.read('project.json'))['entities']
        blocker, local_digest = None, None
        try:
            local = to_local_package(frozen, project_generation=before,
                created_at=str(project.get('created_at') or ''),
                updated_at=str(project.get('updated_at') or ''))
            local_digest = hashlib.sha256(local).hexdigest()
        except LocalPortabilityError as exc:
            blocker = str(exc)
        if self.projects.domain.project_generation(pid, uid) != before:
            raise DocumentConflict('HOSTED_PORTABLE_AUDIT_CONCURRENT_MUTATION')
        return PortabilityAudit(
            project_id=pid, project_generation=before,
            hosted_archive_sha256=hashlib.sha256(frozen).hexdigest(),
            hosted_archive_bytes=len(frozen),
            entity_counts=tuple(sorted((key,len(values)) for key,values in entities.items())),
            local_schema42_compatible=blocker is None,
            local_export_blocker=blocker, local_package_sha256=local_digest,
        )
