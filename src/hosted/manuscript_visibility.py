"""Revision-aware manuscript view over the ordinary, lifecycle-fenced store.

This is a reader foundation, not an enabled import protocol. Production must not
activate a format until every application/operator reader is compatibility-tested.
Retained rows stay under their canonical project paths for recovery and erasure.
The narrow retained view is for authorized journal/staging services, never HTTP
entity reads. It does not unwrap or bypass the account-erasure write fence.
"""
from __future__ import annotations

from copy import deepcopy

from hosted.firestore.ids import require_opaque_id
from hosted.firestore.contracts import (DocumentAlreadyExists, DocumentNotFound,
    DocumentConflict, HostedValidationError, StoredDocument, require_document_path)

FORMAT_FIELD = '_bm_manuscript_format'
REVISION_FIELD = '_bm_manuscript_revision'
HEAD_FIELD = '_bm_manuscript_head'
DELETED_FIELD = '_bm_manuscript_deleted'
ASSET_REVISIONS_FIELD = '_bm_manuscript_asset_revisions'
FORMAT_VERSION = 1
_STATUS = {'active': 'active_manuscript_v1', 'deleted': 'deleted_manuscript_v1'}
_DECODE_STATUS = {value: key for key, value in _STATUS.items()}
_MANAGED = frozenset({FORMAT_FIELD, REVISION_FIELD, HEAD_FIELD, DELETED_FIELD, ASSET_REVISIONS_FIELD})
_GATED = frozenset({'chapters', 'blocks', 'scenes', 'assets', 'annotations'})


def decode_project_root(row):
    """Decode only an exact known format; never mask erasure or unknown formats."""
    if row is None or row.path[0] != 'projects' or len(row.path) != 2:
        return row
    data = deepcopy(dict(row.data))
    version = data.get(FORMAT_FIELD)
    if version is not None:
        if type(version) is not int or version != FORMAT_VERSION:
            raise HostedValidationError('HOSTED_MANUSCRIPT_FORMAT_UNSUPPORTED')
        status = data.get('status')
        if status in _DECODE_STATUS:
            data['status'] = _DECODE_STATUS[status]
        elif status in _STATUS:
            # Never accept an old binary's lossy restore/update as a valid root.
            raise HostedValidationError('HOSTED_MANUSCRIPT_FORMAT_STATUS_INVALID')
        data.pop(FORMAT_FIELD)
    elif data.get('status') in _DECODE_STATUS:
        raise HostedValidationError('HOSTED_MANUSCRIPT_FORMAT_MARKER_REQUIRED')
    return StoredDocument(row.path, data, row.revision)


def encode_project_root(data, previous):
    value = deepcopy(dict(data))
    if previous is not None and FORMAT_FIELD in previous.data:
        # Validate first, even when the next state is erasure_pending.
        decode_project_root(previous)
        value[FORMAT_FIELD] = FORMAT_VERSION
        if value.get('status') in _STATUS:
            value['status'] = _STATUS[value['status']]
    elif FORMAT_FIELD in value or value.get('status') in _DECODE_STATUS:
        raise HostedValidationError('HOSTED_MANUSCRIPT_FORMAT_FIELD_RESERVED')
    return value



def _revision(data, field):
    if field not in data:
        return None
    value = data[field]
    if not isinstance(value, str):
        raise HostedValidationError('HOSTED_MANUSCRIPT_REVISION_INVALID')
    return require_opaque_id(value, label='manuscript_revision')


def _copy(row):
    return None if row is None else StoredDocument(row.path, deepcopy(dict(row.data)), row.revision)


