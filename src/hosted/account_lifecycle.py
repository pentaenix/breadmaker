"""Transactional application write fence for accounts undergoing erasure.

Normal hosted composition uses this facade; the separately privileged operator
uses the underlying store. Reads retain existing authorization. No HTTP request
can create a closure or bypass its fence. This fences Firestore publication, NOT
physical GCS upload completion: content purge remains a separate gated stage.
"""
from __future__ import annotations

from copy import deepcopy
import re

from hosted.firestore.contracts import (DocumentAlreadyExists, DocumentConflict,
    ProjectUnavailable, StoredDocument, require_document_path)
from hosted.storage.keys import require_object_name
from hosted.import_staging import permits_unpublished_import, import_authority_paths

_USER = re.compile(r'usr_[0-9a-f]{32}')
_USER_FIELDS = frozenset({'user_id', 'owner_user_id', 'actor_user_id'})
_CONTROL = frozenset({'account_erasures', 'account_erasure_locks'})


class _LifecycleTransaction:
    def __init__(self, tx):
        self.tx, self.original, self.pending = tx, {}, {}

    def _original(self, path):
        key = require_document_path(path)
        if key not in self.original:
            self.original[key] = self.tx.get(key)
        return self.original[key]

    @staticmethod
    def _copy(row):
        return None if row is None else StoredDocument(row.path, deepcopy(dict(row.data)), row.revision)

    def get(self, path):
        key = require_document_path(path)
        row = self._original(key)
        if key not in self.pending:
            return self._copy(row)
        data = self.pending[key]
        return None if data is None else StoredDocument(key, deepcopy(data), row.revision if row else 0)

    def get_many(self, paths):
        keys = [require_document_path(path) for path in paths]
        absent = list(dict.fromkeys(key for key in keys if key not in self.original))
        if absent:
            for key, row in zip(absent, self.tx.get_many(absent), strict=True):
                self.original[key] = row
        return [self.get(key) for key in keys]

    def query_collection(self, path, *, field=None, value=None):
        prefix = tuple(path)
        # Include pending rows that move INTO the predicate without downloading
        # unrelated provider rows. Apply the predicate again after the overlay.
        rows = self.tx.query_collection(prefix, field=field, value=value)
        by_path = {}
        for row in rows:
            self.original.setdefault(row.path, row)
            by_path[row.path] = row
        for key, data in self.pending.items():
            if key[:-1] == prefix:
                if data is None:
                    by_path.pop(key, None)
                else:
                    by_path[key] = self.get(key)
        return [self._copy(row) for _, row in sorted(by_path.items())
                if field is None or row.data.get(field) == value]

    def count_collection(self, path):
        prefix = tuple(path)
        count = self.tx.count_collection(prefix)
        for key, data in self.pending.items():
            if key[:-1] == prefix:
                before = self._original(key)
                count += int(data is not None) - int(before is not None)
        return count

    def saved_word_project(self, project_id, *, timeout):
        if self.pending:
            raise DocumentConflict('WORD_METADATA_QUERY_AFTER_WRITE')
        return self.tx.saved_word_project(project_id, timeout=timeout)

    def saved_word_metadata(self, project_id, *, limit, timeout):
        if self.pending:
            raise DocumentConflict('WORD_METADATA_QUERY_AFTER_WRITE')
        return self.tx.saved_word_metadata(project_id, limit=limit, timeout=timeout)

    def saved_word_owners(self, project_id, *, timeout):
        if self.pending:
            raise DocumentConflict('WORD_METADATA_QUERY_AFTER_WRITE')
        return self.tx.saved_word_owners(project_id, timeout=timeout)

    def saved_word_active_projects(self, *, timeout):
        if self.pending: raise DocumentConflict('WORD_METADATA_QUERY_AFTER_WRITE')
        return self.tx.saved_word_active_projects(timeout=timeout)

    def saved_word_page(self, project_id, *, after_id=None, limit=33, timeout=2):
        if self.pending: raise DocumentConflict('WORD_METADATA_QUERY_AFTER_WRITE')
        return self.tx.saved_word_page(project_id, after_id=after_id, limit=limit, timeout=timeout)

    def create(self, path, data):
        key = require_document_path(path)
        if self.get(key) is not None:
            raise DocumentAlreadyExists('HOSTED_DOCUMENT_ALREADY_EXISTS')
        self.pending[key] = deepcopy(dict(data))

    def set(self, path, data):
        key = require_document_path(path)
        self._original(key)
        self.pending[key] = deepcopy(dict(data))

    def delete(self, path):
        key = require_document_path(path)
        self._original(key)
        self.pending[key] = None

    def _ownership_fence(self, projects):
        """Bind ownership changes to the canonical root revision, always on.

        No repair or new authority. Unpublished imports remain invisible until
        publication; activation must establish exactly one verified owner.
        """
        owners = set()
        def identity(data):
            return ((data.get('user_id'), data.get('created_at'))
                    if data is not None and data.get('role') == 'owner' else None)
        for pid in projects:
            root = ('projects', pid)
            changed = any(len(path) == 4 and path[:3] == ('projects', pid, 'members')
                and identity(self._original(path).data if self._original(path) else None) != identity(data)
                for path, data in self.pending.items())
            # Presence-only member updates must keep the existing batched fence;
            # they do not need another serial root or owner query.
            if root not in self.pending and not changed:
                continue
            before_row = self._original(root)
            before = dict(before_row.data) if before_row else None
            after = self.pending.get(root, before)
            if after is None or after.get('status') != 'active':
                continue
            activated = before is None or before.get('status') != 'active'
            if not (changed or activated):
                continue
            generation = after.get('generation')
            if type(generation) is not int or not 1 <= generation <= 2**53-1:
                raise DocumentConflict('HOSTED_PROJECT_OWNER_REVISION_REQUIRED')
            if before is not None and (root not in self.pending
                    or type(before.get('generation')) is not int
                    or generation != before['generation'] + 1
                    or after.get('created_at') != before.get('created_at')):
                raise DocumentConflict('HOSTED_PROJECT_OWNER_REVISION_REQUIRED')
            # Two is a sentinel, never evidence that a malformed collection has
            # exactly two owners. Ambiguous prior state needs operator review.
            rows = self.tx.saved_word_owners(pid, timeout=2)
            if len(rows) > 1:
                raise DocumentConflict('HOSTED_PROJECT_OWNER_UNVERIFIED')
            current = {row.path: dict(row.data) for row in rows}
            for path, data in self.pending.items():
                if len(path) == 4 and path[:3] == ('projects', pid, 'members'):
                    current.pop(path, None)
                    if identity(data) is not None:
                        current[path] = data
            if len(current) != 1:
                raise DocumentConflict('HOSTED_PROJECT_OWNER_UNVERIFIED')
            path, data = next(iter(current.items()))
            try:
                from hosted.security.saved_word_identity import verify_owner
                uid = data.get('user_id')
                profile = self.get(('users', uid))
                if profile is not None and (profile.data.get('status') != 'active'
                        or profile.data.get('_bm_erasure_operation')):
                    raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
                if self._original(('account_erasure_locks', uid)) is not None:
                    raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
                if profile is None:
                    raise ValueError()
                verify_owner(project_id=pid, owner_rows=[(path, dict(data))], hosted_user=dict(profile.data))
            except DocumentConflict:
                raise
            except Exception:
                raise DocumentConflict('HOSTED_PROJECT_OWNER_UNVERIFIED') from None
            owners.add(uid)
        return owners

    def flush(self, actor):
        if not self.pending:
            return
        users, projects = set(), set()
        if actor and _USER.fullmatch(str(actor)):
            users.add(actor)
        for path, after in self.pending.items():
            if path[0] in _CONTROL:
                raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_OPERATOR_ONLY')
            if path[0] == 'projects':
                projects.add(path[1])
            if path[0] in {'users', 'hosted_actions'} and _USER.fullmatch(path[1]):
                users.add(path[1])
            before = self._original(path)
            for data in (before.data if before else {}, after or {}):
                for field in _USER_FIELDS:
                    uid = data.get(field)
                    if isinstance(uid, str) and _USER.fullmatch(uid):
                        users.add(uid)
        users.update(self._ownership_fence(projects))
        # All fence reads occur BEFORE any provider writes, including with the
        # action-journal facade nested above this transaction. Firestore retries
        # a pre-freeze writer whose account/project changed before commit.
        paths = [('users', uid) for uid in sorted(users)] + [('projects', pid) for pid in sorted(projects)]
        absent = [path for path in paths if path not in self.original]
        if absent:
            for path, row in zip(absent, self.tx.get_many(absent), strict=True):
                self.original[path] = row
        for uid in users:
            row = self._original(('users', uid))
            if row is not None:
                if row.data.get('status', 'active') != 'active' or row.data.get('_bm_erasure_operation'):
                    raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
            elif self._original(('account_erasure_locks', uid)) is not None:
                raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
        for pid in projects:
            root = ('projects', pid)
            row = self._original(root)
            if row is not None and (row.data.get('status') == 'erasure_pending'
                                    or row.data.get('_bm_erasure_operation')):
                raise ProjectUnavailable('PROJECT_NOT_FOUND')
            if row is None:
                # Recheck the import owner even at final root publication; a
                # pending root is not permission to cross an account freeze.
                staging = permits_unpublished_import(self, pid, actor)
                if not self.pending.get(root) and not staging:
                    raise ProjectUnavailable('PROJECT_NOT_FOUND')
        for path, data in self.pending.items():
            before = self._original(path)
            if data is None:
                self.tx.delete(path)
            elif before is None:
                self.tx.create(path, data)
            else:
                self.tx.set(path, data)


