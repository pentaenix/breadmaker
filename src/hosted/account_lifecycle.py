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
            if row is None and not self.pending.get(root):
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
            if parts[0] == 'projects':
                row = tx.get(('projects', parts[1]))
                if row is None or row.data.get('_bm_erasure_operation') or row.data.get('status') == 'erasure_pending':
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
