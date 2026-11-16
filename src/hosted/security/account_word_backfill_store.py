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
class BackfillBudgetExceeded(BackfillUnavailable): pass


class BackfillBudget:
    """Conservative whole-invocation allowance, shared across owner/job steps.

    Reserved attempts are never refunded. This is an operator safety envelope,
    not a new product refresh allowance or proof of monthly billing headroom.
    """
    def __init__(self,*,reads=20_000,writes=4_000,rpcs=2_000,content_bytes=64*1024*1024,
                 download_bytes=64*1024*1024,seconds=600):
        values=dict(reads=reads,writes=writes,rpcs=rpcs,content_bytes=content_bytes,download_bytes=download_bytes)
        maxima=dict(reads=100_000,writes=10_000,rpcs=10_000,content_bytes=512*1024*1024,download_bytes=512*1024*1024)
        if any(type(v) is not int or not 0<v<=maxima[k] for k,v in values.items()) or type(seconds) is not int or not 1<=seconds<=900:
            raise BackfillUnavailable('WORD_BACKFILL_BUDGET_INVALID')
        self.limits=values;self.used=dict.fromkeys(values,0);self.deadline=time.monotonic()+seconds

    def reserve(self,**charges):
        if time.monotonic()>=self.deadline:raise BackfillBudgetExceeded('WORD_BACKFILL_INVOCATION_DEADLINE')
        if any(k not in self.limits or type(v) is not int or v<0 for k,v in charges.items()):
            raise BackfillUnavailable('WORD_BACKFILL_BUDGET_INVALID')
        if any(self.used[k]+v>self.limits[k] for k,v in charges.items()):
            raise BackfillBudgetExceeded('WORD_BACKFILL_INVOCATION_BUDGET')
        for k,v in charges.items():self.used[k]+=v


class _Transaction(_GoogleTransaction):
    def __init__(self, store, transaction, deadline):
        super().__init__(store.client, transaction)
        self.owner, self.deadline, self.reads, self.budget = store.owner, deadline, 0,store.budget

    def remaining(self):
        left = self.deadline - time.monotonic()
        if self.budget is not None:self.budget.reserve()
        if not 0 < left <= 30: raise BackfillUnavailable('WORD_BACKFILL_DEADLINE')
        return min(2, left)

    def charge_read(self,units=1,**charges):
        self.reads += 1
        if self.reads > 512: raise BackfillUnavailable('WORD_BACKFILL_READ_BUDGET')
        if self.budget is not None:self.budget.reserve(reads=units,rpcs=1,**charges)

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
        self.charge_read(2)
        rows = super().saved_word_owners(pid, timeout=min(timeout, self.remaining()))
        self.remaining(); return rows

    def saved_word_active_projects(self, *, timeout=2):
        self.charge_read(101)
        rows = super().saved_word_active_projects(timeout=min(timeout, self.remaining()))
        self.remaining(); return rows

    def saved_word_page(self, pid, *, after_id=None, limit=33, timeout=2):
        self.charge_read(limit)
        rows = super().saved_word_page(pid, after_id=after_id, limit=limit,
                                       timeout=min(timeout, self.remaining()))
        self.remaining(); return rows

    def saved_word_block(self, pid, bid, *, timeout=2):
        self.charge_read()
        row=super().saved_word_block(pid,bid,timeout=min(timeout,self.remaining()))
        self.remaining();return row

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
    def __init__(self, client, *, project_id, owner_user_id,budget=None):
        if (type(project_id) is not str or re.fullmatch('[a-z][a-z0-9-]{4,61}[a-z0-9]', project_id) is None
                or client.project != project_id or client._database != '(default)'):
            raise BackfillUnavailable('WORD_BACKFILL_DATABASE_SCOPE')
        self.client, self.owner = client, require_user_id(owner_user_id)
        if budget is not None and type(budget) is not BackfillBudget:raise BackfillUnavailable('WORD_BACKFILL_BUDGET_INVALID')
        self.budget=budget
        self.database = 'projects/' + project_id + '/databases/(default)'

    def _rpc(self, method, request, deadline):
        remaining = deadline - time.monotonic()
        if not 0 < remaining <= 30: raise BackfillUnavailable('WORD_BACKFILL_DEADLINE')
        if self.budget is not None:self.budget.reserve(rpcs=1,**({'writes':len(request['writes'])} if method=='commit' else {}))
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
            if isinstance(error,BackfillBudgetExceeded):raise
            if committing: raise BackfillOutcomeUnknown('WORD_BACKFILL_COMMIT_UNKNOWN') from None
            if isinstance(error, BackfillUnavailable): raise
            raise BackfillUnavailable('WORD_BACKFILL_REJECTED') from None
        finally:
            if token and not committed and time.monotonic() < deadline:
                try: self._rpc('rollback', {'database': self.database, 'transaction': token}, deadline)
                except Exception: pass