class AccountLifecycleDocumentStore:
    """No privileged closure methods; every application transaction is fenced."""
    def __init__(self, store, *, actor=lambda: None):
        self.store, self.actor = store, actor

    def __getattr__(self, name):
        return getattr(self.store, name)

    def run_transaction(self, fn, *, max_attempts=5):
        def execute(tx):
            buffered = _LifecycleTransaction(tx)
            result = fn(buffered)
            buffered.flush(self.actor())
            return result
        return self.store.run_transaction(execute, max_attempts=max_attempts)


class AccountLifecycleObjectStore:
    """Reject NEW uploads after closure; an already started upload may finish.

    The transactional document fence still rejects its pointer publication.
    This deliberately does not claim upload quiescence or permit content purge.
    """
    def __init__(self, store, documents):
        self.store, self.documents = store, documents

    def __getattr__(self, name):
        return getattr(self.store, name)

    def write_chunks(self, name, chunks, **kwargs):
        key = require_object_name(name)
        parts = key.split('/')
        if len(parts) < 3:
            raise DocumentConflict('HOSTED_ACCOUNT_OBJECT_SCOPE_INVALID')
        actor = self.documents.actor()
        def check(tx):
            # The same current fence, fetched with one BatchGet rather than a
            # serial round trip per root/reservation/user/lock for each paragraph.
            paths = []
            if parts[0] == 'projects':
                paths.append(('projects', parts[1]))
                paths.extend(import_authority_paths(parts[1]))
            elif parts[0] == 'users' and _USER.fullmatch(parts[1]):
                paths.append(('users', parts[1]))
            if actor:
                paths.append(('users', actor))
            tx.get_many(list(dict.fromkeys(paths)))
            if parts[0] == 'projects':
                row = tx.get(('projects', parts[1]))
                if row is None:
                    if not permits_unpublished_import(tx, parts[1], actor):
                        raise ProjectUnavailable('PROJECT_NOT_FOUND')
                elif row.data.get('_bm_erasure_operation') or row.data.get('status') == 'erasure_pending':
                    raise ProjectUnavailable('PROJECT_NOT_FOUND')
            elif parts[0] == 'users' and _USER.fullmatch(parts[1]):
                target = tx.get(('users', parts[1]))
                if target is None or target.data.get('status', 'active') != 'active':
                    raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
            else:
                raise DocumentConflict('HOSTED_ACCOUNT_OBJECT_SCOPE_INVALID')
            if actor:
                row = tx.get(('users', actor))
                if row is None or row.data.get('status', 'active') != 'active':
                    raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
        self.documents.run_transaction(check)
        return self.store.write_chunks(key, chunks, **kwargs)
