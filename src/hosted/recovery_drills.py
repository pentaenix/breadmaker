from __future__ import annotations

"""M9D: read-only disaster restore rehearsal in isolated in-memory providers.

No read/write to live project documents or the live GCS content bucket. Only
independent archived bytes are consumed; recovery archives are never erased.
The isolated importer is the actual hosted-v3 importer, not a mock decoder.
"""

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any

from hosted.firestore.ids import require_project_id, require_user_id
from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.repositories import HostedProjectRepository, HostedUserRepository, HostedStructuredRepository
from hosted.packages import HostedProjectPackageService, _SKIP
from hosted.recovery_archives import HostedRecoveryArchiveService
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.storage.memory import MemoryObjectStore


class RecoveryDrillError(ValueError):
    pass


@dataclass(frozen=True)
class RecoveryDrillResult:
    source_project_id: str
    restored_project_id: str
    archive_sha256: str
    entity_counts: tuple[tuple[str, int], ...]
    asset_digests: tuple[str, ...]
    semantic_match: bool
    isolated: bool = True


def _inventory(payload: bytes, expected_pid: str, *, allow_private: bool = False) -> tuple[dict[str, tuple], tuple[str, ...]]:
    """Strict content projection; do not quietly ignore unknown model groups."""
    from hosted.firestore.model_registry import MODELS
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            items = archive.infolist()
            names = [item.filename for item in items]
            if len(names) != len(set(names)) or any(
                item.file_size > 64 * 1024 * 1024 or item.filename.startswith('/')
                or '..' in item.filename.split('/') or '\\' in item.filename for item in items
            ) or sum(item.file_size for item in items) > 256 * 1024 * 1024:
                raise RecoveryDrillError('HOSTED_DRILL_ARCHIVE_MEMBERS_INVALID')
            manifest = json.loads(archive.read('manifest.json'))
            document = json.loads(archive.read('project.json'))
            if (manifest.get('format') != 'breadmaker-hosted-project'
                or manifest.get('format_version') != 3
                or manifest.get('source_project_id') != expected_pid
                or not isinstance(document.get('entities'), dict)):
                raise RecoveryDrillError('HOSTED_DRILL_FORMAT_INVALID')
            entities = document['entities']
            if any(logical not in MODELS or (logical in _SKIP and not
                    (allow_private and logical in {'project_asset','hosted_revision_history','member_bookmark','member_ui_state'}))
                    for logical in entities):
                raise RecoveryDrillError('HOSTED_DRILL_ENTITY_UNREPRESENTABLE')
            canonical: dict[str, tuple] = {}
            for logical, records in entities.items():
                if not isinstance(records, list):
                    raise RecoveryDrillError('HOSTED_DRILL_ENTITY_INVALID')
                packed = []
                for record in records:
                    if not isinstance(record, dict) or not isinstance(record.get('ids'), dict) or not isinstance(record.get('data'), dict):
                        raise RecoveryDrillError('HOSTED_DRILL_ENTITY_INVALID')
                    # Physical project identity changes on safe restore. Every
                    # other descendant ID/value must remain the same.
                    ids = {k:v for k,v in record['ids'].items() if k != 'project_id'}
                    data = {k:v for k,v in record['data'].items() if k != 'project_id'}
                    packed.append(json.dumps([ids,data],sort_keys=True,separators=(',',':'),ensure_ascii=False))
                canonical[logical] = tuple(sorted(packed))
            assets = []
            for name in names:
                if not name.startswith('assets/'):
                    continue
                digest = name.split('/')[-1].split('.')[0]
                if len(digest) != 64 or hashlib.sha256(archive.read(name)).hexdigest() != digest:
                    raise RecoveryDrillError('HOSTED_DRILL_ASSET_HASH_INVALID')
                assets.append(name)
            return canonical, tuple(sorted(assets))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RecoveryDrillError('HOSTED_DRILL_ARCHIVE_INVALID') from exc


class HostedRecoveryDrillService:
    """An offline maintenance boundary; not part of the user HTTP runtime."""

    def __init__(self, recovery: HostedRecoveryArchiveService):
        self.recovery = recovery

    def drill_archive(self, project_id: str, owner_user_id: str, *, operation: str) -> RecoveryDrillResult:
        """Owner-authorized drill against already published immutable archive."""
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        # Uses archive verified read and never touches the project source state.
        snapshot = self.recovery.read(pid, uid, operation=operation)
        return self._isolated_drill(snapshot, pid)

    def offline_drill(self, project_id: str, owner_user_id: str, *, operation: str) -> RecoveryDrillResult:
        """Disaster operator mode works without the original Firestore project.

        Requires privileged, independent recovery-bucket access. Not routed to
        the public API; it accepts only the original owner identity/operation.
        """
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        sid, name = self.recovery._key(pid, uid, operation)
        meta = self.recovery.recovery_objects.stat(name)
        if meta is None:
            raise RecoveryDrillError('HOSTED_DRILL_ARCHIVE_MISSING')
        archived = self.recovery._verify(project_id=pid, user_id=uid, snapshot_id=sid, meta=meta)
        snapshot = b''.join(self.recovery.recovery_objects.read_chunks(
            name, max_bytes=self.recovery.maximum, if_generation_match=archived.object_generation))
        if hashlib.sha256(snapshot).hexdigest() != archived.sha256:
            raise RecoveryDrillError('HOSTED_DRILL_ARCHIVE_CHANGED')
        return self._isolated_drill(snapshot, pid)

    @staticmethod
    def _isolated_drill(payload: bytes, source_pid: str) -> RecoveryDrillResult:
        source, source_assets = _inventory(payload, source_pid)
        isolated_docs, isolated_objects = MemoryDocumentStore(), MemoryObjectStore()
        # This deliberate fixed in-memory identity has NO production authority.
        drill_owner = 'usr_' + 'd' * 32
        ensure_hosted_user(isolated_docs, user_id=drill_owner, display_name='Isolated recovery auditor')
        authority = HostedProjectRepository(isolated_docs)
        users = HostedUserRepository(HostedStructuredRepository(isolated_docs))
        projects = HostedCloudProjectRepository(isolated_docs, isolated_objects, authority, users, lambda: drill_owner)
        importer = HostedProjectPackageService(projects)
        restored = importer.import_bytes(payload, drill_owner)
        new_pid = str(restored['id'])
        if new_pid == source_pid:
            raise RecoveryDrillError('HOSTED_DRILL_RESTORE_IDENTITY_NOT_FRESH')
        rebuilt, restored_assets = _inventory(importer.export_bytes(new_pid, drill_owner), new_pid)
        if rebuilt != source or restored_assets != source_assets:
            raise RecoveryDrillError('HOSTED_DRILL_SEMANTIC_MISMATCH')
        return RecoveryDrillResult(
            source_project_id=source_pid, restored_project_id=new_pid,
            archive_sha256=hashlib.sha256(payload).hexdigest(),
            entity_counts=tuple(sorted((name,len(items)) for name,items in source.items())),
            asset_digests=source_assets, semantic_match=True,
        )
