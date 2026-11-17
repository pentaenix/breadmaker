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
                 download_bytes=64*1024*1024,object_rpcs=2_000,response_bytes=64*1024*1024,seconds=600,campaign=None,resource_budget=None):
        values=dict(reads=reads,writes=writes,rpcs=rpcs,content_bytes=content_bytes,download_bytes=download_bytes,
                    object_rpcs=object_rpcs,response_bytes=response_bytes)
        maxima=dict(reads=100_000,writes=10_000,rpcs=10_000,content_bytes=512*1024*1024,download_bytes=512*1024*1024,
                    object_rpcs=10_000,response_bytes=512*1024*1024)
        if any(type(v) is not int or not 0<v<=maxima[k] for k,v in values.items()) or type(seconds) is not int or not 1<=seconds<=900:
            raise BackfillUnavailable('WORD_BACKFILL_BUDGET_INVALID')
        if campaign is not None:
            from .account_word_campaign import BackfillCampaign
            if type(campaign) is not BackfillCampaign:raise BackfillUnavailable('WORD_BACKFILL_CAMPAIGN_REQUIRED')
        self.campaign=campaign;self.resource_budget=resource_budget;self.limits=values;self.used=dict.fromkeys(values,0);self.deadline=time.monotonic()+seconds;self.failed=False

    def reserve(self,**charges):
        if self.failed or time.monotonic()>=self.deadline:raise BackfillBudgetExceeded('WORD_BACKFILL_INVOCATION_DEADLINE')
        if any(k not in self.limits or k=='response_bytes' or type(v) is not int or v<0 for k,v in charges.items()):
            raise BackfillUnavailable('WORD_BACKFILL_BUDGET_INVALID')
        if any(self.used[k]+v>self.limits[k] for k,v in charges.items()):
            raise BackfillBudgetExceeded('WORD_BACKFILL_INVOCATION_BUDGET')
        if self.campaign is not None:self.campaign.reserve(**charges)
        for k,v in charges.items():self.used[k]+=v

    def observe_response(self,size):
        # The final native response has already arrived. Record it even when
        # it crosses a cap, then stop before SDK decode or any later operation.
        if type(size) is not int or not 0<=size<=4*1024*1024:raise BackfillUnavailable('WORD_BACKFILL_RESPONSE_INVALID')
        self.used['response_bytes']+=size
        if self.campaign is not None:self.campaign.observe_response(size)
        if self.used['response_bytes']>self.limits['response_bytes']:
            self.failed=True;raise BackfillBudgetExceeded('WORD_BACKFILL_RESPONSE_BUDGET')


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

    def saved_word_owner_batch(self, pids, *, timeout=2):
        from hosted.firestore.owner_queries import project_ids, OWNER_BATCH_SIZE
        pids=project_ids(pids)
        if not 0<len(pids)<=OWNER_BATCH_SIZE:raise BackfillUnavailable('WORD_BACKFILL_OWNER_SCOPE')
        self.charge_read(len(pids)+1)
        rows=super().saved_word_owner_batch(pids,timeout=min(timeout,self.remaining()))
        self.remaining();return rows

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

    def _rpc(self, method, request, deadline, before_dispatch=None):
        remaining = deadline - time.monotonic()
        if not 0 < remaining <= 30: raise BackfillUnavailable('WORD_BACKFILL_DEADLINE')
        if self.budget is not None:self.budget.reserve(rpcs=1,**({'writes':len(request['writes'])} if method=='commit' else {}))
        if method=='commit' and self.budget is not None and self.budget.resource_budget is not None:
            self.budget.resource_budget.before_writes(request['writes'],budget=self.budget)
            remaining = deadline - time.monotonic()
            if not 0 < remaining <= 30:raise BackfillBudgetExceeded('WORD_RESOURCE_ADMISSION_DEADLINE')
        api=self.client._firestore_api
        options=dict(metadata=self.client._rpc_metadata,retry=None,timeout=min(2,remaining))
        if before_dispatch is not None:
            if getattr(type(api),'supports_before_dispatch',False):options['before_dispatch']=before_dispatch
            else:before_dispatch()
        result = getattr(api, method)(request=request,**options)
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
            def dispatched():
                nonlocal committing
                committing=True
            self._rpc('commit', {'database': self.database, 'transaction': token,
                'writes': list(transaction._write_pbs)}, deadline,before_dispatch=dispatched)
            committed = True
            return result
        except BaseException as error:
            if committing:
                if not isinstance(error,Exception):
                    error._word_backfill_commit_unknown=True
                    raise
                raise BackfillOutcomeUnknown('WORD_BACKFILL_COMMIT_UNKNOWN') from None
            if isinstance(error,BackfillBudgetExceeded):raise
            if isinstance(error, BackfillUnavailable): raise
            if not isinstance(error,Exception):raise
            raise BackfillUnavailable('WORD_BACKFILL_REJECTED') from None
        finally:
            # Once commit was dispatched, its outcome is uncertain. Do not send
            # another provider request or replace the original failure while
            # attempting cleanup of a transaction that may already have landed.
            if token and not committed and not committing and time.monotonic() < deadline:
                try: self._rpc('rollback', {'database': self.database, 'transaction': token}, deadline)
                except Exception: pass
