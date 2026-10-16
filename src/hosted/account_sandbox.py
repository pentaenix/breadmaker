from __future__ import annotations

"""M9C3: restartable account-data erasure *rehearsal* using exact memory providers.

Not an HTTP service, Google adapter, or an Identity Platform revocation client.
Only non-owner account data may be erased here; owned manuscripts MUST be
independently retained/purged through their project-specific lifecycle first.
The terminal state still requires externally verified identity revocation.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from types import SimpleNamespace

from hosted.account_deletion import HostedAccountDeletionPlanner
from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError
from hosted.firestore.ids import require_user_id
from hosted.firestore.memory import MemoryDocumentStore
from hosted.storage.memory import MemoryObjectStore

MAX_ACCOUNT_SANDBOX_ITEMS = 128
MAX_ACCOUNT_SANDBOX_RECEIPT_BYTES = 128 * 1024
ACCOUNT_LEASE_SECONDS = 30
_ALLOWED_KEY = re.compile(r'^[A-Za-z0-9_-]{1,160}$')


class SimulatedAccountCrash(RuntimeError):
    """Failure after a generation-conditioned object delete, before commit."""


@dataclass(frozen=True)
class SandboxAccountProgress:
    state: str
    cursor: int
    total: int
    external_identity_revocation_required: bool = True
    production_account_deletion_authorized: bool = False


class HostedSandboxAccountClosure:
    """Memory-only account erasure rehearsal, with durable progress outside user data.

    Completion of account *data* deletion is NOT completion of external Identity
    Platform identity revocation. There is deliberately no `finish`, `revoke`
    or Google implementation, and no configuration switch to enable one.
    """

    def __init__(self, planner: HostedAccountDeletionPlanner,
                 *, now: Callable[[], datetime] | None = None) -> None:
        if (type(planner) is not HostedAccountDeletionPlanner
                or type(planner.documents) is not MemoryDocumentStore
                or type(planner.content) is not MemoryObjectStore
                or type(planner.recovery) is not MemoryObjectStore
                or planner.content is planner.recovery):
            raise ValueError('HOSTED_ACCOUNT_SANDBOX_MEMORY_ONLY')
        self.planner = planner
        self.documents = planner.documents
        self.content = planner.content
        self.recovery = planner.recovery
        self.now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _path(uid: str) -> tuple[str, ...]:
        return ('account_sandbox_closures', uid)

    @staticmethod
    def _id(uid: str, operation_key: str) -> str:
        if not isinstance(operation_key, str) or not _ALLOWED_KEY.fullmatch(operation_key):
            raise ValueError('HOSTED_ACCOUNT_SANDBOX_OPERATION_KEY_INVALID')
        return hashlib.sha256(f'{uid}\0{operation_key}'.encode()).hexdigest()

    @staticmethod
    def _progress(data: dict) -> SandboxAccountProgress:
        return SandboxAccountProgress(data['state'], data['cursor'], data['total'])

    def _clock(self) -> datetime:
        instant = self.now()
        if instant.tzinfo is None:
            raise ValueError('HOSTED_ACCOUNT_SANDBOX_CLOCK_INVALID')
        return instant.astimezone(timezone.utc)

    def _self(self, uid: str) -> None:
        if require_user_id(self.planner.actor_user_id()) != uid:
            raise HostedAuthorizationError('HOSTED_ACCOUNT_SELF_ONLY')

    @staticmethod
    def _references_user(path: tuple[str, ...], value: dict, uid: str) -> bool:
        return (path[:2] == ('users', uid)
                or (len(path) == 4 and path[:1] == ('projects',)
                    and path[2:] == ('members', uid))
                or value.get('user_id') == uid
                or value.get('owner_user_id') == uid
                or (value.get('actor_user_id') == uid)
                or (len(path) >= 2 and path[0] not in ('projects', 'users') and path[1] == uid))

    def _documents_locked(self, uid: str, tx) -> dict[tuple[str, ...], str]:
        # Whole-memory scan is intentional: an unrecognized user reference must
        # not be invisible merely because no registered collection knows it.
        return {path: str(row[1]) for path, row in tx._owner._documents.items()
                if self._references_user(path, row[0], uid)
                and path != self._path(uid)
                and not (len(path) == 2
                         and path[0] in ('purge_preparations', 'purge_sandbox_executions')
                         and row[0].get('owner_user_id') == uid)}

    def _foreign_locked(self, data: dict, tx) -> None:
        for item in data['foreign_roots']:
            path = tuple(item['path'])
            record = tx.get(path)
            if record is None or str(record.revision) != item['revision']:
                raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_FOREIGN_PROJECT_DRIFT')

    def _check_audits_locked(self, data: dict, tx) -> None:
        observed = {path: str(row[1]) for path, row in tx._owner._documents.items()
                    if len(path) == 2
                    and path[0] in ('purge_preparations', 'purge_sandbox_executions')
                    and row[0].get('owner_user_id') == data['user_id']}
        expected = {tuple(item['path']): item['revision'] for item in data['retained_audits']}
        if observed != expected:
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_AUDIT_DRIFT')

    def _check_docs_locked(self, data: dict, tx) -> None:
        uid = data['user_id']
        expected = {tuple(entry['path']): entry['revision'] for entry in
                    data['documents'][max(0, data['cursor'] - len(data['objects'])):]}
        if self._documents_locked(uid, tx) != expected:
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_DOCUMENT_DRIFT')
        self._foreign_locked(data, tx)
        self._check_audits_locked(data, tx)

    def _check_objects(self, data: dict) -> None:
        uid = data['user_id']
        cursor = data['cursor']
        inventory = data['objects']
        remaining = {entry['name']: entry for entry in inventory[cursor:]}
        observed = {meta.name: meta for meta in self.content.list_prefix(f'users/{uid}/')}
        if set(observed) - set(remaining):
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_OBJECT_DRIFT')
        for name, entry in remaining.items():
            meta = observed.get(name)
            if meta is None:
                if not (data.get('inflight') == cursor and cursor < len(inventory)
                        and inventory[cursor]['name'] == name):
                    raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_OBJECT_DRIFT')
            elif meta.generation != entry['generation'] or meta.sha256 != entry['sha256']:
                raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_OBJECT_DRIFT')
        if cursor >= len(inventory) and observed:
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_OBJECT_DRIFT')

    def _load(self, uid: str, key: str) -> dict:
        entry = self.documents.get(self._path(uid))
        if entry is None:
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_NOT_ARMED')
        data = dict(entry.data)
        if (data.get('receipt_id') != key or data.get('user_id') != uid
                or data.get('production_account_deletion_authorized') is not False
                or data.get('external_identity_revocation_required') is not True
                or not isinstance(data.get('cursor'), int)
                or not 0 <= data['cursor'] <= data['total']
                or data['total'] != len(data['objects']) + len(data['documents'])):
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_RECEIPT_CONFLICT')
        if any(not str(x['name']).startswith(f'users/{uid}/') for x in data['objects']):
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_SCOPE_VIOLATION')
        if any(not (
            tuple(x['path'])[:2] == ('users', uid)
            or (len(x['path']) == 4 and x['path'][0] == 'projects' and x['path'][2:] == ['members', uid])
            or (len(x['path']) == 2 and x['path'][0] in ('identity_bindings', 'hosted_import_operations'))
        ) for x in data['documents']):
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_SCOPE_VIOLATION')
        return data

    def arm(self, user_id: str, *, operation_key: str) -> SandboxAccountProgress:
        uid = require_user_id(user_id)
        self._self(uid)
        receipt_id = self._id(uid, operation_key)
        previous = self.documents.get(self._path(uid))
        if previous is not None:
            return self._progress(self._load(uid, receipt_id))
        preview = self.planner.preview(uid)
        if (preview.owned_projects or preview.missing_project_roots
                or 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' in preview.blockers):
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_PROJECTS_OR_UNKNOWN_REFERENCES')
        with self.documents._lock:
            rows = self._documents_locked(uid, SimpleNamespace(_owner=self.documents, get=self.documents._get_locked))
            if set(rows) != set(preview.account_document_paths):
                raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_DOCUMENT_DRIFT')
            foreign = []
            for pid in preview.collaborative_projects:
                path = ('projects', pid)
                record = self.documents._get_locked(path)
                if record is None:
                    raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_FOREIGN_PROJECT_MISSING')
                foreign.append({'path': list(path), 'revision': str(record.revision)})
        objects = [dict(name=meta.name, generation=meta.generation, sha256=meta.sha256)
                   for meta in self.content.list_prefix(f'users/{uid}/')]
        if tuple(x['name'] for x in objects) != preview.account_object_names:
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_OBJECT_DRIFT')
        # Descendants before parent, profile last. The operation may not delete
        # any collaborator's project root, data or other collaborators' records.
        document_paths = sorted(rows, key=lambda path: (-len(path), path))
        document_paths = [p for p in document_paths if p != ('users', uid)] + [('users', uid)]
        if len(objects) + len(document_paths) + len(preview.retained_purge_audit_paths) > MAX_ACCOUNT_SANDBOX_ITEMS:
            raise ValueError('HOSTED_ACCOUNT_SANDBOX_MANIFEST_TOO_LARGE')
        record = {
            'user_id': uid, 'receipt_id': receipt_id,
            'initial_fingerprint': preview.fingerprint,
            'documents': [{'path': list(p), 'revision': rows[p]} for p in document_paths],
            'objects': objects, 'foreign_roots': foreign,
            'retained_audits': [
                {'path': list(p), 'revision': str(self.documents.get(p).revision)}
                for p in preview.retained_purge_audit_paths
            ],
            'cursor': 0, 'total': len(objects) + len(document_paths),
            'state': 'sandbox_armed', 'inflight': None,
            'lease_owner': None, 'lease_until': None,
            'external_identity_revocation_required': True,
            'production_account_deletion_authorized': False,
        }
        if len(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()) > MAX_ACCOUNT_SANDBOX_RECEIPT_BYTES:
            raise ValueError('HOSTED_ACCOUNT_SANDBOX_MANIFEST_TOO_LARGE')
        def create(tx):
            current = tx.get(self._path(uid))
            if current is not None:
                if dict(current.data) != record:
                    raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_RECEIPT_CONFLICT')
                return dict(current.data)
            self._check_docs_locked(record, tx)
            tx.create(self._path(uid), record)
            return record
        result = self.documents.run_transaction(create)
        self._check_objects(result)
        return self._progress(result)

    def step(self, user_id: str, *, operation_key: str, worker_id: str,
             crash_after_object_delete: bool = False) -> SandboxAccountProgress:
        uid = require_user_id(user_id)
        self._self(uid)
        identity = self._id(uid, operation_key)
        if not isinstance(worker_id, str) or not _ALLOWED_KEY.fullmatch(worker_id):
            raise ValueError('HOSTED_ACCOUNT_SANDBOX_WORKER_INVALID')
        data = self._load(uid, identity)
        if data['state'] == 'sandbox_identity_revocation_pending':
            return self._progress(data)
        if data['state'] not in ('sandbox_armed', 'sandbox_running'):
            raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_STATE_INVALID')
        self._check_objects(data)
        with self.documents._lock:
            self._check_docs_locked(data, SimpleNamespace(_owner=self.documents, get=self.documents._get_locked))
        cursor, now = data['cursor'], self._clock()
        path = self._path(uid)
        if cursor < len(data['objects']):
            item = data['objects'][cursor]
            def claim(tx):
                self._check_docs_locked(data, tx)
                current = tx.get(path)
                if current is None or current.data.get('cursor') != cursor:
                    raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_CURSOR_CHANGED')
                updated = dict(current.data)
                old = updated.get('inflight')
                lease = updated.get('lease_until')
                if old is not None and (old != cursor or (updated.get('lease_owner') != worker_id
                    and lease and datetime.fromisoformat(lease) > now)):
                    raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_LEASE_HELD')
                updated.update(state='sandbox_running', inflight=cursor,
                    lease_owner=worker_id,
                    lease_until=(now + timedelta(seconds=ACCOUNT_LEASE_SECONDS)).isoformat())
                tx.set(path, updated)
            self.documents.run_transaction(claim)
            meta = self.content.stat(item['name'])
            if meta is not None:
                if meta.generation != item['generation'] or meta.sha256 != item['sha256']:
                    raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_OBJECT_DRIFT')
                self.content.delete(item['name'], if_generation_match=item['generation'])
            if crash_after_object_delete:
                raise SimulatedAccountCrash('HOSTED_ACCOUNT_SANDBOX_CRASH_AFTER_OBJECT_DELETE')
            def commit(tx):
                current = tx.get(path)
                if (current is None or current.data.get('cursor') != cursor
                        or current.data.get('inflight') != cursor
                        or current.data.get('lease_owner') != worker_id):
                    raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_LEASE_LOST')
                updated = dict(current.data)
                updated.update(cursor=cursor + 1, inflight=None,
                               lease_owner=None, lease_until=None)
                tx.set(path, updated)
                return updated
            return self._progress(self.documents.run_transaction(commit))
        position = cursor - len(data['objects'])
        entry = data['documents'][position]
        target = tuple(entry['path'])
        def remove(tx):
            self._check_docs_locked(data, tx)
            current = tx.get(path)
            if current is None or current.data.get('cursor') != cursor:
                raise DocumentConflict('HOSTED_ACCOUNT_SANDBOX_CURSOR_CHANGED')
            tx.delete(target)
            updated = dict(current.data)
            updated['cursor'] = cursor + 1
            updated['state'] = ('sandbox_identity_revocation_pending'
                                if updated['cursor'] == updated['total'] else 'sandbox_running')
            tx.set(path, updated)
            return updated
        return self._progress(self.documents.run_transaction(remove))
