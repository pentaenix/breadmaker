"""Scoped finite Google metadata transactions for explicit word backfill.

Injected existing client only. No credential, IAM, account or canonical-content
write; no SDK transaction decorator, automatic retry, delete or independent sum.
"""
import re
import time
from hosted.firestore.contracts import StoredDocument
from hosted.firestore.google_driver import _GoogleTransaction, _snapshot_revision
from hosted.firestore.ids import require_user_id, require_project_id


class BackfillUnavailable(ValueError): pass
class BackfillOutcomeUnknown(BackfillUnavailable): pass


class _Transaction(_GoogleTransaction):
    def __init__(self, store, transaction, deadline):
        super().__init__(store.client, transaction)
        self.owner, self.deadline, self.reads = store.owner, deadline, 0

    def remaining(self):
        left = self.deadline - time.monotonic()
        if not 0 < left <= 30: raise BackfillUnavailable('WORD_BACKFILL_DEADLINE')
        return min(2, left)

    def charge_read(self):
        self.reads += 1
        if self.reads > 512: raise BackfillUnavailable('WORD_BACKFILL_READ_BUDGET')

    def _fields(self, path):
        if path == ('users', self.owner): return ['user_id', 'status', 'created_at']
        if path == ('account_erasure_locks', self.owner): return ['user_id']
        if len(path) == 2 and path[0] == 'projects':
            require_project_id(path[1]); return ['status', 'generation', 'created_at']
        if len(path) == 4 and path[0] == 'projects' and path[2:] == ('members', self.owner):
            require_project_id(path[1]); return ['user_id', 'role', 'created_at']
        if self._writable(path): return None
        raise BackfillUnavailable('WORD_BACKFILL_READ_SCOPE')

    def _writable(self, path):
        return (len(path) == 4 and path[:2] == ('users', self.owner) and
            (path[2:] == ('metrics', 'saved_words_v1') or
             path[2] == 'word_block_counts' and re.fullmatch('[0-9a-f]{64}', path[3]) is not None or
             path[2] == 'metric_jobs' and re.fullmatch('word_[0-9a-f]{64}', path[3]) is not None))

    def get(self, path):
        path = tuple(path); fields = self._fields(path)
        self.charge_read()
        row = self._ref(path).get(field_paths=fields, transaction=self._transaction,
                                  timeout=self.remaining(), retry=None)
        self.remaining()
        return StoredDocument(path, row.to_dict() or {}, _snapshot_revision(row)) if row.exists else None

    def get_many(self, paths): return [self.get(path) for path in paths]

    def saved_word_owners(self, pid, *, timeout=2):
        self.charge_read()
        rows = super().saved_word_owners(pid, timeout=min(timeout, self.remaining()))
        self.remaining(); return rows

    def saved_word_memberships(self, uid, *, timeout=2):
        if uid != self.owner: raise BackfillUnavailable('WORD_BACKFILL_OWNER_SCOPE')
        self.charge_read()
        rows = super().saved_word_memberships(uid, timeout=min(timeout, self.remaining()))
        self.remaining(); return rows

    def saved_word_page(self, pid, *, after_id=None, limit=33, timeout=2):
        self.charge_read()
        rows = super().saved_word_page(pid, after_id=after_id, limit=limit,
                                       timeout=min(timeout, self.remaining()))
        self.remaining(); return rows

    def set(self, path, data):
        if not self._writable(tuple(path)): raise BackfillUnavailable('WORD_BACKFILL_WRITE_SCOPE')
        if len(self._transaction._write_pbs) >= 128: raise BackfillUnavailable('WORD_BACKFILL_WRITE_BUDGET')
        super().set(path, data)

    def create(self, path, data):
        if not self._writable(tuple(path)): raise BackfillUnavailable('WORD_BACKFILL_WRITE_SCOPE')
        if len(self._transaction._write_pbs) >= 128: raise BackfillUnavailable('WORD_BACKFILL_WRITE_BUDGET')
        super().create(path, data)

    def delete(self, path): raise BackfillUnavailable('WORD_BACKFILL_DELETE_FORBIDDEN')
    def query_collection(self, *a, **kw): raise BackfillUnavailable('WORD_BACKFILL_FIXED_QUERY_REQUIRED')
    def count_collection(self, *a, **kw): raise BackfillUnavailable('WORD_BACKFILL_FIXED_QUERY_REQUIRED')


class BoundedAccountWordStore:
    def __init__(self, client, *, project_id, owner_user_id):
        if (type(project_id) is not str or re.fullmatch('[a-z][a-z0-9-]{4,61}[a-z0-9]', project_id) is None
                or client.project != project_id or client._database != '(default)'):
            raise BackfillUnavailable('WORD_BACKFILL_DATABASE_SCOPE')
        self.client, self.owner = client, require_user_id(owner_user_id)
        self.database = 'projects/' + project_id + '/databases/(default)'

    def _rpc(self, method, request, deadline):
        remaining = deadline - time.monotonic()
        if not 0 < remaining <= 30: raise BackfillUnavailable('WORD_BACKFILL_DEADLINE')
        result = getattr(self.client._firestore_api, method)(request=request,
            metadata=self.client._rpc_metadata, retry=None, timeout=min(2, remaining))
        if time.monotonic() >= deadline: raise BackfillUnavailable('WORD_BACKFILL_DEADLINE')
        return result

    def run_transaction(self, fn, *, deadline):
        token = None; committing = committed = False
        try:
            token = self._rpc('begin_transaction', {'database': self.database,
                'options': {'read_write': {}}}, deadline).transaction
            if type(token) is not bytes or not token: raise BackfillUnavailable('WORD_BACKFILL_TRANSACTION')
            transaction = self.client.transaction(max_attempts=1)
            transaction._id = token
            adapter = _Transaction(self, transaction, deadline)
            result = fn(adapter)
            adapter.remaining()
            committing = True
            self._rpc('commit', {'database': self.database, 'transaction': token,
                'writes': list(transaction._write_pbs)}, deadline)
            committed = True
            return result
        except Exception as error:
            if committing: raise BackfillOutcomeUnknown('WORD_BACKFILL_COMMIT_UNKNOWN') from None
            if isinstance(error, BackfillUnavailable): raise
            raise BackfillUnavailable('WORD_BACKFILL_REJECTED') from None
        finally:
            if token and not committed and time.monotonic() < deadline:
                try: self._rpc('rollback', {'database': self.database, 'transaction': token}, deadline)
                except Exception: pass
