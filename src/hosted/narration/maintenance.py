"""Non-HTTP maintenance port; can NEVER mutate canonical/identity/recovery rows.

The operator composition explicitly supplies the underlying provider, not a
generic request-time escape from account lifecycle fences. GC can settle its
own derivative ledger after revocation, but cannot change the revocation.
"""
from hosted.firestore.contracts import require_document_path, require_collection_path
from hosted.narration.recordings import ROOT, USAGE, ATTEMPTS, QUEUE

CONTROL = 'narration_gc_control_v1'
RECEIPTS = 'narration_gc_receipts_v1'


def _writable(path):
    return ((path[0] in {CONTROL, RECEIPTS, USAGE, ATTEMPTS, QUEUE} and len(path) == 2)
            or (path[0] == ROOT and len(path) == 4 and path[2] in {'intents', 'records'}))


class _Transaction:
    def __init__(self, tx): self.tx = tx
    def get(self, path):
        path = require_document_path(path)
        if not (_writable(path) or path[0] in {'projects', 'users', 'account_erasure_locks', 'account_erasures'}):
            raise ValueError('NARRATION_MAINTENANCE_READ_SCOPE')
        return self.tx.get(path)
    def get_many(self, paths): return [self.get(path) for path in paths]
    def query_collection(self, path, **kwargs):
        path = require_collection_path(path)
        if not (len(path) == 3 and ((path[0] == ROOT and path[2] in {'records', 'intents'})
                                   or (path[0] == 'projects' and path[2] == 'members'))):
            raise ValueError('NARRATION_MAINTENANCE_QUERY_SCOPE')
        return self.tx.query_collection(path, **kwargs)
    def _write(self, name, path, *args):
        path = require_document_path(path)
        if not _writable(path):
            raise ValueError('NARRATION_MAINTENANCE_WRITE_SCOPE')
        return getattr(self.tx, name)(path, *args)
    def create(self, path, data): return self._write('create', path, data)
    def set(self, path, data): return self._write('set', path, data)
    def delete(self, path): return self._write('delete', path)


class NarrationMaintenanceStore:
    def __init__(self, provider): self.provider = provider
    def get(self, path): return _Transaction(self.provider).get(path)
    def run_transaction(self, fn, *, max_attempts=5):
        return self.provider.run_transaction(lambda tx: fn(_Transaction(tx)), max_attempts=max_attempts)