class _RevisionTransaction:
    """Buffer writes so dependency/visibility checks precede provider writes."""
    def __init__(self, tx, *, include_retained=False):
        self.tx = tx
        self.include_retained = include_retained
        self.original = {}
        self.pending = {}
        self.prepared = {}

    def _original(self, path):
        key = require_document_path(path)
        if key not in self.original:
            self.original[key] = self.tx.get(key)
        return self.original[key]

    def _physical(self, path):
        key = require_document_path(path)
        before = self._original(key)
        if key not in self.pending:
            return before
        data = self.pending[key]
        return None if data is None else StoredDocument(key, data, before.revision if before else 0)

    def _enabled(self, pid):
        root = self._physical(('projects', pid))
        if root is not None:
            decode_project_root(root)
        return root is not None and root.data.get(FORMAT_FIELD) == FORMAT_VERSION

    def _tab(self, pid, tid):
        if not isinstance(tid, str) or not tid:
            return None
        return self._physical(('projects', pid, 'tabs', tid))

    def _chapter(self, pid, cid):
        if not isinstance(cid, str) or not cid:
            return None
        row = self._physical(('projects', pid, 'chapters', cid))
        return row if self._visible(row) else None

    def _visible(self, row):
        if row is None:
            return False
        path, data = row.path, row.data
        if len(path) != 4 or path[0] != 'projects' or path[2] not in _GATED:
            return True
        if not self._enabled(path[1]):
            # Preserve legacy orphan semantics, but never expose descendants of
            # a staged/tombstoned parent when its compatible root is absent.
            if any(key in data for key in (REVISION_FIELD, DELETED_FIELD, ASSET_REVISIONS_FIELD)):
                return False
            if path[2] in {'blocks', 'scenes', 'annotations'} and data.get('chapter_id'):
                parent = self._physical(('projects', path[1], 'chapters', data['chapter_id']))
                if parent is not None and not self._visible(parent):
                    return False
            if path[2] == 'annotations' and data.get('block_id'):
                parent = self._physical(('projects', path[1], 'blocks', data['block_id']))
                if parent is not None and not self._visible(parent):
                    return False
            return True
        pid, family = path[1], path[2]
        if family == 'chapters':
            if DELETED_FIELD in data and type(data[DELETED_FIELD]) is not bool:
                raise HostedValidationError('HOSTED_MANUSCRIPT_TOMBSTONE_INVALID')
            tab = self._tab(pid, data.get('tab_id'))
            return bool(tab is not None and not data.get(DELETED_FIELD)
                and _revision(data, REVISION_FIELD) == _revision(tab.data, HEAD_FIELD))
        if family == 'blocks':
            chapter = self._chapter(pid, data.get('chapter_id'))
            return bool(chapter is not None and (not data.get('tab_id')
                or data['tab_id'] == chapter.data.get('tab_id')))
        if family == 'scenes':
            if data.get('chapter_id'):
                chapter = self._chapter(pid, data.get('chapter_id'))
                return bool(chapter is not None and (not data.get('tab_id')
                    or data['tab_id'] == chapter.data.get('tab_id')))
            tab = self._tab(pid, data.get('tab_id'))
            return bool(tab is not None and _revision(data, REVISION_FIELD) == _revision(tab.data, HEAD_FIELD))
        if family == 'annotations':
            # Packed highlights have their own payload-level source checks.
            if data.get('block_id'):
                return self._visible(self._physical(('projects', pid, 'blocks', data['block_id'])))
            if data.get('chapter_id'):
                return self._chapter(pid, data['chapter_id']) is not None
            return True
        refs = data.get(ASSET_REVISIONS_FIELD)
        if refs is None:
            return True  # Pre-existing shared assets remain available to History.
        if not isinstance(refs, list) or not refs or len(refs) > 64:
            raise HostedValidationError('HOSTED_MANUSCRIPT_ASSET_REVISION_INVALID')
        for ref in refs:
            if (not isinstance(ref, dict) or set(ref) != {'tab_id', 'revision'}
                    or not isinstance(ref['tab_id'], str) or not isinstance(ref['revision'], str)):
                raise HostedValidationError('HOSTED_MANUSCRIPT_ASSET_REVISION_INVALID')
            revision = require_opaque_id(ref['revision'], label='manuscript_revision')
            tab = self._tab(pid, ref['tab_id'])
            if tab is not None and _revision(tab.data, HEAD_FIELD) == revision:
                return True
        return False

    def get(self, path):
        row = self._physical(path)
        if self.include_retained or self._visible(row):
            return _copy(decode_project_root(row))
        return None

    def get_many(self, paths):
        keys = [require_document_path(path) for path in paths]
        missing = list(dict.fromkeys(path for path in keys if path not in self.original))
        if missing:
            for path, row in zip(missing, self.tx.get_many(missing), strict=True):
                self.original[path] = row
        return [self.get(path) for path in keys]

    def query_collection(self, path, *, field=None, value=None):
        prefix = tuple(path)
        # Lifecycle status is encoded only at physical project roots.
        status_query = prefix == ('projects',) and field == 'status'
        rows = self.tx.query_collection(prefix, field=None if status_query else field, value=value)
        found = {}
        for row in rows:
            self.original.setdefault(row.path, row)
            found[row.path] = row
        for key in self.pending:
            if key[:-1] == prefix:
                found[key] = self._physical(key)
        result = []
        for key in sorted(found):
            row = self.get(key)
            if row is not None and (field is None or row.data.get(field) == value):
                result.append(row)
        return result

    def count_collection(self, path):
        prefix = tuple(path)
        if len(prefix) == 3 and prefix[0] == 'projects' and prefix[2] in _GATED:
            return len(self.query_collection(prefix))
        if self.pending:
            return len(self.query_collection(prefix))
        return self.tx.count_collection(prefix)

    def _write(self, path, data, *, create=False):
        key = require_document_path(path)
        before = self._physical(key)
        if create and before is not None:
            raise DocumentAlreadyExists('HOSTED_DOCUMENT_ALREADY_EXISTS')
        source = self._original(key)
        if source is not None and not self.include_retained:
            # Source eligibility uses the original snapshot. A chapter moved
            # earlier in this same callback must not hide its still-to-be-moved
            # children; final validity is checked against the complete overlay.
            original_view = _RevisionTransaction(self.tx)
            original_view.original = self.original
            if not original_view._visible(source):
                raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        value = None if data is None else deepcopy(dict(data))
        if value is not None:
            if key[0] == 'projects' and len(key) == 2:
                value = encode_project_root(value, self._original(key))
            elif not self.include_retained and value != self.prepared.get(key):
                for marker in _MANAGED:
                    if value.get(marker) != (before.data.get(marker) if before else None):
                        raise HostedValidationError('HOSTED_MANUSCRIPT_FIELD_RESERVED')
        self.pending[key] = value

    def create(self, path, data): self._write(path, data, create=True)
    def set(self, path, data): self._write(path, data)
    def delete(self, path): self._write(path, None)

    def prepare_mutations(self, pending):
        """Let the outer journal capture EXACT normalized product after-images.

        Validate the caller's unnormalized writes first, then derive only the
        server-owned revision fields. No provider write happens in this method.
        """
        probe = _RevisionTransaction(self.tx, include_retained=self.include_retained)
        probe.original = self.original
        for path, value in pending.items():
            probe._write(path, value)
        probe._normalize()
        result = {}
        for path, value in probe.pending.items():
            result[path] = (None if value is None else dict(decode_project_root(
                StoredDocument(path, value, 0)).data))
        self.prepared.update(deepcopy(result))
        return result

    def _normalize(self):
        # Normal creates/moves inherit the current tab revision; stale sources
        # may not be resurrected by raw editing, cache, or entity-store writes.
        if not self.include_retained:
            for path, value in self.pending.items():
                if value is None or len(path) != 4 or path[0] != 'projects' or path[2] not in _GATED:
                    continue
                if not self._enabled(path[1]):
                    continue
                family = path[2]
                if family == 'chapters' or (family == 'scenes' and not value.get('chapter_id')):
                    tab = self._tab(path[1], value.get('tab_id'))
                    if tab is None:
                        raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
                    head = _revision(tab.data, HEAD_FIELD)
                    if head is not None:
                        value[REVISION_FIELD] = head
                    else:
                        value.pop(REVISION_FIELD, None)
            # Validate the completed overlay, independent of write order.
            for path, value in self.pending.items():
                if (value is not None and len(path) == 4 and path[0] == 'projects'
                        and path[2] in _GATED and self._enabled(path[1])
                        and not self._visible(StoredDocument(path, value, 0))):
                    raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')

    def flush(self):
        self._normalize()
        for path, value in self.pending.items():
            before = self._original(path)
            if value is None:
                self.tx.delete(path)
            elif before is None:
                self.tx.create(path, value)
            else:
                self.tx.set(path, value)


class ManuscriptDocumentStore:
    """All normal point/list/count/write paths use the same active revision view."""
    def __init__(self, store, *, include_retained=False):
        self.store = store
        self.include_retained = include_retained

    def __getattr__(self, name):
        return getattr(self.store, name)

    def retained_view(self):
        return ManuscriptDocumentStore(self.store, include_retained=True)

    def run_transaction(self, fn, *, max_attempts=5):
        def execute(tx):
            view = _RevisionTransaction(tx, include_retained=self.include_retained)
            result = fn(view)
            view.flush()
            return result
        return self.store.run_transaction(execute, max_attempts=max_attempts)

    def get(self, path):
        return self.run_transaction(lambda tx: tx.get(path))

    def query_collection(self, path, *, field=None, value=None):
        return self.run_transaction(lambda tx: tx.query_collection(path, field=field, value=value))

    def query_collection_group(self, collection_id, *, field, value):
        # Project/member discovery also uses this entry point. Row dependencies
        # are evaluated together, rather than by independently stale point reads.
        rows = self.store.query_collection_group(collection_id, field=field, value=value)
        def load(tx):
            found = tx.get_many([row.path for row in rows])
            return [row for row in found if row is not None and row.data.get(field) == value]
        return self.run_transaction(load)
