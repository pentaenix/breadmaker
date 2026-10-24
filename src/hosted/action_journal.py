"""Bounded, server-only structural action journal, shared across hosted replicas.

Each product write and its before/after images commit in ONE Firestore transaction.
Recovery never writes GCS or permissions and refuses intervening project edits.
No snapshots of the entire tenant, process-local tokens, or unsafe best-effort restore.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import secrets
import time

from .firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError, StoredDocument
from .firestore.ids import require_opaque_id, require_project_id, require_user_id
from .firestore.repositories import HostedProjectRepository

_ACTION = ContextVar('hosted_structural_action', default=None)
MAX_RECORDS = 100
MAX_BYTES = 450_000
MAX_HISTORY = 100
RETENTION_SECONDS = 7 * 86400
# Permission/session/invitation/history receipts are deliberately NOT restorable.
_PRODUCT_COLLECTIONS = frozenset({'annotations', 'canvases', 'chapters', 'cuts',
    'tabs', 'blocks', 'story_maps', 'milestones', 'references', 'reviews', 'scenes', 'tasks',
    'checkpoints', 'comment_threads', 'icon_markers', 'markup_labels', 'questionnaires'})
_DENIED_COLLECTIONS = frozenset({'members', 'participants', 'sessions', 'invitations',
    'mutation_receipts', 'backup_destinations'})


@contextmanager
def bind_action(token, project_id, user_id):
    value = None if not token else (require_opaque_id(token, label='undo_token'),
        require_project_id(project_id), require_user_id(user_id))
    reset = _ACTION.set(value)
    try:
        yield
    finally:
        _ACTION.reset(reset)


def _path(uid, token):
    return ('hosted_actions', require_user_id(uid), 'tokens', require_opaque_id(token, label='undo_token'))


def _data(row):
    return deepcopy(dict(row.data)) if row is not None else None


def _authorize(tx, pid, uid):
    root, member = HostedProjectRepository(None)._require_member(tx, pid, uid)
    if member.get('role') not in {'owner', 'editor'}:
        raise DocumentNotFound('UNDO_TOKEN_NOT_FOUND')
    return root


def _load(tx, pid, uid, token):
    root = _authorize(tx, pid, uid)
    row = tx.get(_path(uid, token))
    if row is None or row.data.get('project_id') != pid:
        raise DocumentNotFound('UNDO_TOKEN_NOT_FOUND')
    action = deepcopy(dict(row.data))
    if float(action['expires_at']) <= time.time():
        raise DocumentConflict('HOSTED_ACTION_EXPIRED_REOPEN_PROJECT')
    return root, action


def _same_generation(root, action):
    if int(root['generation']) != int(action['generation']):
        raise DocumentConflict('HOSTED_ACTION_STALE_REOPEN_PROJECT')


def _semantic(data):
    return None if data is None else {k:v for k,v in data.items() if k not in {'generation','updated_at','_bm_read_bundle'}}


def _without_read_cache(data):
    # Cache attachment changes no canonical field, generation or authority.
    # Keep ALL other journal comparisons strict, including ABA generations.
    return None if data is None else {k:v for k,v in data.items() if k!='_bm_read_bundle'}


class _BufferedTransaction:
    """Delay provider writes until journal checks/reads finish (Firestore rule)."""
    def __init__(self, tx):
        self.tx, self.pending, self.before = tx, {}, {}

    def get(self, path):
        path = tuple(path)
        if path in self.pending:
            value = self.pending[path]
            return None if value is None else StoredDocument(path, deepcopy(value), 0)
        return self.tx.get(path)

    def query_collection(self, path, **kwargs):
        # Current product callbacks query before modifying a collection. Refuse
        # unsupported read-after-write rather than silently changing query results.
        if any(p[:-1] == tuple(path) for p in self.pending):
            raise HostedValidationError('HOSTED_ACTION_QUERY_AFTER_WRITE')
        return self.tx.query_collection(path, **kwargs)

    def _stage(self, path, data, create=False):
        path = tuple(path)
        if path not in self.before:
            self.before[path] = _data(self.tx.get(path))
        if create and self.get(path) is not None:
            from .firestore.contracts import DocumentAlreadyExists
            raise DocumentAlreadyExists('HOSTED_DOCUMENT_ALREADY_EXISTS')
        self.pending[path] = deepcopy(dict(data)) if data is not None else None

    def set(self, path, data): self._stage(path, data)
    def create(self, path, data): self._stage(path, data, create=True)
    def delete(self, path): self._stage(path, None)


class ActionDocumentStore:
    """Transparent hosted store; journals ONLY explicitly bound product requests."""
    def __init__(self, store): self.store = store
    def __getattr__(self, name): return getattr(self.store, name)

    def run_transaction(self, fn, *, max_attempts=5):
        context = _ACTION.get()
        if context is None:
            return self.store.run_transaction(fn, max_attempts=max_attempts)
        token, pid, uid = context

        def execute(tx):
            buffered = _BufferedTransaction(tx)
            result = fn(buffered)
            if not buffered.pending:
                return result
            root, action = _load(tx, pid, uid, token)
            if action['state'] != 'open':
                raise DocumentConflict('HOSTED_ACTION_ALREADY_FINISHED')
            _same_generation(root, action)
            records = {tuple(r['path']): r for r in action['records']}
            root_path = ('projects', pid)
            for path, after in buffered.pending.items():
                before = buffered.before[path]
                if path == root_path:
                    # Product mutations advance generation, never journal ACLs,
                    # lifecycle status, project titles, or other root authority.
                    clean = lambda row: {k:v for k,v in (row or {}).items() if k not in {'generation', 'updated_at'}}
                    if clean(before) != clean(after):
                        raise HostedValidationError('HOSTED_ACTION_ROOT_AUTHORITY_CHANGE_FORBIDDEN')
                    continue
                # Immutable idempotency receipts must survive Undo. Chapter
                # replay checks project generation before accepting such receipts.
                if path[:3] == root_path + ('mutation_receipts',) and before is None and after is not None:
                    continue
                if (path[:2] != root_path or len(path) < 4 or path[2] not in _PRODUCT_COLLECTIONS
                        or any(p in _DENIED_COLLECTIONS for p in path[2::2])):
                    raise HostedValidationError('HOSTED_ACTION_ENTITY_UNSUPPORTED')
                if path in records and _without_read_cache(records[path]['after']) != _without_read_cache(before):
                    raise DocumentConflict('HOSTED_ACTION_RECORD_CHANGED')
                record = records.setdefault(path, {'path': list(path), 'before': before})
                record['after'] = after
                record['high_water'] = max(int(record.get('high_water', 0)),
                    int((before or {}).get('generation', 0)), int((after or {}).get('generation', 0)))
            action['records'] = list(records.values())
            next_root = buffered.pending.get(root_path)
            if next_root is None:
                next_root = {**root, 'generation': int(root['generation']) + 1}
                buffered.pending[root_path] = next_root
            action['generation'] = int(next_root['generation'])
            if action['generation'] <= int(root['generation']):
                raise DocumentConflict('HOSTED_ACTION_GENERATION_NOT_ADVANCED')
            if len(records) > MAX_RECORDS or len(json.dumps(action, default=str).encode()) > MAX_BYTES:
                raise HostedValidationError('HOSTED_ACTION_TOO_LARGE')
            for path, data in buffered.pending.items():
                if data is None: tx.delete(path)
                elif buffered.before.get(path) is None and path != root_path: tx.create(path, data)
                else: tx.set(path, data)
            tx.set(_path(uid, token), action)
            return result
        return self.store.run_transaction(execute, max_attempts=max_attempts)


class ActionJournal:
    def __init__(self, documents):
        self.documents = documents.store if isinstance(documents, ActionDocumentStore) else documents

    def begin(self, pid, uid, label='', scope='full', scope_id=None):
        pid, uid = require_project_id(pid), require_user_id(uid)
        token, now = 'undo_' + secrets.token_hex(16), time.time()
        def create(tx):
            root = _authorize(tx, pid, uid)
            parent = root.get('_bm_structural_head')
            parent_row = tx.get(_path(uid,parent)) if parent and root.get('_bm_structural_actor') == uid else None
            if (parent_row is None or parent_row.data.get('state') != 'finished'
                    or parent_row.data.get('generation') != root['generation']):
                parent = None
            # User-scoped, bounded history; no scheduled poller or new service.
            rows = tx.query_collection(('hosted_actions', uid, 'tokens'))
            rows = sorted(rows, key=lambda r: float(r.data['created_at']))
            expired = [r for r in rows if float(r.data['expires_at']) <= now]
            retained = [r for r in rows if r not in expired]
            discard = expired + retained[:max(0, len(retained) - MAX_HISTORY + 1)]
            for row in discard: tx.delete(row.path)
            tx.create(_path(uid, token), {'project_id': pid, 'state': 'open',
                'generation': int(root['generation']), 'records': [],
                'parent': parent,
                'created_at': now, 'expires_at': now + RETENTION_SECONDS,
                'label': str(label)[:256], 'scope': str(scope)[:32], 'scope_id': scope_id})
        self.documents.run_transaction(create)
        return {'token': token}

    def finish(self, pid, uid, token):
        def finish(tx):
            root, action = _load(tx, pid, uid, token)
            _same_generation(root, action)
            if action['state'] not in {'open', 'finished'}:
                raise DocumentConflict('HOSTED_ACTION_ALREADY_FINISHED')
            action['state'] = 'finished'
            if action['records']:
                root['_bm_structural_head'], root['_bm_structural_actor'] = token, uid
                tx.set(('projects',pid),root)
            tx.set(_path(uid, token), action)
            return {'ok': True, 'changed': bool(action['records'])}
        return self.documents.run_transaction(finish)

    def restore(self, pid, uid, token, direction):
        if direction not in {'undo', 'redo', 'rollback'}:
            raise HostedValidationError('HOSTED_ACTION_DIRECTION_INVALID')
        def restore(tx):
            root, action = _load(tx, pid, uid, token)
            _same_generation(root, action)
            target_state = {'undo':'undone', 'redo':'finished', 'rollback':'rolled_back'}[direction]
            if action['state'] == target_state:
                return {'ok': True, 'changed': False}
            allowed = {'undo':{'finished'}, 'redo':{'undone'}, 'rollback':{'open'}}[direction]
            if action['state'] not in allowed:
                raise DocumentConflict('HOSTED_ACTION_STATE_CONFLICT')
            if direction == 'undo' and (root.get('_bm_structural_head'),root.get('_bm_structural_actor')) != (token,uid):
                raise DocumentConflict('HOSTED_ACTION_HISTORY_CHANGED')
            pending = []
            for record in action['records']:
                path = tuple(record['path'])
                current = _data(tx.get(path))
                expected = record.get('current', record['after'])
                if _without_read_cache(current) != _without_read_cache(expected):
                    raise DocumentConflict('HOSTED_ACTION_RECORD_CHANGED')
                desired = deepcopy(record['after' if direction == 'redo' else 'before'])
                if desired is not None:
                    # Never reuse an old record generation after a restore (ABA).
                    desired['generation'] = max(int((current or {}).get('generation', 0)),
                        int(desired.get('generation', 0)), int(record.get('high_water', 0))) + 1
                    record['high_water'] = desired['generation']
                record['current'] = desired
                pending.append((path, desired))
            root['generation'] = int(root['generation']) + 1
            action['generation'], action['state'] = root['generation'], target_state
            root['_bm_structural_head'] = token if direction == 'redo' else action.get('parent')
            root['_bm_structural_actor'] = uid
            # Preserve a consecutive Undo/Redo chain without ever reusing old
            # record/project generations. Only rebase a related journal when ALL
            # its expected content matches the state this restore will publish.
            related_token = action.get('redo_child') if direction == 'redo' else action.get('parent')
            related = tx.get(_path(uid,related_token)) if related_token else None
            related_data = deepcopy(dict(related.data)) if related else None
            pending_map = dict(pending)
            if related_data and related_data.get('project_id') == pid:
                valid = related_data['state'] == ('undone' if direction == 'redo' else 'finished')
                for record in related_data['records']:
                    path=tuple(record['path'])
                    actual=pending_map[path] if path in pending_map else _data(tx.get(path))
                    expected=record.get('current',record['after'])
                    if _semantic(actual) != _semantic(expected): valid=False
                    record['current']=actual
                if valid:
                    related_data['generation']=root['generation']
                    if direction != 'redo': related_data['redo_child']=token
                else:
                    related_data=None
            # All membership, root generation and record reads precede any write.
            for path, desired in pending:
                if desired is None: tx.delete(path)
                else: tx.set(path, desired)
            tx.set(('projects', pid), root)
            tx.set(_path(uid, token), action)
            if related_data: tx.set(_path(uid,related_token),related_data)
            return {'ok': True, 'changed': bool(pending)}
        return self.documents.run_transaction(restore)
