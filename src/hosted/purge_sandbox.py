from __future__ import annotations

"""M9C2B: bounded, durable purge *rehearsal*, physically isolated to memory.

This is not a production deletion adapter and is never composed into HTTP,
Cloud Run or a Google SDK. It really removes objects/documents from injected
MemoryObjectStore/MemoryDocumentStore fakes, for crash/replay conformance.
No environment variable, ordinary receipt, or request can enable cloud erasure.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.firestore.memory import MemoryDocumentStore
from hosted.purge_discovery import MemoryProjectTreeDiscovery
from hosted.purge_receipts import HostedPurgePreparationService
from hosted.purge_planning import _utc
from hosted.storage.memory import MemoryObjectStore

# An entire sandbox manifest is stored in ONE memory Firestore document. Real
# purge jobs will need bounded separately sealed manifest shards and GCP IAM.
MAX_SANDBOX_ITEMS = 128
MAX_SANDBOX_MANIFEST_BYTES = 128 * 1024
LEASE_SECONDS = 30
_KEY = re.compile(r'^[A-Za-z0-9_-]{1,160}$')


class SimulatedPurgeCrash(RuntimeError):
    """Crash after a GCS-like deletion but before durable receipt advancement."""


@dataclass(frozen=True)
class SandboxPurgeProgress:
    project_id: str
    state: str
    cursor: int
    total: int
    # Never present this as authorization to delete live project data.
    physical_purge_authorized: bool = False


class HostedSandboxPurgeExecutor:
    """Explicitly injected *exact* memory drivers; impossible to use with GCP.

    Each step removes at most one scoped object or document (except that the
    owner membership and project root are removed atomically together). This
    is a simulated execution protocol, NOT a real Firestore/GCS two-phase
    transaction or a substitute for M9C2C live-provider security review.
    """

    def __init__(self, preparation: HostedPurgePreparationService, *,
                 now: Callable[[], datetime] | None = None) -> None:
        planner = preparation.planner
        if (type(planner.documents) is not MemoryDocumentStore
                or type(planner.content_objects) is not MemoryObjectStore
                or type(planner.recovery_objects) is not MemoryObjectStore
                or type(planner.discovery) is not MemoryProjectTreeDiscovery
                or planner.discovery.documents is not planner.documents):
            raise ValueError('HOSTED_PURGE_SANDBOX_MEMORY_ONLY')
        self.preparation = preparation
        self.planner = planner
        self.documents = planner.documents
        self.content = planner.content_objects
        self.recovery = planner.recovery_objects
        self.now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _identity(pid: str, uid: str, key: str) -> str:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError('HOSTED_PURGE_OPERATION_KEY_INVALID')
        return hashlib.sha256(f'{pid}\0{uid}\0{key}'.encode('utf-8')).hexdigest()

    @staticmethod
    def _receipt_path(pid: str) -> tuple[str, ...]:
        return ('purge_sandbox_executions', pid)

    @staticmethod
    def _progress(data: dict) -> SandboxPurgeProgress:
        return SandboxPurgeProgress(str(data['project_id']), str(data['state']),
                                    int(data['cursor']), int(data['total']))

    def _clock(self) -> datetime:
        instant = self.now()
        if instant.tzinfo is None:
            raise ValueError('HOSTED_PURGE_SANDBOX_CLOCK_INVALID')
        return instant.astimezone(timezone.utc)

    def _verify_archive(self, data: dict, pid: str, uid: str) -> None:
        # This also rehashes the actual bytes, with the exact GCS generation.
        meta = self.recovery.stat(str(data['archive_name']))
        if (meta is None or meta.generation != data['archive_generation']
                or meta.sha256 != data['archive_sha256']):
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_BACKUP_CHANGED')
        snapshot = str(meta.metadata.get('breadmaker-snapshot-id') or '')
        verified = self.preparation.recovery._verify(
            project_id=pid, user_id=uid, snapshot_id=snapshot, meta=meta,
        )
        if (verified.sha256 != data['archive_sha256']
                or verified.project_generation != data['project_generation'] - 1):
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_BACKUP_CHANGED')

    def _assert_tombstone(self, pid: str, uid: str, data: dict, tx=None) -> None:
        store = tx if tx is not None else self.documents
        root = store.get(('projects', pid))
        owner = store.get(('projects', pid, 'members', uid))
        if root is None or owner is None or owner.data.get('role') != 'owner':
            raise ProjectUnavailable('PROJECT_NOT_FOUND')
        if (root.data.get('status') != 'deleted'
                or root.data.get('deleted_at') != data['deleted_at']
                or int(root.data.get('generation') or 0) != data['project_generation']):
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_TOMBSTONE_CHANGED')
        if self._clock() < _utc(data['deleted_at']) + timedelta(days=self.planner.retention_days):
            raise DocumentConflict('HOSTED_PURGE_RETENTION_NOT_ELAPSED')

    @staticmethod
    def _check_scope(pid: str, row: dict) -> None:
        root = ('projects', pid)
        for entry in row['documents']:
            path = tuple(entry['path'])
            if len(path) < 2 or len(path) % 2 or path[:2] != root:
                raise DocumentConflict('HOSTED_PURGE_SANDBOX_SCOPE_VIOLATION')
        for entry in row['objects']:
            if not str(entry['name']).startswith(f'projects/{pid}/'):
                raise DocumentConflict('HOSTED_PURGE_SANDBOX_SCOPE_VIOLATION')
        if not str(row['archive_name']).startswith(f'recovery/v1/projects/{pid}/'):
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_SCOPE_VIOLATION')

    def arm(self, project_id: str, owner_user_id: str, *, operation_key: str) -> SandboxPurgeProgress:
        """Bind a frozen rehearsal manifest to M9C2A's verified preparation.

        Calling arm does not delete anything. There is at most one record per
        project, and it cannot be silently replaced by a different key.
        """
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        operation_hash = self._identity(pid, uid, operation_key)
        existing = self.documents.get(self._receipt_path(pid))
        if existing is not None:
            data = dict(existing.data)
            if data.get('receipt_id') != operation_hash or data.get('owner_user_id') != uid:
                raise DocumentConflict('HOSTED_PURGE_SANDBOX_RECEIPT_CONFLICT')
            self._check_scope(pid, data)
            return self._progress(data)
        prepared = self.preparation.resume_preview(pid, uid, operation_key=operation_key)
        plan = self.planner.preview(pid, uid)
        if (prepared.fingerprint != plan.fingerprint or not plan.document_inventory_exhaustive
                or not plan.retention_elapsed):
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_PREPARATION_STALE')
        tree = self.planner.discovery.scan(pid, max_entries=self.planner.max_entries)
        revisions = dict(tree.document_states)
        root = ('projects', pid)
        owner_path = root + ('members', uid)
        if root not in revisions or owner_path not in revisions:
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_OWNER_MISSING')
        ordinary = sorted((path for path in tree.documents if path not in {root, owner_path}),
                          key=lambda path: (-len(path), path))
        docs = ordinary + [owner_path, root]
        objects = [dict(name=name, generation=gen, sha256=digest)
                   for name, gen, digest in plan.content_objects]
        document_rows = [dict(path=list(path), revision=str(revisions[path])) for path in docs]
        total = len(objects) + len(ordinary) + 1  # final owner+root transaction
        if total > MAX_SANDBOX_ITEMS:
            raise ValueError('HOSTED_PURGE_SANDBOX_MANIFEST_TOO_LARGE')
        data = {
            'project_id': pid, 'owner_user_id': uid, 'receipt_id': operation_hash,
            'project_generation': plan.project_generation, 'deleted_at': plan.deleted_at,
            'initial_fingerprint': plan.fingerprint,
            'archive_name': prepared.archive_name,
            'archive_generation': prepared.archive_generation,
            'archive_sha256': prepared.archive_sha256,
            'objects': objects, 'documents': document_rows,
            'state': 'sandbox_armed', 'cursor': 0, 'total': total,
            'inflight': None, 'lease_owner': None, 'lease_until': None,
            'physical_purge_authorized': False,
        }
        self._check_scope(pid, data)
        if len(json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')) > MAX_SANDBOX_MANIFEST_BYTES:
            raise ValueError('HOSTED_PURGE_SANDBOX_MANIFEST_TOO_LARGE')
        self._verify_archive(data, pid, uid)

        def reserve(tx):
            self._assert_tombstone(pid, uid, data, tx=tx)
            original = tx.get(('purge_preparations', pid))
            if original is None or original.data.get('receipt_id') != operation_hash:
                raise DocumentConflict('HOSTED_PURGE_SANDBOX_PREPARATION_STALE')
            previous = tx.get(self._receipt_path(pid))
            if previous is not None:
                if dict(previous.data) != data:
                    raise DocumentConflict('HOSTED_PURGE_SANDBOX_RECEIPT_CONFLICT')
                return dict(previous.data)
            tx.create(self._receipt_path(pid), data)
            return data

        return self._progress(self.documents.run_transaction(reserve))

    def _load(self, pid: str, uid: str, operation_hash: str) -> dict:
        stored = self.documents.get(self._receipt_path(pid))
        if stored is None:
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_NOT_ARMED')
        data = dict(stored.data)
        if (data.get('receipt_id') != operation_hash or data.get('owner_user_id') != uid
                or data.get('project_id') != pid or data.get('physical_purge_authorized') is not False):
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_RECEIPT_CONFLICT')
        if not isinstance(data.get('cursor'), int) or not 0 <= data['cursor'] <= data['total']:
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_RECEIPT_CORRUPT')
        self._check_scope(pid, data)
        return data

    def _fence_remaining(self, pid: str, data: dict) -> None:
        """Detect unknown/new objects or documents, content drift and stale deletes.

        A missing object is allowed ONLY when its intent was durably claimed,
        to reconcile a crash after a generation-conditioned delete. This rule
        is intentionally sandbox-only, not a real cloud authorization proof.
        """
        cursor = data['cursor']
        objects = data['objects']
        docs = data['documents']
        doc_count_before = max(0, cursor - len(objects))
        expected_docs = {tuple(x['path']): str(x['revision'])
                         for x in docs[:len(docs) - 2][doc_count_before:] + docs[-2:]}
        tree = self.planner.discovery.scan(pid, max_entries=self.planner.max_entries)
        observed_docs = dict(tree.document_states)
        if observed_docs != expected_docs:
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_DOCUMENT_DRIFT')
        current = {obj.name: obj for obj in self.content.list_prefix(f'projects/{pid}/')}
        remaining = {x['name']: x for x in objects[cursor:] if cursor < len(objects)}
        if set(current) - set(remaining):
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_OBJECT_DRIFT')
        for name, item in remaining.items():
            meta = current.get(name)
            if meta is None:
                if not (data.get('inflight') == cursor and cursor < len(objects)
                        and objects[cursor]['name'] == name):
                    raise DocumentConflict('HOSTED_PURGE_SANDBOX_OBJECT_DRIFT')
            elif meta.generation != item['generation'] or meta.sha256 != item['sha256']:
                raise DocumentConflict('HOSTED_PURGE_SANDBOX_OBJECT_DRIFT')
        if cursor >= len(objects) and current:
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_OBJECT_DRIFT')

    def step(self, project_id: str, owner_user_id: str, *, operation_key: str,
             worker_id: str, crash_after_object_delete: bool = False) -> SandboxPurgeProgress:
        """Run one memory-only deletion unit; replay safe across fresh objects.

        The worker_id is a lease identity. Another worker may only take over a
        pending object after 30 seconds; stale workers may not advance receipts.
        """
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        receipt_id = self._identity(pid, uid, operation_key)
        if not _KEY.fullmatch(str(worker_id)):
            raise ValueError('HOSTED_PURGE_SANDBOX_WORKER_ID_INVALID')
        data = self._load(pid, uid, receipt_id)
        if data['state'] == 'sandbox_completed':
            return self._progress(data)
        if data['state'] not in ('sandbox_armed', 'sandbox_running'):
            raise DocumentConflict('HOSTED_PURGE_SANDBOX_STATE_INVALID')
        self._assert_tombstone(pid, uid, data)
        self._verify_archive(data, pid, uid)
        self._fence_remaining(pid, data)
        cursor = data['cursor']
        path = self._receipt_path(pid)
        now = self._clock()
        if cursor < len(data['objects']):
            item = data['objects'][cursor]
            def claim(tx):
                current = tx.get(path)
                if current is None or current.data.get('cursor') != cursor:
                    raise DocumentConflict('HOSTED_PURGE_SANDBOX_CURSOR_CHANGED')
                next_data = dict(current.data)
                old = next_data.get('inflight')
                lease = next_data.get('lease_until')
                if old is not None and (old != cursor or (next_data.get('lease_owner') != worker_id
                        and lease and datetime.fromisoformat(lease) > now)):
                    raise DocumentConflict('HOSTED_PURGE_SANDBOX_LEASE_HELD')
                next_data.update(state='sandbox_running', inflight=cursor, lease_owner=worker_id,
                                 lease_until=(now + timedelta(seconds=LEASE_SECONDS)).isoformat())
                tx.set(path, next_data)
            self.documents.run_transaction(claim)
            meta = self.content.stat(item['name'])
            if meta is not None:
                if meta.generation != item['generation'] or meta.sha256 != item['sha256']:
                    raise DocumentConflict('HOSTED_PURGE_SANDBOX_OBJECT_DRIFT')
                self.content.delete(item['name'], if_generation_match=item['generation'])
            # A missing object is only tolerable because claim is durable.
            if crash_after_object_delete:
                raise SimulatedPurgeCrash('HOSTED_PURGE_SANDBOX_CRASH_AFTER_OBJECT_DELETE')
            def commit(tx):
                current = tx.get(path)
                if (current is None or current.data.get('cursor') != cursor
                        or current.data.get('inflight') != cursor
                        or current.data.get('lease_owner') != worker_id):
                    raise DocumentConflict('HOSTED_PURGE_SANDBOX_LEASE_LOST')
                updated = dict(current.data)
                updated.update(cursor=cursor + 1, inflight=None, lease_owner=None, lease_until=None)
                tx.set(path, updated)
                return updated
            return self._progress(self.documents.run_transaction(commit))

        # A Firestore-like delete and receipt advancement use the SAME memory
        # transaction. A provider retry must not execute any external GCS IO.
        doc_index = cursor - len(data['objects'])
        ordinary = data['documents'][:-2]
        targets = [tuple(ordinary[doc_index]['path'])] if doc_index < len(ordinary) else [
            tuple(data['documents'][-2]['path']), tuple(data['documents'][-1]['path'])]
        def delete_docs(tx):
            current = tx.get(path)
            if current is None or current.data.get('cursor') != cursor:
                raise DocumentConflict('HOSTED_PURGE_SANDBOX_CURSOR_CHANGED')
            self._assert_tombstone(pid, uid, data, tx=tx)
            # Validate the complete remaining project tree under the memory
            # transaction's lock, including UNEXPECTED orphan paths. A plain
            # tx.get loop cannot catch a document inserted after preflight.
            expected = {tuple(entry['path']): str(entry['revision'])
                        for entry in data['documents'][doc_index:]}
            present = {p: str(row[1]) for p, row in tx._owner._documents.items()
                       if p[:2] == ('projects', pid)}
            if present != expected:
                raise DocumentConflict('HOSTED_PURGE_SANDBOX_DOCUMENT_DRIFT')
            for target in targets:
                tx.delete(target)
            updated = dict(current.data)
            updated['cursor'] = cursor + 1
            updated['state'] = 'sandbox_completed' if updated['cursor'] == updated['total'] else 'sandbox_running'
            tx.set(path, updated)
            return updated
        return self._progress(self.documents.run_transaction(delete_docs))
