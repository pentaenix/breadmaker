from __future__ import annotations

"""M9C2: owner-only, read-only deletion inventory with optional exhaustive scan.

Physical erasure is NOT implemented or authorized by any plan or receipt.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from hosted.firestore.contracts import DocumentConflict, DocumentStore, ProjectUnavailable
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.firestore.model_registry import MODELS
from hosted.storage.contracts import ObjectStore
from hosted.purge_discovery import ProjectTreeDiscovery

MIN_TRASH_DAYS = 30
MAX_INVENTORY_ENTRIES = 25_000


def _utc(when: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(when).replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise ValueError('HOSTED_PURGE_TOMBSTONE_TIME_INVALID') from exc
    if parsed.tzinfo is None:
        raise ValueError('HOSTED_PURGE_TOMBSTONE_TIME_INVALID')
    return parsed.astimezone(timezone.utc)


def _serialize_date(when: datetime) -> str:
    return when.isoformat().replace('+00:00', 'Z')


@dataclass(frozen=True)
class PurgeInventory:
    project_id: str
    owner_user_id: str
    project_generation: int
    deleted_at: str
    eligible_after: str
    retention_elapsed: bool
    firestore_paths: tuple[tuple[str, ...], ...]
    content_objects: tuple[tuple[str, int, str], ...]  # name, generation, sha256
    retained_recovery_objects: tuple[tuple[str, int, str], ...]
    fingerprint: str
    # Strictly false: inventory completion never authorizes physical deletion.
    physical_purge_authorized: bool = False
    document_inventory_exhaustive: bool = False
    missing_parent_paths: tuple[tuple[str, ...], ...] = ()

    def public(self) -> dict:
        # No object names, document IDs or owner metadata leak into an ordinary
        # UI preview. The owner-only operator has the full internal dataclass.
        return {
            'project_id': self.project_id,
            'deleted_at': self.deleted_at,
            'eligible_after': self.eligible_after,
            'retention_elapsed': self.retention_elapsed,
            'firestore_documents_found': len(self.firestore_paths),
            'content_objects_found': len(self.content_objects),
            'recovery_archives_preserved': len(self.retained_recovery_objects),
            'physical_purge_authorized': False,
            'document_inventory_exhaustive': self.document_inventory_exhaustive,
            'orphaned_parent_references': len(self.missing_parent_paths),
            'fingerprint': self.fingerprint,
        }


class HostedPurgePlanner:
    """Conservative owner-only dry-run; never physically deletes anything.

    Live content and recovery must be separate object-storage boundaries. Only
    project-scoped keys are considered. User-scoped avatars, shared identity,
    import receipts and independent recovery objects are not purge candidates.
    """

    def __init__(self, projects, recovery_objects: ObjectStore, *,
                 retention_days: int = MIN_TRASH_DAYS,
                 now: Callable[[], datetime] | None = None,
                 max_entries: int = MAX_INVENTORY_ENTRIES,
                 discovery: ProjectTreeDiscovery | None = None) -> None:
        # Retain the canonical hosted project service: preparation receipts
        # delegate independently verified pre-delete backup reads through it.
        # Its Firestore/GCS stores are still the sole runtime authority.
        self.projects = projects
        self.documents: DocumentStore = projects.documents
        self.content_objects: ObjectStore = projects.objects
        self.recovery_objects = recovery_objects
        self.discovery = discovery
        self.now = now or (lambda: datetime.now(timezone.utc))
        if recovery_objects is self.content_objects:
            raise ValueError('HOSTED_PURGE_INDEPENDENT_RECOVERY_REQUIRED')
        live = getattr(getattr(self.content_objects, '_bucket', None), 'name', None)
        recovery = getattr(getattr(recovery_objects, '_bucket', None), 'name', None)
        if live and recovery and live == recovery:
            raise ValueError('HOSTED_PURGE_INDEPENDENT_RECOVERY_REQUIRED')
        self.retention_days = int(retention_days)
        if self.retention_days < MIN_TRASH_DAYS:
            raise ValueError('HOSTED_PURGE_RETENTION_FLOOR_REQUIRED')
        self.max_entries = int(max_entries)
        if self.max_entries < 1 or self.max_entries > MAX_INVENTORY_ENTRIES:
            raise ValueError('HOSTED_PURGE_INVENTORY_BUDGET_INVALID')

    def _known_documents(self, project_id: str) -> tuple[tuple[str, ...], ...]:
        """Discover registered-model paths; DOES NOT establish exhaustive proof.

        Every model is enumerated by the exact collection parent path. Nested
        children are found even when their logical entity name is not listed
        next to its parent in the registry. Unindexed orphan descendants are
        a documented hard blocker to the later purge executor.
        """
        root = ('projects', project_id)
        seen = {root}
        templates = sorted(
            {tuple(model.path_template.split('/')) for model in MODELS.values()
             if model.path_template.startswith('projects/{project_id}/')},
            key=lambda parts: (len(parts), parts),
        )
        for template in templates:
            # Enumerate collections using actual discovered ancestors, never
            # interpolate external request strings into Firestore paths.
            collection_index = len(template) - 2
            prefix = template[:collection_index]
            candidates = [root] if len(prefix) == 2 else [
                path for path in seen if len(path) == len(prefix) and
                all(t.startswith('{') and t.endswith('}') or t == part
                    for t, part in zip(prefix, path))
            ]
            for parent in sorted(candidates):
                collection = parent + (template[collection_index],)
                for doc in self.documents.query_collection(collection):
                    path = tuple(doc.path)
                    if len(path) != len(template) or path[:2] != root:
                        raise DocumentConflict('HOSTED_PURGE_INVENTORY_SCOPE_VIOLATION')
                    if any(not (expected.startswith('{') and expected.endswith('}') or expected == actual)
                           for expected, actual in zip(template, path)):
                        raise DocumentConflict('HOSTED_PURGE_INVENTORY_SCOPE_VIOLATION')
                    seen.add(path)
                    if len(seen) > self.max_entries:
                        raise ValueError('HOSTED_PURGE_INVENTORY_TOO_LARGE')
        return tuple(sorted(seen, key=lambda p: (len(p), p)))

    def preview(self, project_id: str, owner_user_id: str) -> PurgeInventory:
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        root = ('projects', pid)
        before = self.documents.get(root)
        owner = self.documents.get(root + ('members', uid))
        if before is None or owner is None or owner.data.get('role') != 'owner':
            raise ProjectUnavailable('PROJECT_NOT_FOUND')
        data = before.data
        if data.get('status') != 'deleted' or not data.get('deleted_at'):
            raise ValueError('HOSTED_PURGE_TOMBSTONE_REQUIRED')
        deletion_time = _utc(str(data['deleted_at']))
        clock = self.now()
        if clock.tzinfo is None:
            raise ValueError('HOSTED_PURGE_CLOCK_INVALID')
        clock = clock.astimezone(timezone.utc)
        if deletion_time > clock:
            raise ValueError('HOSTED_PURGE_TOMBSTONE_IN_FUTURE')
        eligible = deletion_time + timedelta(days=self.retention_days)
        if self.discovery is not None:
            tree = self.discovery.scan(pid, max_entries=self.max_entries)
            if not tree.complete or root not in tree.documents:
                raise DocumentConflict('HOSTED_PURGE_DISCOVERY_INCOMPLETE')
            docs = tree.documents
            if ({p for p, token in tree.document_states if token} != set(docs)
                    or len(tree.document_states) != len(docs)):
                raise DocumentConflict('HOSTED_PURGE_DISCOVERY_REVISION_INCOMPLETE')
            doc_states = tree.document_states
            missing_parents = tree.missing_parents
            if set(docs) & set(missing_parents):
                raise DocumentConflict('HOSTED_PURGE_DISCOVERY_INCONSISTENT')
            if any(p[:2] != root for p in (*docs, *missing_parents)):
                raise DocumentConflict('HOSTED_PURGE_DISCOVERY_SCOPE_VIOLATION')
        else:
            docs = self._known_documents(pid)
            doc_states = ()
            missing_parents = ()
        prefix = f'projects/{pid}/'
        content = self.content_objects.list_prefix(prefix)
        if any(not obj.name.startswith(prefix) for obj in content):
            raise DocumentConflict('HOSTED_PURGE_OBJECT_SCOPE_VIOLATION')
        recovery_prefix = f'recovery/v1/projects/{pid}/'
        recovery = self.recovery_objects.list_prefix(recovery_prefix)
        if any(not obj.name.startswith(recovery_prefix) for obj in recovery):
            raise DocumentConflict('HOSTED_PURGE_RECOVERY_SCOPE_VIOLATION')
        if len(docs) + len(missing_parents) + len(content) + len(recovery) > self.max_entries:
            raise ValueError('HOSTED_PURGE_INVENTORY_TOO_LARGE')
        # A project entity commit must advance the root generation, but the
        # present query protocol does not guarantee a consistent descendant
        # scan. This is a preview only; never use its hash as purge authority.
        after = self.documents.get(root)
        if after is None or after.revision != before.revision or after.data != before.data:
            raise DocumentConflict('HOSTED_PURGE_CONCURRENT_MUTATION')
        live_objects = tuple(sorted((obj.name, int(obj.generation), obj.sha256) for obj in content))
        saved_objects = tuple(sorted((obj.name, int(obj.generation), obj.sha256) for obj in recovery))
        generation = int(data.get('generation') or 0)
        if generation < 1:
            raise ValueError('HOSTED_PURGE_PROJECT_GENERATION_INVALID')
        summary = {'project_id': pid, 'owner': uid, 'generation': generation,
                   'deleted_at': str(data['deleted_at']), 'docs': docs, 'document_states': doc_states, 'missing_parents': missing_parents,
                   'content': live_objects, 'recovery': saved_objects}
        fingerprint = hashlib.sha256(json.dumps(summary, separators=(',', ':'),
                                               sort_keys=True).encode('utf-8')).hexdigest()
        return PurgeInventory(
            project_id=pid, owner_user_id=uid, project_generation=generation,
            deleted_at=str(data['deleted_at']), eligible_after=_serialize_date(eligible),
            retention_elapsed=clock >= eligible, firestore_paths=docs,
            content_objects=live_objects, retained_recovery_objects=saved_objects,
            fingerprint=fingerprint,
            document_inventory_exhaustive=self.discovery is not None,
            missing_parent_paths=missing_parents,
        )
