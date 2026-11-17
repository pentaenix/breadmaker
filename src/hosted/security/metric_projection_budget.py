"""Optional count-work boundary; no provider, credentials, flags or local wiring.

A trusted private runtime may inject an adapter. Exhaustion discards reporting
updates, never reruns the canonical callback or bypasses its lifecycle fence.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json,time

class ProjectionBudgetUnavailable(Exception):pass
_TRANSACTION=ContextVar('metric_projection_transaction',default=None)
_OPERATION=ContextVar('metric_projection_operation',default=None)

def current_scope():return _TRANSACTION.get()
def current_operation():return _OPERATION.get()

class ProjectionScope:
    def __init__(self,adapter):self.adapter,self.failed=adapter,adapter is None
    def remaining(self):
        if self.failed:raise ProjectionBudgetUnavailable()
        try:
            value=self.adapter.remaining() if callable(getattr(self.adapter,'remaining',None)) else 2
            if value<=0:raise ValueError()
            return min(2,value)
        except Exception:
            self.failed=True;raise ProjectionBudgetUnavailable() from None
    def reserve(self,**charges):
        if self.failed:raise ProjectionBudgetUnavailable()
        try:self.adapter.reserve(**charges)
        except Exception:
            self.failed=True;raise ProjectionBudgetUnavailable() from None
    def envelope(self,kind,key,born,maximum):
        if self.failed:raise ProjectionBudgetUnavailable()
        try:self.adapter.envelope(kind,key,born,maximum)
        except Exception:
            self.failed=True;raise ProjectionBudgetUnavailable() from None

@contextmanager
def transaction_scope(adapter):
    scope=ProjectionScope(adapter);token=_TRANSACTION.set(scope)
    try:yield scope
    finally:_TRANSACTION.reset(token)

class ProjectionTransaction:
    """Prepay bounded logical reads; the private API guard forbids retries.

    Scope is active only around optional metadata reads. Canonical reads and
    flushes keep their original provider behavior. Count maintenance performs
    no arbitrary collection query and cannot read manuscript bodies here.
    """
    def __init__(self,tx,scope):self.tx,self.scope=tx,scope
    def __getattr__(self,name):return getattr(self.tx,name)
    def _read(self,reads,fn,*args,**kw):
        self.scope.reserve(reads=reads)
        token=_OPERATION.set({'dispatched':False,'deadline':time.monotonic()+self.scope.remaining()})
        try:return fn(*args,**kw)
        except Exception:
            # These calls are optional, read-only metadata work. The canonical
            # buffer still passes its original lifecycle/ownership checks later.
            self.scope.failed=True;raise ProjectionBudgetUnavailable() from None
        finally:_OPERATION.reset(token)
    def get(self,path):return self._read(1,self.tx.get,path)
    def get_many(self,paths):
        paths=list(paths)
        if len(paths)>100:raise ProjectionBudgetUnavailable()
        return self._read(len(paths),self.tx.get_many,paths)
    def saved_word_owners(self,pid,*,timeout):return self._read(2,self.tx.saved_word_owners,pid,timeout=timeout)
    def saved_word_metadata(self,pid,*,limit,timeout):
        if type(limit) is not int or not 1<=limit<=1001:raise ProjectionBudgetUnavailable()
        return self._read(limit,self.tx.saved_word_metadata,pid,limit=limit,timeout=timeout)
    def query_collection(self,*a,**kw):raise ProjectionBudgetUnavailable()
    def count_collection(self,*a,**kw):raise ProjectionBudgetUnavailable()

def write_envelope(path,data):
    # Fixed numeric schemas and bounded opaque identifiers only. This deliberately
    # over-reserves document/default-index bytes; it is not serialized plaintext.
    raw=json.dumps([list(path),data],sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
    if len(raw)>512*1024:raise ProjectionBudgetUnavailable()
    return 2048+32*len(raw)
